#!/usr/bin/env python3
"""Passive hook (I-064, decision 36): a residual permission prompt becomes a counted system fault, never a silent park.

Two events, both wired by `dispatch.session_settings()`:
  PreToolUse(Bash)                 capture only — remember the last Bash command and its tool_use_id for this session key
                                   (monitor/last-bash-<key>.json); nothing is counted here.
  Notification(permission_prompt)  the prompt nobody can answer (Claude Code 2.1.251 emits it 6 s after the prompt opens,
                                   `notification_type: permission_prompt`, message "Claude needs your permission to use
                                   <Tool>"): bill one `permission_denials` in the same counts file the other hooks use,
                                   deduplicated per tool call, and leave one structured line in monitor/hook-faults.log
                                   for `server.drain_hook_faults()`.

It never grants, denies or retries: it prints no permission decision, and it exits 0 in every case — a broken hook
must not become a second way to park the session. `subagent_launches` and `cap` are never touched.
"""
import fcntl, hashlib, json, os, re, sys
from pathlib import Path

PROMPT_KINDS = ("permission_prompt", "worker_permission_prompt")   # Notification.notification_type, 2.1.251
COMMAND_CAP, MESSAGE_CAP = 300, 200                                # the fault line stays one bounded line
TOOL_IN_MESSAGE = re.compile(r"permission to use (\S+)")


def main() -> None:
    inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    if not isinstance(inp, dict):
        return
    sid = str(inp.get("session_id") or "unknown")
    root = Path(os.environ.get("ALTITUDE_HOME", Path.home() / ".altitude"))
    mon = root / "monitor"; mon.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("ALTITUDE_SESSION_KEY")
    capture_p = mon / f"last-bash-{key or sid}.json"

    if inp.get("notification_type") is None and inp.get("hook_event_name") != "Notification":
        if inp.get("tool_name") != "Bash":
            return
        cmd = str((inp.get("tool_input") or {}).get("command") or "")
        rec = {"tool_use_id": inp.get("tool_use_id") or hashlib.sha1(cmd.encode()).hexdigest()[:16],
               "command": cmd[:COMMAND_CAP], "session": sid}
        tmp = capture_p.with_name(f"{capture_p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rec)); os.replace(tmp, capture_p)
        return                                                       # capture only: no count, no decision

    kind = inp.get("notification_type")
    if kind not in PROMPT_KINDS:
        return                                                       # every other notification is somebody else's
    message = str(inp.get("message") or "")[:MESSAGE_CAP]
    m = TOOL_IN_MESSAGE.search(message)
    tool = m.group(1) if m else None
    try:
        cap = json.loads(capture_p.read_text()) if capture_p.exists() else {}
    except Exception:
        cap = {}
    command = cap.get("command") if tool in (None, "Bash") and cap else None
    # one bill per tool call: the capture's tool_use_id ties the PreToolUse record and this notification together;
    # a prompt for another tool (or without a capture) is keyed on the session and the message instead
    dkey = (cap.get("tool_use_id") if command is not None and cap.get("tool_use_id") else None) \
        or hashlib.sha1((sid + "\0" + str(tool) + "\0" + message).encode()).hexdigest()[:16]
    counts_p = mon / f"counts-{key or sid}.json"

    def fault(msg):  # decision 36: a hook cannot reach the server, so it leaves a line the tick raises as a system fault
        try:
            with open(mon / "hook-faults.log", "a") as f: f.write(f"permission_prompt_fault.py session={sid} key={key or sid}: {msg}\n")
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

    billed = False
    try:
        legacy_p = mon / f"counts-{sid}.json"
        source_p = legacy_p if key and not counts_p.exists() and legacy_p.exists() else counts_p
        try: counts = json.loads(source_p.read_text()) if source_p.exists() else {}
        except Exception as e: counts = {}; fault(f"counts file unreadable, counter reset: {e}")
        seen = counts.get("prompts_seen") or []
        if dkey not in seen:                                         # the same prompt re-delivered is billed once
            counts["permission_denials"] = int(counts.get("permission_denials", 0)) + 1
            counts["prompts_seen"] = (seen + [dkey])[-200:]
            tmp = counts_p.with_name(f"{counts_p.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(counts)); os.replace(tmp, counts_p)
            billed = True
    finally:
        if lock_f:
            try: fcntl.flock(lock_f, fcntl.LOCK_UN)
            except OSError as e: fault(f"counts file unlock failed: {e}")
            lock_f.close()

    if not billed:
        return
    rec = {"project": os.environ.get("ALTITUDE_PROJECT"), "task": os.environ.get("ALTITUDE_TASK"),
           "actor": os.environ.get("ALTITUDE_ACTOR"), "session": sid, "key": key or sid, "notification_type": kind,
           "tool": tool, "command": command, "message": message, "tool_use_id": cap.get("tool_use_id") if command is not None else None}
    try:
        with open(mon / "hook-faults.log", "a") as f:
            f.write("permission_prompt_fault.py " + json.dumps(rec, sort_keys=True).replace("\n", " ") + "\n")
    except OSError:
        pass


try:
    main()
except Exception:   # noqa: BLE001 — passive by contract: a broken hook must never park or fail the session
    pass
sys.exit(0)
