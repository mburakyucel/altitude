"""Rejected proposal runs carry validation context through a bounded retry lifecycle."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from altitude import config, engines, l3, propose, route, server, state as S, tasks as T


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

    def seed_failure(self, message: str, attempts: int) -> None:
        with S.project_lock("proposal-retry"):
            task = S.load_task("proposal-retry", self.task["slug"])
            task["proposal_error"] = {"message": message, "at": S.now()}
            task["proposal_attempts"] = attempts
            S.save_task("proposal-retry", task)

    def run_failed_flow(self, message: str) -> tuple[RuntimeError, str]:
        choice = {"engine": "codex", "why": "test"}
        failed = {"structured": None, "error": message, "turns": 1, "cost": 0.0}
        with patch.object(route, "pick_engine", return_value=choice), patch.object(
            engines, "codex_exec", return_value=failed
        ) as engine:
            with self.assertRaises(RuntimeError) as raised:
                server.run_proposal_flow("proposal-retry", self.task["slug"])
        return raised.exception, engine.call_args.args[0]

    def test_failed_flow_respawns_twice_then_parks_with_event_and_fyi(self):
        message = "proposal files entry 'missing.py' is invalid: request cites docs/ROLES.md"
        retry_spawns = []

        with patch.object(server, "spawn", side_effect=lambda *args: retry_spawns.append(args) or True):
            prompts = []
            for attempt in range(1, 4):
                raised, prompt = self.run_failed_flow(message)
                prompts.append(prompt)
                self.assertEqual(str(raised), message)
                task = S.load_task("proposal-retry", self.task["slug"])
                self.assertEqual(task["proposal_attempts"], attempt)
                if attempt < 3:
                    self.assertEqual(task["state"], "requested")
                    self.assertTrue(server._resume_stale_proposal(
                        "proposal-retry", self.task["slug"], f"propose:proposal-retry:{self.task['slug']}"
                    ))

        self.assertNotIn(REJECTION_HEADING, prompts[0])
        self.assertIn(message, prompts[1])
        self.assertIn(message, prompts[2])
        self.assertEqual(len(retry_spawns), 2, "only two stale-run retries may be spawned")
        for call in retry_spawns:
            self.assertEqual(call[1:], (server.run_proposal_flow, "proposal-retry", self.task["slug"]))

        task = S.load_task("proposal-retry", self.task["slug"])
        self.assertEqual(task["state"], "parked")
        self.assertIsNone(task["proposal_started"])
        events = S.read_events("proposal-retry", self.task["slug"])
        terminal = [event for event in events if event.get("kind") == "proposal-failed"]
        self.assertEqual(len(terminal), 1)
        self.assertEqual((terminal[0]["attempts"], terminal[0]["message"]), (3, message))
        park = [event for event in events if event.get("kind") == "state" and event.get("to") == "parked"][-1]
        self.assertIn("3 attempts", park["reason"])
        self.assertIn(repr(message), park["reason"])
        fyis = [item for item in T.inbox("proposal-retry") if item.get("slug") == self.task["slug"]]
        self.assertEqual(len(fyis), 1)
        self.assertIn("3 proposal attempts", fyis[0]["text"])
        self.assertIn(repr(message), fyis[0]["text"])

    def test_three_pre_validation_crashes_reconcile_twice_then_park(self):
        key = f"propose:proposal-retry:{self.task['slug']}"
        retry_spawns = []

        def record_spawn(*args):
            retry_spawns.append(args)
            return True

        tick_patches = (
            patch.object(server.quota_codex, "refresh_if_due"),
            patch.object(server, "drain_hook_faults"),
            patch.object(server.dispatch, "poll", return_value=[]),
            patch.object(server, "resume_stranded_reports"),
            patch.object(server.dispatch, "resume_due", return_value=[]),
            patch.object(server, "dispatch_waiting"),
            patch.object(server, "weekly_audit"),
            patch.object(server, "morning_digest"),
            patch.object(server, "spawn", side_effect=record_spawn),
            patch.dict(server._bg, {}, clear=True),
        )
        for context in tick_patches:
            context.start()
            self.addCleanup(context.stop)

        for attempt in range(1, 4):
            with S.project_lock("proposal-retry"):
                task = S.load_task("proposal-retry", self.task["slug"])
                task["proposal_started"] = "2001-01-01T00:00:00+00:00"
                task["proposal_attempts"] = attempt
                if attempt > 1:
                    task["proposal_error"] = {
                        "message": server.STALE_PROPOSAL_FAILURE,
                        "at": "2000-01-01T00:00:00+00:00",
                    }
                S.save_task("proposal-retry", task)
            server.tick()

            task = S.load_task("proposal-retry", self.task["slug"])
            self.assertEqual(task["proposal_attempts"], attempt)
            self.assertEqual(task["proposal_error"]["message"], server.STALE_PROPOSAL_FAILURE)
            if attempt < 3:
                self.assertEqual(task["state"], "requested")
                self.assertIsNone(task["proposal_started"])

        self.assertEqual(retry_spawns, [
            (key, server.run_proposal_flow, "proposal-retry", self.task["slug"]),
            (key, server.run_proposal_flow, "proposal-retry", self.task["slug"]),
        ])
        task = S.load_task("proposal-retry", self.task["slug"])
        self.assertEqual(task["state"], "parked")
        self.assertIsNone(task["proposal_started"])
        terminal = [event for event in S.read_events("proposal-retry", self.task["slug"])
                    if event.get("kind") == "proposal-failed"]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["attempts"], 3)
        self.assertEqual(terminal[0]["message"], server.STALE_PROPOSAL_FAILURE)

    def test_propose_endpoint_clears_failure_state_before_spawning(self):
        self.seed_failure("obsolete validation message", 2)
        with S.project_lock("proposal-retry"):
            task = S.load_task("proposal-retry", self.task["slug"])
            task["proposal_started"] = "2001-01-01T00:00:00+00:00"
            S.save_task("proposal-retry", task)

        observed = []

        def observe_spawn(*args):
            task = S.load_task("proposal-retry", self.task["slug"])
            observed.append((args, task))
            return True

        handler = object.__new__(server.Handler)
        handler.path = "/api/task/action"
        handler._body = lambda: {
            "project": "proposal-retry",
            "slug": self.task["slug"],
            "action": "propose",
        }
        responses = []
        handler._json = lambda body, code=200: responses.append((code, body))

        with patch.object(server, "spawn", side_effect=observe_spawn):
            handler.do_POST()

        self.assertEqual(responses, [(200, {"ok": True, "state": "requested"})])
        self.assertEqual(len(observed), 1)
        args, task_at_spawn = observed[0]
        self.assertEqual(args, (
            f"propose:proposal-retry:{self.task['slug']}",
            server.run_proposal_flow,
            "proposal-retry",
            self.task["slug"],
        ))
        self.assertIsNone(task_at_spawn["proposal_started"])
        self.assertNotIn("proposal_error", task_at_spawn)
        self.assertEqual(task_at_spawn["proposal_attempts"], 0)

    def test_new_proposal_revision_and_unpark_clear_failure_state(self):
        self.assertEqual(S.load_task("proposal-retry", self.task["slug"])["proposal_attempts"], 0)

        self.seed_failure("first failure", 2)
        T.park("proposal-retry", self.task["slug"], "wait", actor="burak")
        unparked = T.unpark("proposal-retry", self.task["slug"], actor="l3")
        self.assertNotIn("proposal_error", unparked)
        self.assertEqual(unparked["proposal_attempts"], 0)

        self.seed_failure("second failure", 2)
        proposed = T.propose("proposal-retry", self.task["slug"], "# Proposal\n", {**BASE_PROPOSAL, "files": []})
        self.assertNotIn("proposal_error", proposed)
        self.assertEqual(proposed["proposal_attempts"], 0)

        self.seed_failure("third failure", 2)
        revised = T.revise("proposal-retry", self.task["slug"], "Use the narrower path.", actor="burak")
        self.assertNotIn("proposal_error", revised)
        self.assertEqual(revised["proposal_attempts"], 0)

    def test_successful_flow_clears_error_and_attempt_count(self):
        self.seed_failure("obsolete validation message", 2)
        proposal = {**BASE_PROPOSAL, "files": []}

        def turn(project, _prompt, **_kwargs):
            T.propose(project, self.task["slug"], "# Proposal\n", proposal)
            return {"_turn_started_at": S.now()}

        with patch.object(propose, "run_proposal", return_value=proposal), patch.object(l3, "turn", side_effect=turn):
            server.run_proposal_flow("proposal-retry", self.task["slug"])

        task = S.load_task("proposal-retry", self.task["slug"])
        self.assertEqual(task["state"], "approved")
        self.assertNotIn("proposal_error", task)
        self.assertEqual(task["proposal_attempts"], 0)

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
            task["proposal_attempts"] = 2
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
        self.assertEqual(S.load_task("proposal-retry", self.task["slug"])["proposal_attempts"], 2)


if __name__ == "__main__":
    unittest.main()
