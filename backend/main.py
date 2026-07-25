#!/usr/bin/env python3
"""Command-line entry point for the local SOMACH demo backend."""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn
from somach.api import create_app
from somach.config import Settings
from somach.sources import HardwareUnavailable


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description=(
            "Decode a local AD8232/ESP32 sEMG stream and post native macOS "
            "SPACE events for Chrome Dino. Defaults to deterministic mock mode."
        )
    )
    mode = command.add_mutually_exclusive_group()
    mode.add_argument(
        "--mock",
        action="store_true",
        help="generate local 1 kHz sEMG and simulate JUMP from the dashboard",
    )
    mode.add_argument(
        "--hardware",
        action="store_true",
        help="read the live ESP32 USB serial stream",
    )
    command.add_argument(
        "--serial-port",
        help="explicit ESP32 device, e.g. /dev/cu.usbserial-0001",
    )
    command.add_argument("--baud", type=int, default=115_200)
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", type=int, default=8_123)
    command.add_argument("--band-low", type=float, default=20.0)
    command.add_argument("--band-high", type=float, default=250.0)
    command.add_argument(
        "--no-keypress",
        action="store_true",
        help="detect events without posting SPACE through Quartz",
    )
    command.add_argument(
        "--prompt-accessibility",
        action="store_true",
        help="ask macOS for Accessibility access during startup",
    )
    command.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error"),
        default="info",
    )
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    mode = "hardware" if args.hardware else "mock"
    settings = Settings(
        mode=mode,
        serial_port=args.serial_port,
        baud_rate=args.baud,
        host=args.host,
        api_port=args.port,
        bandpass_low_hz=args.band_low,
        bandpass_high_hz=args.band_high,
        inject_keys=not args.no_keypress,
        prompt_accessibility=args.prompt_accessibility,
    )
    try:
        settings.validate()
        app = create_app(settings)
    except (HardwareUnavailable, ValueError) as exc:
        print(f"\nSOMACH startup error: {exc}\n", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print(
        f"\nSOMACH local pipeline: {mode.upper()} -> DSP -> Quartz -> Chrome Dino\n"
        f"API: http://{settings.host}:{settings.api_port}\n"
        f"Dashboard WebSocket: ws://{settings.host}:{settings.api_port}/ws\n"
        "Signal: 1000 Hz | notch 60 Hz Q30 | bandpass "
        f"{settings.bandpass_low_hz:g}-{settings.bandpass_high_hz:g} Hz | "
        "RMS 150/20 ms\n"
    )
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.api_port,
        log_level=args.log_level,
        ws_ping_interval=10.0,
        ws_ping_timeout=10.0,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
