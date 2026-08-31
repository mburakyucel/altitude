"""Decision 49: one 300k umbrella for every Claude session — explicit autoCompactWindow on each launch, percentages
against the real 1M window, L3 act line at 300k."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-ctx-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
os.environ["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = "30"  # a stray override in the parent must not reach children
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, engines, dispatch, tasks as T  # noqa: E402


class TestContextWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": str(_TMP)}})

    def test_umbrella_numbers(self):
        self.assertEqual(config.CONTEXT_WINDOW, 1_000_000)
        self.assertEqual(config.AUTOCOMPACT_WINDOW, 300_000)
        self.assertEqual(engines.context_percent(300_000), 30.0)
        self.assertEqual(engines.context_state(30.0), "act")
        self.assertEqual(engines.context_state(6.3), "ok")

    def test_every_launch_states_the_window_explicitly(self):
        env = engines.clean_env()
        self.assertNotIn("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", env)
        p = engines.claude_settings()
        self.assertEqual(json.loads(p.read_text()), {"autoCompactWindow": 300_000})
        T.new("altitude", "ctx-task", "req")
        sp = dispatch.session_settings("altitude", "ctx-task", "key")
        st = json.loads(sp.read_text())
        self.assertEqual(st["autoCompactWindow"], 300_000)
        self.assertNotIn("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", st.get("env", {}))


if __name__ == "__main__":
    unittest.main()
