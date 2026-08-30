#!/usr/bin/env python3
"""PreToolUse hook on Agent|Task|Bash: count subagent launches per session; block past the envelope cap (decision 31).

Inside a dispatched task (decision 45) the count is not this hook's to invent: `hooks/launch_counter.py` reconciles it
from the `l1-started` events `alt l1 run` writes once an engine process has actually started, so a refused or failed
launch is never billed. The only launch form that survives there is `alt l1 run` — this hook cannot prove that a raw
`claude`/`codex`/`Agent` launch ever started, so it refuses those rather than phantom-bill them (R-006: an unprovable
gate is never treated as passed). Outside a task the per-tool-call billing below is unchanged."""
import fcntl, hashlib, json, os, re, shlex, sys
from pathlib import Path

from launch_counter import counts_path, read_counts, reservations, settle_counts, started_count, task_dirs

# I-005: a Bash launch is billed only when it stands in *command position* — heredoc bodies and quoted
# arguments are data, not launches — and every tool call is billed at most once via a dedupe key.
DIRECT = r"(?:(?:\S*/)?codex\s+exec\b|(?:\S*/)?claude\s+(?:-p|--print|--bg)\b)"  # hand-started engine
ALT_RUN = r"(?:\S*/)?alt\s+l1\s+run\b"                          # `alt l1 run` is a launch too (decision 45)
LAUNCH = r"(?:" + DIRECT + r"|" + ALT_RUN + r")"
PREFIX = r"(?:(?:[A-Za-z_]\w*=(?:\"[^\"]*\"|'[^']*'|\S*)|env|nohup|time|exec|sudo|command)\s+)*"
CMDPOS = re.compile(r"(?:^|[;&|(){}`\n])[ \t]*" + PREFIX + r"(" + LAUNCH + r")")
SHELL_C = re.compile(r"(?:^|[;&|(){}`\n])[ \t]*" + PREFIX +
                     r"(?:\S*/)?(?:sh|bash|dash|zsh)\s+-[A-Za-z]*c[A-Za-z]*\s+"
                     r"(?P<body>'[^']*'|\"(?:\\.|[^\"])*\"|\S+)")
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


def without_heredoc_bodies(cmd):
    """Keep executable spelling and quotes, but remove heredoc data before looking for shell wrappers."""
    lines, out, i, stack = cmd.split("\n"), [], 0, []
    while i < len(lines):
        line = lines[i]; i += 1
        blanked, stack = blank_quoted(line, stack)
        out.append(line)
        for m in HEREDOC.finditer(blanked):
            d = HEREDOC.match(line, m.start())
            delim = (d.group(1) or d.group(2) or d.group(3)) if d else None
            while i < len(lines):
                term, i = lines[i], i + 1
                if term.strip() == delim:
                    break
    return "\n".join(out)


def launch_forms(cmd, depth=0):
    """Every launch standing in command position, as "alt" (goes through `alt l1 run`) or "direct" (a raw engine)."""
    s, out = shell_code(cmd), []
    for m in CMDPOS.finditer(s):
        seg = re.split(r"[;&|)`\n]", s[m.start(1):], 1)[0]
        if PROBE.search(seg):
            continue  # capability probe, not a launch (I-004: an L2 lost a launch to a `--help` probe)
        out.append("alt" if re.match(ALT_RUN, m.group(1)) else "direct")
    if depth < 3:
        for m in SHELL_C.finditer(without_heredoc_bodies(cmd)):
            try:
                body = shlex.split(m.group("body"))
            except ValueError:
                continue
            if len(body) == 1:
                out.extend(launch_forms(body[0], depth + 1))
    return out


def is_launch(cmd):
    return bool(launch_forms(cmd))


def launch_kind(tool, cmd):
    """None for anything that is not a launch; otherwise "direct" if any form bypasses `alt l1 run`.

    The hook's matcher is Agent|Task|Bash, so a non-Bash call here is always a subagent launch."""
    if tool != "Bash":
        return "direct"
    forms = launch_forms(cmd)
    if not forms:
        return None
    return "direct" if "direct" in forms else "alt"


inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
sid = inp.get("session_id") or "unknown"
tool = inp.get("tool_name")
tool_input = inp.get("tool_input") or {}
kind = launch_kind(tool, str(tool_input.get("command") or ""))
if kind is None:
    sys.exit(0)  # ordinary shell command: not a launch — no lock, no event log, no counts file rewritten
dkey = inp.get("tool_use_id") or hashlib.sha1(
    (str(tool) + json.dumps(tool_input, sort_keys=True, default=str)).encode()).hexdigest()[:16]
root = Path(os.environ.get("ALTITUDE_HOME", Path.home() / ".altitude"))
mon = root / "monitor"; mon.mkdir(parents=True, exist_ok=True)
key = os.environ.get("ALTITUDE_SESSION_KEY")
project, task = os.environ.get("ALTITUDE_PROJECT"), os.environ.get("ALTITUDE_TASK")
counts_p = mon / f"counts-{key or sid}.json"
def fault(msg):  # decision 36: a hook cannot reach the server, so it leaves a line the tick raises as a system fault
    try:
        with open(mon / "hook-faults.log", "a") as f: f.write(f"subagent_cap.py session={sid} key={key or sid}: {msg}\n")
    except OSError: pass

# A managed session is one whose launches leave an audit trail: a dispatch key and a task folder to read it from.
managed = bool(key and task_dirs(root, project, task))
if managed and kind == "direct":
    print("altitude: `Agent`/`Task` and raw `claude`/`codex` launches are refused inside a dispatched task — the "
          "launch envelope is charged from the `l1-started` events only `alt l1 run` records, and this hook cannot "
          "tell whether a hand-rolled launch ever started. Start L1s with `alt l1 run --brief <sub-brief>` and "
          "collect them with `alt l1 wait`.", file=sys.stderr)
    sys.exit(2)

lock_f = None
try:
    lock_f = open(counts_p.with_name(f"{counts_p.name}.lock"), "w")
    fcntl.flock(lock_f, fcntl.LOCK_EX)
except OSError as e:
    if lock_f:
        lock_f.close()
    lock_f = None
    fault(f"counts file lock unavailable, proceeding unlocked: {e}")

def blocked(billed):
    print(f"altitude: envelope reached ({billed}/{cap} subagent launches). Do not launch more agents: checkpoint progress.md, write the report with `Blocked: envelope (needed N)`, and stop.", file=sys.stderr)
    sys.exit(2)

try:
    legacy_p = mon / f"counts-{sid}.json"
    source_p = legacy_p if key and not counts_p.exists() and legacy_p.exists() else counts_p
    counts = read_counts(root, source_p)  # missing, corrupt, or not a JSON object: rebuilt, never indexed into
    cap = 8
    if key:
        try: cap = int(json.loads((mon / f"envelope-{key}.json").read_text()).get("subagent_launches", cap))
        except Exception as e: fault(f"envelope file for {key} unreadable, default cap {cap} used: {e}")
    if managed:  # `alt l1 run`: the launcher bills it, this hook only enforces the cap against the authoritative count
        started = started_count(root, project, task)
        if started is None:  # decision 36: "cannot tell" is not "none yet" — refuse rather than fail open
            fault(f"launch count for {project}/{task} unknown; refusing the launch instead of reading it as zero")
            print(f"altitude: the launch count for {project}/{task} cannot be read, so the envelope cannot be "
                  f"checked. Stop and report `Blocked: envelope` — do not launch.", file=sys.stderr)
            sys.exit(2)
        reserved = reservations(root, project, task)
        if reserved is None:
            fault(f"launch reservations for {project}/{task} unknown; refusing instead of assuming no slot is held")
            print(f"altitude: the launch reservations for {project}/{task} cannot be read, so the envelope cannot "
                  f"be checked. Stop and report `Blocked: envelope` — do not launch.", file=sys.stderr)
            sys.exit(2)
        held = len(reserved)  # slots taken by launches still on their way to an engine
        settled = settle_counts(root, counts_path(root, key), project, task, cap, locked=lock_f is not None)
        if settled is None:
            fault(f"launch count for {project}/{task} could not be settled; refusing the launch")
            print(f"altitude: the launch count for {project}/{task} cannot be settled safely. Stop and report "
                  f"`Blocked: envelope` — do not launch.", file=sys.stderr)
            sys.exit(2)
        if started + held >= cap: blocked(started + held)
        sys.exit(0)
    seen = counts.get("seen") or []
    if dkey in seen:  # same tool call re-delivered: bill it once, but never weaken blocking (R-006)
        n = int(counts.get("subagent_launches", 0))
        if n > cap: blocked(n - 1)
        sys.exit(0)
    n = int(counts.get("subagent_launches", 0)) + 1
    counts["subagent_launches"] = n; counts["cap"] = cap; counts["seen"] = (seen + [dkey])[-200:]
    tmp = counts_p.with_name(f"{counts_p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(counts)); os.replace(tmp, counts_p)
    if n > cap: blocked(n - 1)
finally:
    if lock_f:
        try: fcntl.flock(lock_f, fcntl.LOCK_UN)
        except OSError as e: fault(f"counts file unlock failed: {e}")
        lock_f.close()
