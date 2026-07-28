#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python backend/drive.py "$@"
fi

uv sync --frozen --python 3.11
exec uv run python backend/drive.py "$@"
