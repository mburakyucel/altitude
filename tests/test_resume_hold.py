"""Decision 39: a blocked task queued to resume keeps its file lease until it can run."""
import contextlib
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-resume-hold-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, engines, state as S, tasks as T  # noqa: E402
from altitude import monitor  # noqa: E402


class TestResumeHold(unittest.TestCase):
    _number = 0
    @classmethod
    def setUpClass(cls):
        cls._config_paths = {
            name: getattr(config, name)
            for name in ("ROOT", "PROJECTS_FILE", "MONITOR_DIR", "INCIDENT_INDEX", "DIGEST_FILE")
        }
        config.ROOT = _TMP
        config.PROJECTS_FILE = _TMP / "projects.json"
        config.MONITOR_DIR = _TMP / "monitor"
        config.INCIDENT_INDEX = _TMP / "incidents.jsonl"
        config.DIGEST_FILE = _TMP / "DIGEST.md"
        cls.addClassCleanup(cls._restore_config_paths)
        config.ensure_root()

    @classmethod
    def _restore_config_paths(cls):
        for name, value in cls._config_paths.items():
            setattr(config, name, value)


    def setUp(self):
        type(self)._number += 1
        self.project = f"resume-hold-{self._number}"
        self.repo = _TMP / self.project / "repo"
        self.repo.mkdir(parents=True)
        config.save_projects({self.project: {
            "name": self.project, "path": str(self.repo), "stacks": ["python"], "wip": 20,
        }})

        self.resumed = []
        self.stopped = []
        self.originals = (dispatch.resume_session, engines.claude_stop, engines.claude_agents,
                          engines.usage_hold, monitor.quota, monitor.quota_hold)
        dispatch.resume_session = self._resume_session
        engines.claude_stop = self.stopped.append
        engines.claude_agents = lambda: []
        engines.usage_hold = lambda: None
        monitor.quota = lambda: {"known": True}
        monitor.quota_hold = lambda: None

    def tearDown(self):
        (dispatch.resume_session, engines.claude_stop, engines.claude_agents,
         engines.usage_hold, monitor.quota, monitor.quota_hold) = self.originals

    def _resume_session(self, project, slug, text, session_id=None):
        self.resumed.append({"project": project, "slug": slug, "text": text, "session_id": session_id})
        return {"agent": {"id": f"new-{slug}"}, "stdout": ""}

    def _task(self, title, state, path, created):
        task = T.new(self.project, title, "S", "request", actor="l3", paths=[path])
        task.update({
            "state": state,
            "created": created,
            "dispatch_id": f"{task['slug']}-1",
            "session_id": f"session-{task['slug']}",
            "agent_id": f"agent-{task['slug']}",
            "worktree": str(self.repo),
        })
        S.save_task(self.project, task)
        return task

    def test_blocked_resume_waits_for_running_holder_and_records_answer(self):
        self._task("running holder", "running", "altitude/shared.py", "2026-01-01T00:00:00+00:00")
        blocked = self._task("blocked worker", "blocked", "altitude/shared.py", "2026-01-02T00:00:00+00:00")

        result = dispatch.resume_blocked(self.project, blocked["slug"], "Use the approved value.", prefix="Altitude: ")

        task = S.load_task(self.project, blocked["slug"])
        self.assertTrue(result["deferred"])
        self.assertEqual(task["state"], "blocked")
        self.assertIn("waiting for lease: `running-holder`", task["blocked_reason"])
        self.assertIn("altitude/shared.py", task["blocked_reason"])
        self.assertEqual(task["resume_answer"], "Use the approved value.")
        self.assertEqual(task["resume_prefix"], "Altitude: ")
        self.assertTrue(task["resume_after"])
        self.assertEqual(self.resumed, [])
        self.assertEqual(self.stopped, [])

    def test_due_resume_uses_stored_answer_after_holder_finishes(self):
        holder = self._task("active lease", "running", "bin/alt", "2026-01-01T00:00:00+00:00")
        blocked = self._task("waiting resume", "blocked", "bin/alt", "2026-01-02T00:00:00+00:00")
        blocked["blocked_reason"] = "Which envelope should I use?"
        S.save_task(self.project, blocked)
        dispatch.resume_blocked(self.project, blocked["slug"], "Keep the raised envelope.", prefix="Altitude: ")
        self.assertEqual(S.load_task(self.project, blocked["slug"])["blocked_question"],
                         "Which envelope should I use?")
        holder["state"] = "done"
        S.save_task(self.project, holder)

        resumed = dispatch.resume_due(self.project)

        task = S.load_task(self.project, blocked["slug"])
        self.assertEqual(resumed, [blocked["slug"]])
        self.assertEqual(task["state"], "running")
        self.assertEqual(self.resumed[0]["text"],
                         "Altitude: Keep the raised envelope.\nContinue from your progress file; finish to *done* and rewrite the report.")
        self.assertEqual(task["blocked_question"], "Which envelope should I use?")
        self.assertNotIn("resume_after", task)
        self.assertNotIn("resume_answer", task)
        self.assertNotIn("resume_prefix", task)

    def test_non_lease_resume_hold_uses_generic_waiting_wording(self):
        blocked = self._task("usage held", "blocked", "altitude/free.py", "2026-01-02T00:00:00+00:00")
        engines.usage_hold = lambda: "2026-01-03T00:00:00+00:00"

        result = dispatch.resume_blocked(self.project, blocked["slug"], "Continue later.")

        waiting = "waiting: usage limit: subscription window exhausted, resets 2026-01-03T00:00:00+00:00"
        self.assertEqual(result["waiting"], waiting)
        self.assertEqual(S.load_task(self.project, blocked["slug"])["blocked_reason"], waiting)

    def test_resume_without_overlap_reattaches_immediately(self):
        self._task("unrelated holder", "running", "altitude/other.py", "2026-01-01T00:00:00+00:00")
        blocked = self._task("free resume", "blocked", "altitude/free.py", "2026-01-02T00:00:00+00:00")

        result = dispatch.resume_blocked(self.project, blocked["slug"], "Continue now.")

        self.assertFalse(result["deferred"])
        self.assertEqual(S.load_task(self.project, blocked["slug"])["state"], "running")
        self.assertEqual([call["slug"] for call in self.resumed], [blocked["slug"]])
        # The real resume_session owns stop-before-replace; this test replaces
        # that function with a recorder, so resume_blocked must not double-stop.
        self.assertEqual(self.stopped, [])

    def test_only_blocked_task_with_pending_resume_holds_its_files(self):
        pending = self._task("pending lease", "blocked", "altitude/pending.py", "2026-01-01T00:00:00+00:00")
        pending["resume_after"] = "2026-01-01T00:00:00+00:00"
        S.save_task(self.project, pending)
        self._task("plain block", "blocked", "altitude/plain.py", "2026-01-01T00:00:00+00:00")
        pending_target = self._task("pending target", "approved", "altitude/pending.py", "2026-01-02T00:00:00+00:00")
        plain_target = self._task("plain target", "approved", "altitude/plain.py", "2026-01-02T00:00:00+00:00")

        leases = dispatch.leases(self.project)

        self.assertEqual(leases, [{"slug": pending["slug"], "paths": ["altitude/pending.py"], "pending_resume": True}])
        self.assertIn("blocked with a pending resume", dispatch.wip_hold(self.project, pending_target) or "")
        self.assertIsNone(dispatch.wip_hold(self.project, plain_target))

    def test_older_approved_candidate_is_held_by_pending_resume_lease(self):
        candidate = self._task("older candidate", "approved", "altitude/shared.py", "2026-01-01T00:00:00+00:00")
        pending = self._task("pending holder", "blocked", "altitude/shared.py", "2026-01-02T00:00:00+00:00")
        pending["resume_after"] = "2026-01-03T00:00:00+00:00"
        S.save_task(self.project, pending)

        hold = dispatch.wip_hold(self.project, candidate)

        self.assertIn(f"`{pending['slug']}` is blocked with a pending resume", hold or "")

    def test_overlapping_pending_resumes_choose_oldest_without_deadlock(self):
        oldest = self._task("oldest resume", "blocked", "altitude/shared.py", "2026-01-01T00:00:00+00:00")
        youngest = self._task("youngest resume", "blocked", "altitude/shared.py", "2026-01-02T00:00:00+00:00")
        for task in (oldest, youngest):
            task["resume_after"] = "2026-01-03T00:00:00+00:00"
            task["resume_answer"] = f"Resume {task['slug']}"
            S.save_task(self.project, task)

        self.assertIsNone(dispatch.wip_hold(self.project, oldest))
        self.assertIn(f"`{oldest['slug']}`", dispatch.wip_hold(self.project, youngest) or "")

        resumed = dispatch.resume_due(self.project)

        self.assertEqual(resumed, [oldest["slug"]])
        self.assertEqual(S.load_task(self.project, oldest["slug"])["state"], "running")
        self.assertEqual(S.load_task(self.project, youngest["slug"])["state"], "blocked")
        self.assertTrue(S.load_task(self.project, youngest["slug"])["resume_after"])

    def test_failed_recovery_never_adopts_unclaimed_terminal_report_and_caps(self):
        blocked = self._task("failed automatic recovery", "blocked", "altitude/recover.py",
                             "2026-01-01T00:00:00+00:00")
        blocked["blocked_reason"] = "lease: the prior holder is gone"
        blocked["l2_engine"] = "codex"
        S.save_task(self.project, blocked)
        S.write_json(S.task_dir(self.project, blocked["slug"]) / "report.json", {"blocked": "old report"})
        S.write_json(dispatch.codex_run_path(self.project, blocked["slug"]), {
            "project": self.project, "slug": blocked["slug"],
            "dispatch_id": blocked["dispatch_id"], "generation": "old-generation",
            "state": "done", "pid": None, "pid_start": None,
        })
        original = dispatch.resume_blocked
        calls = []

        def fail_before_pending(project, slug, answer, prefix=""):
            calls.append(slug)
            raise T.TransitionError("resume refused by Git provenance gate")

        dispatch.resume_blocked = fail_before_pending
        try:
            self.assertFalse(dispatch._claimed_worker_live(self.project, blocked))
            self.assertEqual(dispatch.resume_recoverable(self.project), [])
            self.assertEqual(dispatch.resume_recoverable(self.project), [])
            self.assertEqual(dispatch.resume_recoverable(self.project), [])
        finally:
            dispatch.resume_blocked = original

        current = S.load_task(self.project, blocked["slug"])
        self.assertEqual(current["state"], "blocked")
        self.assertEqual(calls, [blocked["slug"], blocked["slug"]])
        self.assertEqual(current["auto_recovery"]["attempts"], dispatch.AUTO_RECOVERY_LIMIT)
        self.assertFalse(current["auto_recovery"]["in_flight"])
        self.assertTrue(current["auto_recovery"]["finished"])
        self.assertIn("Git provenance gate", current["auto_recovery"]["error"])
        recovered = [event for event in S.read_events(self.project, blocked["slug"])
                     if event.get("recovered_terminal")]
        self.assertEqual(recovered, [])

    def test_recoverable_blockers_never_launch_past_project_wip(self):
        config.save_projects({self.project: {
            "name": self.project, "path": str(self.repo), "stacks": ["python"], "wip": 3,
        }})
        blocked = []
        for number in range(7):
            task = self._task(f"recoverable {number}", "blocked", f"altitude/free-{number}.py",
                              f"2026-01-0{number + 1}T00:00:00+00:00")
            task["blocked_reason"] = "lease: the prior holder is gone"
            S.save_task(self.project, task)
            blocked.append(task)

        resumed = dispatch.resume_recoverable(self.project)

        states = [S.load_task(self.project, task["slug"])["state"] for task in blocked]
        self.assertEqual(len(resumed), 3)
        self.assertEqual(states.count("running"), 3)
        self.assertEqual(states.count("blocked"), 4)
        self.assertEqual(len(self.resumed), 3)
        self.assertEqual(dispatch.resume_recoverable(self.project), [])
        self.assertEqual(len(self.resumed), 3)

    def test_deferred_recovery_attempt_is_durably_settled(self):
        blocked = self._task("deferred automatic recovery", "blocked", "altitude/deferred.py",
                             "2026-01-01T00:00:00+00:00")
        blocked["blocked_reason"] = "lease: the prior holder is gone"
        S.save_task(self.project, blocked)
        original = dispatch.resume_blocked
        dispatch.resume_blocked = lambda *args, **kwargs: {"deferred": True, "hold": "test hold"}
        try:
            self.assertEqual(dispatch.resume_recoverable(self.project), [])
        finally:
            dispatch.resume_blocked = original

        rec = S.load_task(self.project, blocked["slug"])["auto_recovery"]
        self.assertEqual((rec["category"], rec["attempts"]), ("lease-clear", 1))
        self.assertFalse(rec["in_flight"])
        self.assertTrue(rec["finished"])
        self.assertTrue(rec["deferred"])
        self.assertIsNone(rec["error"])

    def test_exact_pending_terminal_adoption_is_once_and_settles_attempt(self):
        blocked = self._task("exact pending adoption", "blocked", "altitude/adopt.py",
                             "2026-01-01T00:00:00+00:00")
        blocked.update({
            "blocked_reason": "lease: the prior holder is gone",
            "l2_engine": "codex",
            "report_not_before": "2000-01-01T00:00:00+00:00",
            "pending_resume": {"dispatch_id": blocked["dispatch_id"], "generation": "resume-g",
                               "engine": "codex", "started": S.now()},
            "auto_recovery": {"category": "lease-clear", "attempts": 2, "token": "attempt-token",
                              "in_flight": True, "started": S.now()},
        })
        S.save_task(self.project, blocked)
        (S.task_dir(self.project, blocked["slug"]) / "report.md").write_text("new report\n")
        S.write_json(S.task_dir(self.project, blocked["slug"]) / "report.json", {"blocked": "new report"})
        S.write_json(dispatch.codex_run_path(self.project, blocked["slug"]), {
            "project": self.project, "slug": blocked["slug"],
            "dispatch_id": blocked["dispatch_id"], "generation": "resume-g",
            "state": "done", "pid": None, "pid_start": None,
        })
        self.assertTrue(dispatch.recovery_pending(self.project, blocked))

        self.assertEqual(dispatch.resume_recoverable(self.project), [blocked["slug"]])
        self.assertEqual(dispatch.resume_recoverable(self.project), [])

        current = S.load_task(self.project, blocked["slug"])
        self.assertEqual(current["state"], "running")
        self.assertNotIn("pending_resume", current)
        self.assertEqual(current["auto_recovery"]["attempts"], 2)
        self.assertFalse(current["auto_recovery"]["in_flight"])
        self.assertTrue(current["auto_recovery"]["finished"])
        recovered = [event for event in S.read_events(self.project, blocked["slug"])
                     if event.get("recovered_terminal")]
        self.assertEqual(len(recovered), 1)

    def test_fresh_final_attempt_remains_recovery_owned_until_settled(self):
        blocked = self._task("final claimed attempt", "blocked", "altitude/final.py",
                             "2026-01-01T00:00:00+00:00")
        blocked.update({
            "blocked_reason": "lease: the prior holder is gone",
            "auto_recovery": {"category": "lease-clear", "attempts": dispatch.AUTO_RECOVERY_LIMIT,
                              "token": "final-token", "in_flight": True, "started": S.now()},
        })
        S.save_task(self.project, blocked)
        self.assertTrue(dispatch.recovery_pending(self.project, blocked))
        blocked["auto_recovery"]["in_flight"] = False
        blocked["auto_recovery"]["finished"] = S.now()
        S.save_task(self.project, blocked)
        self.assertFalse(dispatch.recovery_pending(self.project, blocked))

    def test_terminal_pending_adoption_requires_persisted_report_boundary(self):
        blocked = self._task("boundaryless pending adoption", "blocked", "altitude/boundaryless.py",
                             "2026-01-01T00:00:00+00:00")
        blocked.update({
            "blocked_reason": "lease: the prior holder is gone", "l2_engine": "codex",
            "pending_resume": {"dispatch_id": blocked["dispatch_id"], "generation": "resume-g",
                               "engine": "codex", "started": S.now()},
        })
        S.save_task(self.project, blocked)
        S.write_json(S.task_dir(self.project, blocked["slug"]) / "report.json", {"blocked": "old report"})
        S.write_json(dispatch.codex_run_path(self.project, blocked["slug"]), {
            "project": self.project, "slug": blocked["slug"],
            "dispatch_id": blocked["dispatch_id"], "generation": "resume-g",
            "state": "done", "pid": None, "pid_start": None,
        })

        self.assertFalse(dispatch._claimed_worker_live(self.project, blocked))
        current = S.load_task(self.project, blocked["slug"])
        self.assertEqual(current["state"], "blocked")
        self.assertEqual(current["pending_resume"]["generation"], "resume-g")

    def test_stale_pending_cleanup_cannot_remove_replacement_generation(self):
        blocked = self._task("replacement pending generation", "blocked", "altitude/replacement.py",
                             "2026-01-01T00:00:00+00:00")
        old_pending = {"dispatch_id": blocked["dispatch_id"], "generation": "old-generation",
                       "engine": "codex", "started": "2000-01-01T00:00:00+00:00"}
        blocked.update({"blocked_reason": "lease: the prior holder is gone", "l2_engine": "codex",
                        "pending_resume": old_pending})
        S.save_task(self.project, blocked)
        original = dispatch._claimed_worker_live

        def replace_generation(project, snapshot):
            live = S.load_task(project, snapshot["slug"])
            live["pending_resume"] = {
                "dispatch_id": live["dispatch_id"], "generation": "replacement-generation",
                "engine": "codex", "started": S.now(),
            }
            S.save_task(project, live)
            return False

        dispatch._claimed_worker_live = replace_generation
        try:
            self.assertEqual(dispatch.resume_recoverable(self.project), [])
        finally:
            dispatch._claimed_worker_live = original

        current = S.load_task(self.project, blocked["slug"])
        self.assertEqual(current["state"], "blocked")
        self.assertEqual(current["pending_resume"]["generation"], "replacement-generation")
        self.assertNotIn("auto_recovery", current)
        self.assertEqual(self.resumed, [])

    def test_invalid_missing_and_future_recovery_claim_times_are_stale(self):
        for number, started in enumerate((None, "not-an-iso-time", "2999-01-01T00:00:00+00:00")):
            blocked = self._task(f"stale recovery timestamp {number}", "blocked",
                                 f"altitude/stale-{number}.py", f"2026-01-0{number + 1}T00:00:00+00:00")
            pending = {"dispatch_id": blocked["dispatch_id"], "generation": f"stale-{number}",
                       "engine": "claude"}
            recovery = {"category": "lease-clear", "attempts": 1, "token": f"old-{number}",
                        "in_flight": True}
            if started is not None:
                pending["started"] = started
                recovery["started"] = started
            blocked.update({"blocked_reason": "lease: the prior holder is gone",
                            "pending_resume": pending, "auto_recovery": recovery})
            S.save_task(self.project, blocked)

            self.assertEqual(dispatch._seconds_since(started or ""), float("inf"))
            self.assertEqual(dispatch.resume_recoverable(self.project), [blocked["slug"]])
            current = S.load_task(self.project, blocked["slug"])
            self.assertEqual(current["state"], "running")
            self.assertNotIn("pending_resume", current)
            self.assertEqual(current["auto_recovery"]["attempts"], 2)
            self.assertFalse(current["auto_recovery"]["in_flight"])

    def test_cli_resume_payloads_for_deferred_and_running(self):
        main = runpy.run_path(str(Path(__file__).resolve().parent.parent / "bin" / "alt"))["main"]
        blocked = self._task("cli resume", "blocked", "altitude/cli.py", "2026-01-01T00:00:00+00:00")
        original = dispatch.resume_blocked

        def invoke(fake):
            dispatch.resume_blocked = fake
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output):
                    main(["--project", self.project, "task", "resume", blocked["slug"], "--answer", "Continue."])
            finally:
                dispatch.resume_blocked = original
            return json.loads(output.getvalue())

        def deferred(project, slug, answer, prefix=""):
            task = S.load_task(project, slug)
            task["blocked_reason"] = "waiting: usage limit: test window"
            S.save_task(project, task)
            return {"deferred": True}

        deferred_payload = invoke(deferred)
        self.assertEqual(deferred_payload["state"], "blocked")
        self.assertTrue(deferred_payload["deferred"])
        self.assertEqual(deferred_payload["waiting"], "waiting: usage limit: test window")

        def resumed(project, slug, answer, prefix=""):
            task = S.load_task(project, slug)
            task["state"] = "running"
            S.save_task(project, task)
            return {"deferred": False, "agent": {"id": "new-cli-agent"}}

        running_payload = invoke(resumed)
        self.assertEqual(running_payload["state"], "running")
        self.assertFalse(running_payload["deferred"])
        self.assertEqual(running_payload["agent"], "new-cli-agent")


if __name__ == "__main__":
    unittest.main()
