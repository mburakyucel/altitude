"""`alt task status` compacts task, hook, lease, L1, PR, and main-run state without a network."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, state as S, status as task_status  # noqa: E402


GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")
if os.environ.get("FAKE_GH_FAIL"):
    print("fake gh failure", file=sys.stderr)
    sys.exit(2)
if args[:2] == ["pr", "view"] and os.environ.get("FAKE_GH_FAIL_PR") == args[2]:
    print("fake gh PR failure", file=sys.stderr)
    sys.exit(2)
if args[:2] == ["pr", "view"]:
    number = int(args[2])
    prs = {
        17: {"number": 17, "state": "MERGED", "mergedAt": "2026-08-28T10:00:00Z",
             "mergeCommit": {"oid": "merge-old"}, "headRefName": "worktree-old",
             "headRefOid": "head-old", "statusCheckRollup": [
                 {"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
                 {"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"},
                 {"context": "deploy", "state": "PENDING"}]},
        18: {"number": 18, "state": "MERGED", "mergedAt": "2026-08-29T10:00:00Z",
             "mergeCommit": {"oid": "merge-new"}, "headRefName": "worktree-new",
             "headRefOid": "head-new", "statusCheckRollup": []}}
    print(json.dumps(prs[number]))
elif args[:2] == ["pr", "list"]:
    number = os.environ.get("FAKE_GH_BRANCH_PR")
    print(json.dumps([{"number": int(number)}] if number else []))
elif args[:2] == ["run", "list"]:
    if os.environ.get("FAKE_GH_NO_RUN_MATCH"):
        print(json.dumps([{"databaseId": 2, "headSha": "other-sha", "conclusion": "success",
                           "status": "completed", "workflowName": "CI"}]))
    else:
        print(json.dumps([
            {"databaseId": 9, "headSha": "merge-new", "conclusion": "success",
             "status": "completed", "workflowName": "CI"},
            {"databaseId": 6, "headSha": "merge-old", "conclusion": "success",
             "status": "completed", "workflowName": "CI"}]))
else:
    print("unhandled fake gh call", file=sys.stderr)
    sys.exit(64)
"""

CLAUDE = """#!/usr/bin/env python3
print("[]")
"""


class TestTaskStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="alt-task-status-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "home"
        replacements = {
            "ROOT": self.root,
            "MONITOR_DIR": self.root / "monitor",
            "PROJECTS_FILE": self.root / "projects.json",
            "INCIDENT_INDEX": self.root / "incidents.jsonl",
            "DIGEST_FILE": self.root / "DIGEST.md",
        }
        for name, value in replacements.items():
            old = getattr(config, name)
            setattr(config, name, value)
            self.addCleanup(setattr, config, name, old)
        config.ensure_root()

        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        config.save_projects({"demo": {"path": str(self.repo), "stacks": [], "wip": 5}})

        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        gh = self.bin_dir / "gh"
        gh.write_text(GH)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        claude = self.bin_dir / "claude"
        claude.write_text(CLAUDE)
        claude.chmod(claude.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        old_claude = config.CLAUDE_BIN
        config.CLAUDE_BIN = str(claude)
        self.addCleanup(setattr, config, "CLAUDE_BIN", old_claude)
        self.gh_log = self.tmp / "gh.jsonl"
        self._setenv("PATH", f"{self.bin_dir}:{os.environ.get('PATH', '')}")
        self._setenv("FAKE_GH_LOG", str(self.gh_log))
        self._setenv("ALTITUDE_HOME", str(self.root))
        self._setenv("ALTITUDE_PROJECT", "demo")
        self._setenv("CLAUDE_BIN", str(claude))

        task = {
            "slug": "task-one", "state": "running", "class": "S", "title": "One status",
            "attempt": 1, "dispatch_id": "task-one-1", "session_id": "sid-1", "agent_id": "aid-1",
            "source": "chat", "hold_merge": None, "blocked_reason": None,
            "updated": "2026-08-29T00:00:00+00:00", "worktree": "/tmp/worktree", "branch": "worktree-task-one",
            "envelope": {"l1_in_flight": 1, "subagent_launches": 3, "max_turns": 40,
                         "verification": "reviewer"},
            "paths": ["altitude/status.py", "bin/alt"], "prs": [17],
        }
        task_dir = S.tasks_dir("demo") / "task-one"
        task_dir.mkdir(parents=True)
        S.write_json(task_dir / "status.json", task)
        S.write_json(task_dir / "report.json", {"landed": {}})
        S.write_json(task_dir / "l1" / "implementer-1.json", {
            "name": "implementer-1", "n": 1, "role": "implementer", "engine": "codex",
            "pid": os.getpid(), "done": None, "result": {"pr": 18},
        })
        other_dir = S.tasks_dir("demo") / "other-task"
        other_dir.mkdir(parents=True)
        S.write_json(other_dir / "status.json", {
            "slug": "other-task", "state": "running", "paths": ["altitude/server.py"]})
        S.write_json(config.MONITOR_DIR / "counts-sid-1.json", {
            "subagent_launches": 2, "edits": 7, "files": ["altitude/status.py"]})
        self.enforced = {"project": "demo", "slug": "task-one", "dispatch_id": "task-one-1",
                         **task["envelope"]}
        S.write_json(config.MONITOR_DIR / "envelope-demo--task-one-1.json", self.enforced)
        S.write_json(config.MONITOR_DIR / "statusline-test.json", {
            "_at": time.time(), "rate_limits": {"five_hour": {"used_percentage": 10}}})

    def _setenv(self, key, value):
        old = os.environ.get(key)
        os.environ[key] = value
        self.addCleanup(lambda: os.environ.update({key: old}) if old is not None else os.environ.pop(key, None))

    def calls(self):
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()] if self.gh_log.exists() else []

    def test_happy_path_is_complete_and_compact(self):
        result = task_status.status("demo", "task-one")

        expected_fields = {
            "project", "slug", "state", "class", "title", "attempt", "dispatch_id", "session_id",
            "agent_id", "source", "hold_merge", "blocked_reason", "updated", "worktree", "branch",
            "envelope", "counts", "envelope_file", "l1_runs", "lease", "other_leases", "hold",
            "wip_hold", "report_json", "prs", "main_run", "errors",
        }
        self.assertTrue(expected_fields.issubset(result))
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["counts"], {"subagent_launches": 2, "edits": 7})
        self.assertEqual(result["envelope_file"], self.enforced)
        self.assertEqual(result["l1_runs"]["in_flight"], 1)
        self.assertEqual(result["lease"], ["altitude/status.py", "bin/alt"])
        self.assertEqual(result["other_leases"], [{
            "slug": "other-task", "paths": ["altitude/server.py"],
            "hold_paths": ["altitude/server.py"],
        }])
        self.assertTrue(result["report_json"]["exists"])
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

    def test_bare_top_level_other_lease_does_not_create_wip_hold(self):
        task_path = S.task_dir("demo", "task-one") / "status.json"
        task = S.read_json(task_path)
        task["paths"] = ["tests/test_x.py", "tests/"]
        S.write_json(task_path, task)
        other_path = S.task_dir("demo", "other-task") / "status.json"
        other = S.read_json(other_path)
        other["paths"] = ["tests/"]
        S.write_json(other_path, other)

        result = task_status.status("demo", "task-one")

        self.assertIsNone(result["wip_hold"])
        self.assertIsNone(dispatch.wip_hold("demo", task))
        self.assertEqual(result["lease"], ["tests/test_x.py", "tests/"])
        self.assertEqual(result["other_leases"], [{
            "slug": "other-task", "paths": ["tests/"], "hold_paths": [],
        }])

    def test_bare_top_level_own_lease_does_not_create_wip_hold(self):
        task_path = S.task_dir("demo", "task-one") / "status.json"
        task = S.read_json(task_path)
        task["paths"] = ["tests/"]
        S.write_json(task_path, task)
        other_path = S.task_dir("demo", "other-task") / "status.json"
        other = S.read_json(other_path)
        other["paths"] = ["tests/test_y.py"]
        S.write_json(other_path, other)

        result = task_status.status("demo", "task-one")

        self.assertIsNone(result["wip_hold"])
        self.assertEqual(result["wip_hold"], dispatch.wip_hold("demo", task))

    def test_file_lease_wip_hold_matches_dispatcher(self):
        task_path = S.task_dir("demo", "task-one") / "status.json"
        task = S.read_json(task_path)
        task["paths"] = ["tests/test_x.py"]
        S.write_json(task_path, task)
        other_path = S.task_dir("demo", "other-task") / "status.json"
        other = S.read_json(other_path)
        other["paths"] = ["tests/test_x.py"]
        S.write_json(other_path, other)

        result = task_status.status("demo", "task-one")
        dispatcher_hold = dispatch.wip_hold("demo", task)

        self.assertEqual(dispatcher_hold,
                         "file lease: `other-task` is running on tests/test_x.py")
        self.assertEqual(result["wip_hold"], dispatcher_hold)
        self.assertEqual(result["other_leases"], [{
            "slug": "other-task", "paths": ["tests/test_x.py"],
            "hold_paths": ["tests/test_x.py"],
        }])

    def test_other_leases_are_published_only_after_all_are_annotated(self):
        # `z-` sorts last, so the first entry is annotated before the second one raises: a
        # half-annotated `other_leases` would hand a consumer a KeyError on `hold_paths`.
        broken_dir = S.tasks_dir("demo") / "z-broken-task"
        broken_dir.mkdir(parents=True)
        S.write_json(broken_dir / "status.json", {
            "slug": "z-broken-task", "state": "running", "paths": ["boom/x.py"],
        })
        real_narrow = dispatch.narrow

        def narrow(paths):
            if "boom/x.py" in paths:
                raise ValueError("boom")
            return real_narrow(paths)

        with mock.patch.object(dispatch, "narrow", narrow):
            result = task_status.status("demo", "task-one")

        self.assertEqual(result["other_leases"], [])
        self.assertTrue(any(error.startswith("other_leases:") for error in result["errors"]))

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

    def test_dispatch_keyed_counts_win_over_the_legacy_session_file(self):
        S.write_json(config.MONITOR_DIR / "counts-demo--task-one-1.json",
                     {"subagent_launches": 4, "edits": 11})
        result = task_status.status("demo", "task-one")
        self.assertEqual(result["counts"], {"subagent_launches": 4, "edits": 11})
        self.assertEqual(result["errors"], [])

    def test_missing_main_run_names_the_merge_sha(self):
        self._setenv("FAKE_GH_NO_RUN_MATCH", "1")
        result = task_status.status("demo", "task-one")
        self.assertIsNone(result["main_run"])
        self.assertIn("no main run found for merge-new", result["errors"])

    def test_one_pr_fault_keeps_other_summaries_and_checks_main(self):
        self._setenv("FAKE_GH_FAIL_PR", "18")
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
        run_path = S.task_dir("demo", "task-one") / "l1" / "implementer-1.json"
        run = S.read_json(run_path)
        run["result"] = None
        S.write_json(run_path, run)
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
        run_path = S.task_dir("demo", "task-one") / "l1" / "implementer-1.json"
        run = S.read_json(run_path)
        run["result"] = None
        S.write_json(run_path, run)
        S.write_json(S.task_dir("demo", "task-one") / "report.json", {"landed": {}})
        self._setenv("FAKE_GH_BRANCH_PR", "17")

        result = task_status.status("demo", "task-one")

        self.assertEqual([pr["number"] for pr in result["prs"]], [17])
        self.assertEqual(len([call for call in self.calls() if call[:2] == ["pr", "list"]]), 1)

    def test_stale_l1_is_reported_without_writing(self):
        run_path = S.task_dir("demo", "task-one") / "l1" / "implementer-1.json"
        run = S.read_json(run_path)
        run["pid"] = 999_999_999
        S.write_json(run_path, run)
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}

        result = task_status.status("demo", "task-one")

        after = {str(path.relative_to(self.root)): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)
        self.assertTrue(result["l1_runs"]["runs"][0]["stale"])
        self.assertIsNone(result["l1_runs"]["runs"][0]["done"])
        self.assertEqual(result["l1_runs"]["in_flight"], 0)

    def test_broken_gh_degrades_without_raising(self):
        self._setenv("FAKE_GH_FAIL", "1")
        result = task_status.status("demo", "task-one")
        self.assertEqual(result["prs"], [])
        self.assertIsNone(result["main_run"])
        self.assertTrue(result["errors"])

    def test_cli_prints_json(self):
        alt = Path(__file__).resolve().parent.parent / "bin" / "alt"
        proc = subprocess.run([str(alt), "task", "status", "task-one"], capture_output=True,
                              text=True, env=os.environ.copy(), timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["slug"], "task-one")
        self.assertEqual(result["main_run"]["head_sha"], "merge-new")

    def test_cli_defaults_slug_from_altitude_task(self):
        self._setenv("ALTITUDE_TASK", "task-one")
        alt = Path(__file__).resolve().parent.parent / "bin" / "alt"
        proc = subprocess.run([str(alt), "task", "status"], capture_output=True,
                              text=True, env=os.environ.copy(), timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["slug"], "task-one")


if __name__ == "__main__":
    unittest.main()
