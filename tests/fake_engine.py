"""A deterministic stand-in for both engines' streaming interfaces, run by the engine driver as its child.

`fake_engine.py` speaks Claude's stream-json print mode; `fake_engine.py app-server` speaks Codex's app-server
protocol. FAKE_ENGINE_LOG receives its arguments and GitHub token variable, then every line it reads. FAKE_ENGINE_SESSION,
FAKE_ENGINE_MODEL and FAKE_ENGINE_TEXT replace the session or thread identity, model and reply. With FAKE_ENGINE_TOOL set,
the first turn starts a long command and writes "running" to that path; the command finishes only once a Send now
message reaches the turn (for Claude, its input line and the `background_tasks` request). With FAKE_ENGINE_ASK set,
the Codex server asks the client for an approval first and records the answer.
"""
import json
import os
import sys
from pathlib import Path

TOOL = os.environ.get("FAKE_ENGINE_TOOL")
SESSION, MODEL = os.environ.get("FAKE_ENGINE_SESSION"), os.environ.get("FAKE_ENGINE_MODEL", "fake-model")
TEXT = os.environ.get("FAKE_ENGINE_TEXT", "Done.")


def out(event: dict) -> None:
    print(json.dumps(event), flush=True)


def read():
    if os.environ.get("FAKE_ENGINE_LOG"):
        with open(os.environ["FAKE_ENGINE_LOG"], "a") as log:
            log.write(json.dumps({"environment": {"GH_TOKEN": os.environ.get("GH_TOKEN")}, "argv": sys.argv[1:]}) + "\n")
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
    if TOOL:
        out({"type": "assistant", "message": {"model": MODEL, "content": [
            {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {"command": "sleep 3600"}}]}})
        say("Waiting on the command. ")
        Path(TOOL).write_text("running")
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

    def reply(identity, result=None, error=None):
        out({"id": identity, **({"error": error} if error else {"result": result or {}})})

    def notify(method, params):
        out({"method": method, "params": params})

    def complete(text):
        notify("item/completed", {"item": {"type": "agentMessage", "id": "message", "text": text}})
        notify("thread/tokenUsage/updated", {"tokenUsage": {"total": {"inputTokens": 5, "outputTokens": 2}}})
        notify("turn/completed", {"turn": {"id": "turn-1", "status": "completed"}})

    for message in read():
        method, identity, params = message.get("method"), message.get("id"), message.get("params") or {}
        if method == "initialize":
            reply(identity, {"userAgent": "fake"})
        elif method in ("thread/start", "thread/resume"):
            reply(identity, {"thread": {"id": params.get("threadId") or SESSION or "fake-thread"}, "model": MODEL})
        elif method == "turn/start":
            if os.environ.get("FAKE_ENGINE_ASK"):
                out({"id": "ask-1", "method": "item/commandExecution/requestApproval", "params": {"command": "ls"}})
            reply(identity, {"turn": {"id": "turn-1", "status": "inProgress"}})
            notify("turn/started", {"turn": {"id": "turn-1"}})
            if TOOL:
                active = "turn-1"
                notify("item/started", {"item": {"type": "commandExecution", "id": "tool-1", "command": "sleep 3600",
                                                 "status": "inProgress"}})
                Path(TOOL).write_text("running")
            else:
                complete(TEXT)
        elif method == "turn/steer":
            if active != params.get("expectedTurnId"):
                reply(identity, error={"code": -32600, "message": "no active turn to steer"})
                continue
            reply(identity, {"turnId": active})
            notify("item/completed", {"item": {"type": "commandExecution", "id": "tool-1", "command": "sleep 3600",
                                               "status": "completed", "aggregatedOutput": "", "exitCode": 0}})
            active = None
            complete(f"Read: {params['input'][0]['text']}")


if __name__ == "__main__":
    codex() if sys.argv[1:2] == ["app-server"] else claude()
