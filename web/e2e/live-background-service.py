"""Real task, activity and transcript projections over fictional native worker records with background work.

Each configured engine's task gets records in that engine's own output form, read by the real engine seam,
so the walkthrough exercises the parsing the Live session depends on. Only the worker launch is a fixture.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from service_support import configure, serve
from tests.support import add_worktree, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, server, state as S, tasks as T


def stamp(minutes_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def ms(minutes_ago: float = 0) -> int:
    return int((datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).timestamp() * 1000)


def claude_said(text, minutes_ago=0):
    return {"type": "assistant", "timestamp": stamp(minutes_ago),
            "message": {"id": f"m-{text[:12]}", "content": [{"type": "text", "text": text}]}}


def claude_call(call, name, command, description, minutes_ago, background):
    records = [{"type": "assistant", "timestamp": stamp(minutes_ago), "message": {"id": f"m-{call}", "content": [
        {"type": "tool_use", "id": call, "name": name,
         "input": {"command": command, "description": description, "run_in_background": background}
         if name == "Bash" else {"description": description, "prompt": command, "run_in_background": background}}]}}]
    result = f"Command running in background with ID: {call}-task." if background else "ok"
    records.append({"type": "user", "timestamp": stamp(minutes_ago), "message": {"content": [
        {"type": "tool_result", "tool_use_id": call, "content": result, "is_error": False}]}})
    if background:
        records.append({"type": "system", "subtype": "task_started", "task_id": f"{call}-task", "tool_use_id": call,
                        "description": description, "is_backgrounded": True,
                        "task_type": "local_bash" if name == "Bash" else "local_agent"})
    return records


def claude_end(call, status, minutes_ago=0):
    return [{"type": "system", "subtype": "task_updated", "task_id": f"{call}-task",
             "patch": {"status": status, "end_time": ms(minutes_ago)}},
            {"type": "system", "subtype": "task_notification", "task_id": f"{call}-task", "tool_use_id": call,
             "status": status, "summary": f"Background task {status}"}]


def codex_item(kind, item, minutes_ago=0):
    return {"type": kind, "timestamp": stamp(minutes_ago), "item": item}


def codex_command(call, command, status="in_progress", exit_code=None, output=""):
    return {"id": call, "type": "command_execution", "command": command, "status": status,
            "aggregated_output": output, "exit_code": exit_code}


def main():
    configure()
    fake = FakeL2()
    project = "atlas"
    repo = make_repo(config.PROJECT_ROOTS[0] / project)
    with config.add_project(project, path=repo):
        pass
    engines.installation = lambda _engine: {"available": True, "why": "deterministic fixture"}
    outputs: dict[str, tuple[Path, dict]] = {}
    for name in ("start_l2", "worker", "worker_sends", "remove_l2_worker", "stop_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    engines.activity_source = lambda task, **_kwargs: outputs.get(task.get("agent_id"))
    engines.transcript_sources = lambda task, **_kwargs: [(outputs[task["agent_id"]][0], {})] if task.get("agent_id") in outputs else []
    engines.session_context_source = lambda _task: None

    def task(title, engine, records):
        row = T.new(project, title, "Fictional background work acceptance.")
        worktree = add_worktree(repo, row["slug"])
        worker = fake.start_l2(engine, row["slug"], "Fixture initial turn")["agent"]
        path = config.ROOT / "fixture-output" / f"{worker['id']}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        outputs[worker["id"]] = (path, {"id": worker["id"], "session_id": worker["sessionId"], "started_at": stamp(10)})
        write(path, records)
        T.dispatch(project, row["slug"], attempt=1, session_id=worker["sessionId"], agent_id=worker["id"],
                   worktree=str(worktree), branch=f"worktree-{row['slug']}", l2_engine=engine)
        return row["slug"]

    def write(path, records, append=False):
        with path.open("a" if append else "w") as stream:
            for record in records:
                stream.write(json.dumps(record) + "\n")

    first = config.ENGINES[0]
    tasks = {
        "plain": task("Tidy the pagination copy", first, [
            claude_said("I am tidying the pagination copy.", 2) if first == "claude" else
            codex_item("item.completed", {"id": "said", "type": "agent_message", "text": "I am tidying the pagination copy."}, 2),
        ]),
    }
    scenes = {"claude": {
        "start": [claude_said("I will run the full suite in the background and update the docs meanwhile.", 4),
                  *claude_call("call-suite", "Bash", "make check", "Run the full test suite", 4, True),
                  *claude_call("call-review", "Agent", "Review the pagination change.", "Review the pagination change", 3, True),
                  *claude_call("call-edit", "Bash", "git diff --stat", "Show changed files", 1, False),
                  claude_said("The docs now describe the saved page.", 0.2)],
        "review-done": claude_end("call-review", "completed"),
        "suite-failed": claude_end("call-suite", "failed"),
        "many": [rec for index in range(6) for rec in
                 claude_call(f"call-build-{index}", "Bash", f"make build-{index}", f"Build bundle {index + 1}" if index != 1 else
                             "Build the documentation bundle for the saved page, then compare it with the published copy", 1, True)],
    }, "codex": {
        "start": [codex_item("item.completed", {"id": "said-1", "type": "agent_message",
                                                "text": "I will run the full suite and update the docs meanwhile."}, 4),
                  codex_item("item.started", codex_command("call-suite", "make check"), 4),
                  codex_item("item.completed", codex_command("call-edit", "git diff --stat", "completed", 0, "2 files"), 1),
                  codex_item("item.completed", {"id": "said-2", "type": "agent_message",
                                                "text": "The docs now describe the saved page."}, 0.2)],
        "review-done": [],
        "suite-failed": [codex_item("item.completed", codex_command("call-suite", "make check", "failed", 2, "1 failed"))],
        "many": [codex_item("item.started", codex_command(f"call-build-{index}", f"make build-{index}" if index != 1 else
                                                          "pnpm --dir web exec playwright test e2e/saved-page.pw.ts --project=desktop --reporter=line"), 1)
                 for index in range(6)] + [codex_item("item.completed", {"id": "said-3", "type": "agent_message", "text": "Building."})],
    }}
    for index, engine in enumerate(config.ENGINES):
        tasks[engine] = task(f"Keep the saved page {index + 1}", engine, [])

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/status":
                # What each engine's records call the work, so the walkthrough names no engine.
                return self._json({"plain": tasks["plain"], "sessions": [
                    {"engine": engine, "slug": tasks[engine], "helpers": bool(scenes[engine]["review-done"]),
                     "suite": "Run the full test suite" if engine == "claude" else "make check",
                     "build": "Build bundle 6" if engine == "claude" else "make build-5"} for engine in config.ENGINES]})
            return super().do_GET()

        def do_POST(self):
            if self.path != "/fixture/control":
                return super().do_POST()
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            row = S.load_task(project, tasks[body["engine"]])
            write(outputs[row["agent_id"]][0], scenes[row["l2_engine"]][body["mode"]], append=True)
            return self._json({"ok": True})

    serve(Handler)


if __name__ == "__main__":
    main()
