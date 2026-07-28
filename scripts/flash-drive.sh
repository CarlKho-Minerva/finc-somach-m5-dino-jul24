#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || "$1" != /dev/cu.* ]]; then
  echo "usage: $0 /dev/cu.usbserial-XXXXXXXX" >&2
  exit 2
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
port="$1"

if command -v pio >/dev/null 2>&1; then
  pio_runner=(pio)
elif command -v platformio >/dev/null 2>&1; then
  pio_runner=(platformio)
elif command -v uvx >/dev/null 2>&1; then
  pio_runner=(uvx --from platformio pio)
else
  echo "PlatformIO is missing. Install it with: uv tool install platformio" >&2
  exit 2
fi

echo "Electrodes must be off the body before flashing."
"${pio_runner[@]}" run \
  --project-dir "$project_dir/firmware" \
  -e dual_ad8232 \
  -t upload \
  --upload-port "$port"
