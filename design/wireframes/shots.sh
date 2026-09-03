#!/usr/bin/env bash
# Render every board beside this script to design/wireframes/shots/<Board>.png (gitignored).
# Desktop boards render at 1440x900. A board whose file name starts with "Mobile" renders at
# 390x960: a 116px annotation strip above an exact 390x844 iPhone screen.
# Uses the Chrome on this machine; override with CHROME=/path/to/chrome.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out="$here/shots"
chrome=${CHROME:-/usr/bin/google-chrome}
mkdir -p "$out"
for board in "$here"/*.html; do
  name=$(basename "$board" .html)
  case "$name" in
    Mobile*) size=390,960 ;;
    *) size=1440,900 ;;
  esac
  "$chrome" --headless --disable-gpu --no-sandbox --hide-scrollbars --force-device-scale-factor=1 \
    --window-size="$size" --virtual-time-budget=8000 \
    --screenshot="$out/$name.png" "file://$board" >/dev/null 2>&1
  if [ -s "$out/$name.png" ]; then
    echo "$out/$name.png ($size)"
  else
    echo "shots.sh: no PNG for $name" >&2
    exit 1
  fi
done
