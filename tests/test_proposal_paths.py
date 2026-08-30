"""Proposal file entries are normalised and validated before the proposal is stored."""
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


if __name__ == "__main__":
    unittest.main()
