"""Real task/API storage with deterministic worker output and controllable worker termination."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading

from service_support import configure, serve
from tests.support import add_worktree, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, server, state as S, tasks as T


def main():
    configure()
    fake = FakeL2()
    project = "atlas"
    repo = make_repo(config.PROJECT_ROOTS[0] / project)
    with config.add_project(project, path=repo):
        pass
    engines.installation = lambda _engine: {"available": True, "why": "deterministic fixture"}
    stop_gate = threading.Event()
    stop_gate.set()
    resume_gate = threading.Event()
    resume_gate.set()
    sources = {}
    history = {}
    denied = False

    def source(worker, text=None, *, at=True):
        path = config.ROOT / "fixture-output" / f"{worker['id']}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"id": worker["id"], "session_id": worker["sessionId"], "started_at": S.now(), "input_delivered": True}
        sources[worker["id"]] = (path, record)
        history.setdefault(worker["sessionId"], []).append((path, record))
        if text is None:
            path.write_text("")
        else:
            path.write_text(json.dumps({"fixture_text": text, "at": S.now() if at else None}) + "\n")

    def resume(engine, name, session_id, prompt, **kwargs):
        assert resume_gate.wait(15), "Fixture continuation was not released"
        result = fake.resume_l2(engine, name, session_id, prompt, **kwargs)
        source(result["agent"])
        return result

    def stop(engine, worker_id, **kwargs):
        assert stop_gate.wait(15), "Fixture Stop was not released"
        return fake.stop_l2_worker(engine, worker_id, **kwargs)

    for name in ("start_l2", "worker", "worker_sends", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    engines.resume_l2 = resume
    engines.stop_l2_worker = stop
    engines.activity_source = lambda task, **_kwargs: sources.get(task.get("agent_id"))
    engines.transcript_sources = lambda task, **_kwargs: history.get(task.get("session_id"), [])
    engines.session_context_source = lambda _task: None
    engines.transcript_rows = lambda engine, record, _worktree: [
        {"source": engine, "kind": "message", "role": "assistant", "type": "public", "at": record.get("at"), "text": record["fixture_text"]}
    ] if "fixture_text" in record else [
        {"source": engine, "kind": "command", "role": "assistant", "type": "tool_use", "at": record.get("at"), "text": record["fixture_tool"],
         "tool": "command", "summary": record["fixture_tool"], "tool_use_id": "fixture-long-call"}
    ] if "fixture_tool" in record else []

    for index, engine in enumerate(config.ENGINES):
        row = T.new(project, f"Keep pagination working {index + 1}", "Fictional progress and steering acceptance.")
        slug = row["slug"]
        worktree = add_worktree(repo, slug)
        (worktree / "kept-edit.txt").write_text("An existing edit stays through Stop and continuation.\n")
        worker = fake.start_l2(engine, slug, "Fixture initial turn")["agent"]
        source(worker, "I am checking where pagination loses the selected page before changing the retry behavior. The existing edits stay in this session.")
        T.dispatch(project, slug, attempt=1, session_id=worker["sessionId"], agent_id=worker["id"],
                   worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=engine)
        T.message(project, slug, "l2", "I will check the saved pagination behavior.")

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/status":
                return self._json({"calls": fake.calls, "workers": fake.workers,
                    "tasks": [{"slug": row["slug"], "engine": row["l2_engine"], "state": row["state"],
                               "pending": T.pending(project, row["slug"]),
                               "edit": (Path(row["worktree"]) / "kept-edit.txt").read_text()}
                              for row in S.list_tasks(project)]})
            return super().do_GET()

        def do_POST(self):
            nonlocal denied
            if self.path == "/api/task/action" and denied:
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                return self._json({"error": "Fixture permission denied"}, 403)
            if self.path != "/fixture/control":
                return super().do_POST()
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            mode = body["mode"]
            row = S.load_task(project, body["slug"])
            path, _record = sources[row["agent_id"]]
            if mode == "hold-stop":
                stop_gate.clear()
            elif mode == "release-stop":
                stop_gate.set()
            elif mode == "deliver-send-now":  # what the worker's driver records once the engine accepts it
                claim = row["send_now"]
                T.settle_send_now(project, body["slug"], claim["id"], "delivered", agent_id=claim["agent_id"],
                                  session_id=row.get("session_id"))
            elif mode == "hold-resume":
                resume_gate.clear()
            elif mode == "release-resume":
                resume_gate.set()
            elif mode == "deny":
                denied = True
            elif mode == "allow":
                denied = False
            elif mode == "history":
                for index in range(30):
                    T.message(project, row["slug"], "l2", f"Pagination check {index + 1}: the saved page and retry behavior remain readable in the conversation.")
            elif mode == "empty":
                path.write_text("")
            elif mode == "tool-output":
                with path.open("a") as stream:
                    stream.write(json.dumps({"fixture_tool": "Checking pagination", "at": S.now()}) + "\n")
            elif mode in ("output", "unknown-time", "quiet"):
                stamp = datetime.now(timezone.utc) - timedelta(minutes=5) if mode == "quiet" else datetime.now(timezone.utc)
                with path.open("a") as stream:
                    stream.write(json.dumps({"fixture_text": body.get("text", "I am checking the message race."), "at": None if mode == "unknown-time" else stamp.isoformat()}) + "\n")
                os.utime(path, (stamp.timestamp(), stamp.timestamp()))
            elif mode == "long-call":
                # A call that started four minutes ago and has written nothing since.
                stamp = datetime.now(timezone.utc) - timedelta(minutes=4)
                with path.open("a") as stream:
                    stream.write(json.dumps({"fixture_tool": "pnpm test --run", "at": stamp.isoformat()}) + "\n")
                os.utime(path, (stamp.timestamp(), stamp.timestamp()))
            elif mode == "unavailable":
                with path.open("a") as stream:
                    stream.write("{partial\n")
            elif mode == "unconfirmed-delivery":
                T.take_inbox(project, row["slug"])
            elif mode in ("prelaunch-recovery", "uncertain-recovery"):
                with S.project_lock(project):
                    row["state"] = "blocked"
                    S.save_task(project, row)
                claim = T.claim_resume(project, row["slug"])
                if mode == "uncertain-recovery":
                    T.update_resume_claim(project, row["slug"], claim["id"], phase="launching")
                T.release_resume_claim(project, row["slug"], claim["id"], consume_request=True)
            elif mode == "blocked":
                T.block(project, row["slug"], "Keep the original page size?", actor="l2")
                T.escalate(project, row["slug"], "Keep the original page size?")
            elif mode == "finished":
                T.message(project, row["slug"], "l2", "Pagination and steering are verified.")
                row = S.load_task(project, row["slug"])
                row["state"] = "reported"
                S.save_task(project, row)
                T.done(project, row["slug"], digest="Fixture completed.")
            return self._json({"ok": True})

    serve(Handler, release=lambda: (stop_gate.set(), resume_gate.set()))


if __name__ == "__main__":
    main()
