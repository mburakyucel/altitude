"""Codex L3 actions are one-at-a-time, owner-fenced, and durably idempotent."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(tempfile.mkdtemp(prefix="altitude-l3-actions-"))
os.environ["ALTITUDE_HOME"] = str(_ROOT / "state")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, engines, l3_actions, state as S, tasks as T  # noqa: E402


def envelope(action):
    return {"message": "handled", "actions": [action]}


class TestL3Actions(unittest.TestCase):
    def setUp(self):
        self.repo = _ROOT / "repo"
        self.repo.mkdir(exist_ok=True)
        config.ensure_root()
        config.save_projects({"p": {"name": "p", "path": str(self.repo)}})
        for directory in (S.tasks_dir("p"), S.archive_dir("p"), config.project_dir("p") / "l3-actions"):
            if directory.exists():
                for path in sorted(directory.rglob("*"), reverse=True):
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()

    def task(self, title="Task", state="running", **updates):
        task = T.new("p", title, "request")
        task.update({"state": state, "dispatch_id": f"{task['slug']}-1", "session_id": "session-1",
                     "agent_id": "agent-1", "l2_engine": "claude", **updates})
        S.save_task("p", task)
        return task

    def test_new_task_accepts_the_live_codex_brief_in_text(self):
        brief = "Implement issue #127 as one focused documentation change."
        action = {
            "type": "new_task", "slug": "implement-github-issue-127",
            "title": "Implement GitHub issue #127", "text": brief, "request": None,
            "source": "chat", "engine": "codex", "model": None, "paths": [],
            "reason": None, "digest": None, "answer": None, "hold_merge": None,
            "merge_hold": None, "incident": None, "labels": [],
        }

        source = "Please implement GitHub issue #127 now."
        result = l3_actions.apply("p", envelope(action), action_id="0" * 64,
                                  github_issue_source=source)
        task = S.load_task("p", result[0]["slug"])

        self.assertEqual(task["state"], "queued")
        self.assertEqual(task["engine"], "codex")
        self.assertEqual((S.task_dir("p", task["slug"]) / "request.md").read_text(), brief + "\n")

    def test_new_task_prefers_request_and_rejects_two_empty_brief_fields(self):
        explicit = {"type": "new_task", "title": "Explicit", "request": "canonical brief",
                    "text": "fallback brief", "source": "chat"}
        result = l3_actions.apply("p", envelope(explicit), action_id="a" * 64)
        slug = result[0]["slug"]
        self.assertEqual((S.task_dir("p", slug) / "request.md").read_text(), "canonical brief\n")

        empty = {"type": "new_task", "title": "Empty", "request": None, "text": "  "}
        with self.assertRaisesRegex(l3_actions.L3ActionError, "requires request"):
            l3_actions.apply("p", envelope(empty), action_id="b" * 64)
        self.assertFalse(S.task_dir("p", "empty").exists())
        self.assertFalse((config.project_dir("p") / "l3-actions" / ("b" * 64 + ".json")).exists())

    def test_new_issue_task_must_be_authorized_by_the_current_user_message(self):
        action = {"type": "new_task", "title": "Implement GitHub issue #127",
                  "request": "Implement issue #127 as one focused documentation change."}
        with self.assertRaisesRegex(l3_actions.L3ActionError, "not authorized"):
            l3_actions.apply("p", envelope(action), action_id="4" * 64,
                             github_issue_source="Please improve the documentation")
        self.assertFalse(S.task_dir("p", "implement-github-issue-127").exists())

    def test_one_turn_cannot_apply_multiple_actions(self):
        value = {"message": "too many", "actions": [
            {"type": "task_fyi", "slug": "a", "text": "one"},
            {"type": "task_fyi", "slug": "b", "text": "two"},
        ]}
        with self.assertRaisesRegex(l3_actions.L3ActionError, "at most one"):
            l3_actions.apply("p", value, action_id="a" * 64)
        self.assertEqual(list((config.project_dir("p") / "l3-actions").glob("*.json")), [])

    def test_completed_action_replays_its_result_without_repeating_side_effect(self):
        task = self.task(state="blocked")
        value = envelope({"type": "task_fyi", "slug": task["slug"], "text": "one durable note"})
        first = l3_actions.apply("p", value, action_id="b" * 64)
        second = l3_actions.apply("p", value, action_id="b" * 64)
        self.assertEqual(first, second)
        lines = (config.project_dir("p") / "inbox.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)

    def test_l3_never_blocks_or_completes_a_live_worker(self):
        task = self.task()
        live = [{"id": "agent-1", "state": "working", "status": "busy"}]
        with mock.patch.object(engines, "claude_agents", return_value=live):
            with self.assertRaisesRegex(l3_actions.L3ActionError, "cannot block a live"):
                l3_actions.apply("p", envelope({"type": "task_block", "slug": task["slug"],
                                                "reason": "stop"}), action_id="c" * 64)
        self.assertEqual(S.load_task("p", task["slug"])["state"], "running")

        task["state"] = "reported"
        S.save_task("p", task)
        with mock.patch.object(engines, "claude_agents", return_value=live):
            with self.assertRaisesRegex(l3_actions.L3ActionError, "no live L2"):
                l3_actions.apply("p", envelope({"type": "task_done", "slug": task["slug"],
                                                "digest": "done"}), action_id="d" * 64)
        self.assertEqual(S.load_task("p", task["slug"])["state"], "reported")

    def test_missing_codex_worker_record_fails_closed(self):
        task = self.task(state="reported", l2_engine="codex")
        with mock.patch.object(engines, "codex_worker", return_value=None):
            with self.assertRaisesRegex(l3_actions.L3ActionError, "worker record is missing"):
                l3_actions.apply("p", envelope({"type": "task_done", "slug": task["slug"],
                                                "digest": "done"}), action_id="9" * 64)
        self.assertEqual(S.load_task("p", task["slug"])["state"], "reported")

    def test_resume_requires_and_fences_the_exact_blocked_session(self):
        task = self.task(state="blocked")
        with mock.patch.object(dispatch, "resume_blocked", return_value={"deferred": False}) as resume:
            result = l3_actions.apply(
                "p", envelope({"type": "task_resume", "slug": task["slug"], "answer": "continue"}),
                action_id="e" * 64,
            )
        self.assertFalse(result[0]["deferred"])
        resume.assert_called_once_with(
            "p", task["slug"], "continue", prefix="L3: ", expected_state="blocked",
            expected_dispatch_id=task["dispatch_id"], expected_session_id="session-1", expected_agent_id="agent-1",
        )

        missing = self.task(title="No session", state="blocked", session_id=None, agent_id=None)
        with mock.patch.object(T, "resume") as unsafe_resume:
            with self.assertRaisesRegex(l3_actions.L3ActionError, "no exact blocked L2 session"):
                l3_actions.apply(
                    "p", envelope({"type": "task_resume", "slug": missing["slug"], "answer": "continue"}),
                    action_id="f" * 64,
                )
        unsafe_resume.assert_not_called()

    def test_resume_persists_journaled_paths_before_dispatch(self):
        task = self.task(state="blocked", paths=[])
        action = {"type": "task_resume", "slug": task["slug"], "answer": "continue",
                  "paths": ["altitude/l3_actions.py", "tests/test_l3_actions.py"]}

        def resume(*_args, **_kwargs):
            self.assertEqual(S.load_task("p", task["slug"])["paths"], action["paths"])
            return {"deferred": False}

        with mock.patch.object(dispatch, "resume_blocked", side_effect=resume):
            l3_actions.apply("p", envelope(action), action_id="0" * 63 + "1")

        self.assertEqual(S.load_task("p", task["slug"])["paths"], action["paths"])

    def test_resume_paths_refuse_collision_without_mutating_or_dispatching(self):
        task = self.task(state="blocked", paths=["existing.py"])
        self.task(title="Holder", state="running", paths=["src/shared.py"])
        action = {"type": "task_resume", "slug": task["slug"], "answer": "continue",
                  "paths": ["src/shared.py"]}

        with mock.patch.object(dispatch, "resume_blocked") as resume:
            with self.assertRaisesRegex(l3_actions.L3ActionError, "file lease"):
                l3_actions.apply("p", envelope(action), action_id="0" * 63 + "2")

        resume.assert_not_called()
        self.assertEqual(S.load_task("p", task["slug"])["paths"], ["existing.py"])

    def test_resume_paths_roll_back_when_exact_resume_is_refused(self):
        task = self.task(state="blocked", paths=["existing.py"])
        action = {"type": "task_resume", "slug": task["slug"], "answer": "continue",
                  "paths": ["src/new.py"]}

        with mock.patch.object(dispatch, "resume_blocked",
                               side_effect=T.TransitionError("session changed")):
            with self.assertRaisesRegex(l3_actions.L3ActionError, "session changed"):
                l3_actions.apply("p", envelope(action), action_id="0" * 63 + "3")

        self.assertEqual(S.load_task("p", task["slug"])["paths"], ["existing.py"])

    def test_resume_paths_preserve_the_existing_task_lease(self):
        task = self.task(state="blocked", paths=["existing.py"])
        action = {"type": "task_resume", "slug": task["slug"], "paths": ["src/new.py"]}

        with mock.patch.object(dispatch, "resume_blocked", return_value={"deferred": False}):
            l3_actions.apply("p", envelope(action), action_id="0" * 63 + "7")

        self.assertEqual(S.load_task("p", task["slug"])["paths"],
                         ["existing.py", "src/new.py"])

    def test_resume_paths_reject_non_relative_entries(self):
        task = self.task(state="blocked", paths=[])
        for index, invalid in enumerate(("../outside.py", "/absolute.py", "src//empty.py"), 4):
            action = {"type": "task_resume", "slug": task["slug"], "paths": [invalid]}
            with mock.patch.object(dispatch, "resume_blocked") as resume:
                with self.assertRaisesRegex(l3_actions.L3ActionError, "invalid repo-relative path"):
                    l3_actions.apply("p", envelope(action), action_id="0" * 63 + str(index))
            resume.assert_not_called()
            self.assertEqual(S.load_task("p", task["slug"])["paths"], [])

    def test_github_issue_content_marker_prevents_duplicate_across_turns(self):
        created_body = []

        def run(args, **_kwargs):
            if args[:4] == ["gh", "issue", "list", "--state"]:
                rows = [{"url": "https://example.test/1", "body": created_body[0]}] if created_body else []
                return subprocess.CompletedProcess(args, 0, json.dumps(rows), "")
            self.assertEqual(args[:3], ["gh", "issue", "create"])
            created_body.append(args[args.index("--body") + 1])
            return subprocess.CompletedProcess(args, 0, "https://example.test/1\n", "")

        action = {"type": "github_issue", "title": "Architecture note", "text": "Preserve this idea", "labels": []}
        source = "Please Preserve this idea as an Architecture note"
        action["text"] = source
        draft = l3_actions.apply("p", envelope(action), action_id="1" * 64, github_issue_source=source)
        key = draft[0]["id"]
        approval_source = f"approve github issue publication {key}"
        approval = {"type": "github_issue_approve", "digest": key}
        with mock.patch.object(l3_actions.subprocess, "run", side_effect=run) as process:
            first = l3_actions.apply("p", envelope(approval), action_id="2" * 64,
                                     github_issue_source=approval_source)
            second = l3_actions.apply("p", envelope(approval), action_id="3" * 64,
                                      github_issue_source=approval_source)
        self.assertTrue(draft[0]["pending_review"])
        self.assertFalse(first[0]["reused"])
        self.assertTrue(second[0]["reused"])
        self.assertEqual(sum(call.args[0][:3] == ["gh", "issue", "create"] for call in process.call_args_list), 1)

    def test_github_issue_cannot_publish_hidden_or_security_sensitive_context(self):
        safe_source = "Please preserve the sidecar transcript audit idea"
        hidden = {"type": "github_issue", "title": "sidecar transcript audit idea",
                  "text": safe_source + "\nprivate model-added detail", "labels": []}
        with mock.patch.object(l3_actions.subprocess, "run") as process:
            with self.assertRaisesRegex(l3_actions.L3ActionError, "exact current user message"):
                l3_actions.apply("p", envelope(hidden), action_id="7" * 64,
                                 github_issue_source=safe_source)
        process.assert_not_called()

        sensitive = "Please create a credential issue for API key: sk-abcdefghijklmnopqrstuvwxyz123456"
        action = {"type": "github_issue", "title": "credential issue", "text": sensitive, "labels": []}
        draft = l3_actions.apply("p", envelope(action), action_id="8" * 64,
                                 github_issue_source=sensitive)
        key = draft[0]["id"]
        approval = {"type": "github_issue_approve", "digest": key}
        with mock.patch.object(l3_actions.subprocess, "run") as process:
            with self.assertRaisesRegex(l3_actions.L3ActionError, "may contain a secret"):
                l3_actions.apply("p", envelope(approval), action_id="6" * 64,
                                 github_issue_source=f"approve github issue publication {key}")
        process.assert_not_called()

    def test_security_mechanism_issue_is_private_until_exact_human_approval(self):
        source = "the sandbox lets a worker stop Altitude through the user bus"
        action = {"type": "github_issue", "title": "sandbox lets a worker stop Altitude",
                  "text": source, "labels": []}
        with mock.patch.object(l3_actions.subprocess, "run") as process:
            result = l3_actions.apply("p", envelope(action), action_id="5" * 64,
                                      github_issue_source=source)
        process.assert_not_called()
        self.assertTrue(result[0]["pending_review"])

    def test_common_secret_formats_remain_hard_refused_after_approval(self):
        examples = [
            "xoxb-123456789012-123456789012-abcdefghijklmnopqrstuvwx",
            "AIzaSyD-abcdefghijklmnopqrstuvwxyz1234567",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrstuvwxyz123456",
        ]
        for index, secret in enumerate(examples):
            source = f"Please create token example issue {secret}"
            action = {"type": "github_issue", "title": "token example issue", "text": source, "labels": []}
            draft = l3_actions.apply("p", envelope(action), action_id=f"{index + 1:x}" * 64,
                                     github_issue_source=source)
            key = draft[0]["id"]
            approval = {"type": "github_issue_approve", "digest": key}
            with mock.patch.object(l3_actions.subprocess, "run") as process:
                with self.assertRaisesRegex(l3_actions.L3ActionError, "may contain a secret"):
                    l3_actions.apply("p", envelope(approval), action_id=f"{index + 10:x}" * 64,
                                     github_issue_source=f"approve github issue publication {key}")
            process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
