#!/usr/bin/env python3
"""Deliver queued messages and keep an active owner with native background work in its session.

After a tool call the messages arrive as extra context; when the worker is about to stop they keep it going, with
the messages as the reason. Only a running task's inbox is delivered: a task the worker just blocked keeps its
messages for the resume that answers them. Native Stop evidence prevents premature clean completion
while registered background work is still in flight; explicit blocks retain their normal exit."""
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, engines, images, state as S, tasks as T  # noqa: E402

inp = json.load(sys.stdin) if not sys.stdin.isatty() else {}
project, slug = os.environ.get("ALTITUDE_PROJECT"), os.environ.get("ALTITUDE_TASK")
if not project or not slug:
    sys.exit(0)
try:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task.get("state") != "running":
            sys.exit(0)
        rows = T.pending(project, slug)
        attached = images.resolve(project, [image for row in rows for image in row.get("images") or []], task=slug)
    resolved = {image["id"]: image for image in attached}
    prepared = {row["id"]: T.render_inbox([row]) + engines.image_read_instructions(
        dispatch.l2_engine(task), [resolved[image["id"]] for image in row.get("images") or []]) for row in rows}
    taken = T.take_inbox(project, slug, ids=set(prepared), running_only=True)
    text = "\n\n".join(prepared[row["id"]] for row in taken)
    # #369 recurrence: a final promise of a watcher lets session cleanup kill required work.
    if inp.get("hook_event_name") == "Stop":
        pending = [row["id"] for row in inp.get("background_tasks", [])
                   if row.get("status") in ("running", "pending")]
        if pending:
            text += ("\n\nBackground work is still in flight: " + ", ".join(pending) + ". "
                     "Use native wait/result tools to consume required output and exit status before "
                     "ending this turn. Cancel only work no longer needed. If required results cannot "
                     "be obtained, checkpoint unfinished work and record an explicit supported task block.")
except Exception as exc:  # noqa: BLE001 — leave a line the server raises as a system fault
    config.MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.MONITOR_DIR / "hook-faults.log", "a") as f:
        f.write(f"inbox.py {project}/{slug}: {exc}\n")
    sys.exit(0)
if not text:
    sys.exit(0)
if inp.get("hook_event_name") == "Stop":
    print(json.dumps({"decision": "block", "reason": text}))
else:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}))
