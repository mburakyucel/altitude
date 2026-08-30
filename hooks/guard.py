#!/usr/bin/env python3
"""PreToolUse hook on Bash for L2/L1 sessions (decision 39): shared services and ports are altd's, not a task's.
Blocks (exit 2) restarts/stops of the altitude/tutor units, ufw/wg changes, binding the service ports, deleting the
Altitude home, and force-pushes. Everything else passes."""
import json, os, re, sys
inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
if inp.get("tool_name") != "Bash":
    sys.exit(0)
cmd = str((inp.get("tool_input") or {}).get("command") or "")
ports = os.environ.get("ALTITUDE_SERVICE_PORTS", "8890,8080,8443").replace(",", "|")
RULES = [
    (r"\bsystemctl\b.*\b(restart|stop|kill|disable|mask)\b.*\b(altitude|tutor|wg-quick)", "service units belong to altd/Burak — report 'needs restart' instead"),
    (r"\b(ufw|wg-quick|iptables|nft)\b", "firewall / tunnel changes are never a task's"),
    (r"(:|--port[= ]|PORT=|port\s+)(%s)\b" % ports, "service ports are taken — use ALTITUDE_TIMERS=0 on an ephemeral port for smoke tests"),
    (r"\brm\b.*(\.altitude|ALTITUDE_HOME)", "the Altitude home is live state"),
    (r"\bgit\s+push\b.*(\s-f\b|--force)", "no force-pushes (never-list)"),
    (r"\bkill(all)?\b.*\b(altd|altitude)\b", "altd is not yours to kill"),
]
for pat, why in RULES:
    if re.search(pat, cmd):
        print(f"altitude guard (decision 39): blocked — {why}. Command: {cmd[:160]}", file=sys.stderr)
        sys.exit(2)
sys.exit(0)
