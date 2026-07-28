#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

mode="${1:---hardware}"
if [[ "$mode" != "--hardware" && "$mode" != "--mock" && "$mode" != "--hybrid" ]]; then
  echo "Usage: ./scripts/start-drive-dashboard.sh [--hardware|--mock|--hybrid] [backend options]" >&2
  exit 2
fi
shift || true

echo "Preparing the local SOMACH environment..."
uv sync --frozen --python 3.11
if [[ ! -d frontend/node_modules ]]; then
  npm --prefix frontend ci
fi

mkdir -p .run
backend_log="$project_dir/.run/drive-backend.log"
frontend_log="$project_dir/.run/drive-frontend.log"
: > "$backend_log"
: > "$frontend_log"

backend_pid=""
frontend_pid=""
cleanup() {
  [[ -n "$backend_pid" ]] && kill "$backend_pid" 2>/dev/null || true
  [[ -n "$frontend_pid" ]] && kill "$frontend_pid" 2>/dev/null || true
  [[ -n "$backend_pid" ]] && wait "$backend_pid" 2>/dev/null || true
  [[ -n "$frontend_pid" ]] && wait "$frontend_pid" 2>/dev/null || true
}
stop_cleanly() {
  trap - EXIT INT TERM
  cleanup
  exit 0
}
trap cleanup EXIT
trap stop_cleanly INT TERM

uv run python backend/threshold_drive.py "$mode" "$@" \
  >"$backend_log" 2>&1 &
backend_pid=$!

ready=0
for _ in {1..100}; do
  if curl --silent --fail http://127.0.0.1:8124/health 2>/dev/null \
    | grep -q '"ok":true'; then
    ready=1
    break
  fi
  if ! kill -0 "$backend_pid" 2>/dev/null; then
    echo "Dual drive backend failed to start:" >&2
    sed -n '1,200p' "$backend_log" >&2
    exit 1
  fi
  sleep 0.1
done
if [[ "$ready" -ne 1 ]]; then
  echo "Dual drive backend did not become ready on port 8124:" >&2
  sed -n '1,200p' "$backend_log" >&2
  exit 1
fi

VITE_BACKEND_TARGET=http://127.0.0.1:8124 \
  npm --prefix frontend run dev -- --host 127.0.0.1 --port 3000 --strictPort \
  >"$frontend_log" 2>&1 &
frontend_pid=$!

frontend_ready=0
for _ in {1..80}; do
  if curl --silent --fail http://127.0.0.1:3000/drive >/dev/null 2>&1; then
    frontend_ready=1
    break
  fi
  if ! kill -0 "$frontend_pid" 2>/dev/null; then
    echo "Drive dashboard failed to start:" >&2
    sed -n '1,200p' "$frontend_log" >&2
    exit 1
  fi
  sleep 0.1
done
if [[ "$frontend_ready" -ne 1 ]]; then
  echo "Drive dashboard did not become ready on port 3000:" >&2
  sed -n '1,200p' "$frontend_log" >&2
  exit 1
fi

echo
echo "SOMACH directional drive is live: http://127.0.0.1:3000/drive"
echo "Mode: ${mode#--} | API: http://127.0.0.1:8124"
if [[ "$mode" == "--hybrid" ]]; then
  echo "DISCLOSED HYBRID: live A = forward | simulated B = left | right = disabled"
else
  echo "A mylohyoid = forward | B left masseter = left | A+B = right"
fi
echo "Logs: $backend_log and $frontend_log"
echo "Press Control-C here to stop both services and release every key."
echo

if command -v open >/dev/null 2>&1 && [[ "${SOMACH_NO_OPEN:-0}" != "1" ]]; then
  open http://127.0.0.1:3000/drive
fi

while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done

echo "A drive demo process exited unexpectedly." >&2
sed -n '1,160p' "$backend_log" >&2
sed -n '1,160p' "$frontend_log" >&2
exit 1
