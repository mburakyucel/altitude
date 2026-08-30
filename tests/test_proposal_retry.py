"""Rejected proposal runs preserve validation context for the stale-run retry."""
import tempfile
import unittest
from datetime import datetime
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

REJECTION_HEADING = "## Previous proposal run rejected by validation"


class TestProposalRetry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="altitude-proposal-retry-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.addCleanup(self.temp.cleanup)
        self.config_paths = {
            name: getattr(config, name) for name in ("ROOT", "PROJECTS_FILE", "MONITOR_DIR", "INCIDENT_INDEX", "DIGEST_FILE")
        }
        self.addCleanup(self.restore_config)  # registered before the first mutation: a failure below still restores the globals
        config.ROOT = self.root / "state"
        config.PROJECTS_FILE = config.ROOT / "projects.json"
        config.MONITOR_DIR = config.ROOT / "monitor"
        config.INCIDENT_INDEX = config.ROOT / "incidents.jsonl"
        config.DIGEST_FILE = config.ROOT / "DIGEST.md"
        config.save_projects({"proposal-retry": {"name": "proposal-retry", "path": str(self.repo), "stacks": []}})
        self.task = T.new("proposal-retry", "Retry a rejected proposal", "M", "Make a focused change.", actor="burak")

    def restore_config(self):
        for name, value in self.config_paths.items():
            setattr(config, name, value)

    def create(self, relative: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")

    def result(self, files: list[str]) -> dict:
        return {"structured": {**BASE_PROPOSAL, "files": files}, "error": None, "turns": 1, "cost": 0.0}

    def run_proposal(self, result: dict) -> dict:
        choice = {"engine": "codex", "why": "test"}
        with patch.object(route, "pick_engine", return_value=choice), patch.object(
            engines, "codex_exec", return_value=result
        ) as engine:
            self.engine = engine
            return propose.run_proposal("proposal-retry", self.task["slug"])

    def recorded_error(self) -> dict:
        return S.load_task("proposal-retry", self.task["slug"])["proposal_error"]

    def test_files_failure_is_recorded_threaded_through_and_cleared_on_success(self):
        entry = "missing.py"
        message = f"proposal files entry {entry!r} is invalid: {entry!r} does not exist at proposal base {self.repo}"

        with self.assertRaises(RuntimeError) as raised:
            self.run_proposal(self.result([entry]))

        self.assertEqual(str(raised.exception), message)
        self.assertNotIn(REJECTION_HEADING, self.engine.call_args.args[0])
        self.assertEqual(self.recorded_error()["message"], message)
        datetime.fromisoformat(self.recorded_error()["at"])

        self.create("altitude/propose.py")
        self.run_proposal(self.result(["altitude/propose.py"]))

        retry_prompt = self.engine.call_args.args[0]
        self.assertIn(message, retry_prompt)
        self.assertIn("Correct only this validation failure and otherwise produce a full proposal.", retry_prompt)
        self.assertNotIn("proposal_error", S.load_task("proposal-retry", self.task["slug"]))

    def test_schema_failure_is_recorded_and_threaded_through(self):
        message = "codex exit 1: output did not match schema: /files/0 must be string"
        failed = {"structured": None, "error": message, "turns": 1, "cost": 0.0}

        with self.assertRaises(RuntimeError) as raised:
            self.run_proposal(failed)

        self.assertEqual(str(raised.exception), message)
        self.assertNotIn(REJECTION_HEADING, self.engine.call_args.args[0])
        self.assertEqual(self.recorded_error()["message"], message)
        datetime.fromisoformat(self.recorded_error()["at"])

        self.create("altitude/propose.py")
        self.run_proposal(self.result(["altitude/propose.py"]))

        self.assertIn(message, self.engine.call_args.args[0])
        self.assertNotIn("proposal_error", S.load_task("proposal-retry", self.task["slug"]))

    def test_malformed_engine_payload_is_recorded_and_threaded_through(self):
        # codex_exec json.loads()es the output with no type check, so a JSON array reaches _normalise_proposal_files
        # as a list and raises AttributeError, not RuntimeError — that must still break the identical-prompt replay
        with self.assertRaises(AttributeError) as raised:
            self.run_proposal({"structured": ["not", "an", "object"], "error": None, "turns": 1, "cost": 0.0})

        message = str(raised.exception)
        self.assertEqual(self.recorded_error()["message"], message)
        self.assertNotIn(REJECTION_HEADING, self.engine.call_args.args[0])

        self.create("altitude/propose.py")
        self.run_proposal(self.result(["altitude/propose.py"]))

        self.assertIn(message, self.engine.call_args.args[0])
        self.assertNotIn("proposal_error", S.load_task("proposal-retry", self.task["slug"]))

    def test_a_revision_retry_is_told_to_fix_the_failure_as_well_as_the_revision_brief(self):
        message = "proposal files entry 'missing.py' is invalid"
        S.atomic_write(S.task_dir("proposal-retry", self.task["slug"]) / "proposal.md", "Previous proposal\n")
        with S.project_lock("proposal-retry"):
            task = S.load_task("proposal-retry", self.task["slug"])
            task["proposal_error"] = {"message": message, "at": S.now()}
            S.save_task("proposal-retry", task)

        self.create("altitude/propose.py")
        self.run_proposal(self.result(["altitude/propose.py"]))

        prompt = self.engine.call_args.args[0]
        self.assertIn(message, prompt)
        # the revision block above is binding, so "correct only this" would contradict it
        self.assertIn("Fix this validation failure in addition to everything above.", prompt)
        self.assertNotIn("Correct only this validation failure", prompt)

    def test_critic_prompt_and_task_error_are_unchanged(self):
        message = "proposal files entry 'missing.py' is invalid"
        with S.project_lock("proposal-retry"):
            task = S.load_task("proposal-retry", self.task["slug"])
            task["proposal_engine"] = "claude"
            task["proposal_error"] = {"message": message, "at": S.now()}
            S.save_task("proposal-retry", task)
        task_dir = S.task_dir("proposal-retry", self.task["slug"])
        S.atomic_write(task_dir / "proposal.md", "Valid proposal\n")
        result = {"structured": {"verdict": "pass", "issues": []}, "error": None}

        with patch.object(engines, "codex_exec", return_value=result) as engine:
            critic = propose.run_critic("proposal-retry", self.task["slug"])

        expected_prompt = (
            (config.PERSONAS / "critic.md").read_text()
            + "\n\n## Request\n"
            + (task_dir / "request.md").read_text()
            + "\n\n## Proposal\nValid proposal\n\n\nAnswer as JSON per the output schema."
        )
        self.assertEqual(critic, {"verdict": "pass", "issues": []})
        self.assertEqual(engine.call_args.args[0], expected_prompt)
        self.assertEqual(self.recorded_error()["message"], message)


if __name__ == "__main__":
    unittest.main()
