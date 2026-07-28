from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import numpy as np
import pytest
from fastapi.testclient import TestClient

from somach.dsp import StreamingEMGProcessor
from somach.keypress import KeypressResult
from somach.threshold_drive import (
    DualDriveRuntime,
    DualDriveSettings,
    DualMockSource,
    DualSampleBlock,
    DualSourceMeta,
    DualThresholdArbiter,
    HybridMockChannelB,
    parse_dual_adc_line,
)
from somach.threshold_drive_api import create_dual_drive_app


class StubInjector:
    def __init__(self, *, posted: bool = True):
        self.posted = posted
        self.calls: list[tuple[str, float]] = []
        self.release_calls = 0

    def pulse_direction(
        self, direction: str, *, duration_ms: float
    ) -> KeypressResult:
        self.calls.append((direction, duration_ms))
        return KeypressResult(
            self.posted,
            0.04 if self.posted else None,
            None if self.posted else "test permission failure",
        )

    def release_all(self) -> None:
        self.release_calls += 1

    def refresh_trust(self, *, prompt: bool = False) -> bool:
        return True

    def status(self) -> dict:
        return {
            "enabled": True,
            "available": True,
            "trusted": True,
            "detail": "test",
            "lastCallMs": 0.04 if self.calls else None,
            "lastPosted": bool(self.calls and self.posted),
        }


class IdleSource:
    def __init__(self):
        self.meta = DualSourceMeta(
            source="test",
            device="test://dual",
            connected=True,
            sample_rate=1_000.0,
        )
        self.running = False

    async def start(self) -> None:
        self.running = True
        self.meta.connected = True

    async def stop(self) -> None:
        self.running = False
        self.meta.connected = False

    async def blocks(self) -> AsyncIterator[DualSampleBlock]:
        while self.running:
            await asyncio.sleep(0.05)
            if False:  # pragma: no cover - makes this an async generator
                yield DualSampleBlock(np.zeros((20, 2)), time.perf_counter())


def detector() -> DualThresholdArbiter:
    return DualThresholdArbiter(
        coincidence_ms=80,
        rearm_ratio=0.65,
        rearm_hold_ms=180,
    )


def test_hybrid_is_an_explicit_valid_runtime_mode() -> None:
    DualDriveSettings(mode="hybrid").validate()
    with pytest.raises(ValueError, match="mock.*hardware.*hybrid"):
        DualDriveSettings(mode="ambiguous").validate()


def test_dual_parser_requires_two_twelve_bit_integers() -> None:
    assert parse_dual_adc_line("2048,1999") == (2048, 1999)
    assert parse_dual_adc_line(b"0,4095\n") == (0, 4095)
    assert parse_dual_adc_line("#META,rate_hz=1000") is None
    assert parse_dual_adc_line("2048") is None
    assert parse_dual_adc_line("2048,4096") is None
    assert parse_dual_adc_line("2.5,3") is None


def test_a_only_waits_for_coincidence_then_emits_forward() -> None:
    arbiter = detector()
    assert arbiter.evaluate(11, 0, 10, 10, 0.0, enabled=True) is None
    assert arbiter.pending == "a"
    assert arbiter.evaluate(11, 0, 10, 10, 0.079, enabled=True) is None
    assert arbiter.evaluate(11, 0, 10, 10, 0.080, enabled=True) == "forward"
    assert arbiter.waiting_release is True


def test_b_only_emits_left_and_never_up() -> None:
    arbiter = detector()
    assert arbiter.evaluate(0, 11, 10, 10, 1.0, enabled=True) is None
    assert arbiter.evaluate(0, 11, 10, 10, 1.081, enabled=True) == "left"


def test_actual_high_overlap_takes_precedence_through_window_boundary() -> None:
    arbiter = detector()
    assert arbiter.evaluate(11, 0, 10, 10, 2.0, enabled=True) is None
    assert arbiter.evaluate(11, 11, 10, 10, 2.080, enabled=True) == "right"


def test_nonoverlap_does_not_become_right_from_hysteresis_memory() -> None:
    arbiter = detector()
    assert arbiter.evaluate(11, 0, 10, 10, 3.0, enabled=True) is None
    # A is below its high threshold. It has not crossed the low/rearm line,
    # but that remembered activity is deliberately insufficient for RIGHT.
    assert arbiter.evaluate(7, 11, 10, 10, 3.040, enabled=True) is None
    assert arbiter.evaluate(7, 11, 10, 10, 3.081, enabled=True) == "forward"


def test_global_latch_requires_both_low_for_full_hold() -> None:
    arbiter = detector()
    assert arbiter.evaluate(11, 11, 10, 10, 4.0, enabled=True) == "right"
    assert arbiter.evaluate(0, 0, 10, 10, 4.100, enabled=True) is None
    assert arbiter.waiting_release is True
    assert arbiter.evaluate(0, 0, 10, 10, 4.279, enabled=True) is None
    assert arbiter.waiting_release is True
    assert arbiter.evaluate(0, 0, 10, 10, 4.280, enabled=True) is None
    assert arbiter.waiting_release is False
    assert arbiter.evaluate(11, 0, 10, 10, 4.300, enabled=True) is None
    assert arbiter.evaluate(11, 0, 10, 10, 4.381, enabled=True) == "forward"


def test_quiet_timer_resets_if_either_channel_rises() -> None:
    arbiter = detector()
    arbiter.evaluate(0, 0, 10, 10, 0.0, enabled=False)
    arbiter.evaluate(0, 7, 10, 10, 0.170, enabled=False)
    assert not arbiter.can_arm(0.300)
    arbiter.evaluate(0, 0, 10, 10, 0.310, enabled=False)
    assert not arbiter.can_arm(0.489)
    assert arbiter.can_arm(0.490)


def test_calibration_is_independent_and_uses_robust_floor() -> None:
    settings = DualDriveSettings(
        mode="mock",
        calibration_seconds=0.04,
        inject_keys=False,
    )
    source = DualMockSource(settings)
    source.meta.connected = True
    runtime = DualDriveRuntime(
        settings,
        source=source,
        injector=StubInjector(),
    )

    runtime.begin_calibration()
    runtime._handle_rms(10, 20, captured_at=time.perf_counter(), now=1.0)
    runtime._handle_rms(12, 20, captured_at=time.perf_counter(), now=1.02)

    assert runtime.baseline_mean == {"a": pytest.approx(11), "b": pytest.approx(20)}
    assert runtime.baseline_std == {"a": pytest.approx(1), "b": pytest.approx(0)}
    assert runtime.thresholds["a"] == pytest.approx(17.05)
    assert runtime.thresholds["b"] == pytest.approx(31.0)
    assert runtime.calibrated is True
    assert runtime.armed is False


def test_default_calibrated_rest_can_complete_quiet_arm_hold() -> None:
    settings = DualDriveSettings(mode="mock", inject_keys=False)
    _mean, _std, threshold = DualDriveRuntime._calibrated_threshold(
        [10.0, 10.0, 10.0], settings
    )
    arbiter = DualThresholdArbiter(
        coincidence_ms=settings.coincidence_ms,
        rearm_ratio=settings.rearm_ratio,
        rearm_hold_ms=settings.rearm_hold_ms,
    )

    arbiter.evaluate(10, 10, threshold, threshold, 1.0, enabled=False)
    arbiter.evaluate(10, 10, threshold, threshold, 1.181, enabled=False)

    assert arbiter.can_arm(1.181)


@pytest.mark.parametrize(
    ("samples", "expected_action", "expected_call"),
    [
        (((11, 0, 1.0), (11, 0, 1.081)), "forward", ("up", 1_000)),
        (((0, 11, 2.0), (0, 11, 2.081)), "left", ("left", 200)),
        (((11, 11, 3.0),), "right", ("right", 200)),
    ],
)
def test_runtime_posts_exact_bounded_key_for_each_mapping(
    samples: tuple[tuple[float, float, float], ...],
    expected_action: str,
    expected_call: tuple[str, int],
) -> None:
    settings = DualDriveSettings(mode="mock")
    source = DualMockSource(settings)
    source.meta.connected = True
    injector = StubInjector()
    runtime = DualDriveRuntime(settings, source=source, injector=injector)
    runtime.thresholds = {"a": 10, "b": 10}
    runtime.channel_calibrated = {"a": True, "b": True}
    runtime.armed = True

    event = None
    for rms_a, rms_b, now in samples:
        event = runtime._handle_rms(
            rms_a,
            rms_b,
            captured_at=time.perf_counter(),
            now=now,
        )

    assert event is not None
    assert event["action"] == expected_action
    assert injector.calls == [expected_call]
    assert runtime.counts[expected_action] == 1
    assert event["keyPosted"] is True


def test_failed_quartz_post_is_detected_but_not_counted_as_posted() -> None:
    settings = DualDriveSettings(mode="mock")
    source = DualMockSource(settings)
    source.meta.connected = True
    runtime = DualDriveRuntime(
        settings, source=source, injector=StubInjector(posted=False)
    )
    runtime.thresholds = {"a": 10, "b": 10}
    runtime.channel_calibrated = {"a": True, "b": True}
    runtime.armed = True

    event = runtime._handle_rms(
        11, 11, captured_at=time.perf_counter(), now=1.0
    )

    assert event is not None
    assert event["detected"] is True
    assert event["posted"] is False
    assert runtime.counts["right"] == 1
    assert runtime.posted_counts["right"] == 0


def test_threshold_edit_disarms_cancels_pending_and_releases_keys() -> None:
    settings = DualDriveSettings(mode="mock")
    source = DualMockSource(settings)
    source.meta.connected = True
    injector = StubInjector()
    runtime = DualDriveRuntime(settings, source=source, injector=injector)
    runtime.channel_calibrated = {"a": True, "b": True}
    runtime.armed = True
    runtime.arbiter.pending = "a"
    runtime.arbiter.pending_since = 1.0
    runtime.current_action = "forward"
    runtime.action_until = time.monotonic() + 1

    status = runtime.set_threshold("a", 44.0)

    assert status["channels"]["a"]["threshold"] == 44.0
    assert runtime.armed is False
    assert runtime.arbiter.pending is None
    assert runtime.current_action is None
    assert injector.release_calls == 1


def test_lead_off_blocks_calibration_and_forces_disarm() -> None:
    settings = DualDriveSettings(mode="mock")
    source = DualMockSource(settings)
    source.meta.connected = True
    source.meta.b.reported_leads_off = True
    injector = StubInjector()
    runtime = DualDriveRuntime(settings, source=source, injector=injector)
    runtime.armed = True

    with pytest.raises(ValueError, match="lead-off"):
        runtime.begin_calibration()
    runtime._safety_disarm("test")
    assert runtime.armed is False
    assert injector.release_calls >= 1


def test_hybrid_replaces_physical_b_and_ignores_only_b_health_faults() -> None:
    settings = DualDriveSettings(mode="hybrid", inject_keys=False)
    source = IdleSource()
    source.meta.b.reported_leads_off = True
    source.meta.b.clip_low = 1_000
    runtime = DualDriveRuntime(
        settings,
        source=source,
        injector=StubInjector(),
    )
    physical = np.column_stack(
        (np.full(20, 1_900, dtype=np.uint16), np.zeros(20, dtype=np.uint16))
    )

    prepared = runtime._prepare_raw(physical)

    assert np.array_equal(prepared[:, 0], physical[:, 0])
    assert np.all(prepared[:, 1] > 1_900)
    assert not np.shares_memory(prepared, physical)
    assert runtime._quality_error() is None
    assert runtime._clip_fraction("b") == 0
    channel_b = runtime.snapshot()["channels"]["b"]
    assert channel_b["label"] == "LEFT COMMAND (SIMULATED)"
    assert channel_b["source"] == "simulated"
    assert channel_b["physicalInputIgnored"] is True
    assert channel_b["leadOff"] is False
    assert channel_b["clipLow"] == 0

    source.meta.a.reported_leads_off = True
    assert runtime._quality_error() == "Electrode lead-off on channel(s) A"


def test_hybrid_left_burst_crosses_its_calibrated_dsp_threshold() -> None:
    settings = DualDriveSettings(mode="hybrid", inject_keys=False)
    generator = HybridMockChannelB(settings)
    processor = StreamingEMGProcessor(settings)  # type: ignore[arg-type]
    rest: list[float] = []
    for _ in range(150):
        result = processor.process(generator.generate(20))
        rest.extend(point.value for point in result.rms_points)
    _mean, _std, threshold = DualDriveRuntime._calibrated_threshold(
        rest, settings
    )

    assert generator.inject_left() is True
    burst: list[float] = []
    for _ in range(20):
        result = processor.process(generator.generate(20))
        burst.extend(point.value for point in result.rms_points)

    assert max(burst) > threshold


def test_hybrid_live_a_emits_forward_and_overlap_can_never_emit_right() -> None:
    settings = DualDriveSettings(mode="hybrid")
    source = IdleSource()
    injector = StubInjector()
    runtime = DualDriveRuntime(settings, source=source, injector=injector)
    runtime.thresholds = {"a": 10, "b": 10}
    runtime.channel_calibrated = {"a": True, "b": True}
    runtime.armed = True

    assert runtime._handle_rms(
        11, 0, captured_at=time.perf_counter(), now=1.0
    ) is None
    forward = runtime._handle_rms(
        11, 0, captured_at=time.perf_counter(), now=1.081
    )
    assert forward is not None
    assert forward["action"] == "forward"
    assert injector.calls == [("up", 1_000)]

    second_injector = StubInjector()
    second = DualDriveRuntime(settings, source=source, injector=second_injector)
    second.thresholds = {"a": 10, "b": 10}
    second.channel_calibrated = {"a": True, "b": True}
    second.armed = True
    overlap = second._handle_rms(
        11, 11, captured_at=time.perf_counter(), now=2.0
    )
    assert overlap is not None
    assert overlap["action"] == "left"
    assert second_injector.calls == [("left", 200)]
    assert second.counts == {"forward": 0, "left": 1, "right": 0}
    status = second.snapshot()
    assert status["availableActions"] == ["forward", "left"]
    assert status["mapping"]["a+b"]["available"] is False


def test_hybrid_mock_trigger_api_accepts_only_disclosed_left() -> None:
    settings = DualDriveSettings(mode="hybrid", inject_keys=False)
    source = IdleSource()
    runtime = DualDriveRuntime(
        settings,
        source=source,
        injector=StubInjector(),
    )
    app = create_dual_drive_app(settings, runtime=runtime)

    with TestClient(app) as client:
        status = client.get("/api/status").json()
        assert status["mode"] == "hybrid"
        assert status["channelSources"] == {"a": "hardware", "b": "simulated"}
        assert status["demoDisclosure"].startswith("HYBRID:")

        accepted = client.post("/api/mock/trigger", json={"action": "left"})
        assert accepted.status_code == 200
        assert accepted.json()["accepted"] is True
        busy = client.post("/api/mock/trigger", json={"action": "left"})
        assert busy.status_code == 200
        assert busy.json()["accepted"] is False

        forward = client.post("/api/mock/trigger", json={"action": "forward"})
        right = client.post("/api/mock/trigger", json={"action": "right"})
        assert forward.status_code == 409
        assert right.status_code == 409
        assert "FORWARD is live" in forward.json()["detail"]
        assert "RIGHT is unavailable" in right.json()["detail"]


def test_api_contract_and_websocket_commands() -> None:
    settings = DualDriveSettings(mode="mock", inject_keys=False)
    source = IdleSource()
    runtime = DualDriveRuntime(
        settings,
        source=source,
        injector=StubInjector(),
    )
    app = create_dual_drive_app(settings, runtime=runtime)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["service"] == "dual-threshold-drive"

        assert client.post(
            "/api/threshold", json={"channel": "a", "value": 80}
        ).status_code == 200
        assert client.post(
            "/api/threshold", json={"channel": "b", "value": 90}
        ).status_code == 200
        runtime.arbiter.both_low_since = time.monotonic() - 1
        armed = client.post("/api/armed", json={"armed": True})
        assert armed.status_code == 200
        assert armed.json()["armed"] is True

        status = client.get("/api/status").json()
        assert set(status["channels"]) == {"a", "b"}
        assert status["counts"] == {"forward": 0, "left": 0, "right": 0}
        assert status["forwardPulseMs"] == 1_000
        assert status["turnPulseMs"] == 200

        with client.websocket_connect("/ws") as socket:
            first = socket.receive_json()
            assert first["type"] == "status"
            socket.send_json(
                {"type": "threshold", "channel": "a", "value": 70}
            )
            response = socket.receive_json()
            assert response["channels"]["a"]["threshold"] == 70
            assert response["armed"] is False


def test_telemetry_contains_only_current_block_rms_delta() -> None:
    settings = DualDriveSettings(mode="mock")
    source = DualMockSource(settings)
    source.meta.connected = True
    runtime = DualDriveRuntime(settings, source=source, injector=StubInjector())
    runtime.rms_history["a"].extend([1, 2, 3])
    runtime.rms_history["b"].extend([4, 5, 6])
    raw = np.full((20, 2), 2_000)
    payload = runtime.telemetry(
        raw=raw,
        filtered=np.zeros((20, 2)),
        rms_points={"a": [3.0], "b": [6.0]},
    )

    assert payload["channels"]["a"]["rmsSeries"] == [3.0]
    assert payload["channels"]["b"]["rmsSeries"] == [6.0]
    assert len(payload["channels"]["a"]["rawSeries"]) == 20
