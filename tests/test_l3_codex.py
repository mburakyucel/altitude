"""An L3 turn uses Codex when the Claude window is exhausted before or during the turn."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-l3codex-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, engines, l3, state as S  # noqa: E402


class TestCodexL3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        (_TMP / "repo").mkdir()
        config.save_projects({"k": {"name": "k", "path": str(_TMP / "repo")}})

    def _run(self, held: bool, claude_limited: bool = False):
        calls = []
        real_c, real_x = engines.claude_print, engines.codex_exec
        p = engines.usage_limit_path()
        if held:
            p.write_text(json.dumps({"until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}))
        elif p.exists():
            p.unlink()
        def fake_claude(prompt, **kw):
            calls.append("claude")
            return {"text": "", "session_id": "" if claude_limited else "sid-1", "usage": {}, "context_tokens": 10, "cost": 0.0,
                    "turns": 1, "structured": None, "error": "usage limit" if claude_limited else None, "tools": [],
                    "limited": "2099-01-01T00:00:00+00:00" if claude_limited else None}
        def fake_codex(prompt, **kw):
            calls.append("codex")
            self.assertIn("Engine: Codex", prompt); self.assertIn("state file", prompt)
            self.assertEqual(kw.get("sandbox"), "workspace-write")
            self.assertEqual(kw["extra_env"]["ALTITUDE_PROJECT"], "k")
            return {"text": "done on codex", "structured": None, "error": None, "usage": {"input_tokens": 1200, "output_tokens": 300}, "returncode": 0}
        engines.claude_print, engines.codex_exec = fake_claude, fake_codex
        try:
            res = l3.turn("k", "hello", trigger="chat")
        finally:
            engines.claude_print, engines.codex_exec = real_c, real_x
            if p.exists():
                p.unlink()
        return calls, res

    def test_held_window_goes_straight_to_codex(self):
        calls, res = self._run(held=True)
        self.assertEqual(calls, ["codex"])
        self.assertEqual((res["engine"], res["text"]), ("codex", "done on codex"))
        self.assertIn("exhausted", res["degraded"])
        inf = l3.info("k")
        self.assertEqual(inf.get("engine_last"), "codex"); self.assertEqual(inf.get("codex_turns"), 1)
        self.assertEqual(l3.chat_history("k", 5)[-1].get("engine"), "codex")

    def test_window_closing_mid_turn_reruns_on_codex(self):
        calls, res = self._run(held=False, claude_limited=True)
        self.assertEqual(calls, ["claude", "codex"])
        self.assertEqual(res["engine"], "codex")

    def test_open_window_stays_on_claude(self):
        calls, res = self._run(held=False)
        self.assertEqual(calls, ["claude"]); self.assertNotIn("engine", res)


if __name__ == "__main__":
    unittest.main()
