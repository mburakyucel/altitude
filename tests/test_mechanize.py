"""Decision 47: recurring transcript tool shapes are counted without a model call."""
import importlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

# config and improve both capture ledger paths at import time; tests must never point at ~/.altitude.
os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-mechanize-")

from altitude import config  # noqa: E402

importlib.reload(config)

from altitude import improve  # noqa: E402

importlib.reload(improve)

from altitude import mechanize, state as S  # noqa: E402


class TestMechanize(unittest.TestCase):
    def setUp(self):
        self.base = config.ROOT / "fixtures" / self._testMethodName
        self.project = self.base / "project"
        self.worktree = self.project / ".claude" / "worktrees" / "one"
        self.worktree.mkdir(parents=True)
        self.transcripts = self.base / "home" / ".claude" / "projects"
        self.project_name = f"demo-{self._testMethodName}"
        config.ensure_root()
        projects = config.load_projects()
        projects[self.project_name] = {
            "name": self.project_name,
            "path": str(self.project),
            "stacks": ["python"],
        }
        config.save_projects(projects)

    def _write(self, cwd: Path, lines: list[dict], name: str = "session.jsonl") -> Path:
        slug = str(cwd.resolve()).replace("/", "-").replace(".", "-")
        directory = self.transcripts / slug
        directory.mkdir(parents=True, exist_ok=True)
        body = "\n".join(json.dumps(line) for line in lines) + "\n{malformed\n"
        path = directory / name
        path.write_text(body)
        return path

    @staticmethod
    def _assistant(timestamp: str, tools: list[tuple[str, dict]], usage: tuple[int, int, int],
                   turn_id: str, *, message_id: bool = True, request_id: bool = True) -> list[dict]:
        lines = []
        for name, tool_input in tools:
            line = {
                "type": "assistant",
                "timestamp": timestamp,
                "message": {
                    "content": [{"type": "tool_use", "name": name, "input": tool_input}],
                    "usage": {"input_tokens": usage[0], "cache_read_input_tokens": usage[1],
                              "cache_creation_input_tokens": usage[2]},
                },
            }
            if message_id:
                line["message"]["id"] = turn_id
            if request_id:
                line["requestId"] = f"request-{turn_id}"
            lines.append(line)
        return lines

    def test_histogram_incident_dedup_and_window(self):
        recent = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=8)).replace(microsecond=0).isoformat()
        common = [
            ("Bash", {"command": "FOO=1 sudo git --quiet commit -m 'x'"}),
            ("Bash", {"command": "git commit --amend"}),
            ("Bash", {"command": "alt --verbose l1 wait foo"}),
            ("Read", {"file_path": "/tmp/file"}),
        ]
        root_lines = self._assistant(recent, common + [
            ("Bash", {"command": "cat -n /a/b/altitude/server.py"}),
            ("Bash", {"command": "pytest -q"}),
            ("WebFetch", {"url": "https://example.test"}),
        ], (1, 2, 3), "root-0")
        for turn in range(1, 25):
            root_lines.extend(self._assistant(recent, common, (1, 2, 3), f"root-{turn}"))
        worktree_lines = []
        for turn in range(15):
            worktree_lines.extend(self._assistant(recent, [
                ("Bash", {"command": "alt l1 wait foo"}),
                ("Read", {"file_path": "/tmp/file"}),
            ], (2, 3, 5), f"worktree-{turn}"))
        worktree_lines.extend(self._assistant(
            old, [("Bash", {"command": "gh pr view 123"})], (9, 9, 9), "worktree-old"))
        self._write(self.project, root_lines)
        self._write(self.worktree, worktree_lines)

        with patch.object(mechanize, "_transcript_root", return_value=self.transcripts), \
             patch.object(mechanize.improve, "new_incident", return_value={"id": "I-001"}) as incident, \
             patch.object(mechanize.T, "fyi") as fyi:
            first = mechanize.run_for(self.project_name)
            second = mechanize.run_for(self.project_name)

        rows = {row["shape"]: row for row in first["shapes"]}
        self.assertEqual(rows["git commit"], {"shape": "git commit", "turns": 25,
                                                "context_tokens": 150, "sessions": 1})
        self.assertEqual(rows["alt l1"]["turns"], 40)
        self.assertEqual(rows["alt l1"]["sessions"], 2)
        self.assertEqual(rows["Read"]["turns"], 40)
        self.assertEqual(rows["Read"]["sessions"], 2)
        self.assertEqual(rows["cat server.py"]["turns"], 1)
        self.assertEqual(rows["pytest"]["turns"], 1)
        self.assertEqual(rows["WebFetch"]["turns"], 1)
        self.assertNotIn("gh pr", rows)
        self.assertEqual(first["shapes"], second["shapes"])
        self.assertEqual(incident.call_count, 1)
        self.assertEqual(incident.call_args.kwargs["title"], "mechanize: `git commit`")
        self.assertEqual(fyi.call_count, 1)

        output = S.read_json(config.MONITOR_DIR / f"tool-shapes-{self.project_name}.json")
        self.assertEqual(set(output), {"project", "generated", "window_days", "shapes"})
        self.assertEqual(output["project"], self.project_name)
        self.assertEqual(output["window_days"], 7)
        self.assertEqual(output["shapes"], sorted(output["shapes"],
                         key=lambda row: (-row["turns"], -row["context_tokens"], row["shape"])))
        self.assertEqual(set(output["shapes"][0]), {"shape", "turns", "context_tokens", "sessions"})
        stamps = S.read_json(config.MONITOR_DIR / "mechanize-incidents.json")
        self.assertEqual(stamps[self.project_name]["git commit"]["incident"], "I-001")
        self.assertNotIn("alt l1", stamps[self.project_name])
        self.assertNotIn("Read", stamps[self.project_name])

    def test_bash_shapes_skip_flag_values_and_unsafe_search_text(self):
        cases = {
            "git -C /tmp/worktree status": "git status",
            "git -c user.name=bot commit -m x": "git commit",
            "git --git-dir /tmp/repo/.git status": "git status",
            "git --work-tree /tmp/repo status": "git status",
            "gh -R owner/repo pr view 48": "gh pr",
            "gh --repo owner/repo issue list": "gh issue",
            "grep -rn foo .": "grep .",
            "grep TOKEN=abc": "grep",
            "sed TOKEN=abc": "sed",
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(mechanize._bash_shape(command), expected)

    def test_old_file_mtime_excludes_transcript(self):
        recent = datetime.now(timezone.utc).isoformat()
        path = self._write(self.project, self._assistant(
            recent, [("Bash", {"command": "git status"})], (1, 2, 3), "old-file"))
        old_mtime = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
        os.utime(path, (old_mtime, old_mtime))

        with patch.object(mechanize, "_transcript_root", return_value=self.transcripts):
            rows = mechanize._collect(self.project_name, mechanize.time.time())

        self.assertEqual(rows, [])

    def test_removed_worktree_slug_and_malformed_timestamp_use_mtime(self):
        removed = self.project / ".claude" / "worktrees" / "already-cleaned"
        sibling = self.base / "project-2"
        self.assertFalse(removed.exists())
        self._write(removed, self._assistant(
            "not-a-timestamp", [("Bash", {"command": "git status"})], (1, 2, 3), "removed"))
        self._write(sibling, self._assistant(
            datetime.now(timezone.utc).isoformat(),
            [("Bash", {"command": "gh issue list"})], (1, 2, 3), "sibling"))

        with patch.object(mechanize, "_transcript_root", return_value=self.transcripts):
            rows = {row["shape"]: row for row in mechanize._collect(
                self.project_name, mechanize.time.time())}

        self.assertEqual(rows["git status"]["turns"], 1)
        self.assertNotIn("gh issue", rows)

    def test_request_id_fallback_and_missing_ids(self):
        recent = datetime.now(timezone.utc).isoformat()
        lines = self._assistant(recent, [
            ("Bash", {"command": "git status"}),
            ("Bash", {"command": "git status --short"}),
        ], (1, 2, 3), "request-only", message_id=False)
        lines.extend(self._assistant(recent, [
            ("Bash", {"command": "gh pr view 48"}),
            ("Bash", {"command": "gh pr checks 48"}),
        ], (1, 2, 3), "no-ids", message_id=False, request_id=False))
        self._write(self.project, lines)

        with patch.object(mechanize, "_transcript_root", return_value=self.transcripts):
            rows = {row["shape"]: row for row in mechanize._collect(
                self.project_name, mechanize.time.time())}

        self.assertEqual(rows["git status"]["turns"], 1)
        self.assertEqual(rows["git status"]["context_tokens"], 6)
        self.assertEqual(rows["gh pr"]["turns"], 2)
        self.assertEqual(rows["gh pr"]["context_tokens"], 12)

    def test_incident_dedup_keeps_filing_time_until_expiry(self):
        now = mechanize.time.time()
        last = datetime.fromtimestamp(now - 60, timezone.utc).replace(microsecond=0).isoformat()
        path = config.MONITOR_DIR / "mechanize-incidents.json"
        S.write_json(path, {self.project_name: {
            "git status": {"last": last, "incident": "I-001", "count": 3},
        }})
        rows = [{"shape": "git status", "turns": 25, "context_tokens": 100, "sessions": 1}]

        with patch.object(mechanize.improve, "new_incident") as incident:
            mechanize._file_incidents(self.project_name, rows, now)

        incident.assert_not_called()
        stamp = S.read_json(path)[self.project_name]["git status"]
        self.assertEqual(stamp["last"], last)
        self.assertEqual(stamp["count"], 4)

        after_window = now + mechanize.WINDOW_SECONDS + 1
        filed_at = datetime.fromtimestamp(after_window, timezone.utc).replace(microsecond=0).isoformat()
        with patch.object(mechanize.improve, "new_incident", return_value={"id": "I-002"}) as incident, \
             patch.object(mechanize.T, "fyi"), \
             patch.object(mechanize.S, "now", return_value=filed_at):
            mechanize._file_incidents(self.project_name, rows, after_window)

        incident.assert_called_once()
        stamp = S.read_json(path)[self.project_name]["git status"]
        self.assertEqual(stamp["last"], filed_at)
        self.assertEqual(stamp["incident"], "I-002")

    def test_run_due_short_circuits_and_writes_stamp(self):
        result = {"project": self.project_name, "shapes": []}
        with patch.object(mechanize, "run_for", return_value=result) as run:
            self.assertEqual(mechanize.run_due(self.project_name), result)
            self.assertIsNone(mechanize.run_due(self.project_name))

        run.assert_called_once_with(self.project_name)
        stamps = S.read_json(config.MONITOR_DIR / "mechanize-stamp.json")
        self.assertIn(self.project_name, stamps)

    def test_run_due_stamps_failure_to_back_off_until_tomorrow(self):
        with patch.object(mechanize, "run_for", side_effect=RuntimeError("broken transcript")):
            with self.assertRaisesRegex(RuntimeError, "broken transcript"):
                mechanize.run_due(self.project_name)

        with patch.object(mechanize, "run_for") as retry:
            self.assertIsNone(mechanize.run_due(self.project_name))
        retry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
