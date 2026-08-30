#!/usr/bin/env python3
"""PostToolUse hook on Edit|Write|MultiEdit: count edits per session (collapse signal for L2: 'minor fix' limits)."""
import json, os, sys
from pathlib import Path
inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
sid = inp.get("session_id") or "unknown"
root = Path(os.environ.get("ALTITUDE_HOME", Path.home() / ".altitude"))
mon = root / "monitor"; mon.mkdir(parents=True, exist_ok=True)
p = mon / f"counts-{sid}.json"
try: c = json.loads(p.read_text()) if p.exists() else {}
except Exception as e:  # decision 36: leave a line the server raises as a system fault instead of silently resetting
    c = {}
    try:
        with open(mon / "hook-faults.log", "a") as f: f.write(f"edit_count.py session={sid}: counts file unreadable, reset: {e}\n")
    except OSError: pass
c["edits"] = int(c.get("edits", 0)) + 1
files = set(c.get("files") or []); fp = (inp.get("tool_input") or {}).get("file_path")
if fp: files.add(fp)
c["files"] = sorted(files)[:200]
tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(c)); os.replace(tmp, p)
