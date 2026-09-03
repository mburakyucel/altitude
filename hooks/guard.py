#!/usr/bin/env python3
"""PreToolUse guard for Bash in Altitude-owned sessions.

Shared services, ports, live Altitude state, and protected branches belong to altd and Burak, not
to a task. The installed Git hooks (see altitude.git_policy) are the mechanical guard for protected
refs. This hook only refuses the obvious shell forms and their bypasses so the model gets a clear
reason up front. It is a nudge, not a parser: plain regexes over the command text with heredoc
bodies removed.
"""
import json
import os
import re
import sys

inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
if inp.get("tool_name") != "Bash":
    sys.exit(0)
command = str((inp.get("tool_input") or {}).get("command") or "")

# Drop heredoc bodies (cat <<'EOF' ... EOF) so quoted prose is not judged as a command.
text = re.sub(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", "<<HEREDOC", command, flags=re.S | re.M)

ports = os.environ.get("ALTITUDE_SERVICE_PORTS", "8890,8080,8443").replace(",", "|")
protected = r"(?:refs/heads/)?(?:main|master)\b"
RULES = [
    (r"\bsystemctl\b.*\b(start|restart|stop|kill|enable|disable|mask|unmask)\b.*\b(altitude|tutor|wg-quick)",
     "service units belong to altd and Burak; report 'needs restart' instead"),
    (r"\b(ufw|wg-quick|iptables|nft)\b", "firewall and tunnel changes are never a task's"),
    (rf"(:|--port[= ]|PORT=|port\s+)({ports})\b",
     "service ports are taken; use ALTITUDE_TIMERS=0 on an ephemeral port for smoke tests"),
    (r"\brm\b.*(\.altitude\b|ALTITUDE_HOME)", "the Altitude home is live state"),
    (r"\b(p?kill|killall)\b.*\b(altd|altitude)\b", "altd is not yours to kill"),
    (rf"\bgit\b.*\b(push|update-ref|branch\s+-[fDM])\b.*\S*{protected}",
     "protected branches move only through `alt land`"),
    (r"\bgit\b.*\bpush\b.*(\s-f\b|--force\b)", "force pushes are never a task's; rebase, then `alt land`"),
    (r"\bgit\b.*--no-verify\b", "Git hooks are part of the landing policy; do not bypass them"),
    (r"core\.hooksPath|GIT_CONFIG_(?:COUNT|KEY|VALUE)|\bgit\b.*\bconfig\b.*\bhooks",
     "Git hook configuration belongs to Altitude"),
]
for pattern, why in RULES:
    match = re.search(pattern, text)
    if match:
        fragment = match.group(0).replace("\n", "\\n")[:160]
        print(f"altitude guard: blocked — {why}. Matched: {fragment}", file=sys.stderr)
        sys.exit(2)
sys.exit(0)
