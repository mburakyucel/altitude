#!/usr/bin/env python3
"""PreToolUse hook on Agent|Task: count subagent launches per session; block past the envelope cap (decision 31)."""
import hashlib, json, os, re, sys
from pathlib import Path

# I-005: a Bash launch is billed only when it stands in *command position* — heredoc bodies and quoted
# arguments are data, not launches — and every tool call is billed at most once via a dedupe key.
LAUNCH = r"(?:codex\s+exec\b|claude\s+(?:-p|--print|--bg)\b)"
PREFIX = r"(?:(?:[A-Za-z_]\w*=(?:\"[^\"]*\"|'[^']*'|\S*)|env|nohup|time|exec|sudo)\s+)*"
CMDPOS = re.compile(r"(?:^|[;&|(){}\n])[ \t]*" + PREFIX + r"(" + LAUNCH + r")")
HEREDOC = re.compile(r"<<-?[ \t]*(?:'([^']*)'|\"([^\"]*)\"|([A-Za-z_]\w*))")
PROBE = re.compile(r"(^|\s)(--help|-h|--version|-V)(\s|$)")


def strip_heredocs(cmd):  # a heredoc body is data: drop it before looking for launches
    lines, out, i = cmd.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]; i += 1
        out.append(line)
        for m in HEREDOC.finditer(line):
            delim = m.group(1) or m.group(2) or m.group(3)
            while i < len(lines):  # absent terminator: strip to end of input
                term, i = lines[i], i + 1
                if term.strip() == delim:
                    break
    return "\n".join(out)


def blank_quoted(s):  # blank the inside of quoted strings, keeping length and line structure
    out, q, i = [], None, 0
    while i < len(s):
        c = s[i]
        if q is None:
            if c in "'\"": q = c
            elif c == "\\" and i + 1 < len(s): out.append(c); c = s[i + 1]; i += 1
            out.append(c)
        else:
            if c == q: q = None; out.append(c)
            elif c == "\\" and q == '"' and i + 1 < len(s): out.append("  "); i += 1
            else: out.append(c if c == "\n" else " ")
        i += 1
    return "".join(out)


def is_launch(cmd):
    s = blank_quoted(strip_heredocs(cmd))
    for m in CMDPOS.finditer(s):
        seg = re.split(r"[;&|\n]", s[m.start(1):], 1)[0]
        if PROBE.search(seg):
            continue  # capability probe, not a launch (I-004: an L2 lost a launch to a `--help` probe)
        return True
    return False


inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
sid = inp.get("session_id") or "unknown"
tool = inp.get("tool_name")
tool_input = inp.get("tool_input") or {}
if tool == "Bash" and not is_launch(str(tool_input.get("command") or "")):
    sys.exit(0)  # ordinary shell command: not a launch
dkey = inp.get("tool_use_id") or hashlib.sha1(
    (str(tool) + json.dumps(tool_input, sort_keys=True, default=str)).encode()).hexdigest()[:16]
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
def blocked(n):
    print(f"altitude: envelope reached ({n-1}/{cap} subagent launches). Do not launch more agents: checkpoint progress.md, write the report with `Blocked: envelope (needed N)`, and stop.", file=sys.stderr)
    sys.exit(2)
seen = counts.get("seen") or []
if dkey in seen:  # same tool call re-delivered: bill it once, but never weaken blocking (R-006)
    n = int(counts.get("subagent_launches", 0))
    if n > cap: blocked(n)
    sys.exit(0)
n = int(counts.get("subagent_launches", 0)) + 1
counts["subagent_launches"] = n; counts["cap"] = cap; counts["seen"] = (seen + [dkey])[-200:]
tmp = counts_p.with_suffix(".tmp"); tmp.write_text(json.dumps(counts)); os.replace(tmp, counts_p)
if n > cap: blocked(n)
