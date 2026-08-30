"""Proposal file entries are normalised and validated before the proposal is stored."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from altitude import config, engines, propose, route, state as S, tasks as T


BASE_PROPOSAL = {
    "summary": "Change the requested paths.",
    "approach": "Make the focused change.",
    "alternatives": [],
    "class": "M",
    "estimate": {"turns": 1, "subagent_launches": 0, "l1_count": 1, "model_tiers": "opus"},
    "envelope": {"l1_in_flight": 1, "subagent_launches": 3, "max_turns": 40},
    "decision_needed": False,
    "question": None,
    "options": None,
    "always_list_hits": [],
    "verification": "Run the tests.",
    "risks": [],
    "citations": [],
}


class TestProposalPaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="altitude-proposal-paths-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.config_paths = {
            name: getattr(config, name) for name in ("ROOT", "PROJECTS_FILE", "MONITOR_DIR", "INCIDENT_INDEX", "DIGEST_FILE")
        }
        config.ROOT = self.root / "state"
        config.PROJECTS_FILE = config.ROOT / "projects.json"
        config.MONITOR_DIR = config.ROOT / "monitor"
        config.INCIDENT_INDEX = config.ROOT / "incidents.jsonl"
        config.DIGEST_FILE = config.ROOT / "DIGEST.md"
        config.save_projects({"proposal-paths": {"name": "proposal-paths", "path": str(self.repo), "stacks": []}})
        self.task = T.new("proposal-paths", "Validate proposal paths", "M", "Make a focused change.", actor="burak")

    def tearDown(self):
        for name, value in self.config_paths.items():
            setattr(config, name, value)
        self.temp.cleanup()

    def create(self, relative: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")

    def run_proposal(self, files: list[str]) -> dict:
        structured = {**BASE_PROPOSAL, "files": files}
        result = {"structured": structured, "error": None, "turns": 1, "cost": 0.0}
        choice = {"engine": "codex", "why": "test"}
        with patch.object(route, "pick_engine", return_value=choice), patch.object(engines, "codex_exec", return_value=result):
            return propose.run_proposal("proposal-paths", self.task["slug"])

    def stored_files(self) -> list[str]:
        return S.read_json(self.proposal_path())["files"]

    def proposal_path(self) -> Path:
        return S.task_dir("proposal-paths", self.task["slug"]) / "proposal.json"

    def test_annotated_entry_is_stored_bare(self):
        self.create("altitude/server.py")
        self.run_proposal(["altitude/server.py (Handler._file only)"])
        self.assertEqual(self.stored_files(), ["altitude/server.py"])

    def test_comma_list_is_stored_as_its_parts(self):
        self.create("web/app.js")
        self.create("web/style.css")
        self.run_proposal(["web/app.js, web/style.css"])
        self.assertEqual(self.stored_files(), ["web/app.js", "web/style.css"])

    def test_prose_only_entry_is_rejected_with_its_text(self):
        entry = "(all files under web/)"
        with self.assertRaisesRegex(RuntimeError, r"\(all files under web/\)"):
            self.run_proposal([entry])
        self.assertFalse(self.proposal_path().exists())

    def test_missing_unmarked_path_is_rejected(self):
        entry = "web/does-not-exist.ts"
        with self.assertRaisesRegex(RuntimeError, entry):
            self.run_proposal([entry])
        self.assertFalse(self.proposal_path().exists())

    def test_existing_bare_path_passes_unchanged(self):
        self.create("altitude/propose.py")
        proposal = self.run_proposal(["altitude/propose.py"])
        self.assertEqual(proposal["files"], ["altitude/propose.py"])
        self.assertEqual(self.stored_files(), ["altitude/propose.py"])

    def test_trailing_new_marker_allows_a_new_path(self):
        self.run_proposal(["web/new-component.ts (new)"])
        self.assertEqual(self.stored_files(), ["web/new-component.ts"])

    def test_host_state_entries_are_accepted_and_stored(self):
        entries = [
            "~/.altitude/settings.json",
            "~/.altitude/projects.json",
            "~/.altitude/altitude/events.log",
            "~/.altitude/.dispatch.lock",
            "~/.altitude/.projects.lock",
            "~/.altitude/<project>/tasks/<slug>/status.json",
            "$ALTITUDE_HOME/<project>/tasks/<slug>/.altitude-codex-write-probe-* (transient, removed)",
            "<repo>/.claude/worktrees/<slug>/.altitude-codex-write-probe-* (transient, removed)",
            "/etc/apparmor.d/bwrap-userns-restrict (approval-dependent host file, not committed)",
        ]
        expected = entries[:6] + [
            "$ALTITUDE_HOME/<project>/tasks/<slug>/.altitude-codex-write-probe-*",
            "<repo>/.claude/worktrees/<slug>/.altitude-codex-write-probe-*",
            "/etc/apparmor.d/bwrap-userns-restrict",
        ]

        proposal = self.run_proposal(entries)

        self.assertEqual(proposal["files"], expected)
        self.assertEqual(self.stored_files(), expected)

    def test_malformed_paths_are_rejected_even_when_marked_new(self):
        entries = [
            "web/src/routes/{Inbox,Project}.tsx + .test.tsx (new)",
            "altitude/foo.py(new)",
        ]
        for entry in entries:
            with self.subTest(entry=entry):
                with self.assertRaisesRegex(RuntimeError, re.escape(entry)):
                    self.run_proposal([entry])
                self.assertFalse(self.proposal_path().exists())

    def test_empty_part_of_comma_list_is_rejected_with_part_text(self):
        self.create("web/app.js")
        entry = "web/app.js, (and the rest of web/)"

        with self.assertRaisesRegex(RuntimeError, re.escape("(and the rest of web/)")):
            self.run_proposal([entry])

        self.assertFalse(self.proposal_path().exists())

    def test_file_guidance_uses_existing_approach_field(self):
        persona = (config.PERSONAS / "proposal.md").read_text()
        schema = json.loads((config.SCHEMAS / "proposal.json").read_text())
        description = schema["properties"]["files"]["description"]

        for guidance in (persona, description):
            self.assertIn("`approach`", guidance)
            self.assertNotIn("proposal rationale", guidance)
            self.assertIn("is recorded, not leased", guidance)
            self.assertIn("must end with a space followed by `(new)`", guidance)
            for invalid_marker in ("(new file)", "(NEW)", "(created)", "foo.ts(new)"):
                self.assertIn(invalid_marker, guidance)

    def test_only_space_delimited_lowercase_new_marker_allows_missing_path(self):
        invalid_entries = [
            "web/new-component.ts (new file)",
            "web/new-component.ts (NEW)",
            "web/new-component.ts (created)",
            "web/new-component.ts(new)",
        ]
        for entry in invalid_entries:
            with self.subTest(entry=entry):
                with self.assertRaisesRegex(RuntimeError, re.escape(entry)):
                    self.run_proposal([entry])
                self.assertFalse(self.proposal_path().exists())

    def test_leading_dot_slash_is_normalised(self):
        self.create("web/app.js")

        self.run_proposal(["./web/app.js"])

        self.assertEqual(self.stored_files(), ["web/app.js"])

    def test_comma_list_expands_only_its_individual_parts(self):
        self.create("web/app.js")
        self.create("web/style.css")

        with patch.object(propose, "_expand_entry", wraps=propose._expand_entry) as expand:
            self.run_proposal(["web/app.js, web/style.css"])

        self.assertEqual(expand.call_count, 2)


if __name__ == "__main__":
    unittest.main()
