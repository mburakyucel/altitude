"""A system fault blocks its task, files one incident per kind, and leaves one message for L3."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-faults-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, incidents, l3, verify, engines, dispatch, tasks as T  # noqa: E402


def inbox_texts() -> list[str]:
    p = config.project_dir("altitude") / "inbox.jsonl"
    return [json.loads(line)["text"] for line in p.read_text().splitlines()] if p.exists() else []


def queued() -> list[dict]:
    p = l3.queue_path("altitude")
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


class TestSystemFault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP}})
        os.makedirs(os.path.join(_TMP, "docs"), exist_ok=True)

    def setUp(self):
        incidents.FAULTS.unlink(missing_ok=True)
        l3.queue_path("altitude").unlink(missing_ok=True)
        self._before = {t["slug"] for t in S.list_tasks("altitude")}

    def tearDown(self):  # discovery shares one ALTITUDE_HOME across modules: leave no probe task behind
        for t in S.list_tasks("altitude"):
            if t["slug"] not in self._before:
                shutil.rmtree(S.task_dir("altitude", t["slug"]), ignore_errors=True)

    def test_fault_blocks_its_task_files_one_incident_and_queues_one_l3_message(self):
        task = T.new("altitude", "fault probe", "request", actor="burak")
        first = incidents.system_fault("test-kind", "something broke", project="altitude", task=task["slug"])
        self.assertTrue(first["incident"].startswith("I-"))
        blocked = S.load_task("altitude", task["slug"])
        self.assertEqual(blocked["state"], "blocked")
        self.assertIn("system fault [test-kind]", blocked["blocked_reason"])
        self.assertEqual([row["trigger"] for row in queued()], ["incident"])
        self.assertIn(first["incident"], queued()[0]["text"])
        again = incidents.system_fault("test-kind", "something broke again", project="altitude")
        self.assertIsNone(again, "same kind within 24h must not file a second incident")
        faults = S.read_json(incidents.FAULTS)
        self.assertEqual(faults["test-kind"]["count"], 2)
        self.assertEqual(faults["test-kind"]["incident"], first["incident"])
        self.assertEqual(sum("SYSTEM FAULT [test-kind]" in text for text in inbox_texts()), 1)
        self.assertEqual(len(queued()), 1, "a repeated kind must not queue a second L3 message")
        other = incidents.system_fault("other-kind", "different mechanism")
        self.assertNotEqual(other["incident"], first["incident"])
        self.assertEqual(len(queued()), 2)

    def test_repair_task_fault_reaches_the_inbox_without_waking_l3(self):
        task = T.new("altitude", "repair probe", "request", actor="burak", source="recovery")
        incidents.system_fault("repair-kind", "repair broke", project="altitude", task=task["slug"])
        self.assertEqual(S.load_task("altitude", task["slug"])["state"], "blocked")
        self.assertEqual(queued(), [])
        self.assertTrue(any("[repair-kind]" in text and "not woken" in text for text in inbox_texts()))

    def test_task_blocked_before_launch_is_queued_again_on_resume(self):
        task = T.new("altitude", "requeue probe", "request", actor="burak")
        incidents.system_fault("launch-kind", "launch broke", project="altitude", task=task["slug"])
        res = dispatch.resume_blocked("altitude", task["slug"], "cause fixed")
        self.assertTrue(res["requeued"])
        self.assertEqual(S.load_task("altitude", task["slug"])["state"], "queued")

    def test_queued_message_is_delivered_once_as_one_l3_turn(self):
        l3.queue_message("altitude", "hello L3", trigger="incident")
        with mock.patch.object(l3, "turn", return_value={"completed": True}) as turn, \
             mock.patch.object(l3, "_select", return_value={"engine": "claude", "why": "test"}):
            self.assertEqual(l3.deliver_queued("altitude"), {"completed": True})
            self.assertIsNone(l3.deliver_queued("altitude"))
        turn.assert_called_once_with("altitude", "hello L3", trigger="incident")
        self.assertFalse(l3.queue_path("altitude").exists())

    def test_queued_message_waits_for_an_engine(self):
        l3.queue_message("altitude", "hello L3", trigger="incident")
        with mock.patch.object(l3, "turn") as turn, \
             mock.patch.object(l3, "_select", return_value={"engine": None, "why": "both windows exhausted"}):
            self.assertIsNone(l3.deliver_queued("altitude"))
        turn.assert_not_called()
        self.assertEqual(len(queued()), 1)

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
            S.task_dir("altitude", "poll-probe").mkdir(parents=True, exist_ok=True)
            S.save_task("altitude", {"slug": "poll-probe", "title": "poll-probe", "state": "running",
                                     "l2_engine": "claude", "created": S.now(), "updated": S.now()})
            with self.assertRaises(RuntimeError):
                dispatch.poll("altitude")  # must propagate, never report "all L2s gone"
        finally:
            config.CLAUDE_BIN = old

    def test_verifier_tooling_failure_is_a_fault_verdict(self):
        old = verify.gh
        verify.gh = lambda *a, **k: (_ for _ in ()).throw(verify.VerifierFault("gh: network down"))
        try:
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
            self.assertEqual(S.load_task("altitude", task["slug"])["state"], "blocked")
        finally:
            verify.gh = old


if __name__ == "__main__":
    unittest.main()
