"""Decision 52: L3 raises a blocked task's envelope; task.json and the hook's envelope file agree; lowering is refused."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-env-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T  # noqa: E402


class TestRaiseEnvelope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        (_TMP / "repo").mkdir()
        config.save_projects({"e": {"name": "e", "path": str(_TMP / "repo"), "stacks": ["python"]}})

    def test_raise_updates_task_and_hook_file_and_refuses_lowering(self):
        t = T.new("e", "capped", "S", "r", actor="l3")
        t["state"] = "blocked"; t["dispatch_id"] = "capped-1"; S.save_task("e", t)
        env_file = config.MONITOR_DIR / "envelope-e--capped-1.json"
        S.write_json(env_file, {"project": "e", "slug": t["slug"], "dispatch_id": "capped-1", **t["envelope"]})
        T.raise_envelope("e", t["slug"], launches=4)
        self.assertEqual(S.load_task("e", t["slug"])["envelope"]["subagent_launches"], 4)
        self.assertEqual(S.read_json(env_file)["subagent_launches"], 4)
        self.assertEqual(S.read_json(env_file)["max_turns"], 40, "untouched fields stay")
        with self.assertRaises(T.TransitionError):
            T.raise_envelope("e", t["slug"], launches=3)
        kinds = [e.get("kind") or e.get("event") for e in S.read_events("e", t["slug"])] if hasattr(S, "read_events") else []
        if kinds:
            self.assertIn("envelope-raised", kinds)


if __name__ == "__main__":
    unittest.main()
