from __future__ import annotations

import time

import numpy as np
import pytest
from somach.config import Settings
from somach.keypress import KeypressResult
from somach.runtime import SomachRuntime
from somach.sources import MockSource


class StubInjector:
    def __init__(self):
        self.calls = 0

    def post_space(self) -> KeypressResult:
        self.calls += 1
        return KeypressResult(True, 0.05)

    def refresh_trust(self, *, prompt: bool = False) -> bool:
        return True

    def status(self) -> dict:
        return {
            "enabled": True,
            "available": True,
            "trusted": True,
            "detail": "test",
            "lastCallMs": 0.05,
            "lastPosted": self.calls > 0,
        }


def test_calibration_is_exact_mean_plus_three_point_five_sigma() -> None:
    settings = Settings(calibration_seconds=0.04, inject_keys=False)
    source = MockSource(settings)
    source.meta.connected = True
    runtime = SomachRuntime(settings, source=source, injector=StubInjector())

    runtime.begin_calibration()
    runtime._handle_rms(10.0, 0.0)
    runtime._handle_rms(12.0, 0.0)

    assert runtime.baseline_mean == pytest.approx(11.0)
    assert runtime.baseline_std == pytest.approx(1.0)
    assert runtime.threshold == pytest.approx(14.5)
    assert runtime.calibrated
    assert runtime.armed
    assert runtime.calibration.progress == 1.0


def test_final_calibration_point_cannot_trigger_a_jump() -> None:
    settings = Settings(calibration_seconds=0.02, inject_keys=False)
    source = MockSource(settings)
    source.meta.connected = True
    injector = StubInjector()
    runtime = SomachRuntime(settings, source=source, injector=injector)

    runtime.begin_calibration()
    assert runtime._handle_rms(100.0, time.perf_counter()) is None
    assert runtime.calibrated is True
    assert runtime.armed is True
    assert injector.calls == 0

    event = runtime._handle_rms(101.0, time.perf_counter())
    assert event is not None
    assert injector.calls == 1


def test_lead_off_blocks_calibration_and_arming() -> None:
    settings = Settings(inject_keys=False)
    source = MockSource(settings)
    source.meta.lo_plus = True
    runtime = SomachRuntime(settings, source=source, injector=StubInjector())

    with pytest.raises(ValueError, match="lead-off"):
        runtime.begin_calibration()
    runtime.set_threshold(10)
    with pytest.raises(ValueError, match="lead is off"):
        runtime.set_armed(True)


def test_mock_jump_has_clear_energy_separation_from_rest() -> None:
    settings = Settings(inject_keys=False)
    source = MockSource(settings, seed=123)
    processor = runtime_processor = SomachRuntime(
        settings, source=source, injector=StubInjector()
    ).processor

    rest = source._generate(2_000)
    rest_result = processor.process(rest)
    rest_rms = np.median([point.value for point in rest_result.rms_points[-30:]])

    assert source.inject_jump()
    burst = source._generate(400)
    burst_result = runtime_processor.process(burst)
    burst_rms = max(point.value for point in burst_result.rms_points)

    assert burst_rms > rest_rms * 5


def test_hardware_marker_rejects_recent_adc_clipping(tmp_path) -> None:
    settings = Settings(mode="hardware", inject_keys=False)
    source = MockSource(settings)
    source.meta.clipped = 12
    runtime = SomachRuntime(
        settings,
        source=source,
        injector=StubInjector(),
        dataset_dir=tmp_path,
    )
    runtime.start_recording()

    with pytest.raises(ValueError, match="clipping"):
        runtime.mark_recording("jump")


def test_live_hardware_clipping_aborts_calibration_and_blocks_trigger() -> None:
    settings = Settings(mode="hardware", calibration_seconds=0.04, inject_keys=False)
    source = MockSource(settings)
    injector = StubInjector()
    runtime = SomachRuntime(settings, source=source, injector=injector)

    runtime.begin_calibration()
    source.meta.clipped = 20
    assert runtime._handle_rms(100.0, time.perf_counter()) is None
    assert runtime.calibration.active is False
    assert "clipping" in (runtime.calibration.error or "")

    source.meta.clipped = 0
    runtime.set_threshold(10.0)
    runtime.set_armed(True)
    source.meta.clipped = 20
    assert runtime._handle_rms(100.0, time.perf_counter()) is None
    assert injector.calls == 0
