#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

uv sync --frozen --python 3.11
uv run pytest
npm --prefix frontend ci
npm --prefix frontend run build
if command -v pio >/dev/null 2>&1; then
  pio run --project-dir firmware -e esp32dev
  pio run --project-dir firmware -e dual_ad8232
else
  uvx --with pip --from platformio pio run --project-dir firmware -e esp32dev
  uvx --with pip --from platformio pio run --project-dir firmware -e dual_ad8232
fi
git diff --check

echo "Backend tests, frontend build, ESP32 compile, and whitespace checks passed."
