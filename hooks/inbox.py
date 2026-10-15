#!/usr/bin/env python3
"""Deliver queued messages and keep an active owner with native background work in its session.

altd decides what the checkpoint delivers and takes it from the inbox (`tasks.owner_checkpoint`), through the task
door: after a tool call the messages arrive as extra context; when the worker is about to stop they keep it going,
with the messages as the reason. Native Stop evidence prevents premature clean completion while registered
background work is still in flight; explicit blocks retain their normal exit. While altd cannot be reached the hook
lets the worker go on and says so on its own output."""
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import door  # noqa: E402

door.follow_activation(__file__)

#: While altd cannot be reached a checkpoint holds the worker up only briefly: its messages wait in the inbox for the
#: next one. Once altd has the request it may take the messages from the inbox, so its answer is awaited for nearly
#: the hook's own bound (`dispatch.HOOK_TIMEOUT`), and a repeat after a dropped connection gets the same answer.
WAIT, TIMEOUT = 5, 25

inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
# A helper subagent's tool call fires this hook too; its messages belong to the owner (agent_id marks a subagent).
if not os.environ.get("ALTITUDE_TASK") or inp.get("agent_id"):
    sys.exit(0)
stop = inp.get("hook_event_name") == "Stop"
background = [str(row["id"]) for row in inp.get("background_tasks", []) if row.get("status") in ("running", "pending")]
try:
    text = door.call("inbox", {"stop": stop, "background": background if stop else []}, wait=WAIT,
                     timeout=TIMEOUT)["text"]
except door.DoorError as exc:
    print(f"Altitude could not deliver this checkpoint's messages; they wait for the next one: {exc}", file=sys.stderr)
    sys.exit(0)
if not text:
    sys.exit(0)
if stop:
    print(json.dumps({"decision": "block", "reason": text}))
else:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}))
