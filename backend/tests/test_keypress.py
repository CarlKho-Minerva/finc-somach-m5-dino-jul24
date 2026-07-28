from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from somach.keypress import QuartzKeyInjector


class FakeQuartz:
    kCGHIDEventTap = "hid"

    def __init__(self) -> None:
        self.events: list[tuple[int, bool]] = []
        self.trusted = True

    def AXIsProcessTrusted(self) -> bool:
        return self.trusted

    def CGEventCreateKeyboardEvent(
        self, _source: object, key_code: int, is_down: bool
    ) -> tuple[int, bool]:
        return key_code, is_down

    def CGEventPost(self, event_tap: str, event: tuple[int, bool]) -> None:
        assert event_tap == self.kCGHIDEventTap
        self.events.append(event)


class FakeTimer:
    def __init__(self, interval: float, callback: Callable[[], None]) -> None:
        self.interval = interval
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self, *, even_if_cancelled: bool = False) -> None:
        if even_if_cancelled or not self.cancelled:
            self.callback()


class FakeTimerFactory:
    def __init__(self) -> None:
        self.timers: list[FakeTimer] = []

    def __call__(self, interval: float, callback: Callable[[], None]) -> FakeTimer:
        timer = FakeTimer(interval, callback)
        self.timers.append(timer)
        return timer


def make_injector() -> tuple[QuartzKeyInjector, FakeQuartz, FakeTimerFactory]:
    quartz = FakeQuartz()
    timers = FakeTimerFactory()
    injector = QuartzKeyInjector(quartz=quartz, timer_factory=timers)
    return injector, quartz, timers


def test_post_space_preserves_immediate_down_up_behavior() -> None:
    injector, quartz, _timers = make_injector()

    result = injector.post_space()

    assert result.posted is True
    assert result.call_ms is not None
    assert quartz.events == [(QuartzKeyInjector.SPACE_KEY_CODE, True), (49, False)]


@pytest.mark.parametrize(
    ("direction", "key_code"),
    [("left", 123), ("right", 124), ("down", 125), ("up", 126)],
)
def test_direction_pulse_posts_down_then_timer_backed_up(
    direction: str, key_code: int
) -> None:
    injector, quartz, timers = make_injector()

    result = injector.pulse_direction(direction, duration_ms=180)

    assert result.posted is True
    assert quartz.events == [(key_code, True)]
    assert timers.timers[-1].interval == pytest.approx(0.18)
    assert timers.timers[-1].daemon is True
    assert timers.timers[-1].started is True

    timers.timers[-1].fire()
    assert quartz.events == [(key_code, True), (key_code, False)]


def test_directions_can_overlap_for_acceleration_and_steering() -> None:
    injector, quartz, timers = make_injector()

    assert injector.pulse_direction("up", duration_ms=500).posted
    assert injector.pulse_direction("left", duration_ms=300).posted
    assert quartz.events == [(126, True), (123, True)]

    timers.timers[1].fire()
    assert quartz.events[-1] == (123, False)
    timers.timers[0].fire()
    assert quartz.events[-1] == (126, False)


def test_repulse_extends_deadline_without_duplicate_key_down() -> None:
    injector, quartz, timers = make_injector()

    assert injector.pulse_direction("UP", duration_ms=100).posted
    first_timer = timers.timers[-1]
    assert injector.pulse_direction("up", duration_ms=400).posted
    second_timer = timers.timers[-1]

    assert first_timer.cancelled is True
    assert quartz.events == [(126, True)]

    # Even a cancelled timer racing with its replacement cannot release early.
    first_timer.fire(even_if_cancelled=True)
    assert quartz.events == [(126, True)]
    second_timer.fire()
    assert quartz.events == [(126, True), (126, False)]


def test_pulse_duration_is_clamped_to_finite_safety_bounds() -> None:
    injector, _quartz, timers = make_injector()

    assert injector.pulse_direction("down", duration_ms=-100).posted
    assert timers.timers[-1].interval == pytest.approx(
        QuartzKeyInjector.MIN_PULSE_MS / 1_000
    )
    injector.release_all()

    assert injector.pulse_direction("down", duration_ms=math.inf).posted is False
    assert injector.pulse_direction("down", duration_ms=99_999).posted
    assert timers.timers[-1].interval == pytest.approx(
        QuartzKeyInjector.MAX_PULSE_MS / 1_000
    )


def test_only_named_arrow_directions_are_accepted() -> None:
    injector, quartz, timers = make_injector()

    result = injector.pulse_direction("space", duration_ms=100)

    assert result.posted is False
    assert result.reason == "direction must be up/down/left/right"
    assert quartz.events == []
    assert timers.timers == []


def test_release_all_cancels_watchdogs_and_releases_every_held_key() -> None:
    injector, quartz, timers = make_injector()
    injector.pulse_direction("up", duration_ms=500)
    injector.pulse_direction("right", duration_ms=500)

    injector.release_all()
    injector.release_all()  # cleanup is idempotent

    assert all(timer.cancelled for timer in timers.timers)
    assert quartz.events[:2] == [(126, True), (124, True)]
    assert set(quartz.events[2:]) == {(126, False), (124, False)}

    for timer in timers.timers:
        timer.fire(even_if_cancelled=True)
    assert len(quartz.events) == 4


def test_watchdog_setup_failure_immediately_releases_posted_key() -> None:
    class FailingTimer(FakeTimer):
        def start(self) -> None:
            raise RuntimeError("timer unavailable")

    quartz = FakeQuartz()

    def failing_timer_factory(
        interval: float, callback: Callable[[], None]
    ) -> FailingTimer:
        return FailingTimer(interval, callback)

    injector = QuartzKeyInjector(
        quartz=quartz,
        timer_factory=failing_timer_factory,
    )

    result = injector.pulse_direction("left")

    assert result.posted is False
    assert "timer unavailable" in (result.reason or "")
    assert quartz.events == [(123, True), (123, False)]
