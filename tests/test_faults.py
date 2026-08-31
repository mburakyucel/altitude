"""Altitude's own faults are raised, not papered over. Runs against a throwaway ALTITUDE_HOME."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="altitude-faults-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, incidents, recovery, verify, engines, dispatch  # noqa: E402


class TestSystemFault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP}})
        os.makedirs(os.path.join(_TMP, "docs"), exist_ok=True)

    def tearDown(self):
        recovery.hold_path().unlink(missing_ok=True)

    def test_fault_files_incident_and_inbox_once_per_kind(self):
        first = incidents.system_fault("test-kind", "something broke", project="altitude", task="t1")
        self.assertIsNotNone(first)
        self.assertTrue(first["incident"].startswith("I-"))
        again = incidents.system_fault("test-kind", "something broke again", project="altitude")
        self.assertIsNone(again, "same kind within 24h must not file a second incident")
        faults = S.read_json(incidents.FAULTS)
        self.assertEqual(faults["test-kind"]["count"], 2)
        self.assertEqual(faults["test-kind"]["incident"], first["incident"])
        inbox = [json.loads(l) for l in (config.project_dir("altitude") / "inbox.jsonl").read_text().splitlines()]
        self.assertEqual(sum("SYSTEM FAULT [test-kind]" in i["text"] for i in inbox), 1)
        other = incidents.system_fault("other-kind", "different mechanism")
        self.assertIsNotNone(other)
        self.assertNotEqual(other["incident"], first["incident"])

    def test_corrupt_json_raises_missing_defaults(self):
        p = Path(_TMP) / "corrupt.json"
        p.write_text("{not json")
        with self.assertRaises(ValueError):
            S.read_json(p, {})
        self.assertEqual(S.read_json(Path(_TMP) / "absent.json", {"d": 1}), {"d": 1})

    def test_claude_agents_failure_raises_instead_of_empty_list(self):
        old = config.CLAUDE_BIN
        config.CLAUDE_BIN = "/nonexistent/claude"
        try:
            with self.assertRaises(RuntimeError):
                engines.claude_agents()
            with self.assertRaises(RuntimeError):
                dispatch.poll("altitude")  # must propagate, never report "all L2s gone"
        finally:
            config.CLAUDE_BIN = old

    def test_verifier_tooling_failure_is_a_fault_verdict(self):
        old = verify.gh
        verify.gh = lambda *a, **k: (_ for _ in ()).throw(verify.VerifierFault("gh: network down"))
        try:
            from altitude import tasks as T
            task = T.new("altitude", "verifier fault test", "request", actor="burak")
            task["state"] = "running"; S.save_task("altitude", task)
            d = S.task_dir("altitude", task["slug"])
            S.write_json(d / "report.json", {"landed": {"prs": [{"number": 1, "merged": True}], "main_runs": [], "deploy": "not-applicable"},
                                             "review": [], "deviations": [], "decisions": [], "fyi": [], "blocked": "", "follow_ups": [],
                                             "spend": {"turns": 1, "subagent_launches": 0, "retries": 0, "reverts": 0}})
            v = verify.verify("altitude", task["slug"])
            self.assertEqual(v["verdict"], "fault")
            self.assertIn("verifier fault", v["problems"][0])
            self.assertIn("verifier", S.read_json(incidents.FAULTS))
        finally:
            verify.gh = old


if __name__ == "__main__":
    unittest.main()
