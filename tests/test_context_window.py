"""Claude sessions use one 300k compaction boundary and report percentages against the 1M window."""
import json
import unittest

from tests.support import AltitudeCase
from altitude import config, engines, dispatch, tasks as T


class TestContextWindow(AltitudeCase):
    def test_umbrella_numbers(self):
        self.assertEqual(config.CONTEXT_WINDOW, 1_000_000)
        self.assertEqual(config.AUTOCOMPACT_WINDOW, 300_000)
        self.assertEqual(engines.context_percent(300_000), 30.0)
        self.assertEqual(engines.context_state(30.0), "act")
        self.assertEqual(engines.context_state(6.3), "ok")

    def test_every_launch_states_the_window_explicitly(self):
        self.setenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "30")  # a stray override in the parent must not reach children
        env = engines.clean_env()
        self.assertNotIn("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", env)
        p = engines.claude_settings()
        self.assertEqual(json.loads(p.read_text()), {"autoCompactWindow": 300_000})
        T.new(self.project, "ctx-task", "req")
        sp = dispatch.session_settings(self.project, "ctx-task", "key")
        st = json.loads(sp.read_text())
        self.assertEqual(st["autoCompactWindow"], 300_000)
        self.assertNotIn("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", st.get("env", {}))


if __name__ == "__main__":
    unittest.main()
