"""Claude sessions use one 300k compaction boundary and report percentages against the 1M window."""
import json
import unittest
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, dispatch, monitor, tasks as T


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

    def test_synthetic_zero_usage_does_not_reset_visible_context(self):
        root = self.tmp / "fake-home"; transcript = root / ".claude" / "projects" / "p" / "sid.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        real = {"type": "assistant", "message": {"model": "claude-opus", "usage": {
            "input_tokens": 1000, "cache_read_input_tokens": 135000, "cache_creation_input_tokens": 0}}}
        synthetic = {"type": "assistant", "message": {"model": "<synthetic>", "usage": {
            "input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}
        transcript.write_text(json.dumps(real) + "\n" + json.dumps(synthetic) + "\n")
        with mock.patch.object(Path, "home", return_value=root):
            self.assertEqual(monitor.transcript_context_percent("sid", None), 13.6)


if __name__ == "__main__":
    unittest.main()
