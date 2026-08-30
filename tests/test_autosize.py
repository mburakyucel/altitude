"""Decision 53: auto-sized intake — class None until the sizer runs; S approved at once; failure is a fault, not a default."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-size-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, engines, improve, intake, state as S, tasks as T  # noqa: E402


class TestAutoSize(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        (_TMP / "repo").mkdir()
        config.save_projects({"z": {"name": "z", "path": str(_TMP / "repo"), "stacks": ["python"]}})

    def _fake(self, structured=None, error=None):
        def fake(prompt, **kw):  # decision 56: the sizer is Codex — read-only sandbox, strict schema, persona inline
            self.assertEqual(kw.get("sandbox", "read-only"), "read-only")
            self.assertTrue(str(kw.get("schema")).endswith("size.json"))
            self.assertIn("intake sizer", prompt)
            return {"structured": structured, "error": error, "usage": {}, "returncode": 0}
        return fake

    def test_task_l1_engine_override_does_not_override_fixed_codex_sizer(self):
        task = T.new("z", "size despite L1 override", "auto", "size me", actor="burak", engine="claude")
        real_codex, real_claude = engines.codex_exec, engines.claude_print
        engines.codex_exec = self._fake({"class": "S", "why": "small", "paths": []})
        engines.claude_print = lambda *_a, **_kw: self.fail("task.engine must not route the sizer")
        try:
            result = intake.size("z", task["slug"])
        finally:
            engines.codex_exec, engines.claude_print = real_codex, real_claude
        self.assertEqual(result["class"], "S")
        event = [row for row in S.read_events("z", task["slug"]) if row.get("kind") == "size-run"][-1]
        self.assertEqual(event["engine"], "codex")

    def test_auto_leaves_class_unset_and_s_is_approved_after_sizing(self):
        t = T.new("z", "fix a typo", "auto", "fix the typo in README", actor="burak")
        self.assertIsNone(t["class"]); self.assertEqual(t["envelope"], {})
        real = engines.codex_exec
        engines.codex_exec = self._fake({"class": "S", "why": "doc sync", "paths": ["README.md"]})
        try:
            res = intake.size("z", t["slug"])
        finally:
            engines.codex_exec = real
        t2 = S.load_task("z", t["slug"])
        self.assertEqual(res["class"], "S")
        self.assertEqual(t2["class"], "S"); self.assertEqual(t2["envelope"]["subagent_launches"], 3)
        self.assertEqual(t2["paths"], ["README.md"]); self.assertEqual(t2["state"], "approved")
        self.assertIn("sized", [e.get("kind") for e in S.read_events("z", t["slug"])])

    def test_m_stays_requested_for_the_proposal_flow(self):
        t = T.new("z", "add a feature", "auto", "add the thing", actor="burak", paths=["altitude/x.py"])
        real = engines.codex_exec
        engines.codex_exec = self._fake({"class": "M", "why": "feature within architecture", "paths": ["altitude/y.py"]})
        try:
            intake.size("z", t["slug"])
        finally:
            engines.codex_exec = real
        t2 = S.load_task("z", t["slug"])
        self.assertEqual((t2["class"], t2["state"]), ("M", "requested"))
        self.assertEqual(t2["paths"], ["altitude/x.py"], "declared paths win over the sizer's")

    def test_failure_is_a_fault_not_a_default_class(self):
        t = T.new("z", "vague", "auto", "do something", actor="burak")
        faults = []
        real, real_fault = engines.codex_exec, improve.system_fault
        engines.codex_exec = self._fake(None, error="boom")
        improve.system_fault = lambda kind, detail, **kw: faults.append(kind)
        try:
            with self.assertRaises(RuntimeError):
                intake.size("z", t["slug"])
        finally:
            engines.codex_exec, improve.system_fault = real, real_fault
        t2 = S.load_task("z", t["slug"])
        self.assertIsNone(t2["class"]); self.assertEqual(t2["state"], "requested"); self.assertIn("boom", t2["size_error"])
        self.assertEqual(faults, ["sizer"])
        T.set_class("z", t["slug"], "S", "by hand", actor="l3")
        self.assertIsNone(S.load_task("z", t["slug"])["size_error"])

    def test_explicit_class_still_works_and_bad_class_refused(self):
        t = T.new("z", "explicit", "M", "r", actor="l3")
        self.assertEqual(t["class"], "M")
        with self.assertRaises(T.TransitionError):
            T.new("z", "bad", "XL", "r", actor="l3")


if __name__ == "__main__":
    unittest.main()
