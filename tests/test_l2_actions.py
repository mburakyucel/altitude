"""The contained Codex L2 broker fences ownership before it applies an action."""
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
from altitude import actions, config, dispatch, engines, l1, state as S, tasks as T  # noqa: E402


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
        task.update({"state": "running", "attempt": 1, "session_id": "thread-1", "agent_id": "worker-1",
                     "l2_engine": "codex", "worktree": str(self.repo)})
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

    def test_helper_patch_is_inlined_for_the_contained_owner(self):
        task = self.task()
        patch_dir = S.task_dir("p", task["slug"]) / "l1"
        patch_dir.mkdir(parents=True)
        patch_path = patch_dir / "helper-1-1.patch"
        patch_path.write_text("diff --git a/x b/x\n+new line\n")
        action = {"action": "request_helpers", "helpers": [{
            "role": "implementer", "brief": "change x", "engine": "codex", "model": None,
            "paths": ["x"],
        }]}
        record = {"name": "helper-1-1"}
        result = {"name": "helper-1-1", "role": "implementer", "engine": "codex",
                  "patch": str(patch_path), "summary": "ready", "error": None}
        with mock.patch.object(l1, "load", return_value=None), \
             mock.patch.object(l1, "start", return_value=record) as start, \
             mock.patch.object(l1, "wait", return_value={"run": result}):
            handled = actions._helpers("p", task, action)  # noqa: SLF001 -- broker contract test
        self.assertEqual(handled["kind"], "pending")
        self.assertEqual(start.call_args.kwargs["expected_attempt"], 1)
        held = S.load_task("p", task["slug"])
        self.assertEqual((held["state"], held["blocked_reason"]), ("blocked", "between Codex turns"))
        self.assertTrue(held["resume_after"])
        [pending] = T.pending("p", task["slug"])
        prompt = pending["text"]
        self.assertIn("diff --git a/x b/x", prompt)
        self.assertNotIn(str(patch_path), prompt)
        self.assertIn("No patch was auto-applied", prompt)

    def test_merged_publish_settles_self_deploy_before_the_retry_turn(self):
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

        def next_turn(_project, _task, prompt):
            self.assertTrue(phase["settled"], "the retry turn must not be queued before self-deploy settles")
            self.assertIn("Trusted publish action was refused", prompt)
            return {"kind": "pending"}

        with mock.patch.object(engines, "codex_containment_empty", return_value=True), \
             mock.patch.object(actions.land, "land", side_effect=merged_then_retry), \
             mock.patch.object(dispatch, "pull_after_done", side_effect=settle) as pulled, \
             mock.patch.object(actions, "next_turn", side_effect=next_turn) as retry:
            result = actions.process_l2("p", self.item(task, action))

        self.assertEqual(result["kind"], "pending")
        pulled.assert_called_once_with("p", mock.ANY)
        retry.assert_called_once()
        self.assertEqual(S.read_events("p", task["slug"])[-1]["kind"], "l2-action-refused")

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
