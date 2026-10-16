"""A scripted fictional Codex CLI for the macOS VM lane's runtime phase; no provider, no network.

The guest's installed Altitude runs it as its only engine (`CODEX_BIN`). It answers the sign-in status, the
rate-limit read, the coordinator's `exec` turns and the workers' app-server turns. A turn's prompt steers it with
lines of its own, so the host's harness acts as L2 or L3 by writing them into a brief or a message:

    FIXTURE-RUN: <shell command>     run in the turn's folder; its exit and output go to FIXTURE_LOG
    FIXTURE-WAIT: <path>             after the commands, end the turn only once PATH exists (at most 120 s)
    FIXTURE-END: completed           the default: an answer, then a completed turn
    FIXTURE-END: failed              a failed turn, after which the server stays open until its input closes
    FIXTURE-END: failed-linger <s>   a failed turn, after which the server stays alive, ignoring its input, as an
                                     engine waiting for a background command does
    FIXTURE-END: hold <seconds>      no answer: the turn stays in progress, then the server exits
    FIXTURE-END: exit <code>         the server exits mid-turn

FIXTURE_LOG (JSON lines) receives each turn's role, task and session, then each command's result.
"""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

LOG = Path(os.environ.get("FIXTURE_LOG", "/private/tmp/altitude-fixture/log.jsonl"))  # writable in every sandbox


def out(message: dict) -> None:
    print(json.dumps(message), flush=True)


def record(entry: dict) -> None:
    with LOG.open("a") as log:
        log.write(json.dumps({"at": time.time(), "task": os.environ.get("ALTITUDE_TASK"), **entry}) + "\n")


def directives(prompt: str, name: str) -> list[str]:
    return [line.split(":", 1)[1].strip() for line in prompt.splitlines() if line.startswith(f"FIXTURE-{name}:")]


def turn(role: str, session: str, prompt: str) -> list[str]:
    """Runs the prompt's commands in order, waits for its paths, and returns its ending (default: completed)."""
    runs, waits = directives(prompt, "RUN"), directives(prompt, "WAIT")
    ends = directives(prompt, "END")
    end = ends[-1].split() if ends else ["completed"]
    record({"role": role, "session": session, "end": end, "commands": len(runs)})
    for command in runs:
        result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, timeout=300)
        record({"role": role, "session": session, "command": command, "exit": result.returncode,
                "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]})
    deadline = time.monotonic() + 120
    while not all(Path(path).exists() for path in waits) and time.monotonic() < deadline:
        time.sleep(0.5)
    return end


def coordinator() -> None:
    """`codex exec [resume SESSION] -`: one turn, streamed as exec events."""
    prompt = sys.stdin.read()
    session = sys.argv[-2] if "resume" in sys.argv else str(uuid.uuid4())
    out({"type": "thread.started", "thread_id": session})
    end = turn("l3", session, prompt)
    if end[0] == "failed":
        out({"type": "turn.failed", "error": {"message": "Fictional coordinator turn failed."}})
        raise SystemExit(1)
    out({"type": "item.completed", "item": {"type": "agent_message", "text": "Fictional coordinator turn done."}})
    out({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}})


def server() -> None:
    """`codex app-server`: the rate-limit read and one worker turn per process, as Altitude's driver runs them."""
    session = None

    def notify(method: str, params: dict) -> None:
        out({"method": method, "params": {"threadId": session, **params}})

    for raw in sys.stdin:
        message = json.loads(raw)
        method, identity, params = message.get("method"), message.get("id"), message.get("params") or {}
        if method == "initialize":
            out({"id": identity, "result": {"userAgent": "fixture"}})
        elif method == "account/rateLimits/read":
            resets = int(time.time()) + 3600
            out({"id": identity, "result": {"rateLimits": {
                "primary": {"usedPercent": 1, "resetsAt": resets, "windowDurationMins": 300},
                "secondary": {"usedPercent": 1, "resetsAt": resets + 86400, "windowDurationMins": 10080}}}})
        elif method in ("thread/start", "thread/resume"):
            session = params.get("threadId") or str(uuid.uuid4())
            out({"id": identity, "result": {"thread": {"id": session}, "model": "fixture-model"}})
        elif method == "turn/start":
            prompt = "".join(item.get("text", "") for item in params.get("input", []))
            out({"id": identity, "result": {"turn": {"id": "turn-1", "status": "inProgress"}}})
            notify("turn/started", {"turn": {"id": "turn-1"}})
            end = turn("l2" if os.environ.get("ALTITUDE_TASK") else "l3", session, prompt)
            if end[0] == "hold":
                time.sleep(int(end[1]))
                raise SystemExit(0)
            if end[0] == "exit":
                raise SystemExit(int(end[1]))
            failed = end[0].startswith("failed")
            if not failed:
                notify("item/completed", {"item": {"type": "agentMessage", "id": "message",
                                                   "text": "Fictional turn done."}})
            notify("thread/tokenUsage/updated", {"tokenUsage": {"total": {"inputTokens": 1, "outputTokens": 1}}})
            notify("turn/completed", {"turn": {"id": "turn-1", "status": "failed" if failed else "completed",
                                               **({"error": {"message": "Fictional turn failed."}} if failed else {})}})
            if failed:
                record({"role": "l2", "session": session, "failed": True})
            if end[0] == "failed-linger":
                time.sleep(int(end[1]))
                raise SystemExit(0)
        elif method == "turn/steer":
            out({"id": identity, "error": {"code": -32600, "message": "no active turn"}})
        elif identity is not None:
            out({"id": identity, "result": {}})


if __name__ == "__main__":
    if sys.argv[1:3] == ["login", "status"]:
        print("Logged in using a fictional account")
    elif sys.argv[1:2] == ["--version"]:
        print("codex-cli 0.0.0-fixture")
    elif sys.argv[1:2] == ["exec"]:
        coordinator()
    elif sys.argv[1:2] == ["app-server"]:
        server()
    else:
        print(f"fixture: unsupported arguments {sys.argv[1:]}", file=sys.stderr)
        raise SystemExit(2)
