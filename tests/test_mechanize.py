"""Decision 47: recurring transcript tool shapes are counted without a model call."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from altitude import config, mechanize, state as S


class TestMechanize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="altitude-mechanize-")
        self.base = Path(self.tmp.name)
        self.project = self.base / "project"
        self.worktree = self.project / ".claude" / "worktrees" / "one"
        self.worktree.mkdir(parents=True)
        self.transcripts = self.base / "home" / ".claude" / "projects"
        self.old_config = (config.ROOT, config.MONITOR_DIR, config.PROJECTS_FILE, config.INCIDENT_INDEX)
        config.ROOT = self.base / "altitude-home"
        config.MONITOR_DIR = config.ROOT / "monitor"
        config.PROJECTS_FILE = config.ROOT / "projects.json"
        config.INCIDENT_INDEX = config.ROOT / "incidents.jsonl"
        config.ensure_root()
        config.save_projects({"demo": {"name": "demo", "path": str(self.project), "stacks": ["python"]}})

    def tearDown(self):
        config.ROOT, config.MONITOR_DIR, config.PROJECTS_FILE, config.INCIDENT_INDEX = self.old_config
        self.tmp.cleanup()

    def _write(self, cwd: Path, lines: list[dict]) -> None:
        slug = str(cwd.resolve()).replace("/", "-").replace(".", "-")
        directory = self.transcripts / slug
        directory.mkdir(parents=True)
        body = "\n".join(json.dumps(line) for line in lines) + "\n{malformed\n"
        (directory / "session.jsonl").write_text(body)

    @staticmethod
    def _assistant(timestamp: str, tools: list[tuple[str, dict]], usage: tuple[int, int, int]) -> dict:
        return {
            "type": "assistant",
            "timestamp": timestamp,
            "message": {
                "content": [
                    {"type": "tool_use", "name": name, "input": tool_input}
                    for name, tool_input in tools
                ],
                "usage": {"input_tokens": usage[0], "cache_read_input_tokens": usage[1],
                          "cache_creation_input_tokens": usage[2]},
            },
        }

    def test_histogram_incident_dedup_and_window(self):
        recent = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=8)).replace(microsecond=0).isoformat()
        common = [
            ("Bash", {"command": "FOO=1 sudo git --quiet commit -m 'x'"}),
            ("Bash", {"command": "git commit --amend"}),
            ("Bash", {"command": "alt --verbose l1 wait foo"}),
            ("Read", {"file_path": "/tmp/file"}),
        ]
        root_lines = [
            self._assistant(recent, common + [
                ("Bash", {"command": "cat -n /a/b/altitude/server.py"}),
                ("Bash", {"command": "pytest -q"}),
                ("WebFetch", {"url": "https://example.test"}),
            ], (1, 2, 3))
        ]
        root_lines.extend(self._assistant(recent, common, (1, 2, 3)) for _ in range(24))
        worktree_lines = [
            self._assistant(recent, [
                ("Bash", {"command": "alt l1 wait foo"}),
                ("Read", {"file_path": "/tmp/file"}),
            ], (2, 3, 5))
            for _ in range(15)
        ]
        worktree_lines.append(self._assistant(old, [("Bash", {"command": "gh pr view 123"})], (9, 9, 9)))
        self._write(self.project, root_lines)
        self._write(self.worktree, worktree_lines)

        with patch.object(mechanize, "_transcript_root", return_value=self.transcripts), \
             patch.object(mechanize.improve, "new_incident", return_value={"id": "I-001"}) as incident, \
             patch.object(mechanize.T, "fyi") as fyi:
            first = mechanize.run_for("demo")
            second = mechanize.run_for("demo")

        rows = {row["shape"]: row for row in first["shapes"]}
        self.assertEqual(rows["git commit"], {"shape": "git commit", "turns": 25,
                                               "context_tokens": 150, "sessions": 1})
        self.assertEqual(rows["alt l1"]["turns"], 40)
        self.assertEqual(rows["Read"]["turns"], 40)
        self.assertEqual(rows["cat server.py"]["turns"], 1)
        self.assertEqual(rows["pytest"]["turns"], 1)
        self.assertEqual(rows["WebFetch"]["turns"], 1)
        self.assertNotIn("gh pr", rows)
        self.assertEqual(first["shapes"], second["shapes"])
        self.assertEqual(incident.call_count, 1)
        self.assertEqual(incident.call_args.kwargs["title"], "mechanize: `git commit`")
        self.assertEqual(fyi.call_count, 1)

        output = S.read_json(config.MONITOR_DIR / "tool-shapes-demo.json")
        self.assertEqual(set(output), {"project", "generated", "window_days", "shapes"})
        self.assertEqual(output["project"], "demo")
        self.assertEqual(output["window_days"], 7)
        self.assertEqual(output["shapes"], sorted(output["shapes"],
                         key=lambda row: (-row["turns"], -row["context_tokens"], row["shape"])))
        self.assertEqual(set(output["shapes"][0]), {"shape", "turns", "context_tokens", "sessions"})
        stamps = S.read_json(config.MONITOR_DIR / "mechanize-incidents.json")
        self.assertEqual(stamps["demo"]["git commit"]["incident"], "I-001")
        self.assertNotIn("alt l1", stamps["demo"])
        self.assertNotIn("Read", stamps["demo"])


if __name__ == "__main__":
    unittest.main()
