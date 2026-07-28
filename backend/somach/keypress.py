"""Native macOS key injection through Quartz CoreGraphics.

Directional input is deliberately exposed only as a bounded pulse.  Every key
down therefore has both a timer-backed key up and an explicit ``release_all``
cleanup path, so a detector crash or retrigger cannot leave a driving key held.
"""

from __future__ import annotations

import atexit
import math
import platform
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Protocol


class _CancellableTimer(Protocol):
    """Small Timer surface used by the injector and deterministic tests."""

    daemon: bool

    def start(self) -> None: ...

    def cancel(self) -> None: ...


TimerFactory = Callable[[float, Callable[[], None]], _CancellableTimer]


@dataclass(slots=True)
class KeypressResult:
    posted: bool
    call_ms: float | None
    reason: str | None = None

    def to_dict(self) -> dict[str, bool | float | str | None]:
        return asdict(self)


class QuartzKeyInjector:
    """Post hardware-level key events without PyAutoGUI's built-in pauses."""

    SPACE_KEY_CODE = 49

    # macOS virtual key codes from HIToolbox/Events.h.  Keep this mapping
    # intentionally small: callers cannot turn classifier labels into arbitrary
    # keyboard input.
    DIRECTION_KEY_CODES = {
        "left": 123,
        "right": 124,
        "down": 125,
        "up": 126,
    }
    MIN_PULSE_MS = 20.0
    MAX_PULSE_MS = 2_000.0

    def __init__(
        self,
        *,
        enabled: bool = True,
        quartz: object | None = None,
        timer_factory: TimerFactory = threading.Timer,
    ):
        self.enabled = enabled
        self.available = False
        self.trusted = False
        self.detail = "Key injection disabled by --no-keypress"
        self.last_call_ms: float | None = None
        self.last_posted = False
        self._quartz = None
        self._lock = threading.RLock()
        self._timer_factory = timer_factory
        self._held_directions: set[str] = set()
        self._release_timers: dict[
            str, tuple[_CancellableTimer, object]
        ] = {}

        if not enabled:
            return
        if quartz is not None:
            self._quartz = quartz
            self.available = True
            self.refresh_trust(prompt=False)
            atexit.register(self.release_all)
            return
        if platform.system() != "Darwin":
            self.detail = "Quartz key injection is available only on macOS"
            return
        try:
            import Quartz
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - host bridge
            self.detail = f"Quartz import failed: {exc}"
            return

        self._quartz = Quartz
        self.available = True
        self.refresh_trust(prompt=False)
        atexit.register(self.release_all)

    def refresh_trust(self, *, prompt: bool = False) -> bool:
        if not self.available or self._quartz is None:
            return False
        try:
            if prompt and hasattr(self._quartz, "AXIsProcessTrustedWithOptions"):
                option = getattr(
                    self._quartz,
                    "kAXTrustedCheckOptionPrompt",
                    "AXTrustedCheckOptionPrompt",
                )
                self.trusted = bool(
                    self._quartz.AXIsProcessTrustedWithOptions({option: True})
                )
            elif hasattr(self._quartz, "AXIsProcessTrusted"):
                self.trusted = bool(self._quartz.AXIsProcessTrusted())
            else:
                # Very old bindings may omit the preflight API. Posting is still
                # attempted and macOS remains the final permission authority.
                self.trusted = True
            self.detail = (
                "Accessibility access granted"
                if self.trusted
                else "Accessibility access required for game key injection"
            )
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - ObjC bridge
            self.trusted = False
            self.detail = f"Could not check Accessibility access: {exc}"
        return self.trusted

    def post_space(self) -> KeypressResult:
        if not self.enabled:
            return KeypressResult(False, None, "key injection disabled")
        if not self.available or self._quartz is None:
            return KeypressResult(False, None, self.detail)
        if not self.refresh_trust(prompt=False):
            return KeypressResult(False, None, self.detail)

        started = time.perf_counter_ns()
        with self._lock:
            try:
                down = self._create_event(self.SPACE_KEY_CODE, True)
                up = self._create_event(self.SPACE_KEY_CODE, False)
                self._post_event(down)
                self._post_event(up)
            except Exception as exc:  # noqa: BLE001  # pragma: no cover - ObjC bridge
                self.last_posted = False
                return KeypressResult(False, None, f"Quartz post failed: {exc}")

            self.last_call_ms = (time.perf_counter_ns() - started) / 1_000_000
            self.last_posted = True
            return KeypressResult(True, self.last_call_ms)

    def pulse_direction(
        self, direction: str, *, duration_ms: float = 250.0
    ) -> KeypressResult:
        """Hold one whitelisted arrow key briefly, then release it automatically.

        Separate directions have independent timers, so calls for ``up`` and
        ``left``/``right`` can overlap.  Re-pulsing an already-held direction
        extends its deadline without posting a second key-down event.  Durations
        are clamped to a short, finite safety range.
        """

        normalized = direction.strip().lower() if isinstance(direction, str) else ""
        key_code = self.DIRECTION_KEY_CODES.get(normalized)
        if key_code is None:
            return KeypressResult(False, None, "direction must be up/down/left/right")
        try:
            requested_ms = float(duration_ms)
        except (TypeError, ValueError):
            return KeypressResult(False, None, "pulse duration must be a finite number")
        if not math.isfinite(requested_ms):
            return KeypressResult(False, None, "pulse duration must be a finite number")
        bounded_ms = min(max(requested_ms, self.MIN_PULSE_MS), self.MAX_PULSE_MS)

        if not self.enabled:
            return KeypressResult(False, None, "key injection disabled")
        if not self.available or self._quartz is None:
            return KeypressResult(False, None, self.detail)
        if not self.refresh_trust(prompt=False):
            return KeypressResult(False, None, self.detail)

        started = time.perf_counter_ns()
        with self._lock:
            previous = self._release_timers.pop(normalized, None)
            if previous is not None:
                previous[0].cancel()

            posted_down = False
            token = object()
            try:
                if normalized not in self._held_directions:
                    self._post_event(self._create_event(key_code, True))
                    self._held_directions.add(normalized)
                    posted_down = True

                timer = self._timer_factory(
                    bounded_ms / 1_000.0,
                    lambda: self._release_direction(normalized, token),
                )
                # A release watchdog must never keep the backend process alive.
                timer.daemon = True
                self._release_timers[normalized] = (timer, token)
                timer.start()
            except Exception as exc:  # noqa: BLE001
                self._release_timers.pop(normalized, None)
                # If either this call or an earlier pulse is holding the key,
                # release immediately when watchdog setup fails.
                if posted_down or normalized in self._held_directions:
                    self._release_now(normalized)
                self.last_posted = False
                return KeypressResult(False, None, f"direction pulse failed: {exc}")

            self.last_call_ms = (time.perf_counter_ns() - started) / 1_000_000
            self.last_posted = True
            return KeypressResult(True, self.last_call_ms)

    def release_all(self) -> None:
        """Cancel watchdogs and post key-up for every held direction.

        This method is idempotent and registered with :mod:`atexit` whenever
        Quartz is available.  Runtime shutdown paths may also call it directly.
        """

        with self._lock:
            timers = list(self._release_timers.values())
            self._release_timers.clear()
            for timer, _token in timers:
                timer.cancel()
            for direction in sorted(self._held_directions):
                self._release_now(direction)

    def _release_direction(self, direction: str, token: object) -> None:
        """Release only if this is still the direction's newest watchdog."""

        with self._lock:
            current = self._release_timers.get(direction)
            if current is None or current[1] is not token:
                return
            self._release_timers.pop(direction, None)
            self._release_now(direction)

    def _release_now(self, direction: str) -> None:
        """Best-effort key-up used while the caller holds ``self._lock``."""

        if direction not in self._held_directions:
            return
        try:
            key_code = self.DIRECTION_KEY_CODES[direction]
            self._post_event(self._create_event(key_code, False))
        except Exception:  # noqa: BLE001  # cleanup must remain idempotent
            pass
        finally:
            self._held_directions.discard(direction)

    def _create_event(self, key_code: int, is_down: bool) -> object:
        if self._quartz is None:  # pragma: no cover - guarded by callers
            raise RuntimeError("Quartz is unavailable")
        event = self._quartz.CGEventCreateKeyboardEvent(None, key_code, is_down)
        if event is None:
            raise RuntimeError("CoreGraphics did not create keyboard events")
        return event

    def _post_event(self, event: object) -> None:
        if self._quartz is None:  # pragma: no cover - guarded by callers
            raise RuntimeError("Quartz is unavailable")
        self._quartz.CGEventPost(self._quartz.kCGHIDEventTap, event)

    def status(self) -> dict[str, bool | float | str | None]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "available": self.available,
                "trusted": self.trusted,
                "detail": self.detail,
                "lastCallMs": self.last_call_ms,
                "lastPosted": self.last_posted,
            }
