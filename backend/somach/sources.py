"""Acquisition sources for the deterministic mock and the live ESP32."""

from __future__ import annotations

import asyncio
import glob
import logging
import math
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import numpy as np

from .config import Settings

logger = logging.getLogger(__name__)


class HardwareUnavailable(RuntimeError):
    """Raised when hardware mode cannot open a suitable serial device."""


@dataclass(slots=True)
class SourceMeta:
    source: str
    device: str
    connected: bool = False
    sample_rate: float = 0.0
    lo_plus: bool = False
    lo_minus: bool = False
    clipped: int = 0
    dropped: int = 0
    late: int = 0
    host_dropped_blocks: int = 0
    error: str | None = None

    @property
    def leads_off(self) -> bool:
        return self.lo_plus or self.lo_minus


@dataclass(slots=True, frozen=True)
class SampleBlock:
    raw: np.ndarray
    captured_at: float


class SampleSource:
    """Minimal interface shared by mock and serial acquisition."""

    meta: SourceMeta

    async def start(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def stop(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def blocks(self) -> AsyncIterator[SampleBlock]:  # pragma: no cover
        raise NotImplementedError


class MockSource(SampleSource):
    """Generate reproducible ADC-like rest noise and an sEMG-shaped JUMP burst."""

    def __init__(self, settings: Settings, *, seed: int = 7):
        self.settings = settings
        self.meta = SourceMeta(
            source="mock",
            device="synthetic://somach-emg",
            sample_rate=float(settings.sample_rate_hz),
        )
        self._rng = np.random.default_rng(seed)
        self._running = False
        self._sample_index = 0
        self._burst_index: int | None = None
        self._burst_length = round(settings.sample_rate_hz * 0.18)

    async def start(self) -> None:
        self._running = True
        self.meta.connected = True

    async def stop(self) -> None:
        self._running = False
        self.meta.connected = False

    def inject_jump(self) -> bool:
        """Schedule one 180 ms, band-limited mock JUMP articulation burst."""

        already_active = self._burst_index is not None
        if not already_active:
            self._burst_index = 0
        return not already_active

    def _generate(self, count: int) -> np.ndarray:
        fs = self.settings.sample_rate_hz
        indices = np.arange(self._sample_index, self._sample_index + count)
        seconds = indices / fs

        # Resting signal: ADC midpoint, white electrode noise, slow drift, and
        # a small 60 Hz component so the notch is visible on the dashboard.
        raw = (
            2_000.0
            + self._rng.normal(0.0, 7.0, count)
            + 5.0 * np.sin(2 * np.pi * 60.0 * seconds)
            + 3.0 * np.sin(2 * np.pi * 1.2 * seconds)
        )

        for offset in range(count):
            if self._burst_index is None:
                break
            phase = self._burst_index / max(1, self._burst_length - 1)
            envelope = math.sin(math.pi * phase) ** 2
            t = (self._sample_index + offset) / fs
            muscle = (
                260.0 * math.sin(2 * math.pi * 83.0 * t)
                + 180.0 * math.sin(2 * math.pi * 137.0 * t + 0.4)
                + 110.0 * self._rng.normal()
            )
            raw[offset] += envelope * muscle
            self._burst_index += 1
            if self._burst_index >= self._burst_length:
                self._burst_index = None

        self._sample_index += count
        return np.clip(np.rint(raw), 0, 4_095).astype(np.uint16)

    async def blocks(self) -> AsyncIterator[SampleBlock]:
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
                # A suspended laptop should resume now, not emit a huge catch-up.
                deadline = loop.time()

            yield SampleBlock(self._generate(count), time.perf_counter())


def _looks_like_esp32_port(device: str, description: str = "") -> bool:
    value = f"{device} {description}".lower()
    excluded = ("bluetooth", "debug-console")
    markers = (
        "usbserial",
        "usbmodem",
        "wchusb",
        "slab_usb",
        "cp210",
        "ch340",
        "ftdi",
        "uart",
        "esp32",
    )
    return not any(word in value for word in excluded) and any(
        word in value for word in markers
    )


def serial_port_inventory() -> list[tuple[str, str]]:
    """Return all visible serial ports without failing if pyserial is absent."""

    try:
        from serial.tools import list_ports
    except ImportError:
        return [(path, "") for path in sorted(glob.glob("/dev/cu.*"))]
    return [(port.device, port.description or "") for port in list_ports.comports()]


def detect_serial_port(explicit: str | None = None) -> str:
    if explicit:
        return explicit

    inventory = serial_port_inventory()
    candidates = [
        device
        for device, description in inventory
        if _looks_like_esp32_port(device, description)
    ]
    if candidates:
        # macOS callout devices (/dev/cu.*) avoid waiting for carrier detect.
        candidates.sort(key=lambda value: (not value.startswith("/dev/cu."), value))
        return candidates[0]

    visible = ", ".join(device for device, _ in inventory) or "none"
    raise HardwareUnavailable(
        "No ESP32 USB serial port is visible. Expected /dev/cu.usbserial-*, "
        "/dev/cu.usbmodem*, /dev/cu.wchusbserial*, or /dev/cu.SLAB_USBtoUART. "
        f"Visible ports: {visible}. Reconnect a data-capable USB cable, press the "
        "ESP32 EN button once, and retry; use --serial-port PATH if needed."
    )


def parse_meta_line(line: str) -> dict[str, str]:
    """Parse `#META,key=value,...` while tolerating future firmware fields."""

    if not line.startswith("#META"):
        return {}
    fields: dict[str, str] = {}
    for token in line.split(",")[1:]:
        key, separator, value = token.partition("=")
        if separator:
            fields[key.strip().lower()] = value.strip()
    return fields


def parse_adc_line(line: str) -> int | None:
    """Accept only a plain 12-bit integer; ignore boot and metadata logs."""

    value = line.strip()
    if not value or not value.isdecimal():
        return None
    parsed = int(value)
    return parsed if 0 <= parsed <= 4_095 else None


class SerialSource(SampleSource):
    """Read 1 kHz newline-framed ADC values on a dedicated thread."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.port = detect_serial_port(settings.serial_port)
        self.meta = SourceMeta(source="hardware", device=self.port)
        self._queue: asyncio.Queue[SampleBlock | None] = asyncio.Queue(maxsize=50)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._serial = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _open_serial(self):
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - dependency bootstrap
            raise HardwareUnavailable(
                "pyserial is missing; run `uv sync --python 3.11` first"
            ) from exc

        # Start closed. Set both handshake modes and physical line states before
        # open(), reducing the chance that an ESP32 auto-reset circuit is pulsed.
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
                f"Could not open ESP32 at {self.port}: {exc}. Close Arduino "
                "Serial Monitor or any other process using the port."
            ) from exc

        # Required boot-settle interval and removal of bootloader text/stale data.
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
            name="esp32-serial-reader",
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

    def _offer(self, block: SampleBlock | None) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self.meta.host_dropped_blocks += 1
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(block)

    def _apply_meta(self, fields: dict[str, str]) -> None:
        def integer(name: str, current: int) -> int:
            try:
                return int(float(fields.get(name, current)))
            except ValueError:
                return current

        def boolean(name: str, current: bool) -> bool:
            raw = fields.get(name)
            return current if raw is None else raw.lower() in {"1", "true", "yes"}

        try:
            self.meta.sample_rate = float(
                fields.get(
                    "rate_hz",
                    fields.get(
                        "rate", fields.get("sample_rate", self.meta.sample_rate)
                    ),
                )
            )
        except ValueError:
            pass
        self.meta.lo_plus = boolean("lo_plus", self.meta.lo_plus)
        self.meta.lo_minus = boolean("lo_minus", self.meta.lo_minus)
        clip_low = integer("clip_low", 0)
        clip_high = integer("clip_high", 0)
        self.meta.clipped = integer("clipped", clip_low + clip_high)
        self.meta.dropped = integer(
            "tx_drop_total", integer("dropped", self.meta.dropped)
        )
        self.meta.late = integer("missed_total", integer("late", self.meta.late))

    def _reader_loop(self) -> None:
        assert self._loop is not None
        assert self._serial is not None
        values: list[int] = []
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
                sample = parse_adc_line(line)
                if sample is None:
                    continue

                values.append(sample)
                rate_count += 1
                now = time.monotonic()
                elapsed = now - rate_started
                if elapsed >= 1.0 and self.meta.sample_rate <= 0:
                    self.meta.sample_rate = rate_count / elapsed
                    rate_started = now
                    rate_count = 0

                if len(values) >= block_size:
                    block = SampleBlock(
                        np.asarray(values[:block_size], dtype=np.uint16),
                        time.perf_counter(),
                    )
                    del values[:block_size]
                    self._loop.call_soon_threadsafe(self._offer, block)
        except Exception as exc:  # serial disconnects arrive as driver exceptions
            if not self._stop_event.is_set():
                self.meta.error = f"ESP32 serial stream stopped: {exc}"
                self.meta.connected = False
                logger.exception("Serial reader stopped")
                self._loop.call_soon_threadsafe(self._offer, None)

    async def blocks(self) -> AsyncIterator[SampleBlock]:
        while not self._stop_event.is_set():
            block = await self._queue.get()
            if block is None:
                raise HardwareUnavailable(
                    self.meta.error or "ESP32 serial stream disconnected"
                )
            yield block


def create_source(settings: Settings) -> SampleSource:
    if settings.mode == "mock":
        return MockSource(settings)
    return SerialSource(settings)
