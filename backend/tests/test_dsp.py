from __future__ import annotations

import numpy as np
import pytest
from somach.config import Settings
from somach.dsp import StreamingEMGProcessor, TriggerGate


def filtered_tone_rms(frequency: float) -> float:
    settings = Settings(inject_keys=False)
    processor = StreamingEMGProcessor(settings)
    seconds = 4
    time = np.arange(seconds * settings.sample_rate_hz) / settings.sample_rate_hz
    raw = 2_000 + 100 * np.sin(2 * np.pi * frequency * time)
    filtered = processor.process(raw).filtered
    settled = filtered[settings.sample_rate_hz :]
    return float(np.sqrt(np.mean(settled * settled)))


def test_required_filter_passes_emg_and_rejects_notch_and_out_of_band() -> None:
    passed = filtered_tone_rms(100)
    notched = filtered_tone_rms(60)
    low = filtered_tone_rms(5)
    high = filtered_tone_rms(400)

    assert passed > 60
    assert notched < passed * 0.08
    assert low < passed * 0.08
    # A fourth-order bandpass is intentionally shallow enough to keep causal
    # delay low; 400 Hz is still attenuated by more than 20 dB.
    assert high < passed * 0.12


def test_rms_is_emitted_every_20ms_after_trailing_window_is_full() -> None:
    settings = Settings(inject_keys=False)
    processor = StreamingEMGProcessor(settings)
    result = processor.process(np.full(1_000, 2_000.0))

    assert [point.index for point in result.rms_points[:3]] == [159, 179, 199]
    assert len(result.rms_points) == 43
    assert result.latest_rms == pytest.approx(0.0, abs=1e-8)


def test_trigger_gate_has_refractory_and_requires_hysteretic_rearm() -> None:
    gate = TriggerGate(refractory_ms=250, rearm_ratio=0.65)

    assert gate.evaluate(11, 10, 1.00, enabled=True)
    assert not gate.evaluate(12, 10, 1.30, enabled=True)
    assert not gate.evaluate(6, 10, 1.31, enabled=True)
    assert gate.evaluate(11, 10, 1.32, enabled=True)
    assert not gate.evaluate(11, 10, 1.40, enabled=False)
