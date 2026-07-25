#!/usr/bin/env python3
"""Open the ESP32 safely and print a short live ADC/lead-off diagnostic."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from somach.config import Settings
from somach.sources import HardwareUnavailable, SerialSource


async def probe(port: str | None, sample_count: int) -> int:
    try:
        source = SerialSource(Settings(mode="hardware", serial_port=port))
        print(f"Opening {source.port} at 115200 baud without DTR/RTS reset...")
        await source.start()
    except HardwareUnavailable as exc:
        print(f"Hardware check failed: {exc}", file=sys.stderr)
        return 2

    values: list[int] = []
    try:
        iterator = source.blocks().__aiter__()
        while len(values) < sample_count:
            block = await asyncio.wait_for(iterator.__anext__(), timeout=5.0)
            values.extend(int(value) for value in block.raw)
    except (TimeoutError, StopAsyncIteration):
        print(
            "The port opened, but no ADC lines arrived within 5 seconds. "
            "Flash firmware/ and close Serial Monitor.",
            file=sys.stderr,
        )
        return 3
    finally:
        await source.stop()

    values = values[:sample_count]
    print("\n--- LIVE sEMG ADC STREAM (GPIO 36) ---")
    for value in values:
        print(f"Raw ADC: {value}")
    print(
        f"\nmean={statistics.fmean(values):.1f} "
        f"min={min(values)} max={max(values)} "
        f"lead_off={source.meta.leads_off} "
        f"firmware_rate={source.meta.sample_rate:.1f} Hz"
    )
    if source.meta.leads_off:
        print("WARNING: LO+ or LO- reports a detached electrode.")
    if min(values) <= 4 or max(values) >= 4091:
        print("WARNING: ADC is rail-clipping; inspect sensor power and contacts.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="explicit /dev/cu.* device")
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    return asyncio.run(probe(args.port, max(1, args.samples)))


if __name__ == "__main__":
    raise SystemExit(main())
