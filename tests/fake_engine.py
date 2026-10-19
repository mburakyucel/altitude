"""A deterministic stand-in for both engines' streaming interfaces, run by the engine driver as its child.

`fake_engine.py` speaks Claude's stream-json print mode; `fake_engine.py app-server` speaks Codex's app-server
protocol. FAKE_ENGINE_LOG receives its arguments, GitHub token variable, Claude folder and sign-in, then every line it
reads. FAKE_ENGINE_SESSION, FAKE_ENGINE_MODEL and FAKE_ENGINE_TEXT replace the session or thread identity, model and reply. With FAKE_ENGINE_TOOL set,
the first turn starts a long command and writes "running" to that path; the command finishes only once a Send now
message reaches the turn (for Claude, its input line and the `background_tasks` request). With FAKE_ENGINE_ASK set,
the Codex server asks the client for an approval first and records the answer. With FAKE_ENGINE_REFUSE_STEER set, the
Codex server refuses the first steer of a running turn and ends that turn a second later. The Codex server accepts a
sign-in handed over by its client unless FAKE_ENGINE_REFUSE_SIGN_IN is set; with FAKE_ENGINE_EXPIRE set, its turn
asks the client for a fresh access token, as Codex does when the provider refuses one, and ends once answered.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

TOOL = os.environ.get("FAKE_ENGINE_TOOL")
SESSION, MODEL = os.environ.get("FAKE_ENGINE_SESSION"), os.environ.get("FAKE_ENGINE_MODEL", "fake-model")
TEXT = os.environ.get("FAKE_ENGINE_TEXT", "Done.")


def command():
    """A real child remains alive until its fake engine releases the tool after receiving the message."""
    child = subprocess.Popen([sys.executable, "-c",
                              "import pathlib, sys; sys.stdin.read(); pathlib.Path(sys.argv[1]).write_text('completed')",
                              TOOL + ".completed"], stdin=subprocess.PIPE)
    Path(TOOL + ".pid").write_text(str(child.pid))
    Path(TOOL).write_text("running")
    return child


def finish_command(child):
    child.stdin.close()
    if child.wait(timeout=10):
        raise RuntimeError("fake tool failed")


def out(event: dict) -> None:
    print(json.dumps(event), flush=True)


def read():
    if os.environ.get("FAKE_ENGINE_LOG"):
        with open(os.environ["FAKE_ENGINE_LOG"], "a") as log:
            log.write(json.dumps({"environment": {"GH_TOKEN": os.environ.get("GH_TOKEN")}, "argv": sys.argv[1:],
                                  "claude": {key: os.environ.get(key) for key in ("CLAUDE_CONFIG_DIR",
                                                                                  "CLAUDE_CODE_OAUTH_TOKEN")},
                                  "codex": os.environ.get("CODEX_HOME")}) + "\n")
    for raw in sys.stdin:
        message = json.loads(raw)
        if os.environ.get("FAKE_ENGINE_LOG"):
            with open(os.environ["FAKE_ENGINE_LOG"], "a") as log:
                log.write(json.dumps(message) + "\n")
        yield message


def say(text: str) -> None:
    out({"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": text}}})
    out({"type": "assistant", "message": {"model": MODEL, "content": [{"type": "text", "text": text}]}})


def claude() -> None:
    lines = read()
    first = next(lines)
    out({"type": "system", "subtype": "init", "session_id": SESSION or "fake-session", "model": MODEL})
    out({"type": "user", "isReplay": True, "uuid": first["uuid"], "message": first["message"]})
    if os.environ.get("FAKE_ENGINE_HELPERS"):
        for status in ("completed", "failed"):
            out({"type": "system", "subtype": "task_started", "task_id": f"helper-{status}",
                 "description": "Fictional helper review", "session_id": SESSION or "fake-session"})
            out({"type": "system", "subtype": "task_notification", "task_id": f"helper-{status}",
                 "status": status, "summary": "Helper review ended.", "session_id": SESSION or "fake-session"})
    if TOOL:
        out({"type": "assistant", "message": {"model": MODEL, "content": [
            {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {"command": "sleep 3600"}}]}})
        say("Waiting on the command. ")
        child = command()
        heard, backgrounded = None, False
        while heard is None or not backgrounded:
            message = next(lines)
            if message["type"] == "user":
                out({"type": "user", "isReplay": True, "uuid": message["uuid"], "message": message["message"]})
                heard = message["message"]["content"][0]["text"]
            elif message.get("request", {}).get("subtype") == "background_tasks":
                out({"type": "control_response", "response": {"subtype": "success",
                                                              "request_id": message["request_id"]}})
                backgrounded = True
        Path(TOOL).write_text("backgrounded")
        finish_command(child)
        say(f"Read: {heard}")
    else:
        say(TEXT)
    out({"type": "result", "subtype": "success", "is_error": False, "result": TEXT, "session_id": SESSION or "fake-session",
         "usage": {"input_tokens": 5, "output_tokens": 2}})
    for message in lines:  # input accepted after a result is the next turn
        if message["type"] == "user":
            out({"type": "user", "isReplay": True, "uuid": message["uuid"], "message": message["message"]})
            say(f"Next turn: {message['message']['content'][0]['text']}")
            out({"type": "result", "subtype": "success", "is_error": False, "result": "Next.",
                 "session_id": SESSION or "fake-session"})


def codex() -> None:
    active = None
    thread = SESSION or "fake-thread"

    def reply(identity, result=None, error=None):
        out({"id": identity, **({"error": error} if error else {"result": result or {}})})

    def notify(method, params):
        out({"method": method, "params": {"threadId": thread, **params}})

    def helpers():
        # A helper finishes while the owner is still awaiting its results (#873).
        statuses = (("completed",) if os.environ["FAKE_ENGINE_HELPERS"] == "completed"
                    else ("failed", "interrupted", "completed"))
        for status in statuses:
            identity = {"threadId": f"helper-{status}", "turnId": f"helper-turn-{status}"}
            notify("turn/started", {**identity, "turn": {"id": identity["turnId"], "status": "inProgress"}})
            for method in ("item/started", "item/completed"):
                notify(method, {**identity, "item": {"type": "agentMessage", "id": "helper-answer",
                                                     "text": "Helper review finished."}})
            notify("thread/tokenUsage/updated", {**identity, "tokenUsage": {"total": {"inputTokens": 999}}})
            if status == "failed":
                notify("error", {**identity, "willRetry": False, "error": {"message": "Helper failed."}})
            notify("turn/completed", {**identity, "turn": {"id": identity["turnId"], "status": status}})

    def complete(text):
        status = os.environ.get("FAKE_ENGINE_END", "completed")
        if status == "eof":
            raise SystemExit(0)
        notify("item/completed", {"item": {"type": "agentMessage", "id": "message", "text": text}})
        notify("thread/tokenUsage/updated", {"tokenUsage": {"total": {"inputTokens": 5, "outputTokens": 2}}})
        notify("turn/completed", {"turn": {"id": "turn-1", "status": status}})

    for message in read():
        method, identity, params = message.get("method"), message.get("id"), message.get("params") or {}
        if method == "initialize":
            reply(identity, {"userAgent": "fake"})
        elif method == "account/login/start":
            if os.environ.get("FAKE_ENGINE_REFUSE_SIGN_IN"):
                reply(identity, error={"code": -32600, "message": "external auth is not supported"})
            else:
                reply(identity, {"type": "chatgptAuthTokens"})
        elif identity == "refresh-1" and not method:  # the client's fresh token: the retried request succeeds
            complete(TEXT)
        elif method in ("thread/start", "thread/resume"):
            thread = params.get("threadId") or thread
            reply(identity, {"thread": {"id": thread}, "model": MODEL})
        elif method == "turn/start":
            if os.environ.get("FAKE_ENGINE_ASK"):
                out({"id": "ask-1", "method": "item/commandExecution/requestApproval", "params": {"command": "ls"}})
            reply(identity, {"turn": {"id": "turn-1", "status": "inProgress"}})
            notify("turn/started", {"turn": {"id": "turn-1"}})
            if os.environ.get("FAKE_ENGINE_HELPERS"):
                helpers()
            if os.environ.get("FAKE_ENGINE_EXPIRE"):
                out({"id": "refresh-1", "method": "account/chatgptAuthTokens/refresh",
                     "params": {"reason": "unauthorized", "previousAccountId": "fixture-account"}})
            elif TOOL:
                active = "turn-1"
                notify("item/started", {"item": {"type": "commandExecution", "id": "tool-1", "command": "sleep 3600",
                                                 "status": "inProgress"}})
                child = command()
            else:
                complete(TEXT)
        elif method == "turn/steer":
            if active != params.get("expectedTurnId"):
                reply(identity, error={"code": -32600, "message": "no active turn to steer"})
                continue
            if os.environ.get("FAKE_ENGINE_REFUSE_STEER"):
                reply(identity, error={"code": -32600, "message": "steer refused"})
                time.sleep(1)
                finish_command(child)
                active = None
                complete(TEXT)
                continue
            if not os.environ.get("FAKE_ENGINE_DROP_STEER_ACK"):
                reply(identity, {"turnId": active})
            finish_command(child)
            notify("item/completed", {"item": {"type": "commandExecution", "id": "tool-1", "command": "sleep 3600",
                                               "status": "completed", "aggregatedOutput": "", "exitCode": 0}})
            active = None
            complete(f"Read: {params['input'][0]['text']}")


if __name__ == "__main__":
    codex() if sys.argv[1:2] == ["app-server"] else claude()
