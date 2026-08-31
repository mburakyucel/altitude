#!/usr/bin/env python3
"""PostToolUse hook on Edit|Write|MultiEdit: count edits per session (collapse signal for L2: 'minor fix' limits)."""
import fcntl, json, os, sys
from pathlib import Path
inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
sid = inp.get("session_id") or "unknown"
root = Path(os.environ.get("ALTITUDE_HOME", Path.home() / ".altitude"))
mon = root / "monitor"; mon.mkdir(parents=True, exist_ok=True)
key = os.environ.get("ALTITUDE_SESSION_KEY")
p = mon / f"counts-{key or sid}.json"

def fault(msg):  # leave a line the server raises as a system fault instead of silently resetting
    try:
        with open(mon / "hook-faults.log", "a") as f: f.write(f"edit_count.py session={sid} key={key or sid}: {msg}\n")
    except OSError: pass

lock_f = None
try:
    lock_f = open(p.with_name(f"{p.name}.lock"), "w")
    fcntl.flock(lock_f, fcntl.LOCK_EX)
except OSError as e:
    if lock_f:
        lock_f.close()
    lock_f = None
    fault(f"counts file lock unavailable, proceeding unlocked: {e}")

try:
    try: c = json.loads(p.read_text()) if p.exists() else {}
    except Exception as e: c = {}; fault(f"counts file unreadable, reset: {e}")
    c["edits"] = int(c.get("edits", 0)) + 1
    files = set(c.get("files") or []); fp = (inp.get("tool_input") or {}).get("file_path")
    if fp: files.add(fp)
    c["files"] = sorted(files)[:200]
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(c)); os.replace(tmp, p)
finally:
    if lock_f:
        try: fcntl.flock(lock_f, fcntl.LOCK_UN)
        except OSError as e: fault(f"counts file unlock failed: {e}")
        lock_f.close()
