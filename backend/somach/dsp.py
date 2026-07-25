"""Causal, stateful sEMG filtering and an O(1) RMS envelope."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from scipy import signal

from .config import Settings


@dataclass(slots=True, frozen=True)
class RmsPoint:
    """An RMS evaluation aligned to an index in the current input block."""

    index: int
    value: float


@dataclass(slots=True, frozen=True)
class ProcessedBlock:
    filtered: np.ndarray
    rms_points: tuple[RmsPoint, ...]
    latest_rms: float


class StreamingEMGProcessor:
    """Apply the required notch, bandpass, and trailing RMS in real time.

    The IIR state is retained between calls. No forward/backward filtering is
    used because that would require future samples and cannot run live.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

        notch_b, notch_a = signal.iirnotch(
            settings.notch_hz,
            settings.notch_q,
            fs=settings.sample_rate_hz,
        )
        notch_sos = signal.tf2sos(notch_b, notch_a)
        bandpass_sos = signal.butter(
            settings.bandpass_prototype_order,
            [settings.bandpass_low_hz, settings.bandpass_high_hz],
            btype="bandpass",
            fs=settings.sample_rate_hz,
            output="sos",
        )
        self.sos = np.vstack((notch_sos, bandpass_sos))
        self._steady_state_zi = signal.sosfilt_zi(self.sos)
        self._zi: np.ndarray | None = None

        self._squares: deque[float] = deque(maxlen=settings.rms_window_samples)
        self._square_sum = 0.0
        self._sample_count = 0
        self.latest_rms = 0.0

    def reset(self) -> None:
        """Reset filter and envelope state."""

        self._zi = None
        self._squares.clear()
        self._square_sum = 0.0
        self._sample_count = 0
        self.latest_rms = 0.0

    def process(self, raw: np.ndarray) -> ProcessedBlock:
        samples = np.asarray(raw, dtype=np.float64)
        if samples.ndim != 1:
            raise ValueError("raw samples must be one-dimensional")
        if samples.size == 0:
            return ProcessedBlock(samples.copy(), (), self.latest_rms)

        # Scale the steady-state initial conditions by the first ADC value.
        # This avoids a false muscle burst from the ~2,000-count DC baseline.
        if self._zi is None:
            self._zi = self._steady_state_zi * samples[0]

        filtered, self._zi = signal.sosfilt(self.sos, samples, zi=self._zi)

        rms_points: list[RmsPoint] = []
        window_size = self.settings.rms_window_samples
        stride = self.settings.rms_stride_samples

        for index, value in enumerate(filtered):
            square = float(value * value)
            if len(self._squares) == window_size:
                self._square_sum -= self._squares[0]
            self._squares.append(square)
            self._square_sum += square
            self._sample_count += 1

            if len(self._squares) == window_size and self._sample_count % stride == 0:
                self.latest_rms = float(
                    np.sqrt(max(0.0, self._square_sum) / window_size)
                )
                rms_points.append(RmsPoint(index=index, value=self.latest_rms))

        return ProcessedBlock(filtered, tuple(rms_points), self.latest_rms)


class TriggerGate:
    """Optional refractory plus hysteretic re-arm for discrete gestures."""

    def __init__(self, refractory_ms: int, rearm_ratio: float):
        self.refractory_seconds = refractory_ms / 1_000
        self.rearm_ratio = rearm_ratio
        self.last_trigger_at = float("-inf")
        self.ready = True

    def reset(self) -> None:
        self.last_trigger_at = float("-inf")
        self.ready = True

    def evaluate(
        self,
        rms: float,
        threshold: float,
        now: float,
        *,
        enabled: bool,
    ) -> bool:
        if rms < threshold * self.rearm_ratio:
            self.ready = True

        if (
            enabled
            and self.ready
            and rms > threshold
            and now - self.last_trigger_at >= self.refractory_seconds
        ):
            self.ready = False
            self.last_trigger_at = now
            return True
        return False
