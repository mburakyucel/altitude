"""2026-09-09 stalled recovery: durable, finite evidence without waking the faulted owner."""
import copy
import json
import subprocess
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from altitude import dispatch, engines, l3, server, state as S, status, tasks as T


class TestCIRecheck(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        git("remote", "set-url", "origin", "git@github.com:team/project.git", cwd=self.repo)
        self.now = "2026-09-09T10:00:00+00:00"
        self.enterContext(mock.patch.object(S, "now", side_effect=lambda: self.now))
        self.slug = "blocked-owner"
        self.owner = {"slug": self.slug, "title": "CI recovery", "state": "blocked", "fault": "artifact quota",
                      "blocked_reason": "Upload refused", "block_id": "block-1", "attempt": 2,
                      "agent_id": "worker", "session_id": "session", "l2_engine": engines.config.ENGINES[0],
                      "hold_merge": "operator review", "questions": [{"id": "question", "status": "open",
                          "audience": "operator", "revision": 1, "question": "Review the held PR?"}],
                      "source": "recovery", "paths": [], "created": self.now}
        S.tasks_dir(self.project).joinpath(self.slug).mkdir(parents=True)
        S.save_task(self.project, self.owner)
        self.run = {"id": 71, "repository": {"full_name": "team/project"}, "run_attempt": 1,
                    "workflow_id": 9, "head_branch": "main", "event": "push", "pull_requests": [],
                    "head_repository": {"full_name": "team/project"}, "status": "completed",
                    "conclusion": "failure", "created_at": "2026-09-09T08:00:00Z",
                    "updated_at": "2026-09-09T09:00:00Z", "run_started_at": "2026-09-09T08:00:00Z"}
        self.candidates = []
        self.artifacts = []
        self.posts = []
        self.transport = dispatch._ci_api
        self.api = self.enterContext(mock.patch.object(dispatch, "_ci_api", side_effect=self.response))
        self.delivery = l3.queue_ci_recheck
        self.queue = self.enterContext(mock.patch.object(l3, "queue_ci_recheck", create=True))

    def response(self, project, repository, suffix, *, method="GET"):
        self.assertEqual((project, repository), (self.project, "team/project"))
        if method == "POST":
            self.posts.append(suffix)
            return {}
        if suffix.startswith("workflows/"):
            return {"workflow_runs": copy.deepcopy(self.candidates)}
        if "/artifacts?" in suffix:
            return {"artifacts": copy.deepcopy(self.artifacts)}
        return copy.deepcopy(next((r for r in self.candidates if suffix.split("/")[1] == str(r["id"])), self.run))

    def schedule(self, **kwargs):
        return T.recheck_ci(self.project, self.slug, 71, kwargs.pop("at", self.now), "Verify CI upload", actor="l3", **kwargs)

    def record(self):
        return S.load_task(self.project, self.slug)["ci_recheck"]

    def tick(self, minutes=0):
        self.now = (datetime.fromisoformat(self.now) + timedelta(minutes=minutes)).isoformat()
        dispatch.run_ci_recheck(self.project, self.slug)

    def complete_rerun(self, *, upload=True, conclusion="success"):
        self.run.update(run_attempt=2, status="completed", conclusion=conclusion,
                        run_started_at="2026-09-09T10:01:00Z", updated_at="2026-09-09T10:04:00Z")
        if upload:
            self.artifacts = [{"id": 18, "created_at": "2026-09-09T10:03:00Z", "size_in_bytes": 100, "expired": False}]

    def assert_owner_preserved(self):
        task = S.load_task(self.project, self.slug)
        for key, value in self.owner.items():
            if key != "updated":
                self.assertEqual(task[key], value, key)
        self.assertFalse(task.get("resume_after"))
        self.assertFalse(task.get("daemon_request"))

    def test_due_registration_status_and_one_rerun_survive_new_invocations(self):
        at = "2026-09-09T10:05:00+00:00"
        receipt = self.schedule(at=at)
        self.assertEqual(self.schedule(at=at), receipt)
        self.tick()
        self.api.assert_not_called()
        self.assertEqual(status.status(self.project, self.slug)["ci_recheck"]["due_at"], at)
        self.tick(5)
        self.assertEqual(self.posts, ["runs/71/rerun"])
        self.assertEqual(self.record()["submission"]["baseline_attempt"], 1)
        self.run.update(run_attempt=2, status="in_progress", conclusion=None)
        self.tick(5)
        self.assertEqual(self.record()["submission"]["status"], "observed")
        self.assertEqual(len(self.posts), 1)
        self.assert_owner_preserved()

    def test_fresh_artifacts_reach_coordinator_without_owner_resume(self):
        self.schedule()
        self.tick()
        self.complete_rerun()
        self.tick(5)
        self.assertEqual(self.record()["status"], "notifying")
        self.assertEqual(self.record()["evidence"]["artifact_upload"], "available")
        self.assertEqual(self.record()["evidence"]["artifact_ids"], [18])
        self.queue.assert_called_with(self.project, self.slug)
        self.assert_owner_preserved()

    def test_tolerated_success_old_expired_or_empty_artifacts_do_not_prove_recovery(self):
        self.run["conclusion"] = "success"
        self.schedule()
        self.tick()
        self.complete_rerun(upload=False)
        self.artifacts = [
            {"id": 1, "created_at": "2026-09-09T09:00:00Z", "size_in_bytes": 10, "expired": False},
            {"id": 2, "created_at": "2026-09-09T10:03:00Z", "size_in_bytes": 10, "expired": True},
            {"id": 3, "created_at": "2026-09-09T10:03:00Z", "size_in_bytes": 0, "expired": False}]
        self.tick(5)
        self.assertEqual(self.record()["evidence"]["artifact_upload"], "unverified")
        self.assertEqual(self.record()["status"], "unchanged")
        calls = self.api.call_count
        self.tick(10)
        self.assertEqual(self.api.call_count, calls)

    def test_relevant_running_fresh_run_is_preferred_and_foreign_runs_are_ignored(self):
        fresh = {**self.run, "id": 72, "status": "in_progress", "conclusion": None}
        foreign = {**fresh, "id": 73, "head_branch": "unrelated"}
        self.candidates = [foreign, fresh]
        self.schedule()
        self.tick()
        self.assertEqual(self.record()["target"], 72)
        self.assertFalse(self.posts)
        fresh.update(status="completed", conclusion="success", run_started_at=self.now, updated_at=self.now)
        self.artifacts = [{"id": 18, "created_at": self.now, "size_in_bytes": 100}]
        self.tick(5)
        self.assertEqual(self.record()["evidence"]["url"], "https://github.com/team/project/actions/runs/72")

    def test_fresh_completed_gate_change_is_compared_to_original_failure(self):
        self.candidates = [{**self.run, "id": 72, "conclusion": "success", "created_at": self.now,
                            "updated_at": self.now, "run_started_at": self.now}]
        self.schedule()
        self.tick()
        self.assertFalse(self.posts)
        self.assertEqual(self.record()["status"], "notifying")
        self.assertEqual(self.record()["evidence"]["artifact_upload"], "unverified")
        self.assertEqual(self.record()["evidence"]["conclusion"], "success")

    def test_scheduled_wait_requires_evidence_from_due_time_not_registration(self):
        self.candidates = [{**self.run, "id": 72, "conclusion": "success",
                            "updated_at": "2026-09-09T10:01:00Z"}]
        self.schedule(at="2026-09-09T11:00:00Z")
        self.tick(60)
        self.assertEqual(self.record()["target"], 71)
        self.assertEqual(self.posts, ["runs/71/rerun"])

    def test_run_started_before_due_and_finished_after_due_is_fresh(self):
        self.candidates = [{**self.run, "id": 72, "conclusion": "success",
                            "updated_at": "2026-09-09T11:01:00Z"}]
        self.schedule(at="2026-09-09T11:00:00Z")
        self.tick(65)
        self.assertEqual(self.record()["target"], 72)
        self.assertFalse(self.posts)

    def test_run_that_finished_during_discovery_is_adopted_without_rerun(self):
        self.schedule()
        original = self.api.side_effect
        def complete(*args, **kwargs):
            if args[2].startswith("workflows/"):
                self.run.update(run_attempt=2, updated_at=self.now)
            return original(*args, **kwargs)
        self.api.side_effect = complete
        self.tick()
        self.assertFalse(self.posts)
        self.assertNotIn("submission", self.record())

    def test_artifacts_from_another_attempt_cannot_prove_current_upload(self):
        self.schedule()
        self.tick()
        self.complete_rerun()
        self.artifacts[0]["created_at"] = "2026-09-09T10:08:00Z"
        self.tick(10)
        self.assertEqual(self.record()["evidence"]["artifact_upload"], "unverified")

    def test_submission_uncertainty_and_crash_never_repeat_the_write(self):
        self.schedule()
        original = self.api.side_effect

        def uncertain(*args, **kwargs):
            if kwargs.get("method") == "POST":
                self.assertEqual(self.record()["submission"]["status"], "intent")
                self.posts.append(args[2])
                raise subprocess.TimeoutExpired("gh", 20)
            return original(*args, **kwargs)

        self.api.side_effect = uncertain
        self.tick()
        self.assertEqual(self.record()["submission"]["status"], "uncertain")
        self.tick(5)
        self.assertEqual(len(self.posts), 1)
        self.complete_rerun()
        self.tick(5)
        self.assertEqual(self.record()["submission"]["status"], "observed")
        self.assertEqual(self.record()["evidence"]["artifact_upload"], "available")

    def test_saved_intent_before_process_exit_is_reconciled_without_submission(self):
        class Crash(BaseException):
            pass
        self.schedule()
        original = self.api.side_effect
        def crash(*args, **kwargs):
            if kwargs.get("method") == "POST":
                raise Crash()
            return original(*args, **kwargs)
        self.api.side_effect = crash
        with self.assertRaises(Crash):
            self.tick()
        self.assertEqual(self.record()["submission"]["status"], "intent")
        self.api.side_effect = original
        self.tick(5)
        self.assertFalse(self.posts)
        self.tick(120)
        self.assertEqual(self.record()["status"], "notifying")
        self.assertIn("exhausted", self.record()["evidence"]["error"])

    def test_read_failures_and_successful_pending_reads_are_finite(self):
        self.schedule()
        self.api.side_effect = OSError("DNS unavailable")
        for _ in range(3):
            self.tick(5)
        self.assertEqual(self.record()["read_failures"], 3)
        self.assertEqual(self.record()["status"], "notifying")
        self.assert_owner_preserved()

    def test_pending_ci_stops_at_read_bound_even_when_reads_succeed(self):
        self.run.update(status="in_progress", conclusion=None)
        self.schedule()
        for _ in range(24):
            self.tick(5)
        self.assertEqual(self.record()["status"], "notifying")
        self.assertLessEqual(self.record()["reads"], 24)
        self.assertIn("exhausted", self.record()["evidence"]["error"])
        self.assertFalse(self.posts)

    def test_probe_to_real_queue_and_terminal_turn_preserves_owner(self):
        self.queue.side_effect = self.delivery
        self.schedule()
        self.tick()
        self.complete_rerun()
        self.tick(5)
        self.assertEqual([row["id"] for row in l3.queued(self.project)], [self.record()["id"]])
        result = {"text": "Verified the fresh evidence; holds remain.", "session_id": "coordinator",
                  "reported_session_id": "coordinator", "usage": {}, "context_tokens": 10,
                  "cost": 0.0, "tools": []}
        with mock.patch.object(l3, "_select", return_value={"engine": "claude", "why": "fixture"}), \
             mock.patch.object(engines, "claude_print", return_value=result):
            l3.deliver_queued(self.project)
        self.assertEqual(self.record()["status"], "done")
        self.assertEqual(l3.queued(self.project), [])
        self.assert_owner_preserved()

    def test_same_project_broker_registers_without_external_io(self):
        self.setenv("ALTITUDE_ACTOR", "l3")
        # The real broker subprocess does not inherit this fixture's S.now patch.
        due = datetime.now(timezone.utc) + timedelta(hours=1)
        result = server.l3_verb_request(self.project, {"kind": "alt", "args": ["task", "recheck-ci", self.slug,
            "--run", "71", "--at", due.isoformat().replace("+00:00", "Z"), "--reason", "Verify upload"]})
        self.assertEqual(result["returncode"], 0, result["stderr"])
        record = json.loads(result["stdout"])
        self.assertEqual(record["actor"], "l3")
        self.assertEqual(record["due_at"], due.isoformat())
        self.api.assert_not_called()

    def test_stale_attempt_block_and_lifecycle_requests_prevent_effects(self):
        self.schedule()
        dispatch.request_task_operation(self.project, self.slug, "resume", "Verified gate repair", actor="l3")
        self.tick()
        self.api.assert_not_called()
        self.assertEqual(self.record()["status"], "invalidated")
        self.assertEqual(S.load_task(self.project, self.slug)["daemon_request"]["operation"], "resume")

    def test_stale_identity_during_read_prevents_submission_and_result(self):
        self.schedule()
        original = self.api.side_effect
        def replace(*args, **kwargs):
            task = S.load_task(self.project, self.slug)
            task["block_id"] = "new-block"
            S.save_task(self.project, task)
            return original(*args, **kwargs)
        self.api.side_effect = replace
        self.tick()
        self.assertFalse(self.posts)
        self.tick()
        self.assertEqual(self.record()["status"], "invalidated")

    def test_command_authority_and_input_bounds(self):
        self.setenv("ALTITUDE_PROJECT", self.project)
        due = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        for actor, code in (("l2", 1), ("l3", 0)):
            self.setenv("ALTITUDE_ACTOR", actor)
            result = self.alt("task", "recheck-ci", self.slug, "--run", "71", "--at", due,
                              "--reason", "Verify upload")
            self.assertEqual(result.returncode, code, result.stderr)
        for run, at in ((0, self.now), (71, "2026-09-09T10:00:00"), (71, "2030-01-01T00:00:00Z")):
            with self.assertRaises(T.TransitionError):
                T.recheck_ci(self.project, self.slug, run, at, "Verify", actor="l3")
        with self.assertRaises(ValueError):
            server._validate_l3_alt_args(["task", "recheck-ci", "../foreign"])

    def test_github_transport_binds_repository_host_method_and_timeout(self):
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "{}", "")) as run:
            self.transport(self.project, "team/project", "runs/71/rerun", method="POST")
        self.assertEqual(run.call_args.args[0], ["gh", "api", "--hostname", "github.com", "--method", "POST",
                                              "repos/team/project/actions/runs/71/rerun"])
        self.assertEqual(run.call_args.kwargs["timeout"], 20)
        self.assertNotIn("GH_REPO", run.call_args.kwargs["env"])
