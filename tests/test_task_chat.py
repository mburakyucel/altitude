"""The task page is a durable, two-sided conversation with the exact current L2."""
import contextlib
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="altitude-task-chat-"))
os.environ["ALTITUDE_HOME"] = str(_TMP / "home")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, engines, server, state as S, tasks as T  # noqa: E402


class TestTaskConversation(unittest.TestCase):
    _number = 0

    @classmethod
    def setUpClass(cls):
        config.ensure_root()

    def setUp(self):
        type(self)._number += 1
        self.project = f"task-chat-{self._number}"
        self.repo = _TMP / self.project / "repo"
        self.repo.mkdir(parents=True)
        projects = config.load_projects()
        projects[self.project] = {"name": self.project, "path": str(self.repo), "stacks": []}
        config.save_projects(projects)
        task = T.new(self.project, "Direct conversation", "Build the focused change.")
        self.slug = task["slug"]
        self.worktree = self.repo / ".claude" / "worktrees" / self.slug
        self.worktree.mkdir(parents=True)
        task.update({
            "state": "running",
            "dispatch_id": f"{self.slug}-1",
            "session_id": "session-old",
            "agent_id": "agent-old",
            "worktree": str(self.worktree),
            "branch": f"worktree-{self.slug}",
        })
        S.save_task(self.project, task)

    def test_burak_message_is_durable_and_fenced_to_the_snapshot_it_resumes(self):
        resumed = {"stdout": "", "stderr": "", "agent": {"id": "agent-new"}}
        with mock.patch.object(dispatch, "_resume_session_locked", return_value=resumed) as resume:
            result = dispatch.message_l2(self.project, self.slug, "Prefer the smaller diff.")

        history = T.task_messages(self.project, self.slug)
        self.assertEqual([(item["role"], item["text"]) for item in history],
                         [("burak", "Prefer the smaller diff.")])
        self.assertEqual(result["message"]["id"], history[0]["id"])
        self.assertEqual(resume.call_args.args[:3],
                         (self.project, self.slug, "Prefer the smaller diff."))
        self.assertEqual(resume.call_args.kwargs, {
            "expected_dispatch_id": f"{self.slug}-1",
            "expected_session_id": "session-old",
            "expected_agent_id": "agent-old",
            "expected_state": "running",
        })

    def test_l2_reply_and_task_api_show_both_sides_without_qa_log(self):
        T.append_task_message(self.project, self.slug, "burak", "Can we keep this small?",
                              expected_dispatch_id=f"{self.slug}-1", actor="burak")
        T.append_task_message(self.project, self.slug, "l2", "Yes. I will keep one focused PR.",
                              expected_dispatch_id=f"{self.slug}-1", actor="l2")
        (S.task_dir(self.project, self.slug) / "qa.md").write_text("legacy log dump\n")

        with mock.patch.object(server.monitor, "sessions", return_value=[]):
            view = server.task_view(self.project, self.slug)

        self.assertEqual([message["role"] for message in view["messages"]], ["burak", "l2"])
        self.assertEqual([message["text"] for message in view["messages"]],
                         ["Can we keep this small?", "Yes. I will keep one focused PR."])
        self.assertNotIn("qa", view["files"])

    def test_terminal_or_replaced_dispatch_cannot_append(self):
        task = S.load_task(self.project, self.slug)
        task["dispatch_id"] = f"{self.slug}-2"
        S.save_task(self.project, task)
        with self.assertRaisesRegex(T.TransitionError, "dispatch changed"):
            T.append_task_message(self.project, self.slug, "l2", "stale reply",
                                  expected_dispatch_id=f"{self.slug}-1", actor="l2")
        self.assertEqual(T.task_messages(self.project, self.slug), [])

        task = S.load_task(self.project, self.slug)
        task["state"] = "done"
        S.save_task(self.project, task)
        with mock.patch.object(dispatch, "_resume_session_locked") as resume:
            with self.assertRaisesRegex(T.TransitionError, "running or blocked"):
                dispatch.message_l2(self.project, self.slug, "late steering")
        resume.assert_not_called()
        self.assertEqual(T.task_messages(self.project, self.slug), [])

    def test_blocked_message_uses_the_blocked_resume_path(self):
        task = S.load_task(self.project, self.slug)
        task["state"] = "blocked"
        task["blocked_reason"] = "Need one decision."
        S.save_task(self.project, task)
        resumed = {"stdout": "", "stderr": "", "deferred": True}
        with mock.patch.object(dispatch, "_resume_blocked_locked", return_value=resumed) as resume:
            result = dispatch.message_l2(self.project, self.slug, "Use the existing API.")

        self.assertTrue(result["deferred"])
        self.assertEqual(resume.call_args.args[:3],
                         (self.project, self.slug, "Use the existing API."))
        self.assertEqual(T.task_messages(self.project, self.slug)[0]["role"], "burak")

    def test_current_l2_cli_can_reply_but_a_human_shell_cannot_impersonate_it(self):
        cli = Path(__file__).resolve().parent.parent / "bin" / "alt"
        old = {name: os.environ.get(name) for name in (
            "ALTITUDE_ACTOR", "ALTITUDE_PROJECT", "ALTITUDE_TASK", "ALTITUDE_DISPATCH_ID")}
        os.environ.update({
            "ALTITUDE_ACTOR": "l2",
            "ALTITUDE_PROJECT": self.project,
            "ALTITUDE_TASK": self.slug,
            "ALTITUDE_DISPATCH_ID": f"{self.slug}-1",
        })
        try:
            namespace = runpy.run_path(str(cli))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                namespace["main"](["task", "reply", "I can implement this directly."])
            payload = json.loads(output.getvalue())
        finally:
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertEqual(payload["role"], "l2")
        self.assertEqual(T.task_messages(self.project, self.slug)[0]["text"],
                         "I can implement this directly.")

        os.environ["ALTITUDE_ACTOR"] = "burak"
        try:
            namespace = runpy.run_path(str(cli))
            with self.assertRaisesRegex(SystemExit, "only the current L2"):
                namespace["main"](["--project", self.project, "task", "reply", "forged"])
        finally:
            if old["ALTITUDE_ACTOR"] is None:
                os.environ.pop("ALTITUDE_ACTOR", None)
            else:
                os.environ["ALTITUDE_ACTOR"] = old["ALTITUDE_ACTOR"]

    def test_task_conversation_corruption_is_not_silently_dropped(self):
        path = S.task_dir(self.project, self.slug) / "conversation.jsonl"
        path.write_text("{not json\n")
        with self.assertRaisesRegex(ValueError, "corrupt task conversation"):
            T.task_messages(self.project, self.slug)


class TestResumeGenerationFence(unittest.TestCase):
    def test_task_change_during_resume_stops_the_replacement_worker(self):
        project = "task-chat-resume-race"
        repo = _TMP / project / "repo"
        worktree = repo / ".claude" / "worktrees" / "resume-race"
        worktree.mkdir(parents=True, exist_ok=True)
        projects = config.load_projects()
        projects[project] = {"name": project, "path": str(repo), "stacks": []}
        config.save_projects(projects)
        S.save_task(project, {
            "slug": "resume-race", "title": "race", "state": "running",
            "dispatch_id": "resume-race-1", "session_id": "session-old", "agent_id": "agent-old",
            "worktree": str(worktree), "created": S.now(),
        })

        def launch(*_args, **_kwargs):
            T.park(project, "resume-race", "cancelled while resuming")
            return {"stdout": "", "stderr": "", "returncode": 0}

        rows = [{"id": "agent-new", "sessionId": "session-new",
                 "name": f"{project}/resume-race-1", "state": "working", "startedAt": 2}]
        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree"), \
             mock.patch.object(engines, "claude_resume_bg", side_effect=launch), \
             mock.patch.object(engines, "claude_agents", return_value=rows), \
             mock.patch.object(engines, "claude_rm", return_value="removed"), \
             mock.patch.object(engines, "claude_stop", return_value="stopped") as stop:
            with self.assertRaisesRegex(T.TransitionError, "generation changed during resume"):
                dispatch.resume_session(project, "resume-race", "continue")

        stop.assert_called_once_with("agent-new")
        self.assertEqual(S.load_task(project, "resume-race")["state"], "parked")


if __name__ == "__main__":
    unittest.main()
