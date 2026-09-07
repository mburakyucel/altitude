"""Temporary model capacity backs off one L2 without rerouting it or raising a system fault."""
import unittest
from datetime import datetime, timezone
from unittest import mock

from tests.support import AltitudeCase
from altitude import dispatch, engines, incidents, server, state as S


class TestTemporaryCapacity(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.quiet_engines()
        self.private_ledgers()
        self.register(self.project, wip=4)

    def _task(self, slug: str = "same-model") -> dict:
        S.task_dir(self.project, slug).mkdir(parents=True, exist_ok=True)
        task = {
            "slug": slug, "title": slug, "request": "continue", "state": "running",
            "created": S.now(), "updated": S.now(), "attempt": 1,
            "session_id": f"session-{slug}", "agent_id": f"agent-{slug}",
            "l2_engine": "codex", "engine_model": "gpt-test-stable",
        }
        S.save_task(self.project, task)
        return task

    def test_detector_requires_the_exact_provider_warning(self):
        warning = engines.TEMPORARY_CAPACITY_TEXT
        self.assertTrue(engines.temporary_capacity_in(warning))
        self.assertTrue(engines.temporary_capacity_in(f"provider: {warning}\n"))
        self.assertFalse(engines.temporary_capacity_in("The model may be at capacity; switch models."))
        self.assertFalse(engines.temporary_capacity_in(None))

    def test_poll_classifies_capacity_before_a_failed_worker_as_died(self):
        task = self._task("capacity-poll")
        worker = {
            "id": task["agent_id"], "sessionId": task["session_id"], "state": "failed", "status": "exited",
            "detail": engines.TEMPORARY_CAPACITY_TEXT,
        }
        with mock.patch.object(S, "list_tasks", return_value=[task]), \
                mock.patch.object(engines, "codex_worker", return_value=worker):
            out = dispatch.poll(self.project)

        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].get("capacity"))
        self.assertFalse(out[0].get("died", False))

    def test_codex_failed_quota_message_is_a_provider_hold_not_a_dead_worker(self):
        task = self._task("codex-quota-poll")
        worker = {
            "id": task["agent_id"], "sessionId": task["session_id"], "state": "failed", "status": "exited",
            "detail": "You've hit your usage limit · resets 8pm (America/Los_Angeles)",
        }
        with mock.patch.object(S, "list_tasks", return_value=[task]), \
                mock.patch.object(engines, "codex_worker", return_value=worker):
            out = dispatch.poll(self.project)

        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].get("limited"))
        self.assertFalse(out[0].get("died", False))
        with mock.patch.object(incidents, "system_fault") as fault, \
                mock.patch.object(engines, "note_usage_limit") as global_hold, \
                mock.patch.object(engines, "remove_l2_worker", return_value="removed") as removed:
            server.on_l2_finished(self.project, out[0])
        switched = S.load_task(self.project, task["slug"])
        self.assertEqual(switched["state"], "queued", "a fresh attempt on the other engine, from saved progress")
        self.assertEqual((switched["l2_engine"], switched["engine_model"], switched["agent_id"], switched["session_id"]),
                         (None, None, None, None))
        self.assertNotIn("resume_after", switched)
        removed.assert_called_once_with("codex", task["agent_id"],
                                        job_root=dispatch.l2_job_root(self.project, task["slug"]))
        global_hold.assert_not_called()
        fault.assert_not_called()

    def test_pinned_engine_parks_until_its_window_reopens(self):
        task = self._task("codex-quota-pinned")
        task["engine"] = "codex"
        S.save_task(self.project, task)
        worker = {"id": task["agent_id"], "sessionId": task["session_id"], "state": "failed", "status": "exited",
                  "detail": "You've hit your usage limit · resets 8pm (America/Los_Angeles)"}
        with mock.patch.object(S, "list_tasks", return_value=[task]), \
                mock.patch.object(engines, "codex_worker", return_value=worker):
            out = dispatch.poll(self.project)
        with mock.patch.object(incidents, "system_fault") as fault, \
                mock.patch.object(engines, "note_usage_limit") as global_hold:
            server.on_l2_finished(self.project, out[0])
        held = S.load_task(self.project, task["slug"])
        self.assertEqual(held["state"], "blocked")
        self.assertEqual((held["l2_engine"], held["engine_model"], held["agent_id"]),
                         ("codex", "gpt-test-stable", task["agent_id"]))
        self.assertEqual(held["resume_after"], out[0]["limited"])
        global_hold.assert_not_called()
        fault.assert_not_called()

    def test_server_retries_same_engine_and_model_with_per_task_backoff(self):
        task = self._task("capacity-backoff")
        item = {"task": task, "agent": {"id": task["agent_id"]}, "capacity": True}
        before = datetime.now(timezone.utc)

        with mock.patch.object(incidents, "system_fault") as fault, \
                mock.patch.object(server.verify, "verify") as verify:
            server.on_l2_finished(self.project, item)

        first = S.load_task(self.project, task["slug"])
        first_wait = (datetime.fromisoformat(first["resume_after"]) - before).total_seconds()
        self.assertEqual(first["state"], "blocked")
        self.assertEqual((first["l2_engine"], first["engine_model"]), ("codex", "gpt-test-stable"))
        self.assertEqual(first["capacity_retries"], 1)
        self.assertGreaterEqual(first_wait, 28)
        self.assertLessEqual(first_wait, 31)
        fault.assert_not_called()
        verify.assert_not_called()

        first["state"] = "running"
        first["updated"] = S.now()
        S.save_task(self.project, first)
        before_second = datetime.now(timezone.utc)
        with mock.patch.object(incidents, "system_fault") as fault, \
                mock.patch.object(server.verify, "verify") as verify:
            server.on_l2_finished(self.project, {**item, "task": first})

        second = S.load_task(self.project, task["slug"])
        second_wait = (datetime.fromisoformat(second["resume_after"]) - before_second).total_seconds()
        self.assertEqual((second["l2_engine"], second["engine_model"]), ("codex", "gpt-test-stable"))
        self.assertEqual(second["capacity_retries"], 2)
        self.assertGreaterEqual(second_wait, 58)
        self.assertLessEqual(second_wait, 61)
        fault.assert_not_called()
        verify.assert_not_called()

        second["resume_after"] = "1970-01-01T00:00:00+00:00"
        second["worktree"] = str(self.repo)
        S.save_task(self.project, second)
        seen = {}

        def resume(engine, name, session_id, prompt, **kwargs):
            seen.update(engine=engine, name=name, session_id=session_id, model=kwargs.get("model"))
            return {"returncode": 0, "stdout": "", "stderr": "", "agent": {
                "id": "replacement-worker", "sessionId": session_id, "state": "working", "startedAt": 2,
            }}

        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
                mock.patch.object(dispatch, "_validate_task_worktree"), \
                mock.patch.object(engines, "worker_live", return_value=False), \
                mock.patch.object(engines, "resume_l2", side_effect=resume), \
                mock.patch.object(dispatch.route, "pick_engine", side_effect=AssertionError("must not reroute")):
            self.assertEqual(dispatch.resume_due(self.project), [task["slug"]])
            resumed = dispatch.resume(self.project, task["slug"])

        self.assertEqual(resumed["agent"]["id"], "replacement-worker")
        self.assertEqual((seen["engine"], seen["name"], seen["model"]),
                         ("codex", f"{self.project}/{task['slug']}-1", "gpt-test-stable"))
        self.assertEqual(seen["session_id"], task["session_id"])
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")


if __name__ == "__main__":
    unittest.main()
