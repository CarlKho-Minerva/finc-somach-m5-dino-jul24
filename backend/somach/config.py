"""Runtime configuration with all signal-processing constants in one place."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class Settings:
    """Configuration for acquisition, DSP, detection, and the local server."""

    mode: str = "mock"
    serial_port: str | None = None
    baud_rate: int = 115_200
    sample_rate_hz: int = 1_000
    websocket_batch_samples: int = 20

    notch_hz: float = 60.0
    notch_q: float = 30.0
    bandpass_low_hz: float = 20.0
    bandpass_high_hz: float = 250.0
    # scipy.signal.butter doubles N for a bandpass. N=2 is overall order 4.
    bandpass_prototype_order: int = 2

    rms_window_ms: int = 150
    rms_stride_ms: int = 20
    calibration_seconds: float = 3.0
    threshold_sigma: float = 3.5
    refractory_ms: int = 0
    rearm_ratio: float = 0.65

    host: str = "127.0.0.1"
    api_port: int = 8_123
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
            self.calibration_seconds * self.sample_rate_hz / self.rms_stride_samples
        )

    def validate(self) -> None:
        nyquist = self.sample_rate_hz / 2
        if self.mode not in {"mock", "hardware"}:
            raise ValueError("mode must be 'mock' or 'hardware'")
        if not 0 < self.bandpass_low_hz < self.bandpass_high_hz < nyquist:
            raise ValueError(f"bandpass must satisfy 0 < low < high < {nyquist:g} Hz")
        if not 0 < self.notch_hz < nyquist:
            raise ValueError("notch frequency must be below Nyquist")
        if self.websocket_batch_samples <= 0:
            raise ValueError("websocket batch size must be positive")
        if self.rms_window_samples <= 0 or self.rms_stride_samples <= 0:
            raise ValueError("RMS window and stride must be positive")
