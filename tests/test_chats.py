"""Agent chats (docs/ARCHITECTURE.md#agent-chats): a chat's first message makes it and its copy of main, each turn
resumes the chat's one session, messages sent while it answers wait and go together next, Stop, a lost or interrupted
turn and Retry, another engine's new session given the conversation so far, archive, reopen and delete, the routes
only a paired browser's page may use, `alt chats` for the operator alone, and the launch: the chat role, restricted
but committing on its own branch. Projects are fictional; the engine is scripted at the engine seam."""
import http.client
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest import mock

from tests.support import REPO, AltitudeCase, git, make_repo
from altitude import (access, chats, config, engines, git_policy, images, monitor, platform, route, server, state as S,
                      terminal, tidy)

PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def say(text: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def bash(ident: str, command: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": ident, "name": "Bash", "input": {"command": command}}]}}


def output(ident: str, text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": ident, "content": text, "is_error": False}]}}


class ChatCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.patch(chats, "WATCH", 0)
        self.patch(route, "pick_engine", side_effect=lambda role, **kw: {
            "engine": kw.get("forced") or "claude", "model": kw.get("model") or "fixture-model", "effort": None})
        self.launches: list[dict] = []
        self.workers: dict[str, dict] = {}  # each worker's state as owned_worker reads it
        self.answers: dict[str, str] = {}
        self.session_of = {"claude": "claude-session", "codex": "codex-session"}
        self.patch(engines, "start_chat_turn", side_effect=self.start)
        self.patch(engines, "owned_worker", side_effect=lambda worker, job_root: self.workers.get(worker))
        self.patch(engines, "worker_answer", side_effect=lambda worker, job_root: self.answers[worker])
        self.patch(engines, "worker_events", return_value=[])
        self.stopped = self.patch(engines, "stop_l2_worker", side_effect=self.stop)
        self.requests = 0

    def start(self, engine, name, prompt, **kw):
        launch = {"engine": engine, "name": name, "prompt": prompt, **kw}
        self.launches.append(launch)
        worker = f"worker-{len(self.launches)}"
        self.workers[worker] = {"state": "working", "detail": "", "rejection": None}
        self.answers[worker] = f"Answer {len(self.launches)}"
        session = kw["resume"] or self.session_of[engine]
        return {"returncode": 0, "agent": {"id": worker, "sessionId": session, "engine_model": "fixture-model"}}

    def stop(self, engine, worker, job_root):
        self.workers[worker]["state"] = "stopped"
        return "stopped"

    def request(self) -> str:
        self.requests += 1
        return f"request-{self.requests:04d}"

    def finish(self, worker: str, state: str = "done") -> None:
        self.workers[worker]["state"] = state

    def new(self, text="Why is the export slow?", **kw) -> str:
        return chats.new(self.project, text, self.request(), **kw)["id"]

    def chat(self, chat_id: str) -> dict:
        return chats.load(self.project, chat_id)

    def answered_all(self, chat_id: str) -> None:
        """Every worker started so far, and every one the chat starts after them, ends with its answer."""
        original = self.start

        def done(*a, **kw):
            result = original(*a, **kw)
            self.finish(result["agent"]["id"])
            return result
        self.patch(engines, "start_chat_turn", side_effect=done)
        for worker in self.workers:
            self.finish(worker)
        chats.run(self.project, chat_id)


class TestTurns(ChatCase):
    def test_a_chat_starts_in_its_own_copy_of_main_and_each_turn_resumes_its_one_session(self):
        chat_id = self.new()
        self.assertEqual(self.chat(chat_id)["name"], "Why is the export slow?")
        self.assertEqual(chats.view(self.project, chat_id)["state"], "starting")
        chats._start(self.project, chat_id)
        first = self.launches[0]
        copy = chats.worktree(self.project, chat_id)
        self.assertEqual(first["cwd"], copy)
        self.assertEqual(first["prompt"], "Why is the export slow?")
        self.assertIsNone(first["resume"])
        self.assertEqual(first["extra_env"]["ALTITUDE_ACTOR"], "chat")
        self.assertEqual(first["job_root"], chats.folder(self.project, chat_id) / "l2-engine")
        main = git("rev-parse", "origin/main", cwd=self.repo).strip()
        self.assertEqual(git("rev-parse", f"chat-{chat_id}", cwd=self.repo).strip(), main)
        self.assertEqual(self.chat(chat_id)["base"], main)
        self.assertEqual(chats.view(self.project, chat_id)["state"], "answering")
        # A message sent while it answers waits; two waiting go together as the next turn.
        chats.message(self.project, chat_id, "Also check the import.", self.request())
        chats.message(self.project, chat_id, "And the tests.", self.request())
        waiting = [item for item in chats.view(self.project, chat_id)["items"] if item["kind"] == "you"]
        self.assertEqual([item["waiting"] for item in waiting], [False, True, True])
        self.answered_all(chat_id)
        self.assertEqual(len(self.launches), 2)
        self.assertEqual(self.launches[1]["prompt"], "Also check the import.\n\nAnd the tests.")
        self.assertEqual(self.launches[1]["resume"], "claude-session")
        view = chats.view(self.project, chat_id)
        self.assertEqual(view["state"], "idle")
        turns = [item for item in view["items"] if item["kind"] == "turn"]
        self.assertEqual([(t["state"], t["answer"]) for t in turns], [("done", "Answer 1"), ("done", "Answer 2")])
        self.assertEqual([item["kind"] for item in view["items"]], ["you", "event", "turn", "you", "you", "turn"])
        self.assertEqual(view["items"][1]["event"], "started")
        # The copy stays while the chat is open, however long it is idle.
        self.assertTrue(copy.is_dir())

    def test_stop_ends_the_running_turn_and_a_waiting_message_goes_next(self):
        chat_id = self.new()
        chats._start(self.project, chat_id)
        chats.message(self.project, chat_id, "Then this.", self.request())
        request = self.request()
        chats.stop(self.project, chat_id, request)
        self.stopped.assert_called_once()
        self.assertEqual(self.workers["worker-1"]["state"], "stopped")
        chats.stop(self.project, chat_id, request)  # a replayed Stop stops nothing more
        self.assertEqual(self.stopped.call_count, 1)
        self.answered_all(chat_id)
        turns = self.chat(chat_id)["turns"]
        self.assertEqual([t["state"] for t in turns], ["stopped", "done"])
        self.assertEqual(self.launches[1]["prompt"], "Then this.")

    def test_a_lost_turn_reads_cut_off_and_retry_sends_its_messages_again(self):
        chat_id = self.new()
        chats._start(self.project, chat_id)
        del self.workers["worker-1"]  # its job's record is gone
        chats.run(self.project, chat_id)
        self.assertEqual(self.chat(chat_id)["turns"][-1]["state"], "cut")
        self.assertEqual(chats.view(self.project, chat_id)["state"], "failed")
        chats.retry(self.project, chat_id, self.request())
        self.answered_all(chat_id)
        record = self.chat(chat_id)
        self.assertEqual([t["state"] for t in record["turns"]], ["done"])
        self.assertEqual(self.launches[-1]["prompt"], "Why is the export slow?")
        with self.assertRaisesRegex(chats.Refused, "nothing to retry"):
            chats.retry(self.project, chat_id, self.request())

    def test_a_turn_a_restart_interrupted_while_starting_reads_cut_off(self):
        chat_id = self.new()
        with S.project_lock(self.project):
            record = self.chat(chat_id)
            record["messages"][0]["turn"] = 1
            record["turns"].append({"n": 1, "seq": 2, "state": "starting", "worker": None, "started": S.now(), "ended": None,
                                    "engine": "claude", "model": "fixture-model", "answer": None, "error": None})
            chats._save(self.project, record)
        self.assertEqual(chats.due(self.project), [chat_id])
        chats.service_started()  # it did not go on, so no line says it continued
        chats.run(self.project, chat_id)
        self.assertEqual(self.chat(chat_id)["turns"][0]["state"], "cut")
        self.assertEqual(self.chat(chat_id)["events"], [])
        self.assertEqual(self.launches, [])

    def test_stop_pressed_while_a_turn_starts_stops_its_worker_once_known(self):
        chat_id = self.new()
        original = self.start

        def pressed(*a, **kw):
            chats.stop(self.project, chat_id, self.request())
            return original(*a, **kw)
        self.patch(engines, "start_chat_turn", side_effect=pressed)
        chats.run(self.project, chat_id)
        self.stopped.assert_called_once()
        self.assertEqual(self.chat(chat_id)["turns"][0]["state"], "stopped")

    def test_a_message_sent_as_the_thread_ends_goes_next_without_waiting_for_the_tick(self):
        chat_id = self.new()
        self.answered_all(chat_id)
        original, sent = chats._start, []

        def ending(project, chat):
            started = original(project, chat)
            if started is False and not sent:  # the route's thread found this one still alive
                sent.append(chats.message(project, chat, "One more thing.", self.request()))
            return started
        self.patch(chats, "_start", side_effect=ending)
        chats.run(self.project, chat_id)
        self.assertEqual([t["state"] for t in self.chat(chat_id)["turns"]], ["done", "done"])
        self.assertEqual(self.launches[-1]["prompt"], "One more thing.")

    def test_main_that_cannot_be_fetched_fails_the_turn_with_retry(self):
        chat_id = self.new()
        self.patch(git_policy, "fetch_origin", side_effect=git_policy.FetchError("could not read from remote"))
        chats.run(self.project, chat_id)
        turn = self.chat(chat_id)["turns"][0]
        self.assertEqual((turn["state"], turn["error"]),
                         ("failed", "Its copy of main could not be made: could not read from remote"))
        self.assertEqual(self.launches, [])

    def test_a_running_turn_survives_a_restart_and_says_so(self):
        chat_id = self.new()
        chats._start(self.project, chat_id)
        chats.service_started()
        self.assertEqual([e["kind"] for e in self.chat(chat_id)["events"]], ["restarted"])
        rows = monitor.sessions(chats_too=True)
        row = next(r for r in rows if r.get("kind") == "chat")
        self.assertEqual((row["chat_id"], row["name"], row["engine"]), (chat_id, "Why is the export slow?", "claude"))
        self.answered_all(chat_id)
        self.assertEqual(self.chat(chat_id)["turns"][0]["answer"], "Answer 1")
        self.assertEqual(chats.monitor_sessions(self.project), [])

    def test_a_failed_turn_keeps_the_engines_reason(self):
        chat_id = self.new()
        chats._start(self.project, chat_id)
        self.workers["worker-1"].update(state="failed", detail="You've hit your usage limit")
        chats.run(self.project, chat_id)
        turn = self.chat(chat_id)["turns"][0]
        self.assertEqual((turn["state"], turn["error"]), ("failed", "You've hit your usage limit"))

    def test_another_engine_starts_a_new_session_given_the_conversation_so_far(self):
        chat_id = self.new()
        self.answered_all(chat_id)
        chats.set_choice(self.project, chat_id, {"engine": "codex", "model": None}, self.request())
        chats.message(self.project, chat_id, "Now try it this way.", self.request())
        self.answered_all(chat_id)
        later = self.launches[-1]
        self.assertEqual((later["engine"], later["resume"]), ("codex", None))
        self.assertIn("> Why is the export slow?", later["prompt"])
        self.assertIn("> Answer 1", later["prompt"])
        self.assertTrue(later["prompt"].endswith("Now try it this way."))
        record = self.chat(chat_id)
        self.assertEqual((record["engine"], record["session"]), ("codex", "codex-session"))
        self.assertEqual([t.get("opened") for t in record["turns"]], ["started", "new_session"])
        # The same engine again resumes the session it now has.
        chats.message(self.project, chat_id, "Thanks.", self.request())
        self.answered_all(chat_id)
        self.assertEqual(self.launches[-1]["resume"], "codex-session")

    def test_a_session_the_engine_lost_starts_again_with_the_conversation(self):
        chat_id = self.new()
        self.answered_all(chat_id)
        original = self.start

        def lost(*a, **kw):
            if kw["resume"]:
                self.launches.append({"resume": kw["resume"]})
                return {"returncode": 1, "stderr": "No conversation found", "agent": {}, "rejection": None}
            return original(*a, **kw)
        self.patch(engines, "start_chat_turn", side_effect=lost)
        chats.message(self.project, chat_id, "Go on.", self.request())
        chats._start(self.project, chat_id)
        self.assertEqual(self.launches[-2], {"resume": "claude-session"})
        self.assertIsNone(self.launches[-1]["resume"])
        self.assertIn("> Answer 1", self.launches[-1]["prompt"])
        self.assertEqual(self.chat(chat_id)["turns"][-1]["opened"], "new_session")

    def test_a_turn_that_cannot_start_says_why(self):
        chat_id = self.new()
        self.patch(engines, "start_chat_turn", return_value={
            "returncode": 1, "stderr": "Claude needs you to sign in again", "agent": {}, "rejection": {"sign_in": True}})
        noted = self.patch(route, "note_rejection")
        chats.run(self.project, chat_id)
        turn = self.chat(chat_id)["turns"][0]
        self.assertEqual((turn["state"], turn["error"]), ("failed", "Claude needs you to sign in again"))
        noted.assert_called_once()

    def test_steps_come_from_the_workers_events_and_the_reply_is_not_repeated(self):
        chat_id = self.new()
        chats._start(self.project, chat_id)
        events = [say("Looking at the export."), bash("t1", "make test"), output("t1", "ok"), say("Answer 1")]
        self.patch(engines, "worker_events", return_value=events)
        self.patch(engines, "transcript_rows", new=engines.common.transcript_rows)
        Path(chats.folder(self.project, chat_id) / "l2-engine").mkdir(parents=True, exist_ok=True)
        (chats.folder(self.project, chat_id) / "l2-engine" / "worker-1.stdout.jsonl").write_text("x\n")
        turn = next(i for i in chats.view(self.project, chat_id)["items"] if i["kind"] == "turn")
        self.assertEqual([(s["kind"], s.get("verb")) for s in turn["steps"]],
                         [("say", None), ("step", "ran"), ("say", None)])
        self.answered_all(chat_id)
        (chats.folder(self.project, chat_id) / "l2-engine" / "worker-1.stdout.jsonl").write_text("xy\n")
        turn = next(i for i in chats.view(self.project, chat_id)["items"] if i["kind"] == "turn")
        self.assertEqual([s.get("text") for s in turn["steps"]][-1], "make test")
        self.assertEqual(turn["answer"], "Answer 1")

    def test_images_go_to_the_agent_and_stay_readable_with_the_chat(self):
        self.patch(images, "_normalize", side_effect=lambda raw, directory: (raw, "image/png", 1, 1))
        chat_id = self.new("What is wrong here?", uploads=[{"name": "screen.png", "data": PNG}])
        ref = self.chat(chat_id)["messages"][0]["images"][0]
        self.assertEqual(images.read(self.project, ref["id"])[1]["id"], ref["id"])
        chats._start(self.project, chat_id)
        sent = self.launches[0]["images"]
        self.assertEqual([image["id"] for image in sent], [ref["id"]])
        self.assertTrue(Path(sent[0]["path"]).is_file())


class TestCopyAndLife(ChatCase):
    def test_archive_names_unsaved_changes_and_removes_the_copy_and_branch(self):
        chat_id = self.new()
        self.answered_all(chat_id)
        copy = chats.worktree(self.project, chat_id)
        (copy / "export.py").write_text("print('faster')\n")
        self.assertEqual(chats.view(self.project, chat_id)["changes"], 1)
        with self.assertRaisesRegex(chats.Refused, "1 changed file no task has"):
            chats.archive(self.project, chat_id, self.request())
        chats.archive(self.project, chat_id, self.request(), discard=True)
        self.assertFalse(copy.exists())
        self.assertNotIn(f"chat-{chat_id}", git("branch", "--list", cwd=self.repo))
        view = chats.view(self.project, chat_id)
        self.assertEqual((view["state"], view["changes"]), ("archived", None))
        with self.assertRaisesRegex(chats.Refused, "archived"):
            chats.message(self.project, chat_id, "More?", self.request())
        # Reopen goes on in a fresh copy of main with the same session.
        chats.reopen(self.project, chat_id, self.request())
        chats.message(self.project, chat_id, "More?", self.request())
        self.answered_all(chat_id)
        self.assertTrue(copy.is_dir())
        self.assertFalse((copy / "export.py").exists())
        self.assertEqual(self.launches[-1]["resume"], "claude-session")
        self.assertEqual(self.launches[-1]["prompt"], chats.FRESH + "More?")
        self.assertEqual([e["kind"] for e in self.chat(chat_id)["events"]], ["archived", "reopened"])

    def test_committed_changes_count_and_archive_waits_for_the_reply(self):
        chat_id = self.new()
        chats._start(self.project, chat_id)
        with self.assertRaisesRegex(chats.Refused, "when the reply has ended"):
            chats.archive(self.project, chat_id, self.request())
        self.answered_all(chat_id)
        copy = chats.worktree(self.project, chat_id)
        (copy / "a.txt").write_text("a\n")
        git("add", "a.txt", cwd=copy)
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "-q", "-m", "a", cwd=copy)
        (copy / "b.txt").write_text("b\n")
        self.assertEqual(chats.changes(self.project, chat_id), 2)

    def test_a_copy_whose_folder_went_missing_comes_back_on_its_branch(self):
        chat_id = self.new()
        self.answered_all(chat_id)
        copy = chats.worktree(self.project, chat_id)
        (copy / "a.txt").write_text("a\n")
        git("add", "a.txt", cwd=copy)
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "-q", "-m", "a", cwd=copy)
        made = git("rev-parse", "HEAD", cwd=copy).strip()
        subprocess.run(["rm", "-rf", str(copy)], check=True)
        chats.message(self.project, chat_id, "Go on.", self.request())
        self.answered_all(chat_id)
        self.assertEqual(git("rev-parse", "HEAD", cwd=copy).strip(), made)
        self.assertEqual(self.launches[-1]["prompt"], "Go on.")

    def test_delete_takes_an_archived_chat_for_good(self):
        chat_id = self.new()
        self.answered_all(chat_id)
        with self.assertRaisesRegex(chats.Refused, "Archive this chat"):
            chats.delete(self.project, chat_id)
        chats.archive(self.project, chat_id, self.request())
        chats.delete(self.project, chat_id)
        self.assertFalse(chats.folder(self.project, chat_id).exists())
        self.assertEqual(chats.listing(self.project), {"chats": []})
        with self.assertRaises(chats.Missing):
            chats.view(self.project, chat_id)

    def test_rename_remove_and_replays(self):
        chat_id = self.new("x" * 100)
        self.assertEqual(self.chat(chat_id)["name"], "x" * 79 + "…")
        for name in ("", "y" * 81):
            with self.assertRaisesRegex(chats.Refused, "1 to 80 characters"):
                chats.rename(self.project, chat_id, name, self.request())
        chats.rename(self.project, chat_id, "  Export   speed ", self.request())
        self.assertEqual(self.chat(chat_id)["name"], "Export speed")
        chats._start(self.project, chat_id)
        request = self.request()
        for _ in range(2):
            chats.message(self.project, chat_id, "Waiting one", request)
        waiting = chats._waiting(self.chat(chat_id))
        self.assertEqual(len(waiting), 1)
        chats.remove(self.project, chat_id, waiting[0]["id"], self.request())
        self.assertEqual(chats._waiting(self.chat(chat_id)), [])
        with self.assertRaisesRegex(chats.Refused, "already gone"):
            chats.remove(self.project, chat_id, self.chat(chat_id)["messages"][0]["id"], self.request())
        same = self.request()
        self.assertEqual(chats.new(self.project, "Another", same), chats.new(self.project, "Another", same))
        self.assertEqual(len(chats.records(self.project)), 2)

    def test_the_list_orders_by_the_latest_message_or_reply(self):
        older = self.new("First")
        newer = self.new("Second")
        for chat_id, at in ((older, "2026-10-20T10:00:00+00:00"), (newer, "2026-10-21T10:00:00+00:00")):
            with S.project_lock(self.project):
                record = self.chat(chat_id)
                record["messages"][0]["at"] = at
                chats._save(self.project, record)
        self.assertEqual([r["id"] for r in chats.listing(self.project)["chats"]], [newer, older])
        self.answered_all(older)
        rows = chats.listing(self.project)["chats"]
        self.assertEqual([r["id"] for r in rows], [older, newer])
        self.assertEqual(rows[0]["latest"]["by"], "agent")

    def test_tidy_leaves_a_chats_copy_alone(self):
        chat_id = self.new()
        self.answered_all(chat_id)
        self.assertNotIn(f"chat-{chat_id}", tidy._candidates(self.project))
        # A task's worktree whose name merely starts like a chat's stays a candidate.
        git("worktree", "add", "-q", "-b", "chat-export-polish", str(config.task_worktree(self.project, "chat-export-polish")),
            cwd=self.repo)
        self.assertIn("chat-export-polish", tidy._candidates(self.project))


class TestLaunch(ChatCase):
    def test_a_turn_runs_the_chat_role_restricted_but_committing_on_its_own_branch(self):
        calls = []
        self.patch(engines.common, "_start_worker", side_effect=lambda *a, **kw: calls.append(kw) or {})
        engines.common.start_chat_turn("claude", "p/chat-x", "hi", cwd=self.repo, job_root=self.tmp / "j",
                                       resume=None, model=None, effort=None, images=(), extra_env={})
        self.assertEqual((calls[0]["restricted"], calls[0]["commits"], calls[0]["persona"].name),
                         (True, True, "chat.md"))
        copy = self.repo / ".claude" / "worktrees" / "chat-x"
        git("worktree", "add", "-q", "--detach", str(copy), cwd=self.repo)
        for engine in config.ENGINES:
            native = engines.common.implementation(engine)
            if hasattr(native, "claude_agents"):
                self.patch(native, "claude_agents", return_value=[])
            kw = dict(cwd=copy, scratch=self.tmp / "scratch", job_root=self.tmp / engine, persona=None, model=None,
                      settings=None, effort=None, resume=None, images=(), restricted=True)
            shared, _ = git_policy.worker_git_access(copy)
            _, _, writable, *_ = native.prepare_worker("p/chat-x", "hi", **kw, commits=True)
            self.assertTrue(set(shared) <= set(writable), engine)
            _, _, writable, *_ = native.prepare_worker("p/chat-x", "hi", **kw)
            self.assertFalse(shared[0] in writable, engine)

    def test_every_agents_sandbox_hides_the_chats_and_a_turn_reopens_only_its_own_engine_folder(self):
        chats_folder = os.path.realpath(config.CHATS)
        hidden = platform._operator_places()
        self.assertIn(chats_folder, hidden, "present or not, every job's sandbox hides it")
        other = os.path.realpath(chats.folder(self.project, "0" * 12))
        copy = self.repo / ".claude" / "worktrees" / "chat-x"
        git("worktree", "add", "-q", "--detach", str(copy), cwd=self.repo)
        for engine in config.ENGINES:
            native = engines.common.implementation(engine)
            if hasattr(native, "claude_agents"):
                self.patch(native, "claude_agents", return_value=[])
            _, _, writable, read_only, *_ = native.prepare_worker(
                "p/chat-x", "hi", cwd=copy, scratch=self.tmp / "scratch", persona=None, model=None, settings=None,
                effort=None, resume=None, images=(), restricted=True, commits=True,
                job_root=chats.folder(self.project, "a" * 12) / "l2-engine")
            writable = [os.path.realpath(path) for path in writable]
            own = next(path for path in writable if path.startswith(chats_folder + os.sep))
            argv = platform.bubblewrap_profile(writable, [str(path) for path in read_only],
                                               temporary=str(self.tmp / "job-tmp"), hidden=hidden)
            spans = [argv[n:n + 3] for n in range(len(argv))]
            hide = [span[:2] for span in spans].index(["--tmpfs", chats_folder])
            self.assertLess(hide, spans.index(["--bind", own, own]), f"{engine}: the turn's own folder is mounted after")
            self.assertFalse([arg for arg in argv if arg.startswith(other)], engine)
            profile = platform.seatbelt_profile(writable, [str(path) for path in read_only],
                                                temporary=str(self.tmp / "job-tmp"), hidden=hidden)
            deny = profile.index(f'(deny file-read* file-write* (subpath "{chats_folder}"))')
            for verb in ("read", "write"):
                self.assertGreater(profile.rindex(f'(allow file-{verb}* (subpath "{own}"))'), deny,
                                   f"{engine}: Seatbelt's later rules reopen the turn's own folder")
            self.assertNotIn(other, profile, engine)
        settings = f"{own}/settings"  # a read-only path in a reopened root stays read-only, as on Linux
        profile = platform.seatbelt_profile([own], [settings], temporary=str(self.tmp / "job-tmp"), hidden=hidden)
        self.assertGreater(profile.rindex(f'(deny file-write* (subpath "{settings}"))'),
                           profile.rindex(f'(allow file-write* (subpath "{own}"))'))
        # This case's chats lie in the shared temporary folder, which a Codex profile refuses whole.
        with mock.patch.object(platform, "operator_paths", return_value=["/home/fictional/.altitude-chats"]) as named:
            rules = next(s for s in engines.codex_l3_permissions(copy, project=self.project) if ".filesystem=" in s)
        self.assertIn(str(config.CHATS), named.call_args.args[0])
        self.assertIn('"/home/fictional/.altitude-chats"="deny"', rules, "the Codex coordinator, outside Altitude's sandbox")


class TestWhoReads(ChatCase):
    """Every chat route admits only a paired browser's own page; `alt chats` only the operator."""

    def setUp(self):
        super().setUp()
        self.agent = self.patch(terminal, "agent_connection", return_value=False)
        self.patch(access, "device", side_effect=lambda key: {"id": "phone", "name": "Phone"} if key == "paired" else None)
        self.patch(access, "renew", return_value=False)
        self.patch(server, "log", new=lambda *a, **k: None)
        self.patch(server, "spawn", return_value=True)
        server.Handler._seen_clients.clear()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method: str, path: str, body: dict | None = None, *, status: int, headers: dict | None = None) -> dict:
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=60)
        try:
            sent = {"Cookie": f"{access.COOKIE}=paired", **({"Content-Type": "application/json"} if body is not None else {}),
                    **(headers or {})}
            connection.request(method, path, json.dumps(body) if body is not None else None, sent)
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, status, payload)
            return payload
        finally:
            connection.close()

    def test_agents_l3_the_machine_key_and_other_sites_are_refused_on_every_route(self):
        base = f"/api/chats/{self.project}"
        chat_id = self.call("POST", base, {"text": "Hello", "request": "request-0001"}, status=200)["id"]
        routes = [(base, {"text": "x", "request": "request-0101"})] + [
            (f"{base}/{chat_id}/{action}", {"request": f"request-01{n:02d}", "text": "x", "name": "x"})
            for n, action in enumerate(("message", "remove", "retry", "rename", "model", "stop", "archive", "reopen",
                                        "delete"), 2)]
        refusals = (({"Cookie": ""}, chats.FROM_BROWSER), ({access.KEY_HEADER: "anything"}, chats.FROM_BROWSER),
                    ({"Content-Type": "text/plain"}, chats.FROM_BROWSER),
                    ({"Origin": "https://elsewhere.example"}, "Requests must come from Altitude's own page."))
        for path, body in routes:
            for headers, error in refusals:
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.call("POST", path, body, status=403, headers=headers)["error"], error)
            self.agent.return_value = True
            with self.subTest(path=path, agent=True):
                self.assertEqual(self.call("POST", path, body, status=403)["error"], chats.FROM_BROWSER)
            self.agent.return_value = False
        for page in (base, f"{base}/{chat_id}"):
            for headers in ({"Cookie": ""}, {access.KEY_HEADER: "anything"}, {"Sec-Fetch-Site": "cross-site"}):
                with self.subTest(page=page, headers=headers):
                    self.assertEqual(self.call("GET", page, status=403, headers=headers)["error"],
                                     chats.PAGE_FROM_BROWSER)
            self.agent.return_value = True
            self.assertEqual(self.call("GET", page, status=403)["error"], chats.PAGE_FROM_BROWSER)
            self.agent.return_value = False
        self.assertEqual(self.call("GET", f"{base}/{chat_id}", status=200)["name"], "Hello")
        self.assertEqual([r["id"] for r in self.call("GET", base, status=200)["chats"]], [chat_id])
        # The paired page steers it; a replayed message acts once, and a refusal says why.
        for _ in range(2):
            self.call("POST", f"{base}/{chat_id}/message", {"text": "More", "request": "request-0002"}, status=200)
        self.assertEqual(len(chats.load(self.project, chat_id)["messages"]), 2)
        self.assertEqual(self.call("POST", f"{base}/{chat_id}/stop", {"request": "request-0003"}, status=409)["error"],
                         "No reply is running.")
        self.assertEqual(self.call("POST", f"{base}/{'0' * 12}/stop", {"request": "request-0004"}, status=404)["error"],
                         "There is no such chat.")
        self.assertEqual(self.call("GET", "/api/chats/elsewhere", status=404)["error"], "There is no such project.")

    def test_only_the_paired_browsers_monitor_shows_an_answering_chat(self):
        self.patch(monitor, "routing", return_value=[])  # this case's engine choice is a fixture
        chat_id = self.new("Tidy the fixtures")
        chats._start(self.project, chat_id)
        rows = self.call("GET", "/api/monitor", status=200)["sessions"]
        self.assertEqual([r["chat_id"] for r in rows if r.get("kind") == "chat"], [chat_id])
        alt = [sys.executable, str(REPO / "bin" / "alt"), "monitor"]
        for actor in ("l3", None):  # L3's broker runs `alt monitor` inside altd, outside every sandbox
            env = {**{k: v for k, v in os.environ.items() if k != "ALTITUDE_ACTOR"}, **({"ALTITUDE_ACTOR": actor} if actor else {})}
            seen = subprocess.run(alt, capture_output=True, text=True, env=env)
            self.assertEqual(seen.returncode, 0, seen.stderr)
            self.assertNotIn(chat_id, seen.stdout, actor)
            self.assertNotIn("Tidy the fixtures", seen.stdout, actor)

    def test_alt_chats_is_the_operators_alone_and_a_chats_agent_only_reads(self):
        chat_id = self.new("Tidy the fixtures")
        alt = [sys.executable, str(REPO / "bin" / "alt"), "--project", self.project]
        env = {k: v for k, v in os.environ.items() if k != "ALTITUDE_ACTOR"}
        listed = subprocess.run([*alt, "chats", "list", "--json"], capture_output=True, text=True, env=env)
        self.assertEqual([row["id"] for row in json.loads(listed.stdout)], [chat_id], listed.stderr)
        shown = subprocess.run([*alt, "chats", "show", "tidy"], capture_output=True, text=True, env=env)
        self.assertIn("You, ", shown.stdout, shown.stderr)
        self.assertIn("Tidy the fixtures", shown.stdout)
        for actor in ("l3", "l2", "chat", "project-agent-run"):
            refused = subprocess.run([*alt, "chats", "list"], capture_output=True, text=True,
                                     env={**env, "ALTITUDE_ACTOR": actor})
            self.assertNotEqual(refused.returncode, 0, actor)
            self.assertNotIn(chat_id, refused.stdout, actor)
        changing = subprocess.run([*alt, "task", "new", "--title", "x"], capture_output=True, text=True,
                                  env={**env, "ALTITUDE_ACTOR": "chat"}, input="brief")
        self.assertIn("not available in an agent chat", changing.stderr)
