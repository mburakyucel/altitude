#!/usr/bin/env python3
"""PreToolUse hook on Agent|Task: count subagent launches per session; block past the envelope cap (decision 31)."""
import json, os, sys
from pathlib import Path
inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
sid = inp.get("session_id") or "unknown"
if inp.get("tool_name") == "Bash":
    import re
    cmd = str((inp.get("tool_input") or {}).get("command") or "")
    if not re.search(r"\bcodex\s+exec\b|\bclaude\s+(-p|--print|--bg)\b", cmd):
        sys.exit(0)  # ordinary shell command: not a launch
    if re.search(r"(^|\s)(--help|-h|--version|-V)(\s|$)", cmd):
        sys.exit(0)  # capability probe, not a launch (I-004 candidate: an L2 lost a launch to `codex exec --help`)
root = Path(os.environ.get("ALTITUDE_HOME", Path.home() / ".altitude"))
mon = root / "monitor"; mon.mkdir(parents=True, exist_ok=True)
counts_p = mon / f"counts-{sid}.json"
def fault(msg):  # decision 36: a hook cannot reach the server, so it leaves a line the tick raises as a system fault
    try:
        with open(mon / "hook-faults.log", "a") as f: f.write(f"subagent_cap.py session={sid}: {msg}\n")
    except OSError: pass
try: counts = json.loads(counts_p.read_text()) if counts_p.exists() else {}
except Exception as e: counts = {}; fault(f"counts file unreadable, counter reset: {e}")
cap = 8
key = os.environ.get("ALTITUDE_SESSION_KEY")
if key:
    try: cap = int(json.loads((mon / f"envelope-{key}.json").read_text()).get("subagent_launches", cap))
    except Exception as e: fault(f"envelope file for {key} unreadable, default cap {cap} used: {e}")
n = int(counts.get("subagent_launches", 0)) + 1
counts["subagent_launches"] = n; counts["cap"] = cap
tmp = counts_p.with_suffix(".tmp"); tmp.write_text(json.dumps(counts)); os.replace(tmp, counts_p)
if n > cap:
    print(f"altitude: envelope reached ({n-1}/{cap} subagent launches). Do not launch more agents: checkpoint progress.md, write the report with `Blocked: envelope (needed N)`, and stop.", file=sys.stderr)
    sys.exit(2)
