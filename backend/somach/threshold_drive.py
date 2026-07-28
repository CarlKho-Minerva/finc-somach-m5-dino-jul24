"""Threshold-based sEMG controller for the directional driving demo.

This module is intentionally independent of the one-channel Flappy runtime.
Sensor A observes the mylohyoid region and Sensor B observes the left masseter:

* A alone -> a one-second UP/forward pulse
* B alone -> a 200 ms LEFT pulse
* A and B overlapping -> a 200 ms RIGHT pulse

Hybrid mode is an explicit, disclosed fallback for a failed second sensor.  It
keeps Channel A on the live serial stream, replaces Channel B with a local
synthetic trace before DSP, and permits only an operator-requested LEFT pulse
on the synthetic channel.  RIGHT is unavailable in hybrid mode.

The short arbitration delay is essential.  It gives the second channel time to
cross its threshold before an A-only or B-only action is committed.  Following
any action, both channels must remain below hysteresis for a continuous quiet
period before another action can be emitted.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

import numpy as np

from .dsp import StreamingEMGProcessor
from .keypress import KeypressResult, QuartzKeyInjector
from .sources import HardwareUnavailable, detect_serial_port, parse_meta_line

logger = logging.getLogger(__name__)

ChannelName = Literal["a", "b"]
DriveAction = Literal["forward", "left", "right"]
Publisher = Callable[[dict], Awaitable[None]]


async def _noop_publish(_: dict) -> None:
    return None


@dataclass(slots=True, frozen=True)
class DualDriveSettings:
    """Acquisition, DSP, arbitration, and server settings."""

    mode: str = "hardware"
    serial_port: str | None = None
    baud_rate: int = 460_800
    sample_rate_hz: int = 1_000
    websocket_batch_samples: int = 20

    notch_hz: float = 60.0
    notch_q: float = 30.0
    bandpass_low_hz: float = 20.0
    bandpass_high_hz: float = 250.0
    bandpass_prototype_order: int = 2
    rms_window_ms: int = 150
    rms_stride_ms: int = 20

    calibration_seconds: float = 3.0
    threshold_sigma: float = 4.0
    threshold_mean_ratio: float = 1.55
    coincidence_ms: int = 80
    # With the calibrated 1.55x-mean threshold, 0.65T lies almost exactly at
    # the resting mean and makes a continuous two-channel quiet hold unlikely.
    # 0.80T preserves hysteresis while keeping relaxed arming deterministic.
    rearm_ratio: float = 0.80
    rearm_hold_ms: int = 180
    forward_pulse_ms: int = 1_000
    turn_pulse_ms: int = 200

    host: str = "127.0.0.1"
    api_port: int = 8_124
    inject_keys: bool = True
    prompt_accessibility: bool = False

    @property
    def rms_window_samples(self) -> int:
        return round(self.sample_rate_hz * self.rms_window_ms / 1_000)

    @property
    def rms_stride_samples(self) -> int:
        return round(self.sample_rate_hz * self.rms_stride_ms / 1_000)

    @property
    def calibration_points(self) -> int:
        return round(
            self.calibration_seconds
            * self.sample_rate_hz
            / self.rms_stride_samples
        )

    @property
    def history_points(self) -> int:
        return max(1, round(2_500 / self.rms_stride_ms))

    def validate(self) -> None:
        nyquist = self.sample_rate_hz / 2
        if self.mode not in {"mock", "hardware", "hybrid"}:
            raise ValueError("mode must be 'mock', 'hardware', or 'hybrid'")
        if self.baud_rate <= 0 or self.sample_rate_hz <= 0:
            raise ValueError("baud rate and sample rate must be positive")
        if self.websocket_batch_samples <= 0:
            raise ValueError("websocket batch size must be positive")
        if not 0 < self.notch_hz < nyquist:
            raise ValueError("notch frequency must be below Nyquist")
        if not 0 < self.bandpass_low_hz < self.bandpass_high_hz < nyquist:
            raise ValueError("bandpass must be ordered and below Nyquist")
        if self.rms_window_samples <= 0 or self.rms_stride_samples <= 0:
            raise ValueError("RMS window and stride must be positive")
        if self.calibration_points < 2:
            raise ValueError("calibration must contain at least two RMS points")
        if not 0 < self.rearm_ratio < 1:
            raise ValueError("rearm ratio must be between zero and one")
        if self.coincidence_ms < self.rms_stride_ms:
            raise ValueError("coincidence window must be at least one RMS stride")
        if self.rearm_hold_ms < self.rms_stride_ms:
            raise ValueError("rearm hold must be at least one RMS stride")
        if self.forward_pulse_ms != 1_000 or self.turn_pulse_ms != 200:
            raise ValueError("drive pulse contract is UP=1000 ms, turns=200 ms")


@dataclass(slots=True)
class DualChannelMeta:
    lo_plus: bool = False
    lo_minus: bool = False
    reported_leads_off: bool = False
    clip_low: int = 0
    clip_high: int = 0

    @property
    def leads_off(self) -> bool:
        return self.reported_leads_off or self.lo_plus or self.lo_minus


@dataclass(slots=True)
class DualSourceMeta:
    source: str
    device: str
    connected: bool = False
    sample_rate: float = 0.0
    a: DualChannelMeta = field(default_factory=DualChannelMeta)
    b: DualChannelMeta = field(default_factory=DualChannelMeta)
    dropped: int = 0
    late: int = 0
    queued: int = 0
    host_dropped_blocks: int = 0
    error: str | None = None


@dataclass(slots=True, frozen=True)
class DualSampleBlock:
    raw: np.ndarray
    captured_at: float


class DualSampleSource(Protocol):
    meta: DualSourceMeta

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def blocks(self) -> AsyncIterator[DualSampleBlock]: ...


def parse_dual_adc_line(line: str | bytes) -> tuple[int, int] | None:
    """Parse the dual firmware's strict ``A,B`` 12-bit sample format."""

    if isinstance(line, bytes):
        text = line.decode("ascii", errors="ignore").strip()
    elif isinstance(line, str):
        text = line.strip()
    else:
        raise TypeError("serial line must be str or bytes")
    if not text or text.startswith("#META"):
        return None
    fields = [field.strip() for field in text.split(",")]
    if len(fields) != 2 or any(not field.isdecimal() for field in fields):
        return None
    values = tuple(int(field) for field in fields)
    if any(value < 0 or value > 4_095 for value in values):
        return None
    return values[0], values[1]


class DualSerialSource:
    """Read synchronized paired ADC samples without toggling ESP32 reset lines."""

    def __init__(self, settings: DualDriveSettings):
        self.settings = settings
        self.port = detect_serial_port(settings.serial_port)
        self.meta = DualSourceMeta(source="hardware", device=self.port)
        self._queue: asyncio.Queue[DualSampleBlock | None] = asyncio.Queue(
            maxsize=50
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._serial = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _open_serial(self):
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - bootstrap failure
            raise HardwareUnavailable(
                "pyserial is missing; run `uv sync --python 3.11` first"
            ) from exc

        # Match the proven Flappy serial sequence exactly: configure flow
        # control and physical line states while closed, then open, settle, and
        # discard bootloader/stale bytes. This avoids DTR/RTS reset loops.
        connection = serial.Serial(
            port=None,
            baudrate=self.settings.baud_rate,
            timeout=0.1,
            write_timeout=1.0,
            inter_byte_timeout=0.1,
            dsrdtr=False,
            rtscts=False,
        )
        connection.dtr = False
        connection.rts = False
        connection.port = self.port
        try:
            connection.open()
        except (OSError, serial.SerialException) as exc:
            connection.close()
            raise HardwareUnavailable(
                f"Could not open ESP32 at {self.port}: {exc}. Close every "
                "serial monitor and other SOMACH backend."
            ) from exc
        time.sleep(2.0)
        connection.reset_input_buffer()
        return connection

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._serial = await asyncio.to_thread(self._open_serial)
        self._stop_event.clear()
        self.meta.connected = True
        self._thread = threading.Thread(
            target=self._reader_loop,
            name="esp32-dual-serial-reader",
            daemon=True,
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop_event.set()
        connection = self._serial
        if connection is not None and connection.is_open:
            await asyncio.to_thread(connection.close)
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 1.0)
        self.meta.connected = False

    def _offer(self, block: DualSampleBlock | None) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self.meta.host_dropped_blocks += 1
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(block)

    @staticmethod
    def _integer(fields: dict[str, str], name: str, current: int = 0) -> int:
        try:
            return int(float(fields.get(name, current)))
        except ValueError:
            return current

    @staticmethod
    def _boolean(
        fields: dict[str, str], name: str, current: bool = False
    ) -> bool:
        raw = fields.get(name)
        return current if raw is None else raw.lower() in {"1", "true", "yes"}

    def _apply_meta(self, fields: dict[str, str]) -> None:
        try:
            self.meta.sample_rate = float(
                fields.get("rate_hz", fields.get("rate", self.meta.sample_rate))
            )
        except ValueError:
            pass
        for name, channel in (("a", self.meta.a), ("b", self.meta.b)):
            channel.lo_plus = self._boolean(
                fields, f"{name}_lo_plus", channel.lo_plus
            )
            channel.lo_minus = self._boolean(
                fields, f"{name}_lo_minus", channel.lo_minus
            )
            channel.reported_leads_off = self._boolean(
                fields, f"{name}_leads_off", channel.reported_leads_off
            )
            channel.clip_low = self._integer(fields, f"{name}_clip_low")
            channel.clip_high = self._integer(fields, f"{name}_clip_high")
        self.meta.dropped = self._integer(
            fields, "tx_drop_total", self.meta.dropped
        )
        self.meta.late = self._integer(
            fields, "missed_total", self.meta.late
        )
        self.meta.queued = self._integer(fields, "queued", self.meta.queued)

    def _reader_loop(self) -> None:
        assert self._loop is not None
        assert self._serial is not None
        frames: list[tuple[int, int]] = []
        rate_count = 0
        rate_started = time.monotonic()
        block_size = self.settings.websocket_batch_samples
        try:
            while not self._stop_event.is_set():
                payload = self._serial.readline()
                if not payload:
                    continue
                line = payload.decode("ascii", errors="ignore").strip()
                if line.startswith("#META"):
                    self._apply_meta(parse_meta_line(line))
                    continue
                frame = parse_dual_adc_line(line)
                if frame is None:
                    continue
                frames.append(frame)
                rate_count += 1
                now = time.monotonic()
                elapsed = now - rate_started
                if elapsed >= 1.0 and self.meta.sample_rate <= 0:
                    self.meta.sample_rate = rate_count / elapsed
                    rate_count = 0
                    rate_started = now
                if len(frames) >= block_size:
                    raw = np.asarray(frames[:block_size], dtype=np.uint16)
                    del frames[:block_size]
                    self._loop.call_soon_threadsafe(
                        self._offer,
                        DualSampleBlock(raw=raw, captured_at=time.perf_counter()),
                    )
        except Exception as exc:  # serial driver disconnect
            if not self._stop_event.is_set():
                self.meta.error = f"ESP32 dual serial stream stopped: {exc}"
                self.meta.connected = False
                logger.exception("Dual serial reader stopped")
                self._loop.call_soon_threadsafe(self._offer, None)

    async def blocks(self) -> AsyncIterator[DualSampleBlock]:
        while not self._stop_event.is_set():
            block = await self._queue.get()
            if block is None:
                raise HardwareUnavailable(
                    self.meta.error or "ESP32 dual serial stream disconnected"
                )
            yield block


class DualMockSource:
    """Deterministic two-channel source for dashboard and arbitration tests."""

    def __init__(self, settings: DualDriveSettings, *, seed: int = 17):
        self.settings = settings
        self.meta = DualSourceMeta(
            source="mock",
            device="synthetic://somach-dual-emg",
            sample_rate=float(settings.sample_rate_hz),
        )
        self._rng = np.random.default_rng(seed)
        self._running = False
        self._index = 0
        self._bursts = {"a": -1, "b": -1}
        self._burst_samples = round(settings.sample_rate_hz * 0.24)

    async def start(self) -> None:
        self._running = True
        self.meta.connected = True

    async def stop(self) -> None:
        self._running = False
        self.meta.connected = False

    def inject(self, action: DriveAction) -> bool:
        if action not in {"forward", "left", "right"}:
            raise ValueError("mock action must be forward, left, or right")
        channels = {
            "forward": ("a",),
            "left": ("b",),
            "right": ("a", "b"),
        }[action]
        if any(self._bursts[channel] >= 0 for channel in channels):
            return False
        for channel in channels:
            self._bursts[channel] = 0
        return True

    def _generate(self, count: int) -> np.ndarray:
        indices = np.arange(self._index, self._index + count)
        seconds = indices / self.settings.sample_rate_hz
        output = np.column_stack(
            (
                2_000
                + self._rng.normal(0, 7, count)
                + 5 * np.sin(2 * np.pi * 60 * seconds),
                2_030
                + self._rng.normal(0, 8, count)
                + 4 * np.sin(2 * np.pi * 60 * seconds + 0.3),
            )
        )
        for column, channel in enumerate(("a", "b")):
            for offset in range(count):
                burst_index = self._bursts[channel]
                if burst_index < 0:
                    break
                phase = burst_index / max(1, self._burst_samples - 1)
                envelope = math.sin(math.pi * phase) ** 2
                sample_time = (self._index + offset) / self.settings.sample_rate_hz
                muscle = (
                    300 * math.sin(2 * math.pi * 83 * sample_time)
                    + 190 * math.sin(2 * math.pi * 137 * sample_time + column)
                    + 100 * self._rng.normal()
                )
                output[offset, column] += envelope * muscle
                self._bursts[channel] += 1
                if self._bursts[channel] >= self._burst_samples:
                    self._bursts[channel] = -1
        self._index += count
        return np.clip(np.rint(output), 0, 4_095).astype(np.uint16)

    async def blocks(self) -> AsyncIterator[DualSampleBlock]:
        loop = asyncio.get_running_loop()
        count = self.settings.websocket_batch_samples
        period = count / self.settings.sample_rate_hz
        deadline = loop.time()
        while self._running:
            deadline += period
            delay = deadline - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -period * 4:
                deadline = loop.time()
            yield DualSampleBlock(self._generate(count), time.perf_counter())


class HybridMockChannelB:
    """Synthetic Channel B clocked by live Channel A serial blocks.

    The physical B column is never passed to DSP in hybrid mode.  This object
    generates a quiet, deterministic replacement signal and a short muscle-
    shaped burst only after an explicit LEFT request through the mock-trigger
    control surface.
    """

    def __init__(self, settings: DualDriveSettings, *, seed: int = 29):
        self.settings = settings
        self._rng = np.random.default_rng(seed)
        self._index = 0
        self._burst_index = -1
        self._burst_samples = round(settings.sample_rate_hz * 0.24)

    @property
    def active(self) -> bool:
        return self._burst_index >= 0

    def inject_left(self) -> bool:
        if self.active:
            return False
        self._burst_index = 0
        return True

    def generate(self, count: int) -> np.ndarray:
        if count < 0:
            raise ValueError("hybrid sample count cannot be negative")
        indices = np.arange(self._index, self._index + count)
        seconds = indices / self.settings.sample_rate_hz
        output = (
            2_030
            + self._rng.normal(0, 8, count)
            + 4 * np.sin(2 * np.pi * 60 * seconds + 0.3)
        )
        for offset in range(count):
            if not self.active:
                break
            phase = self._burst_index / max(1, self._burst_samples - 1)
            envelope = math.sin(math.pi * phase) ** 2
            sample_time = (self._index + offset) / self.settings.sample_rate_hz
            muscle = (
                300 * math.sin(2 * math.pi * 83 * sample_time)
                + 190 * math.sin(2 * math.pi * 137 * sample_time + 1.0)
                + 100 * self._rng.normal()
            )
            output[offset] += envelope * muscle
            self._burst_index += 1
            if self._burst_index >= self._burst_samples:
                self._burst_index = -1
        self._index += count
        return np.clip(np.rint(output), 0, 4_095).astype(np.uint16)


def create_dual_source(settings: DualDriveSettings) -> DualSampleSource:
    if settings.mode == "mock":
        return DualMockSource(settings)
    # Hybrid still requires live serial acquisition for Channel A.  Runtime
    # replaces the physical B column before DSP and ignores B health metadata.
    return DualSerialSource(settings)


@dataclass(slots=True, frozen=True)
class DualProcessedBlock:
    filtered: np.ndarray
    rms_points: tuple[tuple[int, float, float], ...]
    latest_a: float
    latest_b: float


class DualEMGProcessor:
    """Run identical causal DSP state machines on both synchronized channels."""

    def __init__(self, settings: DualDriveSettings):
        self.a = StreamingEMGProcessor(settings)  # type: ignore[arg-type]
        self.b = StreamingEMGProcessor(settings)  # type: ignore[arg-type]

    def reset(self) -> None:
        self.a.reset()
        self.b.reset()

    def process(self, raw: np.ndarray) -> DualProcessedBlock:
        values = np.asarray(raw)
        if values.ndim != 2 or values.shape[1] != 2:
            raise ValueError("dual raw samples must have shape (samples, 2)")
        first = self.a.process(values[:, 0])
        second = self.b.process(values[:, 1])
        if len(first.rms_points) != len(second.rms_points):
            raise RuntimeError("synchronized channel RMS schedules diverged")
        points: list[tuple[int, float, float]] = []
        for a_point, b_point in zip(first.rms_points, second.rms_points, strict=True):
            if a_point.index != b_point.index:
                raise RuntimeError("synchronized channel RMS indices diverged")
            points.append((a_point.index, a_point.value, b_point.value))
        return DualProcessedBlock(
            filtered=np.column_stack((first.filtered, second.filtered)),
            rms_points=tuple(points),
            latest_a=first.latest_rms,
            latest_b=second.latest_rms,
        )


class DualThresholdArbiter:
    """Resolve A-only, B-only, and truly overlapping A+B contractions."""

    def __init__(
        self,
        *,
        coincidence_ms: int = 80,
        rearm_ratio: float = 0.65,
        rearm_hold_ms: int = 180,
    ):
        self.coincidence_seconds = coincidence_ms / 1_000
        self.rearm_ratio = rearm_ratio
        self.rearm_hold_seconds = rearm_hold_ms / 1_000
        self.pending: ChannelName | None = None
        self.pending_since: float | None = None
        self.waiting_release = False
        self.both_low_since: float | None = None

    def reset(self) -> None:
        self.pending = None
        self.pending_since = None
        self.waiting_release = False
        self.both_low_since = None

    def cancel_pending(self) -> None:
        self.pending = None
        self.pending_since = None

    def _update_quiet(
        self,
        rms_a: float,
        rms_b: float,
        threshold_a: float,
        threshold_b: float,
        now: float,
    ) -> bool:
        both_low = (
            rms_a < threshold_a * self.rearm_ratio
            and rms_b < threshold_b * self.rearm_ratio
        )
        if both_low:
            if self.both_low_since is None:
                self.both_low_since = now
        else:
            self.both_low_since = None
        quiet_for = (
            0.0
            if self.both_low_since is None
            else max(0.0, now - self.both_low_since)
        )
        return quiet_for >= self.rearm_hold_seconds

    def quiet_ms(self, now: float) -> float:
        if self.both_low_since is None:
            return 0.0
        return max(0.0, (now - self.both_low_since) * 1_000)

    def can_arm(self, now: float) -> bool:
        return self.quiet_ms(now) >= self.rearm_hold_seconds * 1_000

    def evaluate(
        self,
        rms_a: float,
        rms_b: float,
        threshold_a: float,
        threshold_b: float,
        now: float,
        *,
        enabled: bool,
    ) -> DriveAction | None:
        quiet_complete = self._update_quiet(
            rms_a, rms_b, threshold_a, threshold_b, now
        )
        if not enabled:
            self.cancel_pending()
            self.waiting_release = False
            return None

        if self.waiting_release:
            if quiet_complete:
                self.waiting_release = False
            return None

        high_a = rms_a >= threshold_a
        high_b = rms_b >= threshold_b
        released_a = rms_a < threshold_a * self.rearm_ratio
        released_b = rms_b < threshold_b * self.rearm_ratio

        if self.pending is not None:
            assert self.pending_since is not None
            elapsed = now - self.pending_since

            # RIGHT is emitted only while both channels are actually above
            # their high thresholds. A remembered/decaying first channel does
            # not count as coactivation.
            boundary_epsilon = 1e-9
            if (
                elapsed <= self.coincidence_seconds + boundary_epsilon
                and high_a
                and high_b
            ):
                return self._emit("right")

            if elapsed >= self.coincidence_seconds - boundary_epsilon:
                return self._emit(
                    "forward" if self.pending == "a" else "left"
                )

            first_released = released_a if self.pending == "a" else released_b
            if first_released:
                self.cancel_pending()
                if high_a and high_b:
                    return self._emit("right")
                if high_a:
                    self.pending = "a"
                    self.pending_since = now
                elif high_b:
                    self.pending = "b"
                    self.pending_since = now
            return None

        if high_a and high_b:
            return self._emit("right")
        if high_a:
            self.pending = "a"
            self.pending_since = now
        elif high_b:
            self.pending = "b"
            self.pending_since = now
        return None

    def _emit(self, action: DriveAction) -> DriveAction:
        self.cancel_pending()
        self.waiting_release = True
        self.both_low_since = None
        return action


@dataclass(slots=True)
class DualCalibration:
    target_points: int
    active: bool = False
    values_a: list[float] = field(default_factory=list)
    values_b: list[float] = field(default_factory=list)
    error: str | None = None

    def begin(self) -> None:
        self.active = True
        self.values_a.clear()
        self.values_b.clear()
        self.error = None

    def abort(self, reason: str) -> None:
        self.active = False
        self.error = reason

    @property
    def progress(self) -> float:
        return min(1.0, len(self.values_a) / max(1, self.target_points))


class DirectionInjector(Protocol):
    def pulse_direction(
        self, direction: str, *, duration_ms: float
    ) -> KeypressResult: ...

    def release_all(self) -> None: ...

    def refresh_trust(self, *, prompt: bool = False) -> bool: ...

    def status(self) -> dict: ...


class DualDriveRuntime:
    """Own the independent dual-channel acquisition and control pipeline."""

    def __init__(
        self,
        settings: DualDriveSettings,
        *,
        source: DualSampleSource | None = None,
        injector: DirectionInjector | None = None,
        publisher: Publisher | None = None,
    ):
        settings.validate()
        self.settings = settings
        self.source = source or create_dual_source(settings)
        self.injector = injector or QuartzKeyInjector(enabled=settings.inject_keys)
        self.publisher = publisher or _noop_publish
        self.processor = DualEMGProcessor(settings)
        self.arbiter = DualThresholdArbiter(
            coincidence_ms=settings.coincidence_ms,
            rearm_ratio=settings.rearm_ratio,
            rearm_hold_ms=settings.rearm_hold_ms,
        )
        self.calibration = DualCalibration(settings.calibration_points)
        self._hybrid_b = (
            HybridMockChannelB(settings) if settings.mode == "hybrid" else None
        )

        self.thresholds = {"a": 50.0, "b": 50.0}
        self.channel_calibrated = {"a": False, "b": False}
        self.baseline_mean: dict[str, float | None] = {"a": None, "b": None}
        self.baseline_std: dict[str, float | None] = {"a": None, "b": None}
        self.armed = False
        self.counts = {"forward": 0, "left": 0, "right": 0}
        self.posted_counts = {"forward": 0, "left": 0, "right": 0}
        self.latest_rms = {"a": 0.0, "b": 0.0}
        self.rms_history = {
            "a": deque(maxlen=settings.history_points),
            "b": deque(maxlen=settings.history_points),
        }
        clip_samples = max(settings.sample_rate_hz, settings.websocket_batch_samples)
        self._clip_history = {
            "a": deque(maxlen=clip_samples),
            "b": deque(maxlen=clip_samples),
        }
        self.current_action: DriveAction | None = None
        self.action_until = float("-inf")
        self.last_action_event: dict | None = None
        self.last_error: str | None = None
        self.processing_ms = 0.0
        self.pipeline_ms: float | None = None
        self.sequence = 0
        self.started_at = time.monotonic()
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def calibrated(self) -> bool:
        return all(self.channel_calibrated.values())

    async def start(self) -> None:
        if self._running:
            return
        await self.source.start()
        if self.settings.prompt_accessibility:
            self.injector.refresh_trust(prompt=True)
        self._running = True
        self.started_at = time.monotonic()
        self._task = asyncio.create_task(
            self._run(), name="somach-dual-threshold-pipeline"
        )

    async def stop(self) -> None:
        self._running = False
        self.armed = False
        self.injector.release_all()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.source.stop()

    def _track_clipping(self, raw: np.ndarray) -> None:
        channels: tuple[ChannelName, ...] = (
            ("a",) if self.settings.mode == "hybrid" else ("a", "b")
        )
        for channel in channels:
            column = 0 if channel == "a" else 1
            clipped = (raw[:, column] <= 4) | (raw[:, column] >= 4_091)
            self._clip_history[channel].extend(bool(value) for value in clipped)

    def _clip_fraction(self, channel: ChannelName) -> float:
        if self.settings.mode == "hybrid" and channel == "b":
            return 0.0
        history = self._clip_history[channel]
        host_fraction = sum(history) / len(history) if history else 0.0
        meta_channel = getattr(self.source.meta, channel)
        observed_rate = self.source.meta.sample_rate or self.settings.sample_rate_hz
        firmware_fraction = (
            meta_channel.clip_low + meta_channel.clip_high
        ) / max(1.0, observed_rate)
        return max(host_fraction, firmware_fraction)

    def _quality_error(self) -> str | None:
        channels: tuple[ChannelName, ...] = (
            ("a",) if self.settings.mode == "hybrid" else ("a", "b")
        )
        bad_contact = [
            name.upper()
            for name in channels
            if getattr(self.source.meta, name).leads_off
        ]
        if bad_contact:
            return f"Electrode lead-off on channel(s) {', '.join(bad_contact)}"
        bad_clip = [
            name.upper()
            for name in channels
            if self._clip_fraction(name) > 0.01
        ]
        if bad_clip:
            return f"ADC clipping exceeds 1% on channel(s) {', '.join(bad_clip)}"
        return None

    def _prepare_raw(self, raw: np.ndarray) -> np.ndarray:
        """Return the acquisition block that is allowed to enter DSP."""

        values = np.asarray(raw)
        if values.ndim != 2 or values.shape[1] != 2:
            raise ValueError("dual raw samples must have shape (samples, 2)")
        if self._hybrid_b is None:
            return values
        prepared = values.copy()
        prepared[:, 1] = self._hybrid_b.generate(len(prepared))
        return prepared

    def _safety_disarm(self, reason: str) -> None:
        if self.armed or self.arbiter.pending is not None:
            logger.warning("Dual drive disarmed: %s", reason)
        self.armed = False
        self.arbiter.cancel_pending()
        self.current_action = None
        self.action_until = float("-inf")
        self.injector.release_all()

    async def _run(self) -> None:
        try:
            async for block in self.source.blocks():
                if not self._running:
                    break
                started = time.perf_counter()
                raw = self._prepare_raw(block.raw)
                self._track_clipping(raw)
                processed = self.processor.process(raw)
                self.latest_rms = {
                    "a": processed.latest_a,
                    "b": processed.latest_b,
                }
                fault = self._quality_error()
                if fault is not None:
                    if self.calibration.active:
                        self.calibration.abort(fault)
                    self._safety_disarm(fault)

                action_events: list[dict] = []
                block_rms = {"a": [], "b": []}
                for _index, rms_a, rms_b in processed.rms_points:
                    self.latest_rms = {"a": rms_a, "b": rms_b}
                    self.rms_history["a"].append(rms_a)
                    self.rms_history["b"].append(rms_b)
                    block_rms["a"].append(rms_a)
                    block_rms["b"].append(rms_b)
                    event = self._handle_rms(
                        rms_a,
                        rms_b,
                        captured_at=block.captured_at,
                    )
                    if event is not None:
                        action_events.append(event)

                self.processing_ms = (time.perf_counter() - started) * 1_000
                self.sequence += 1
                for event in action_events:
                    await self.publisher(event)
                await self.publisher(
                    self.telemetry(
                        raw=raw,
                        filtered=processed.filtered,
                        rms_points=block_rms,
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc)
            self.source.meta.connected = False
            self._safety_disarm(str(exc))
            logger.exception("Dual threshold pipeline stopped")
            await self.publisher({"type": "error", "message": str(exc)})

    @staticmethod
    def _calibrated_threshold(
        values: list[float], settings: DualDriveSettings
    ) -> tuple[float, float, float]:
        array = np.asarray(values, dtype=np.float64)
        mean = float(np.mean(array))
        std = float(np.std(array, ddof=0))
        threshold = max(
            mean + settings.threshold_sigma * std,
            settings.threshold_mean_ratio * mean,
            1.0,
        )
        return mean, std, threshold

    def _handle_rms(
        self,
        rms_a: float,
        rms_b: float,
        *,
        captured_at: float,
        now: float | None = None,
    ) -> dict | None:
        evaluated_at = time.monotonic() if now is None else now
        was_calibrating = self.calibration.active
        if was_calibrating:
            fault = self._quality_error()
            if fault is not None:
                self.calibration.abort(fault)
                self._safety_disarm(fault)
            else:
                self.calibration.values_a.append(rms_a)
                self.calibration.values_b.append(rms_b)
                if len(self.calibration.values_a) >= self.calibration.target_points:
                    for channel, values in (
                        ("a", self.calibration.values_a),
                        ("b", self.calibration.values_b),
                    ):
                        mean, std, threshold = self._calibrated_threshold(
                            values, self.settings
                        )
                        self.baseline_mean[channel] = mean
                        self.baseline_std[channel] = std
                        self.thresholds[channel] = threshold
                        self.channel_calibrated[channel] = True
                    self.calibration.active = False
                    self.armed = False
                    self.arbiter.reset()

        # Calibration samples, including the final one, can never become input.
        enabled = (
            not was_calibrating
            and self.armed
            and self.calibrated
            and self._quality_error() is None
            and self.source.meta.connected
        )
        action = self.arbiter.evaluate(
            rms_a,
            rms_b,
            self.thresholds["a"],
            self.thresholds["b"],
            evaluated_at,
            enabled=enabled,
        )
        if action is None:
            return None
        # Synthetic B is an explicit LEFT command.  If a real A contraction
        # happens to overlap that operator-requested pulse, coactivation must
        # still resolve to LEFT: RIGHT is intentionally unavailable in hybrid.
        if self.settings.mode == "hybrid" and action == "right":
            action = "left"

        key_direction = "up" if action == "forward" else action
        duration_ms = (
            self.settings.forward_pulse_ms
            if action == "forward"
            else self.settings.turn_pulse_ms
        )
        result = self.injector.pulse_direction(
            key_direction, duration_ms=duration_ms
        )
        self.counts[action] += 1
        if result.posted:
            self.posted_counts[action] += 1
        self.current_action = action
        self.action_until = evaluated_at + duration_ms / 1_000
        self.pipeline_ms = max(0.0, (time.perf_counter() - captured_at) * 1_000)
        event = {
            "type": "action",
            "timestamp": time.time(),
            "action": action,
            "key": key_direction,
            "durationMs": duration_ms,
            "detected": True,
            "posted": result.posted,
            "keyPosted": result.posted,
            "postError": result.reason,
            "keyError": result.reason,
            "keyCallMs": result.call_ms,
            "pipelineMs": round(self.pipeline_ms, 3),
            "rms": {"a": round(rms_a, 4), "b": round(rms_b, 4)},
            "thresholds": {
                "a": round(self.thresholds["a"], 4),
                "b": round(self.thresholds["b"], 4),
            },
            "counts": dict(self.counts),
            "postedCounts": dict(self.posted_counts),
        }
        self.last_action_event = event
        return event

    def begin_calibration(self) -> dict:
        fault = self._quality_error()
        if fault is not None:
            raise ValueError(f"Cannot calibrate: {fault}")
        if not self.source.meta.connected:
            raise ValueError("Cannot calibrate while the signal source is disconnected")
        self._safety_disarm("calibration started")
        self.channel_calibrated = {"a": False, "b": False}
        self.baseline_mean = {"a": None, "b": None}
        self.baseline_std = {"a": None, "b": None}
        self.calibration.begin()
        self.arbiter.reset()
        return self.snapshot()

    def set_threshold(self, channel: ChannelName, value: float) -> dict:
        if channel not in {"a", "b"}:
            raise ValueError("channel must be 'a' or 'b'")
        if not np.isfinite(value) or not 0 < value <= 4_095:
            raise ValueError("threshold must be a finite value from 0 (exclusive) to 4095")
        self._safety_disarm("threshold changed")
        self.calibration.active = False
        self.calibration.error = None
        self.thresholds[channel] = float(value)
        self.channel_calibrated[channel] = True
        self.arbiter.reset()
        return self.snapshot()

    def set_armed(self, armed: bool) -> dict:
        if not armed:
            self._safety_disarm("operator disarmed")
            self.arbiter.reset()
            return self.snapshot()
        if not self.calibrated:
            raise ValueError("Calibrate both channels or set both thresholds first")
        fault = self._quality_error()
        if fault is not None:
            raise ValueError(f"Cannot arm: {fault}")
        if not self.source.meta.connected:
            raise ValueError("Cannot arm while the signal source is disconnected")
        now = time.monotonic()
        if not self.arbiter.can_arm(now):
            remaining = max(
                0.0,
                self.settings.rearm_hold_ms - self.arbiter.quiet_ms(now),
            )
            raise ValueError(
                "Relax both muscles below the release lines for "
                f"{remaining:.0f} ms before arming"
            )
        self.armed = True
        self.arbiter.cancel_pending()
        return self.snapshot()

    def reset_counter(self) -> dict:
        self.counts = {"forward": 0, "left": 0, "right": 0}
        self.posted_counts = {"forward": 0, "left": 0, "right": 0}
        return self.snapshot()

    def inject_mock(self, action: DriveAction) -> dict:
        if self.settings.mode == "hybrid":
            if action != "left":
                raise ValueError(
                    "Hybrid mode permits only simulated LEFT; FORWARD is live "
                    "Channel A hardware and RIGHT is unavailable"
                )
            assert self._hybrid_b is not None
            accepted = self._hybrid_b.inject_left()
        elif isinstance(self.source, DualMockSource):
            accepted = self.source.inject(action)
        else:
            raise ValueError(
                "Mock trigger is available only in --mock mode, or for LEFT "
                "in --hybrid mode"
            )
        return {"accepted": accepted, **self.snapshot()}

    def prompt_accessibility(self) -> dict:
        self.injector.refresh_trust(prompt=True)
        return self.snapshot()

    def _active_action(self, now: float) -> str:
        if self.current_action is not None and now < self.action_until:
            return self.current_action
        self.current_action = None
        return "idle"

    def _channel_state(self, channel: ChannelName) -> dict:
        meta = getattr(self.source.meta, channel)
        fraction = self._clip_fraction(channel)
        simulated = self.settings.mode == "mock" or (
            self.settings.mode == "hybrid" and channel == "b"
        )
        ignored_physical_b = self.settings.mode == "hybrid" and channel == "b"
        return {
            "label": (
                "MYLOHYOID"
                if channel == "a"
                else "LEFT COMMAND (SIMULATED)"
                if ignored_physical_b
                else "LEFT MASSETER"
            ),
            "source": "simulated" if simulated else "hardware",
            "simulated": simulated,
            "physicalInputIgnored": ignored_physical_b,
            "rms": round(self.latest_rms[channel], 4),
            "threshold": round(self.thresholds[channel], 4),
            "rmsSeries": [round(value, 3) for value in self.rms_history[channel]],
            "active": self.latest_rms[channel] >= self.thresholds[channel],
            "calibrated": self.channel_calibrated[channel],
            "baselineMean": self.baseline_mean[channel],
            "baselineStd": self.baseline_std[channel],
            "leadOff": False if ignored_physical_b else meta.leads_off,
            "loPlus": False if ignored_physical_b else meta.lo_plus,
            "loMinus": False if ignored_physical_b else meta.lo_minus,
            "clipping": fraction > 0.01,
            "clipFraction": round(fraction, 6),
            "clipLow": 0 if ignored_physical_b else meta.clip_low,
            "clipHigh": 0 if ignored_physical_b else meta.clip_high,
        }

    def _common_state(self) -> dict:
        now = time.monotonic()
        fault = self._quality_error()
        hybrid = self.settings.mode == "hybrid"
        pending_action = {
            "a": "forward",
            "b": "left",
            None: None,
        }[self.arbiter.pending]
        return {
            "mode": self.settings.mode,
            "demoDisclosure": (
                "HYBRID: Channel A is live hardware; Channel B is simulated "
                "because the second sensor is unavailable; RIGHT is disabled."
                if hybrid
                else None
            ),
            "availableActions": (
                ["forward", "left"]
                if hybrid
                else ["forward", "left", "right"]
            ),
            "channelSources": {
                "a": "simulated" if self.settings.mode == "mock" else "hardware",
                "b": (
                    "simulated"
                    if self.settings.mode in {"mock", "hybrid"}
                    else "hardware"
                ),
            },
            "connected": self.source.meta.connected,
            "device": self.source.meta.device,
            "sampleRate": round(
                self.source.meta.sample_rate or self.settings.sample_rate_hz, 2
            ),
            "armed": self.armed,
            "calibrated": self.calibrated,
            "action": self._active_action(now),
            "counts": dict(self.counts),
            "postedCounts": dict(self.posted_counts),
            "channels": {
                "a": self._channel_state("a"),
                "b": self._channel_state("b"),
            },
            "calibration": {
                "active": self.calibration.active,
                "progress": self.calibration.progress,
                "remaining": max(
                    0.0,
                    (1.0 - self.calibration.progress)
                    * self.settings.calibration_seconds,
                ),
                "error": self.calibration.error,
                "seconds": self.settings.calibration_seconds,
            },
            "arbitration": {
                "state": (
                    "waiting-release"
                    if self.arbiter.waiting_release
                    else "pending"
                    if self.arbiter.pending is not None
                    else "ready"
                ),
                "pending": pending_action,
                "waitingRelease": self.arbiter.waiting_release,
                "coincidenceMs": self.settings.coincidence_ms,
                "rearmRatio": self.settings.rearm_ratio,
                "rearmHoldMs": self.settings.rearm_hold_ms,
                "bothLowMs": round(self.arbiter.quiet_ms(now), 1),
                "canArm": self.arbiter.can_arm(now),
            },
            # Stable top-level mirrors keep the live dashboard protocol terse;
            # the nested object remains the complete diagnostic surface.
            "arbitrationState": (
                "waiting-release"
                if self.arbiter.waiting_release
                else "pending"
                if self.arbiter.pending is not None
                else "ready"
            ),
            "waitingForRelease": self.arbiter.waiting_release,
            "coincidenceMs": self.settings.coincidence_ms,
            "forwardPulseMs": self.settings.forward_pulse_ms,
            "turnPulseMs": self.settings.turn_pulse_ms,
            "mapping": {
                "a": {
                    "muscle": "mylohyoid",
                    "action": "forward",
                    "key": "up",
                    "durationMs": 1_000,
                },
                "b": {
                    "muscle": (
                        "simulated left command" if hybrid else "left masseter"
                    ),
                    "action": "left",
                    "key": "left",
                    "durationMs": 200,
                    "available": True,
                    "source": "simulated" if hybrid else "hardware",
                },
                "a+b": {
                    "muscle": "coactivation",
                    "action": "right",
                    "key": None if hybrid else "right",
                    "durationMs": None if hybrid else 200,
                    "available": not hybrid,
                    "reason": (
                        "Disabled: hybrid mode has no second live sensor"
                        if hybrid
                        else None
                    ),
                },
            },
            "pinMap": {
                "a": {
                    "output": "GPIO36/VP",
                    "sdn": "GPIO27",
                    "loPlus": "GPIO32",
                    "loMinus": "GPIO35",
                },
                "b": {
                    "output": "GPIO39/VN",
                    "sdn": "GPIO26",
                    "loPlus": "GPIO33",
                    "loMinus": "GPIO34",
                    "physicalInputIgnored": hybrid,
                },
                "baud": 460_800,
            },
            "sourceMeta": {
                "dropped": self.source.meta.dropped,
                "late": self.source.meta.late,
                "queued": self.source.meta.queued,
                "hostDroppedBlocks": self.source.meta.host_dropped_blocks,
            },
            "quartz": self.injector.status(),
            "processingMs": round(self.processing_ms, 3),
            "pipelineMs": None if self.pipeline_ms is None else round(self.pipeline_ms, 3),
            "qualityError": fault,
            "error": self.last_error or self.source.meta.error,
            "uptime": round(now - self.started_at, 1),
        }

    def snapshot(self) -> dict:
        return {"type": "status", **self._common_state()}

    def telemetry(
        self,
        *,
        raw: np.ndarray,
        filtered: np.ndarray,
        rms_points: dict[str, list[float]],
    ) -> dict:
        state = self._common_state()
        for index, channel in enumerate(("a", "b")):
            # Telemetry carries only values produced by this block. The browser
            # owns its rolling display buffer, so history is not duplicated at
            # 50 Hz. A one-shot status snapshot may still include full history.
            state["channels"][channel]["rmsSeries"] = [
                round(value, 3) for value in rms_points[channel]
            ]
            state["channels"][channel]["rawSeries"] = raw[:, index].astype(int).tolist()
            state["channels"][channel]["filteredSeries"] = np.round(
                filtered[:, index], 3
            ).tolist()
        return {
            "type": "telemetry",
            "seq": self.sequence,
            "timestamp": time.time(),
            **state,
        }
