"""`alt task status` compacts task, hook, lease, PR, and main-run state without a network."""
import json
import shutil
import unittest
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, state as S, status as task_status

PRS = {
    "17": {"number": 17, "state": "MERGED", "mergedAt": "2026-08-28T10:00:00Z",
           "mergeCommit": {"oid": "merge-old"}, "headRefName": "worktree-old",
           "headRefOid": "head-old", "statusCheckRollup": [
               {"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
               {"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"},
               {"context": "deploy", "state": "PENDING"}]},
    "18": {"number": 18, "state": "MERGED", "mergedAt": "2026-08-29T10:00:00Z",
           "mergeCommit": {"oid": "merge-new"}, "headRefName": "worktree-new",
           "headRefOid": "head-new", "statusCheckRollup": []},
}
RUNS = [{"databaseId": 9, "headSha": "merge-new", "conclusion": "success", "status": "completed", "workflowName": "CI"},
        {"databaseId": 6, "headSha": "merge-old", "conclusion": "success", "status": "completed", "workflowName": "CI"}]
UNRELATED_RUNS = [{"databaseId": 2, "headSha": "other-sha", "conclusion": "success",
                   "status": "completed", "workflowName": "CI"}]


class TestTaskStatus(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        self.ghdir = self.fake_gh()
        (self.ghdir / "prs.json").write_text(json.dumps(PRS))
        (self.ghdir / "runs.json").write_text(json.dumps(RUNS))

        (self.repo / ".github" / "workflows").mkdir(parents=True)
        self.register("demo", path=self.repo, wip=5)
        self.setenv("ALTITUDE_PROJECT", "demo")

        task = {
            "slug": "task-one", "state": "running", "title": "One status",
            "attempt": 1, "session_id": "sid-1", "agent_id": "aid-1",
            "source": "chat", "hold_merge": None, "blocked_reason": None,
            "updated": "2026-08-29T00:00:00+00:00", "worktree": "/tmp/worktree", "branch": "worktree-task-one",
            "paths": ["altitude/status.py", "bin/alt"], "prs": [17, 18],
        }
        task_dir = S.tasks_dir("demo") / "task-one"
        task_dir.mkdir(parents=True)
        S.write_json(task_dir / "status.json", task)
        S.write_json(task_dir / "report.json", {"landed": {}})
        other_dir = S.tasks_dir("demo") / "other-task"
        other_dir.mkdir(parents=True)
        S.write_json(other_dir / "status.json", {
            "slug": "other-task", "state": "running", "paths": ["altitude/server.py"]})
        S.write_json(config.MONITOR_DIR / "counts-demo--task-one-1.json", {
            "edits": 7, "files": ["altitude/status.py"]})

    def calls(self):
        return self.gh_log()

    def test_brief_displays_observed_model_and_old_records_still_render(self):
        task = S.load_task("demo", "task-one")
        task.update(l2_engine="codex", engine_model="actual-model", engine_reasoning_effort="high")
        S.save_task("demo", task)
        result = self.alt("task", "status", "task-one", "--brief")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("engine/model: codex/actual-model", result.stdout)
        self.assertEqual(task_status.status("demo", "task-one")["engine_reasoning_effort"], "high")
        del task["engine_model"]
        del task["engine_reasoning_effort"]
        S.save_task("demo", task)
        result = self.alt("task", "status", "task-one", "--brief")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("engine/model: codex/default", result.stdout)

    def test_happy_path_is_complete_and_compact(self):
        result = task_status.status("demo", "task-one")

        expected_fields = {
            "project", "slug", "state", "title", "attempt", "session_id",
            "agent_id", "source", "hold_merge", "blocked_reason", "updated", "worktree", "branch",
            "counts", "lease", "other_leases", "hold",
            "wip_hold", "gate", "repository", "report_json", "prs", "main_run", "errors",
        }
        self.assertTrue(expected_fields.issubset(result))
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["counts"], {"edits": 7})
        self.assertEqual(result["lease"], ["altitude/status.py", "bin/alt"])
        self.assertEqual(result["other_leases"], [{
            "slug": "other-task", "paths": ["altitude/server.py"],
        }])
        self.assertTrue(result["report_json"]["exists"])
        self.assertEqual(result["gate"], "github-actions")
        self.assertEqual([pr["number"] for pr in result["prs"]], [17, 18])
        self.assertEqual(result["prs"][0]["merge_sha"], "merge-old")
        self.assertEqual(result["prs"][0]["checks"], {
            "total": 3, "passed": 1, "failed": 1, "pending": 1, "failing": ["lint"]})
        self.assertEqual(result["main_run"], {
            "id": 9, "workflow": "CI", "status": "completed", "conclusion": "success",
            "head_sha": "merge-new"})
        self.assertEqual(len([call for call in self.calls() if call[:2] == ["run", "list"]]), 1)
        run_call = next(call for call in self.calls() if call[:2] == ["run", "list"])
        self.assertEqual(run_call[run_call.index("--limit") + 1], "100")

    def test_repository_status_is_serialized_from_the_read_only_inspector(self):
        repository = {
            "branch": "main", "dirty": False, "head": "head-sha", "origin_sha": "origin-sha",
            "ahead": 0, "behind": 2, "local_only_shas": [], "oldest_local_sha": None,
            "determinate": True, "error": None,
        }
        inspected = mock.Mock()
        inspected.as_dict.return_value = repository

        with mock.patch.object(task_status.git_policy, "inspect_repository", return_value=inspected) as inspect:
            result = task_status.status("demo", "task-one")

        inspect.assert_called_once_with(self.repo)
        self.assertEqual(result["repository"], repository)

    def test_repository_status_fault_does_not_break_task_status(self):
        with mock.patch.object(task_status.git_policy, "inspect_repository", side_effect=OSError("git unavailable")):
            result = task_status.status("demo", "task-one")

        self.assertIsNone(result["repository"])
        self.assertTrue(any(error.startswith("repository: git unavailable") for error in result["errors"]))

    def test_record_slug_is_excluded_from_other_leases(self):
        task_path = S.task_dir("demo", "task-one") / "status.json"
        task = S.read_json(task_path)
        task["slug"] = "record-slug"
        S.write_json(task_path, task)

        result = task_status.status("demo", "task-one")

        self.assertEqual(result["slug"], "record-slug")
        self.assertNotIn("record-slug", [lease["slug"] for lease in result["other_leases"]])
        self.assertIsNone(result["wip_hold"])
        self.assertEqual(result["wip_hold"], dispatch.wip_hold("demo", task))

    def test_attempt_keyed_counts_are_read(self):
        S.write_json(config.MONITOR_DIR / "counts-demo--task-one-1.json",
                     {"edits": 11})
        result = task_status.status("demo", "task-one")
        self.assertEqual(result["counts"], {"edits": 11})
        self.assertEqual(result["errors"], [])

    def test_missing_main_run_names_the_merge_sha(self):
        (self.ghdir / "runs.json").write_text(json.dumps(UNRELATED_RUNS))
        result = task_status.status("demo", "task-one")
        self.assertIsNone(result["main_run"])
        self.assertEqual(result["gate"], "github-actions")
        self.assertIn("no main run found for merge-new", result["errors"])

    def test_repo_without_workflows_uses_local_suite_without_main_run_error(self):
        shutil.rmtree(self.repo / ".github")
        (self.ghdir / "runs.json").write_text(json.dumps(UNRELATED_RUNS))

        result = task_status.status("demo", "task-one")

        self.assertEqual(result["errors"], [])
        self.assertIsNone(result["main_run"])
        self.assertEqual(result["gate"], "local-suite")
        self.assertFalse(any(call[:2] == ["run", "list"] for call in self.calls()))

    def test_one_pr_fault_keeps_other_summaries_and_checks_main(self):
        (self.ghdir / "fail_prs.json").write_text(json.dumps(["18"]))
        result = task_status.status("demo", "task-one")
        self.assertEqual([pr["number"] for pr in result["prs"]], [17])
        self.assertTrue(any("fake gh PR failure" in error for error in result["errors"]))
        self.assertEqual(result["main_run"]["head_sha"], "merge-old")
        self.assertEqual(len([call for call in self.calls() if call[:2] == ["run", "list"]]), 1)

    def test_report_prs_are_reused_before_branch_fallback(self):
        task_path = S.task_dir("demo", "task-one") / "status.json"
        task = S.read_json(task_path)
        task["prs"] = []
        S.write_json(task_path, task)
        S.write_json(S.task_dir("demo", "task-one") / "report.json",
                     {"landed": {"prs": [{"number": 17}]}})

        result = task_status.status("demo", "task-one")

        self.assertEqual([pr["number"] for pr in result["prs"]], [17])
        self.assertFalse(any(call[:2] == ["pr", "list"] for call in self.calls()))

    def test_branch_fallback_finds_one_live_pr(self):
        task_path = S.task_dir("demo", "task-one") / "status.json"
        task = S.read_json(task_path)
        task["prs"] = []
        S.write_json(task_path, task)
        S.write_json(S.task_dir("demo", "task-one") / "report.json", {"landed": {}})
        (self.ghdir / "pr_list.json").write_text(json.dumps([{"number": 17}]))

        result = task_status.status("demo", "task-one")

        self.assertEqual([pr["number"] for pr in result["prs"]], [17])
        self.assertEqual(len([call for call in self.calls() if call[:2] == ["pr", "list"]]), 1)

    def test_broken_gh_degrades_without_raising(self):
        (self.ghdir / "fail.txt").write_text("gh is broken\n")
        result = task_status.status("demo", "task-one")
        self.assertEqual(result["prs"], [])
        self.assertIsNone(result["main_run"])
        self.assertTrue(result["errors"])

    def test_cli_prints_json(self):
        proc = self.alt("task", "status", "task-one")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["slug"], "task-one")
        self.assertEqual(result["main_run"]["head_sha"], "merge-new")

    def test_cli_defaults_slug_from_altitude_task(self):
        proc = self.alt("task", "status", env={"ALTITUDE_TASK": "task-one"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["slug"], "task-one")


if __name__ == "__main__":
    unittest.main()
