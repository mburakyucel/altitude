#!/usr/bin/env bash
# Wrapper for the global statusline: snapshot the JSON for Altitude's monitor, then run the original statusline.
# Install with `alt install-statusline` (edits ~/.claude/settings.json — only with Burak's OK).
set -u
ROOT="${ALTITUDE_HOME:-$HOME/.altitude}"
mkdir -p "$ROOT/monitor"
input="$(cat)"
sid="$(printf '%s' "$input" | jq -r '.session_id // "unknown"' 2>/dev/null || echo unknown)"
printf '%s' "$input" | jq --arg at "$(date +%s)" '. + {_at: ($at|tonumber)}' > "$ROOT/monitor/.statusline-$sid.tmp" 2>/dev/null && mv "$ROOT/monitor/.statusline-$sid.tmp" "$ROOT/monitor/statusline-$sid.json"
ORIG="${ALTITUDE_ORIG_STATUSLINE:-$HOME/.claude/statusline.sh}"
[ -x "$ORIG" ] && printf '%s' "$input" | "$ORIG"
