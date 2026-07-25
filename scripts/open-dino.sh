#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d "/Applications/Google Chrome.app" ]]; then
  echo "Google Chrome is not installed in /Applications." >&2
  exit 1
fi

# Passing the internal URL as a Chrome argument works even though ordinary web
# pages are not allowed to open the privileged chrome:// scheme themselves.
open -na "Google Chrome" --args --new-window "chrome://dino"
