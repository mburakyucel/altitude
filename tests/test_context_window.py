"""Claude sessions use one 300k compaction boundary and report percentages against the 1M window."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="altitude-ctx-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
os.environ["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = "30"  # a stray override in the parent must not reach children
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, engines, dispatch, monitor, tasks as T  # noqa: E402


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

    def test_legacy_claude_settings_remain_read_only_not_an_l2_launch_surface(self):
        env = engines.clean_env()
        self.assertNotIn("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", env)
        p = engines.claude_settings()
        self.assertEqual(json.loads(p.read_text()), {"autoCompactWindow": 300_000})
        self.assertFalse(hasattr(dispatch, "session_settings"))

    def test_synthetic_zero_usage_does_not_reset_visible_context(self):
        root = _TMP / "fake-home"; transcript = root / ".claude" / "projects" / "p" / "sid.jsonl"
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
