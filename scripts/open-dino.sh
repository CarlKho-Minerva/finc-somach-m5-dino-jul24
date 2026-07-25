#!/usr/bin/env bash
set -euo pipefail

# Backward-compatible wrapper for the original Dino demo script name.
exec "$(dirname "${BASH_SOURCE[0]}")/open-flappy.sh"
