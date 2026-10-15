#!/usr/bin/env python3
"""PostToolUse hook on Edit|Write|MultiEdit: count the session's edits (collapse signal for L2: 'minor fix' limits).
altd keeps the count (`state.count_edit`), through the task door; while altd cannot be reached the edit goes
uncounted and the hook says so on its own output."""
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import door  # noqa: E402

door.follow_activation(__file__)

inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
if not os.environ.get("ALTITUDE_TASK"):
    sys.exit(0)
try:
    door.call("count", {"path": (inp.get("tool_input") or {}).get("file_path")}, wait=5, timeout=20)
except door.DoorError as exc:
    print(f"Altitude did not count this edit: {exc}", file=sys.stderr)
