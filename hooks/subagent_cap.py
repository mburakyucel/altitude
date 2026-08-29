#!/usr/bin/env python3
"""PreToolUse hook on Agent|Task: count subagent launches per session; block past the envelope cap (decision 31)."""
import json, os, sys
from pathlib import Path
inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
sid = inp.get("session_id") or "unknown"
root = Path(os.environ.get("ALTITUDE_HOME", Path.home() / ".altitude"))
mon = root / "monitor"; mon.mkdir(parents=True, exist_ok=True)
counts_p = mon / f"counts-{sid}.json"
try: counts = json.loads(counts_p.read_text())
except Exception: counts = {}
cap = 8
key = os.environ.get("ALTITUDE_SESSION_KEY")
if key:
    try: cap = int(json.loads((mon / f"envelope-{key}.json").read_text()).get("subagent_launches", cap))
    except Exception: pass
n = int(counts.get("subagent_launches", 0)) + 1
counts["subagent_launches"] = n; counts["cap"] = cap
tmp = counts_p.with_suffix(".tmp"); tmp.write_text(json.dumps(counts)); os.replace(tmp, counts_p)
if n > cap:
    print(f"altitude: envelope reached ({n-1}/{cap} subagent launches). Do not launch more agents: checkpoint progress.md, write the report with `Blocked: envelope (needed N)`, and stop.", file=sys.stderr)
    sys.exit(2)
