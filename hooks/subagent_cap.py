#!/usr/bin/env python3
"""PreToolUse hook on Agent|Task: count subagent launches per session; block past the envelope cap (decision 31)."""
import fcntl, hashlib, json, os, re, sys
from pathlib import Path

# I-005: a Bash launch is billed only when it stands in *command position* — heredoc bodies and quoted
# arguments are data, not launches — and every tool call is billed at most once via a dedupe key.
LAUNCH = r"(?:codex\s+exec\b|claude\s+(?:-p|--print|--bg)\b|(?:\S*/)?alt\s+l1\s+run\b)"  # `alt l1 run` is a launch too (decision 45)
PREFIX = r"(?:(?:[A-Za-z_]\w*=(?:\"[^\"]*\"|'[^']*'|\S*)|env|nohup|time|exec|sudo)\s+)*"
CMDPOS = re.compile(r"(?:^|[;&|(){}`\n])[ \t]*" + PREFIX + r"(" + LAUNCH + r")")
HEREDOC = re.compile(r"(?<!<)<<-?(?!<)[ \t]*(?:'([^']*)'|\"([^\"]*)\"|([A-Za-z_]\w*))")
PROBE = re.compile(r"(^|\s)(--help|-h|--version|-V)(\s|$)")


def blank_quoted(s, stack=None):
    """Blank what a shell reads as data, keeping length and line structure; return the open-context stack.

    Single quotes hide everything. Double quotes hide everything *except* command substitution —
    `$(...)` and backticks still execute inside them — so those stay visible as code.
    """
    stack, out, i = list(stack or []), [], 0
    while i < len(s):
        c, top = s[i], stack[-1] if stack else None
        if top in ("'", '"'):  # data, unless a substitution opens
            if c == top: stack.pop(); out.append(c)
            elif top == '"' and c == "$" and s[i:i + 2] == "$(": stack.append("("); out.append("$("); i += 1
            elif top == '"' and c == "`": stack.append("`"); out.append(c)
            elif top == '"' and c == "\\" and i + 1 < len(s): out.append("  "); i += 1
            else: out.append(c if c == "\n" else " ")
        else:  # code: top level, or inside a substitution
            if c in "'\"": stack.append(c); out.append(c)
            elif s[i:i + 2] == "$(": stack.append("("); out.append("$("); i += 1
            elif c == "`": stack.pop() if top == "`" else stack.append(c); out.append(c)
            elif c == ")" and top == "(": stack.pop(); out.append(c)
            elif c == "\\" and i + 1 < len(s): out.append(" "); i += 1; out.append(s[i])
            else: out.append(c)
        i += 1
    return "".join(out), stack


def shell_code(cmd):
    """The executable part of a command: quoted text blanked, heredoc bodies dropped.

    Line by line, carrying quote state, because the two depend on each other: a `<<EOF` inside a
    quoted string is not a redirection, and a heredoc body may hold stray quotes that are not.
    """
    lines, out, i, stack = cmd.split("\n"), [], 0, []
    while i < len(lines):
        line = lines[i]; i += 1
        blanked, stack = blank_quoted(line, stack)
        out.append(blanked)
        for m in HEREDOC.finditer(blanked):
            d = HEREDOC.match(line, m.start())  # blanking keeps length: read the delimiter unblanked
            delim = (d.group(1) or d.group(2) or d.group(3)) if d else None
            while i < len(lines):  # absent terminator: strip to end of input
                term, i = lines[i], i + 1
                if term.strip() == delim:
                    break
    return "\n".join(out)


def is_launch(cmd):
    s = shell_code(cmd)
    for m in CMDPOS.finditer(s):
        seg = re.split(r"[;&|)`\n]", s[m.start(1):], 1)[0]
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
key = os.environ.get("ALTITUDE_SESSION_KEY")
counts_p = mon / f"counts-{key or sid}.json"
def fault(msg):  # decision 36: a hook cannot reach the server, so it leaves a line the tick raises as a system fault
    try:
        with open(mon / "hook-faults.log", "a") as f: f.write(f"subagent_cap.py session={sid} key={key or sid}: {msg}\n")
    except OSError: pass

lock_f = None
try:
    lock_f = open(counts_p.with_name(f"{counts_p.name}.lock"), "w")
    fcntl.flock(lock_f, fcntl.LOCK_EX)
except OSError as e:
    if lock_f:
        lock_f.close()
    lock_f = None
    fault(f"counts file lock unavailable, proceeding unlocked: {e}")

def blocked(n):
    print(f"altitude: envelope reached ({n-1}/{cap} subagent launches). Do not launch more agents: checkpoint progress.md, write the report with `Blocked: envelope (needed N)`, and stop.", file=sys.stderr)
    sys.exit(2)

try:
    legacy_p = mon / f"counts-{sid}.json"
    source_p = legacy_p if key and not counts_p.exists() and legacy_p.exists() else counts_p
    try: counts = json.loads(source_p.read_text()) if source_p.exists() else {}
    except Exception as e: counts = {}; fault(f"counts file unreadable, counter reset: {e}")
    cap = 8
    if key:
        try: cap = int(json.loads((mon / f"envelope-{key}.json").read_text()).get("subagent_launches", cap))
        except Exception as e: fault(f"envelope file for {key} unreadable, default cap {cap} used: {e}")
    seen = counts.get("seen") or []
    if dkey in seen:  # same tool call re-delivered: bill it once, but never weaken blocking (R-006)
        n = int(counts.get("subagent_launches", 0))
        if n > cap: blocked(n)
        sys.exit(0)
    n = int(counts.get("subagent_launches", 0)) + 1
    counts["subagent_launches"] = n; counts["cap"] = cap; counts["seen"] = (seen + [dkey])[-200:]
    tmp = counts_p.with_name(f"{counts_p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(counts)); os.replace(tmp, counts_p)
    if n > cap: blocked(n)
finally:
    if lock_f:
        try: fcntl.flock(lock_f, fcntl.LOCK_UN)
        except OSError as e: fault(f"counts file unlock failed: {e}")
        lock_f.close()
