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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, status as task_status  # noqa: E402


GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")
if os.environ.get("FAKE_GH_FAIL"):
    print("fake gh failure", file=sys.stderr)
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
elif args[:2] == ["run", "list"]:
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
        self.assertEqual(result["other_leases"], [{"slug": "other-task", "paths": ["altitude/server.py"]}])
        self.assertTrue(result["report_json"]["exists"])
        self.assertEqual([pr["number"] for pr in result["prs"]], [17, 18])
        self.assertEqual(result["prs"][0]["merge_sha"], "merge-old")
        self.assertEqual(result["prs"][0]["checks"], {
            "total": 3, "passed": 1, "failed": 1, "pending": 1, "failing": ["lint"]})
        self.assertEqual(result["main_run"], {
            "id": 9, "workflow": "CI", "status": "completed", "conclusion": "success",
            "head_sha": "merge-new"})
        self.assertEqual(len([call for call in self.calls() if call[:2] == ["run", "list"]]), 1)

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


if __name__ == "__main__":
    unittest.main()
