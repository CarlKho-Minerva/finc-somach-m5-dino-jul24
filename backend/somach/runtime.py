"""Real-time orchestration: source -> DSP -> calibration -> game jump."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import Settings
from .dsp import StreamingEMGProcessor, TriggerGate
from .keypress import QuartzKeyInjector
from .learning import ModelManager, SessionRecorder
from .sources import MockSource, SampleSource, create_source

logger = logging.getLogger(__name__)

Publisher = Callable[[dict], Awaitable[None]]


async def _noop_publish(_: dict) -> None:
    return None


@dataclass(slots=True)
class Calibration:
    target_points: int
    active: bool = False
    values: list[float] = field(default_factory=list)
    error: str | None = None
    completed_at: float | None = None

    def begin(self) -> None:
        self.active = True
        self.values.clear()
        self.error = None
        self.completed_at = None

    def abort(self, reason: str) -> None:
        self.active = False
        self.error = reason

    @property
    def progress(self) -> float:
        if not self.active and self.completed_at is not None:
            return 1.0
        return min(1.0, len(self.values) / max(1, self.target_points))

    def snapshot(self, stride_ms: int) -> dict:
        remaining_points = max(0, self.target_points - len(self.values))
        return {
            "active": self.active,
            "progress": self.progress,
            "remaining": remaining_points * stride_ms / 1_000,
            "error": self.error,
        }


class SomachRuntime:
    """Own the single source and processing task for one local demo session."""

    def __init__(
        self,
        settings: Settings,
        *,
        source: SampleSource | None = None,
        injector: QuartzKeyInjector | None = None,
        publisher: Publisher | None = None,
        recorder: SessionRecorder | None = None,
        model_manager: ModelManager | None = None,
        dataset_dir: str | Path = "datasets",
    ):
        settings.validate()
        self.settings = settings
        self.source = source or create_source(settings)
        self.injector = injector or QuartzKeyInjector(enabled=settings.inject_keys)
        self.publisher = publisher or _noop_publish
        self.processor = StreamingEMGProcessor(settings)
        self.gate = TriggerGate(settings.refractory_ms, settings.rearm_ratio)
        self.calibration = Calibration(settings.calibration_points)
        self.recorder = recorder or SessionRecorder(
            settings.sample_rate_hz, dataset_dir=dataset_dir
        )
        self.model = model_manager or ModelManager(
            settings.sample_rate_hz, dataset_dir=dataset_dir
        )

        self.threshold = 50.0
        self.baseline_mean: float | None = None
        self.baseline_std: float | None = None
        self.calibrated = False
        self.armed = False
        self.jump_count = 0
        self.sequence = 0
        self.latest_rms = 0.0
        self.processing_ms = 0.0
        self.pipeline_ms: float | None = None
        self.last_error: str | None = None
        self.started_at = time.monotonic()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        await self.source.start()
        if self.settings.prompt_accessibility:
            self.injector.refresh_trust(prompt=True)
        self._running = True
        self.started_at = time.monotonic()
        self._task = asyncio.create_task(self._run(), name="somach-signal-pipeline")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.recorder.status()["active"]:
            try:
                await asyncio.to_thread(self.recorder.stop)
            except Exception as exc:  # persistence error must not block shutdown
                self.last_error = f"Could not save active recording: {exc}"
                logger.exception("Could not save active recording during shutdown")
        await self.source.stop()

    async def _run(self) -> None:
        try:
            async for block in self.source.blocks():
                if not self._running:
                    break
                processing_started = time.perf_counter()
                result = self.processor.process(block.raw)
                self.latest_rms = result.latest_rms
                self.recorder.ingest(block.raw, result.filtered, block.captured_at)
                self.model.observe(result.filtered)

                jump_events: list[dict] = []
                for point in result.rms_points:
                    event = self._handle_rms(point.value, block.captured_at)
                    if event is not None:
                        jump_events.append(event)

                self.processing_ms = (time.perf_counter() - processing_started) * 1_000
                self.sequence += 1
                await self.publisher(
                    self.telemetry(
                        raw=block.raw,
                        filtered=result.filtered,
                    )
                )
                for event in jump_events:
                    await self.publisher(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc)
            self.source.meta.connected = False
            logger.exception("Signal pipeline stopped")
            await self.publisher({"type": "error", "message": str(exc)})

    def _handle_rms(self, rms: float, captured_at: float) -> dict | None:
        was_calibrating = self.calibration.active
        clipping_active = (
            self.settings.mode == "hardware" and self._live_clip_fraction() > 0.01
        )
        if was_calibrating:
            if self.source.meta.leads_off:
                self.calibration.abort(
                    "Electrode lead-off detected. Reattach all pads and retry."
                )
            elif clipping_active:
                self.calibration.abort(
                    "ADC clipping exceeded 1% during calibration. Fix the "
                    "sensor output range and retry."
                )
            else:
                self.calibration.values.append(rms)
                if len(self.calibration.values) >= self.calibration.target_points:
                    values = np.asarray(self.calibration.values, dtype=np.float64)
                    self.baseline_mean = float(np.mean(values))
                    self.baseline_std = float(np.std(values, ddof=0))
                    self.threshold = self.baseline_mean + (
                        self.settings.threshold_sigma * self.baseline_std
                    )
                    self.calibrated = True
                    self.calibration.active = False
                    self.calibration.completed_at = time.monotonic()
                    # Calibration establishes a threshold but never grants key-posting
                    # authority. The wearer explicitly arms after setup/narration.
                    self.armed = False
                    self.gate.reset()

        # A baseline sample must never double as a command, including the final
        # sample that completes calibration.
        if was_calibrating:
            return None

        enabled = (
            self.armed
            and self.calibrated
            and not self.calibration.active
            and not self.source.meta.leads_off
            and not clipping_active
        )
        detector = self._effective_detector()
        if detector == "model":
            detector_value = float(self.model.probability)
            detector_threshold = self.model.threshold
        else:
            detector_value = rms
            detector_threshold = self.threshold

        now = time.monotonic()
        if not self.gate.evaluate(
            detector_value,
            detector_threshold,
            now,
            enabled=enabled,
        ):
            return None

        key_result = self.injector.post_space()
        self.jump_count += 1
        self.pipeline_ms = (time.perf_counter() - captured_at) * 1_000
        return {
            "type": "jump",
            "timestamp": time.time(),
            "jumpCount": self.jump_count,
            "rms": round(rms, 4),
            "threshold": round(self.threshold, 4),
            "detector": detector,
            "modelProbability": None
            if self.model.probability is None
            else round(self.model.probability, 6),
            "modelThreshold": self.model.threshold,
            "pipelineMs": round(self.pipeline_ms, 3),
            "keyCallMs": key_result.call_ms,
            "keyPosted": key_result.posted,
            "keyError": key_result.reason,
        }

    def begin_calibration(self) -> dict:
        if self.source.meta.leads_off:
            raise ValueError(
                "Electrode lead-off is active. Reattach the pads before calibration."
            )
        if self.settings.mode == "hardware" and self._live_clip_fraction() > 0.01:
            raise ValueError(
                "ADC clipping exceeds 1%. Fix sensor power/SDN/output range "
                "before calibration."
            )
        self.armed = False
        self.calibrated = False
        self.baseline_mean = None
        self.baseline_std = None
        self.calibration.begin()
        self.gate.reset()
        return self.snapshot()

    def set_threshold(self, value: float) -> dict:
        if not np.isfinite(value) or not 0.0 <= value <= 4_095.0:
            raise ValueError("threshold must be a finite value from 0 to 4095")
        self.threshold = float(value)
        self.calibrated = True
        self.calibration.active = False
        self.calibration.error = None
        self.gate.reset()
        return self.snapshot()

    def set_armed(self, armed: bool) -> dict:
        if armed and not self.calibrated:
            raise ValueError("Run the 3-second calibration or set a threshold first")
        if armed and self.source.meta.leads_off:
            raise ValueError("Cannot arm while an electrode lead is off")
        self.armed = bool(armed)
        self.gate.reset()
        return self.snapshot()

    def inject_mock_jump(self) -> dict:
        if not isinstance(self.source, MockSource):
            raise ValueError(  # noqa: TRY004 - invalid runtime mode, not arg type
                "Mock JUMP is available only when the backend uses --mock"
            )
        accepted = self.source.inject_jump()
        return {"accepted": accepted, **self.snapshot()}

    def reset_counter(self) -> dict:
        self.jump_count = 0
        return self.snapshot()

    def prompt_accessibility(self) -> dict:
        self.injector.refresh_trust(prompt=True)
        return self.snapshot()

    def start_recording(self) -> dict:
        self.recorder.start()
        return self.snapshot()

    def mark_recording(self, label: str) -> dict:
        if self.settings.mode == "hardware" and self._live_clip_fraction() > 0.01:
            raise ValueError(
                "Marker rejected: ADC clipping exceeds 1%. Fix sensor "
                "power/SDN/output range before collecting model trials."
            )
        marker = self.recorder.mark(label)
        return {"marker": marker, **self.snapshot()}

    def _live_clip_fraction(self) -> float:
        observed_rate = self.source.meta.sample_rate or self.settings.sample_rate_hz
        firmware_fraction = self.source.meta.clipped / max(1.0, observed_rate)
        return max(firmware_fraction, self.recorder.recent_clip_fraction())

    def stop_recording(self) -> dict:
        saved = self.recorder.stop()
        return {"recordingResult": saved, **self.snapshot()}

    def recording_status(self) -> dict:
        return self.recorder.status()

    def train_model(self) -> dict:
        self.model.train()
        self.gate.reset()
        return self.snapshot()

    def set_model_active(self, active: bool) -> dict:
        self.model.set_active(active)
        self.gate.reset()
        return self.snapshot()

    def _effective_detector(self) -> str:
        if self.model.active and self.model.probability is not None:
            return "model"
        if self.model.active:
            return "rms_fallback"
        return "rms"

    def _common_state(self) -> dict:
        meta = self.source.meta
        return {
            "source": meta.source,
            "device": meta.device,
            "connected": meta.connected,
            "sampleRate": round(meta.sample_rate, 2),
            "expectedSampleRate": self.settings.sample_rate_hz,
            "rms": round(self.latest_rms, 4),
            "threshold": round(self.threshold, 4),
            "baselineMean": None
            if self.baseline_mean is None
            else round(self.baseline_mean, 4),
            "baselineStd": None
            if self.baseline_std is None
            else round(self.baseline_std, 4),
            "calibrated": self.calibrated,
            "armed": self.armed,
            "jumpCount": self.jump_count,
            "leadsOff": meta.leads_off,
            "loPlus": meta.lo_plus,
            "loMinus": meta.lo_minus,
            "clipping": meta.clipped,
            "liveClipFraction": round(self._live_clip_fraction(), 6),
            "dropped": meta.dropped,
            "late": meta.late,
            "hostDroppedBlocks": meta.host_dropped_blocks,
            "processingMs": round(self.processing_ms, 3),
            "pipelineMs": None
            if self.pipeline_ms is None
            else round(self.pipeline_ms, 3),
            "refractoryMs": self.settings.refractory_ms,
            "detector": self._effective_detector(),
            "recording": self.recorder.status(),
            "model": self.model.status(),
            "calibration": self.calibration.snapshot(self.settings.rms_stride_ms),
            "quartz": self.injector.status(),
            "filter": {
                "notchHz": self.settings.notch_hz,
                "notchQ": self.settings.notch_q,
                "bandpassHz": [
                    self.settings.bandpass_low_hz,
                    self.settings.bandpass_high_hz,
                ],
                "bandpassOrder": self.settings.bandpass_prototype_order * 2,
                "rmsWindowMs": self.settings.rms_window_ms,
                "rmsStrideMs": self.settings.rms_stride_ms,
            },
            "error": self.last_error or meta.error,
            "uptime": round(time.monotonic() - self.started_at, 1),
        }

    def snapshot(self) -> dict:
        return {"type": "status", **self._common_state()}

    def telemetry(self, *, raw: np.ndarray, filtered: np.ndarray) -> dict:
        return {
            "type": "telemetry",
            "seq": self.sequence,
            "timestamp": time.time(),
            "raw": raw.astype(int).tolist(),
            "filtered": np.round(filtered, 3).tolist(),
            **self._common_state(),
        }
