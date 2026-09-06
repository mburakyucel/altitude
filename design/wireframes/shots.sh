#!/usr/bin/env bash
# Render every board beside this script to design/wireframes/shots/<Board>.png and
# <Board>.dark.png (gitignored). Each board renders at its own native size, read from the root
# element's inline width and height, so a sheet and a phone need no list here.
# Uses the Chrome on this machine; override with CHROME=/path/to/chrome.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out="$here/shots"
chrome=${CHROME:-/usr/bin/google-chrome}
mkdir -p "$out"
for board in "$here"/*.html; do
  name=$(basename "$board" .html)
  [ "$name" = index ] && continue
  size=$(grep -o 'style="width:[0-9]*px;height:[0-9]*px"' "$board" | head -1 | tr -dc '0-9;' | tr ';' ',')
  for theme in light dark; do
    png="$out/$name.png"; url="file://$board"
    if [ "$theme" = dark ]; then png="$out/$name.dark.png"; url="$url?dark"; fi
    "$chrome" --headless --disable-gpu --no-sandbox --hide-scrollbars --force-device-scale-factor=1 \
      --window-size="$size" --virtual-time-budget=8000 \
      --screenshot="$png" "$url" >/dev/null 2>&1
    if [ -s "$png" ]; then
      echo "$png ($size)"
    else
      echo "shots.sh: no PNG for $name ($theme)" >&2
      exit 1
    fi
  done
done
