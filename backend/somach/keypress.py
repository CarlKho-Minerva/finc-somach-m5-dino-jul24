"""Native macOS SPACE key injection through Quartz CoreGraphics."""

from __future__ import annotations

import platform
import time
from dataclasses import asdict, dataclass


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

    def __init__(self, *, enabled: bool = True):
        self.enabled = enabled
        self.available = False
        self.trusted = False
        self.detail = "Key injection disabled by --no-keypress"
        self.last_call_ms: float | None = None
        self.last_posted = False
        self._quartz = None

        if not enabled:
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
        try:
            down = self._quartz.CGEventCreateKeyboardEvent(
                None, self.SPACE_KEY_CODE, True
            )
            up = self._quartz.CGEventCreateKeyboardEvent(
                None, self.SPACE_KEY_CODE, False
            )
            if down is None or up is None:
                raise RuntimeError("CoreGraphics did not create keyboard events")
            self._quartz.CGEventPost(self._quartz.kCGHIDEventTap, down)
            self._quartz.CGEventPost(self._quartz.kCGHIDEventTap, up)
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - ObjC bridge
            self.last_posted = False
            return KeypressResult(False, None, f"Quartz post failed: {exc}")

        self.last_call_ms = (time.perf_counter_ns() - started) / 1_000_000
        self.last_posted = True
        return KeypressResult(True, self.last_call_ms)

    def status(self) -> dict[str, bool | float | str | None]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "trusted": self.trusted,
            "detail": self.detail,
            "lastCallMs": self.last_call_ms,
            "lastPosted": self.last_posted,
        }
