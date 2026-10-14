"""Ordinary chats, queued follow-ups, provider sessions and task transcripts keep their project."""
import json
import threading
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.support import AltitudeCase
from altitude import config, dispatch, engines, l3, platform, server, state as S, tasks as T, transcript


class TestChatIsolation(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.projects = ("altitude", "demo")
        for project in self.projects:
            path = self.tmp / project
            path.mkdir()
            self.register(project, path=path)
        self.patch(server, "log", new=lambda _text: None)
        self.patch(server, "ensure_l3_verb_broker")  # mocked providers never invoke daemon verbs
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}{path}", data=data,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read().decode()

    def chat(self, project):
        return json.loads(self.request(f"/api/chat/{project}"))

    def exercise_concurrent_chats(self, engine):
        started = {project: threading.Event() for project in self.projects}
        release = threading.Event()
        calls = {project: [] for project in self.projects}
        self.patch(l3, "_select", return_value={"engine": engine, "why": "isolation test"})

        def provider(prompt, **kwargs):
            project = kwargs["extra_env"]["ALTITUDE_PROJECT"]
            other = next(p for p in self.projects if p != project)
            self.assertEqual(Path(kwargs["cwd"]).parent, platform.coordinator_directory())
            self.assertEqual(l3.turn_unit(project), kwargs["unit"], "the socket serves only this turn's job")
            self.assertNotEqual(l3.turn_unit(other), kwargs["unit"])
            self.assertNotIn(f"message::{other}::", prompt)
            calls[project].append((prompt, kwargs["resume"]))
            number = len(calls[project])
            sid = f"{engine}-{project}"
            answer = f"answer::{project}::{number}"
            if kwargs.get("on_start"):
                kwargs["on_start"](1234)
            if kwargs.get("on_session"):
                kwargs["on_session"]({"session_id": sid, "engine_model": "fixture-model"})
            if kwargs.get("on_text"):
                kwargs["on_text"](answer)
            if number == 1:
                started[project].set()
                self.assertTrue(release.wait(10))
            return {"text": answer, "session_id": sid, "reported_session_id": sid,
                    "usage": {}, "context_tokens": 0, "cost": 0, "turns": 1, "error": None, "tools": []}

        self.patch(engines, "claude_print" if engine == "claude" else "codex_exec", new=provider)
        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = {project: pool.submit(self.request, "/api/chat",
                        {"project": project, "text": f"message::{project}::first"}) for project in self.projects}
            try:
                for event in started.values():
                    self.assertTrue(event.wait(5), "both projects must enter their own provider turn concurrently")
                active = {project: self.chat(project)["active"]["id"] for project in self.projects}
                self.assertEqual(len(set(active.values())), 2)
                for project in self.projects:
                    for suffix in ("follow-up-1", "follow-up-2"):
                        text = f"message::{project}::{suffix}"
                        queued = json.loads(self.request("/api/chat", {"project": project, "text": text}))
                        self.assertEqual(queued["queued"]["text"], text)
                    view = self.chat(project)
                    self.assertEqual(view["active"]["id"], active[project])
                    self.assertEqual([row["text"] for row in view["queued"]],
                                     [f"message::{project}::follow-up-{n}" for n in (1, 2)])
            finally:
                release.set()
            responses = {project: request.result(timeout=10) for project, request in requests.items()}
        # Initial requests have finished scheduling their drains; join those finite test threads.
        with server._bg_guard:
            drains = [thread for key, thread in server._bg.items()
                      if key in {f"l3-queue:{project}" for project in self.projects}]
        for thread in drains:
            thread.join(10)
            self.assertFalse(thread.is_alive())
        for project in self.projects:
            other = next(p for p in self.projects if p != project)
            view = self.chat(project)
            self.assertEqual(view["queued"], [])
            self.assertIsNone(view["active"])
            self.assertEqual([row["text"] for row in view["history"]], [
                f"message::{project}::first", f"answer::{project}::1",
                f"message::{project}::follow-up-1\n\nmessage::{project}::follow-up-2", f"answer::{project}::2"])
            self.assertEqual([call[1] for call in calls[project]], [None, f"{engine}-{project}"])
            self.assertEqual(view["l3"]["sessions"][engine]["session_id"], f"{engine}-{project}")
            self.assertIn(f'{engine}-{project}', responses[project])
            frames = [json.loads(line) for line in responses[project].splitlines()]
            self.assertEqual(frames[0]["turn"]["id"], active[project])
            if engine == "claude":
                self.assertEqual("".join(frame.get("t", "") for frame in frames), f"answer::{project}::1")
            self.assertNotIn(f"answer::{other}::", responses[project])
            self.assertEqual([row["turn_id"] for row in view["history"][:2]], [active[project]] * 2)

    def test_concurrent_claude_chats_and_queued_resumes_keep_their_project(self):
        self.exercise_concurrent_chats("claude")

    def test_concurrent_codex_chats_and_queued_resumes_keep_their_project(self):
        self.exercise_concurrent_chats("codex")

    def test_same_task_slugs_keep_conversation_events_and_provider_transcripts_separate(self):
        for engine in ("claude", "codex"):
            for project in self.projects:
                task = T.new(project, f"Shared {engine} task", "request")
                slug, sid = task["slug"], f"{engine}-{project}"
                task.update(state="running", attempt=1, l2_engine=engine, session_id=sid, agent_id="worker")
                S.save_task(project, task)
                T.message(project, slug, "burak", f"message::{project}::task")
                S.append_event(project, slug, "isolation-event", reason=f"event::{project}::task")
                if engine == "codex":
                    root = dispatch.l2_job_root(project, slug)
                    S.write_json(root / "worker.json", {"session_id": sid, "started_at": S.now()})
                    path = root / "worker.stdout.jsonl"
                    row = {"type": "item.completed", "item": {"type": "agent_message", "text": f"provider::{project}"}}
                else:
                    path = config.HOME / ".claude" / "projects" / project / f"{sid}.jsonl"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self.addCleanup(path.unlink, True)
                    row = {"type": "assistant", "message": {"content": f"provider::{project}"}}
                path.write_text(json.dumps(row) + "\n")
            for project in self.projects:
                other = next(p for p in self.projects if p != project)
                sid = f"{engine}-{project}"
                view = json.loads(self.request(f"/api/transcript/{project}/{slug}?engine={engine}&session_id={sid}&attempt=1&raw=1"))
                texts = "\n".join(row["text"] for row in view["events"])
                self.assertIn(f"event::{project}::task", texts)
                self.assertIn(f"provider::{project}", texts)
                self.assertNotIn(f"event::{other}::task", texts)
                self.assertNotIn(f"provider::{other}", texts)
                self.assertEqual([row["text"] for row in T.task_messages(project, slug)], [f"message::{project}::task"])
                self.assertEqual(T.pending(project, slug), T.task_messages(project, slug))
                with self.assertRaises(transcript.TranscriptAccessError):
                    transcript.view(project, slug, engine=engine, session_id=f"{engine}-{other}", attempt=1)

    def test_command_times_reach_http_from_the_first_recorded_event(self):
        started, ended = "2026-09-03T10:00:01+00:00", "2026-09-03T10:00:05+00:00"
        for engine in ("claude", "codex"):
            for timestamp in (started, None):
                with self.subTest(engine=engine, timestamp=timestamp):
                    task = T.new(self.project, f"Command time {engine} {timestamp is None}", "Observe")
                    sid, slug = f"time-{engine}-{timestamp is None}", task["slug"]
                    task.update(state="running", attempt=1, l2_engine=engine, session_id=sid)
                    S.save_task(self.project, task)
                    if engine == "codex":
                        root = dispatch.l2_job_root(self.project, slug)
                        S.write_json(root / "worker.json", {"session_id": sid, "started_at": S.now()})
                        path = root / "worker.stdout.jsonl"
                        rows = [{"type": "item.started", "timestamp": timestamp, "item": {
                            "type": "command_execution", "id": "call", "command": "echo fixture", "status": "in_progress"}},
                            {"type": "item.completed", "timestamp": ended, "item": {
                                "type": "command_execution", "id": "call", "command": "echo fixture",
                                "status": "completed", "aggregated_output": "fixture"}}]
                    else:
                        path = config.HOME / ".claude" / "projects" / "fictional" / f"{sid}.jsonl"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        self.addCleanup(path.unlink, True)
                        rows = [{"type": "assistant", "timestamp": timestamp, "message": {"content": [
                            {"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": "echo fixture"}}]}},
                            {"type": "user", "timestamp": ended, "message": {"content": [
                                {"type": "tool_result", "tool_use_id": "call", "content": "fixture"}]}}]
                    url = f"/api/transcript/{self.project}/{slug}?engine={engine}&session_id={sid}&attempt=1"
                    path.write_text(json.dumps(rows[0]) + "\n")
                    first = json.loads(self.request(url))
                    command, = [row for row in first["events"] if row["kind"] == "command"]
                    self.assertEqual(command["at"], timestamp)
                    with path.open("a") as stream:
                        stream.write(json.dumps(rows[1]) + "\n")
                    delta = json.loads(self.request(url + f"&mode=delta&cursor={first['cursor']}"))
                    updated, = delta["events"]
                    self.assertEqual((updated["id"], updated["at"], updated["status"], updated["output"]),
                                     (command["id"], timestamp or ended, "completed", "fixture"))
                    reconnect = json.loads(self.request(url))
                    command, = [row for row in reconnect["events"] if row["kind"] == "command"]
                    self.assertEqual(command["at"], timestamp or ended)

    def test_an_untimed_command_takes_the_first_timed_result(self):
        ended = "2026-09-03T10:00:05+00:00"
        task = T.new(self.project, "Command time from results", "Observe")
        sid, slug = "time-results", task["slug"]
        task.update(state="running", attempt=1, l2_engine="claude", session_id=sid)
        S.save_task(self.project, task)
        path = config.HOME / ".claude" / "projects" / "fictional" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(path.unlink, True)
        result = lambda timestamp, text: {"type": "user", "timestamp": timestamp, "message": {"content": [
            {"type": "tool_result", "tool_use_id": "call", "content": text}]}}
        rows = [{"type": "assistant", "timestamp": None, "message": {"content": [
            {"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": "echo fixture"}}]}},
            result(None, "first"), result(ended, "second")]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        view = json.loads(self.request(f"/api/transcript/{self.project}/{slug}?engine=claude&session_id={sid}&attempt=1"))
        command, = [row for row in view["events"] if row["kind"] == "command"]
        self.assertEqual((command["at"], command["status"]), (ended, "completed"))


if __name__ == "__main__":
    unittest.main()
