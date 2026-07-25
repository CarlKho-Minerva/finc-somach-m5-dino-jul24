#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d "/Applications/Google Chrome.app" ]]; then
  echo "Google Chrome is not installed in /Applications." >&2
  exit 1
fi

open -na "Google Chrome" --args --new-window "https://flappybird.io/"
