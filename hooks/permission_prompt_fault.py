#!/usr/bin/env python3
"""Passive hook (I-064, decision 36): a residual permission prompt becomes a counted system fault, never a silent park.

Three events, wired into every Claude launch by `permissions.prompt_fault_hooks()` — both `engines.claude_settings`
(L3, L1s, proposals, critic, sizer) and `dispatch.session_settings` (L2s, fresh and resumed):
  PreToolUse(Bash)                 capture only — add the call (tool_use_id, command) to this session key's pending
                                   captures, monitor/pending-bash-<key>.json: newest last, at most PENDING_CAP kept,
                                   the oldest dropped first. Nothing is counted here.
  PostToolUse(Bash)                release — the call ran, so it was not parked: its capture leaves the pending set.
  Notification(permission_prompt)  the prompt nobody can answer (Claude Code 2.1.251 emits it once, 6 s after the prompt
                                   opens: `notification_type: permission_prompt`, message "Claude needs your permission
                                   to use <Tool>"): bill one `permission_denials` in the same counts file the other
                                   hooks use and leave one structured line in monitor/hook-faults.log for
                                   `server.drain_hook_faults()`. A Bash prompt consumes the newest unconsumed capture,
                                   each capture at most once, so two calls parked side by side are two faults with two
                                   commands — never one borrowed tool_use_id and a second prompt dropped as a duplicate.
                                   Re-delivery (C3, the second review of PR #89): a notification whose exact payload
                                   (session, notification_type, tool, message) was already billed may not consume a
                                   capture opened *after* that billing — with only newer captures open it is the same
                                   prompt delivered again, so nothing is consumed and nothing is counted; it only
                                   advances the payload's watermark (`notif_seen` in the counts file), and the next
                                   identical notification — a genuinely new prompt for one of those calls — bills
                                   normally. A duplicate therefore never turns an open capture for a command that then
                                   runs fine into a fault. With nothing open at all the notification is keyed to the
                                   last consumed capture and billed once; a prompt for another tool, or with no capture
                                   at all, is keyed on the session and the message.

Sessions without ALTITUDE_SESSION_KEY (everything but an L2) key their files on the Claude session id, and the fault
line carries whatever of ALTITUDE_PROJECT / ALTITUDE_TASK / ALTITUDE_ACTOR the launcher set — possibly none of them;
the drain copes with the absent fields.

It never grants, denies or retries: it prints no permission decision, and it exits 0 in every case — a broken hook
must not become a second way to park the session. `subagent_launches` and `cap` are never touched; the counts file
keeps its fcntl lock discipline, and the pending file takes the same kind of lock. Every append to
monitor/hook-faults.log holds monitor/hook-faults.log.lock (`_append_log`) — the advisory lock
`server.drain_hook_faults()` takes around its rotate — so a line this hook writes cannot land in an inode the drain
has already read and is about to unlink (C4). Lock order, fixed: pending file → counts file → hook-faults.log.
"""
import fcntl, hashlib, json, os, re, sys
from pathlib import Path

PROMPT_KINDS = ("permission_prompt", "worker_permission_prompt")   # Notification.notification_type, 2.1.251
COMMAND_CAP, MESSAGE_CAP = 300, 200                                # the fault line stays one bounded line
PENDING_CAP = 32                                                   # captures kept per session key, oldest dropped first
TOOL_IN_MESSAGE = re.compile(r"permission to use (\S+)")


def _lock(path, fault):
    """The exclusive flock the counts file already uses, on <path>.lock; None (and a fault line) when it cannot be taken."""
    f = None
    try:
        f = open(path.with_name(f"{path.name}.lock"), "w")
        fcntl.flock(f, fcntl.LOCK_EX)
        return f
    except OSError as e:
        if f:
            f.close()
        fault(f"{path.name} lock unavailable, proceeding unlocked: {e}")
        return None


def _unlock(f, fault):
    if f is None:
        return
    try: fcntl.flock(f, fcntl.LOCK_UN)
    except OSError as e: fault(f"unlock failed: {e}")
    f.close()


def _write(path, obj):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj)); os.replace(tmp, path)


def _append_log(mon, text):
    """One line into monitor/hook-faults.log, held under monitor/hook-faults.log.lock — the same advisory lock
    `server.drain_hook_faults()` takes around its rotate (C4): an append that opened the log before the rotate finishes
    before the rename, so the drain reads it instead of losing it in the renamed inode. In the fixed lock order
    (pending file → counts file → this log lock) this lock is always last: taken innermost by `fault()` or with the
    other two already released (the billed record). If the lock cannot be taken the line is appended unlocked — the
    exit-0 contract outranks the residual race."""
    log_p = mon / "hook-faults.log"
    lock_f = None
    try:
        lock_f = open(log_p.with_name(f"{log_p.name}.lock"), "w")
        fcntl.flock(lock_f, fcntl.LOCK_EX)
    except OSError:
        if lock_f:
            lock_f.close()
        lock_f = None
    try:
        with open(log_p, "a") as f:
            f.write(text)
    except OSError:
        pass
    finally:
        if lock_f:
            try: fcntl.flock(lock_f, fcntl.LOCK_UN)
            except OSError: pass
            lock_f.close()


def _pending(path, fault):
    """The pending captures on disk: (seq, entries) — entries newest last, never more than PENDING_CAP."""
    try: pend = json.loads(path.read_text()) if path.exists() else {}
    except Exception as e: pend = {}; fault(f"pending captures unreadable, reset: {e}")
    if not isinstance(pend, dict):
        pend = {}
    entries = [e for e in (pend.get("entries") or []) if isinstance(e, dict) and e.get("tool_use_id")]
    return int(pend.get("seq") or 0), entries[-PENDING_CAP:]


def main() -> None:
    inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    if not isinstance(inp, dict):
        return
    sid = str(inp.get("session_id") or "unknown")
    root = Path(os.environ.get("ALTITUDE_HOME", Path.home() / ".altitude"))
    mon = root / "monitor"; mon.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("ALTITUDE_SESSION_KEY")
    pending_p = mon / f"pending-bash-{key or sid}.json"
    counts_p = mon / f"counts-{key or sid}.json"

    def fault(msg):  # decision 36: a hook cannot reach the server, so it leaves a line the tick raises as a system fault
        _append_log(mon, f"permission_prompt_fault.py session={sid} key={key or sid}: {msg}\n")

    event = inp.get("hook_event_name")
    if inp.get("notification_type") is None and event != "Notification":
        if inp.get("tool_name") != "Bash":
            return
        cmd = str((inp.get("tool_input") or {}).get("command") or "")
        tid = str(inp.get("tool_use_id") or hashlib.sha1(cmd.encode()).hexdigest()[:16])
        if event == "PostToolUse":                                   # release: the call ran, so it was never parked
            if not pending_p.exists():
                return
            lock_f = _lock(pending_p, fault)
            try:
                seq, entries = _pending(pending_p, fault)
                _write(pending_p, {"seq": seq, "entries": [e for e in entries if e.get("tool_use_id") != tid]})
            finally:
                _unlock(lock_f, fault)
            return
        lock_f = _lock(pending_p, fault)                             # capture: pending until released or consumed
        try:
            seq, entries = _pending(pending_p, fault)
            seq += 1
            entries = [e for e in entries if e.get("tool_use_id") != tid]
            entries.append({"tool_use_id": tid, "command": cmd[:COMMAND_CAP], "session": sid, "seq": seq, "consumed": False})
            _write(pending_p, {"seq": seq, "entries": entries[-PENDING_CAP:]})
        finally:
            _unlock(lock_f, fault)
        return                                                       # capture only: no count, no decision

    kind = inp.get("notification_type")
    if kind not in PROMPT_KINDS:
        return                                                       # every other notification is somebody else's
    message = str(inp.get("message") or "")[:MESSAGE_CAP]
    m = TOOL_IN_MESSAGE.search(message)
    tool = m.group(1) if m else None
    # the payload key (C3): one prompt as Claude delivers it, identical however many times it is re-delivered
    nkey = hashlib.sha1((sid + "\0" + str(kind) + "\0" + str(tool) + "\0" + message).encode()).hexdigest()[:16]

    # The consume-or-absorb decision needs both files, so their locks nest here — in the fixed order (docstring):
    # pending file first, counts file second, hook-faults.log last and never while wanting either of the other two.
    use_pending = tool in (None, "Bash") and pending_p.exists()
    pend_f = _lock(pending_p, fault) if use_pending else None
    cap, seq, billed = None, None, False
    try:
        counts_f = _lock(counts_p, fault)
        try:
            legacy_p = mon / f"counts-{sid}.json"
            source_p = legacy_p if key and not counts_p.exists() and legacy_p.exists() else counts_p
            try: counts = json.loads(source_p.read_text()) if source_p.exists() else {}
            except Exception as e: counts = {}; fault(f"counts file unreadable, counter reset: {e}")
            if not isinstance(counts, dict):
                counts = {}
            notif_seen = counts.get("notif_seen") if isinstance(counts.get("notif_seen"), dict) else {}
            try: watermark = int(notif_seen[nkey])                   # the pending seq when this payload last billed
            except (KeyError, TypeError, ValueError): watermark = None

            def mark(at):                                            # notif_seen[nkey] = at, newest last, bounded
                notif_seen.pop(nkey, None); notif_seen[nkey] = at
                counts["notif_seen"] = dict(list(notif_seen.items())[-200:])

            if use_pending:
                seq, entries = _pending(pending_p, fault)
                open_ = [e for e in entries if not e.get("consumed")]
                billable = open_ if watermark is None else [e for e in open_ if int(e.get("seq") or 0) <= watermark]
                if billable:                                         # the newest parked call this payload may bill,
                    cap = billable[-1]; cap["consumed"] = True       # consumed exactly once
                    _write(pending_p, {"seq": seq, "entries": entries})
                elif open_:                                          # every open capture postdates this payload's last
                    mark(seq)                                        # billing: the same prompt delivered again (C3).
                    _write(counts_p, counts)                         # Consume and count nothing; advance the watermark
                    return                                           # so the next delivery — a new prompt — bills them.
                else:                                                # nothing left: the last prompt delivered again
                    done = [e for e in entries if e.get("consumed")]
                    cap = done[-1] if done else None
            command = cap.get("command") if cap else None
            # one bill per tool call: the capture's tool_use_id ties the PreToolUse record and this notification
            # together; a prompt for another tool (or without a capture) is keyed on the session and the message
            dkey = (cap.get("tool_use_id") if cap else None) \
                or hashlib.sha1((sid + "\0" + str(tool) + "\0" + message).encode()).hexdigest()[:16]
            seen = counts.get("prompts_seen") or []
            if dkey not in seen:                                     # the same prompt re-delivered is billed once
                counts["permission_denials"] = int(counts.get("permission_denials", 0)) + 1
                counts["prompts_seen"] = (seen + [dkey])[-200:]
                if seq is not None:
                    mark(seq)
                _write(counts_p, counts)
                billed = True
        finally:
            _unlock(counts_f, fault)
    finally:
        _unlock(pend_f, fault)

    if not billed:
        return
    rec = {"project": os.environ.get("ALTITUDE_PROJECT"), "task": os.environ.get("ALTITUDE_TASK"),
           "actor": os.environ.get("ALTITUDE_ACTOR"), "session": sid, "key": key or sid, "notification_type": kind,
           "tool": tool, "command": command, "message": message, "tool_use_id": cap.get("tool_use_id") if cap else None}
    _append_log(mon, "permission_prompt_fault.py " + json.dumps(rec, sort_keys=True).replace("\n", " ") + "\n")


try:
    main()
except Exception:   # noqa: BLE001 — passive by contract: a broken hook must never park or fail the session
    pass
sys.exit(0)
