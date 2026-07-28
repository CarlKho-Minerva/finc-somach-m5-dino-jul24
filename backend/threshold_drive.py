#!/usr/bin/env python3
"""Launch the local two-channel threshold driving backend on port 8124."""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from somach.sources import HardwareUnavailable
from somach.threshold_drive import DualDriveSettings
from somach.threshold_drive_api import create_dual_drive_app


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description=(
            "Drive with two thresholded AD8232 channels: mylohyoid=UP, "
            "left masseter=LEFT, coactivation=RIGHT. Hybrid mode keeps A live "
            "and replaces a failed B sensor with an explicitly simulated LEFT."
        )
    )
    mode = command.add_mutually_exclusive_group()
    mode.add_argument("--hardware", action="store_true", help="read A,B at 460800")
    mode.add_argument("--mock", action="store_true", help="generate two local signals")
    mode.add_argument(
        "--hybrid",
        action="store_true",
        help="read A at 460800; ignore physical B and simulate LEFT only",
    )
    command.add_argument("--serial-port")
    command.add_argument("--baud", type=positive_int, default=460_800)
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", type=positive_int, default=8_124)
    command.add_argument("--coincidence-ms", type=positive_int, default=80)
    command.add_argument("--rearm-hold-ms", type=positive_int, default=180)
    command.add_argument("--rearm-ratio", type=float, default=0.80)
    command.add_argument("--no-keypress", action="store_true")
    command.add_argument("--prompt-accessibility", action="store_true")
    command.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error"),
        default="info",
    )
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = DualDriveSettings(
        mode="mock" if args.mock else "hybrid" if args.hybrid else "hardware",
        serial_port=args.serial_port,
        baud_rate=args.baud,
        host=args.host,
        api_port=args.port,
        coincidence_ms=args.coincidence_ms,
        rearm_hold_ms=args.rearm_hold_ms,
        rearm_ratio=args.rearm_ratio,
        inject_keys=not args.no_keypress,
        prompt_accessibility=args.prompt_accessibility,
    )
    try:
        settings.validate()
        app = create_dual_drive_app(settings)
    except (HardwareUnavailable, ValueError) as exc:
        print(f"\nSOMACH drive startup error: {exc}\n", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mapping = (
        "HYBRID (DISCLOSED): live A mylohyoid -> UP 1000 ms | "
        "simulated B via mock-trigger -> LEFT 200 ms | RIGHT DISABLED"
        if settings.mode == "hybrid"
        else "A mylohyoid -> UP 1000 ms | B left masseter -> LEFT 200 ms | "
        "A+B -> RIGHT 200 ms"
    )
    print(
        "\nSOMACH dual threshold drive\n"
        f"API: http://{settings.host}:{settings.api_port} | "
        f"WebSocket: ws://{settings.host}:{settings.api_port}/ws\n"
        f"{mapping}\n"
        f"Arbitration: {settings.coincidence_ms} ms coincidence | release "
        f"below {settings.rearm_ratio:.2f}T for {settings.rearm_hold_ms} ms\n"
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
