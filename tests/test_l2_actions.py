"""The contained Codex L2 broker fences ownership and preserves held actions without another model turn."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(tempfile.mkdtemp(prefix="altitude-l2-actions-"))
os.environ["ALTITUDE_HOME"] = str(_ROOT / "state")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import actions, config, dispatch, engines, recovery, state as S, tasks as T  # noqa: E402


class TestL2Actions(unittest.TestCase):
    def setUp(self):
        self.repo = _ROOT / "repo"
        self.repo.mkdir(exist_ok=True)
        config.ensure_root()
        config.save_projects({"p": {"name": "p", "path": str(self.repo)}})
        for directory in (S.tasks_dir("p"), S.archive_dir("p")):
            if directory.exists():
                for path in sorted(directory.rglob("*"), reverse=True):
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()

    def task(self, title="Codex task"):
        task = T.new("p", title, "request")
        task.update({"state": "running", "dispatch_id": f"{task['slug']}-1", "session_id": "thread-1",
                     "agent_id": "worker-1", "l2_engine": "codex", "l2_token": "token-1",
                     "attempt": 1, "worktree": str(self.repo)})
        S.save_task("p", task)
        return task

    @staticmethod
    def item(task, action):
        return {"task": task, "agent": {"id": task["agent_id"], "action": action}, "action": action}

    def test_missing_action_is_a_schema_error(self):
        task = self.task()
        with mock.patch.object(engines, "codex_containment_empty", return_value=True):
            with self.assertRaisesRegex(actions.ActionError, "schema-valid action"):
                actions.process_l2("p", {"task": task, "agent": {"id": "worker-1"}})

    def test_recovery_held_action_executes_after_clear_without_a_model_resume(self):
        task = self.task()
        action = {"action": "block", "blocked_reason": "needs a user decision", "message": "I need input"}
        with mock.patch.object(engines, "codex_containment_empty", return_value=True), \
             mock.patch.object(recovery, "dispatch_hold", side_effect=["recovery fuse", None]), \
             mock.patch.object(dispatch, "resume_session") as resume:
            first = actions.process_l2("p", self.item(task, action))
            held = S.load_task("p", task["slug"])
            second = actions.process_l2("p", self.item(held, action))

        self.assertEqual(first["kind"], "pending")
        self.assertEqual(second["kind"], "blocked")
        self.assertEqual(S.load_task("p", task["slug"])["blocked_reason"], "needs a user decision")
        self.assertNotIn("pending_action", S.load_task("p", task["slug"]))
        resume.assert_not_called()

    def test_concurrent_replacement_fences_a_held_action(self):
        task = self.task()
        action = {"action": "block", "blocked_reason": "old action"}
        with mock.patch.object(engines, "codex_containment_empty", return_value=True), \
             mock.patch.object(recovery, "dispatch_hold", return_value="recovery fuse"):
            actions.process_l2("p", self.item(task, action))
        held = S.load_task("p", task["slug"])
        held["agent_id"] = "replacement-worker"
        S.save_task("p", held)
        with mock.patch.object(engines, "codex_containment_empty", return_value=True), \
             mock.patch.object(recovery, "dispatch_hold", return_value=None):
            with self.assertRaisesRegex(actions.ActionError, "ownership changed"):
                actions.process_l2("p", self.item(task, action))
        self.assertEqual(S.load_task("p", task["slug"])["state"], "blocked")

    def test_helper_request_is_dormant_before_any_artifact_or_resume(self):
        task = self.task()
        action = {"action": "request_helpers", "helpers": [{
            "role": "implementer", "brief": "change x", "engine": "codex", "model": None,
            "paths": ["x"],
        }]}
        with mock.patch.object(S, "atomic_write") as write, \
             mock.patch.object(dispatch, "resume_session") as resume, \
             self.assertRaisesRegex(actions.ActionError, "await.*Phase 1B.3"):
            actions._helpers("p", task, action)  # noqa: SLF001 -- broker contract test
        write.assert_not_called()
        resume.assert_not_called()
        self.assertFalse((S.task_dir("p", task["slug"]) / "l1").exists())

        before = {path.relative_to(config.ROOT): path.read_bytes()
                  for path in config.ROOT.rglob("*") if path.is_file()}
        with mock.patch.object(actions, "_require_contained_exit") as contained, \
             self.assertRaisesRegex(actions.ActionError, "await.*Phase 1B.3"):
            actions.process_l2("p", self.item(task, action))
        contained.assert_not_called()
        after = {path.relative_to(config.ROOT): path.read_bytes()
                 for path in config.ROOT.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_post_stop_recovery_race_keeps_exact_prompt_and_drops_old_action(self):
        task = self.task()
        identity = actions._identity(task)  # noqa: SLF001 -- exact pending record fixture
        task["pending_action"] = {"action": {"action": "continue", "continue_reason": "finish"},
                                  "identity": identity, "claimed": S.now(), "message_posted": False}
        S.save_task("p", task)

        def defer(_project, slug, prompt, **_expected):
            live = S.load_task("p", slug)
            live.update({"state": "blocked", "resume_exact_prompt": True, "resume_answer": prompt,
                         "blocked_reason": "waiting: recovery hold"})
            S.save_task("p", live)
            raise T.TransitionError("recovery hold")

        with mock.patch.object(dispatch, "resume_session", side_effect=defer):
            result = actions._resume_same("p", task, "exact correction")  # noqa: SLF001
        live = S.load_task("p", task["slug"])
        self.assertEqual(result["kind"], "pending")
        self.assertEqual(live["resume_answer"], "exact correction")
        self.assertNotIn("pending_action", live)

    def test_merged_publish_settles_self_deploy_before_retry_resume(self):
        task = self.task("post merge settlement")
        action = {"action": "publish", "commit_message": "fix: settle merge", "merge": True}
        phase = {"merged": False, "settled": False}

        def merged_then_retry(*_args, **_kwargs):
            phase["merged"] = True
            raise actions.land.LandError("the pinned base/head moved after GitHub accepted the merge")

        def settle(_project, _task):
            self.assertTrue(phase["merged"])
            phase["settled"] = True
            return ["self-deploy settled"]

        def resume(_project, _slug, _prompt, **_expected):
            self.assertTrue(phase["settled"], "retry must not reach provenance before self-deploy settles")
            return {"agent": {"id": "worker-2"}}

        with mock.patch.object(engines, "codex_containment_empty", return_value=True), \
             mock.patch.object(recovery, "dispatch_hold", return_value=None), \
             mock.patch.object(actions.land, "land", side_effect=merged_then_retry), \
             mock.patch.object(dispatch, "pull_after_done", side_effect=settle) as pulled, \
             mock.patch.object(dispatch, "resume_session", side_effect=resume):
            result = actions.process_l2("p", self.item(task, action))

        self.assertEqual(result["kind"], "resumed")
        pulled.assert_called_once_with("p", mock.ANY)
        self.assertNotIn("pending_action", S.load_task("p", task["slug"]))

    def test_provenance_gate_waits_for_publication_settlement(self):
        entered = threading.Event()
        release = threading.Event()
        passed = threading.Event()

        def publisher():
            with dispatch.publication_settlement("p"):
                entered.set()
                release.wait(2)

        def provenance():
            entered.wait(2)
            with dispatch.publication_settlement("p"):
                passed.set()

        publishing = threading.Thread(target=publisher)
        checking = threading.Thread(target=provenance)
        publishing.start()
        checking.start()
        self.assertTrue(entered.wait(2))
        self.assertFalse(passed.wait(.05), "provenance observed the merge-to-fast-forward interval")
        release.set()
        publishing.join(2)
        checking.join(2)
        self.assertTrue(passed.is_set())


if __name__ == "__main__":
    unittest.main()
