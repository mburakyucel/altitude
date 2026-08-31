"""Decision 56: the L3 turn runs on Codex when the Claude window is exhausted (before the turn, or when it closes mid-turn)."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_TMP = Path(tempfile.mkdtemp(prefix="altitude-l3codex-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, engines, l3, monitor, route, state as S  # noqa: E402


class TestCodexL3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # unittest discovery may already have imported config against the live
        # home. Patch every runtime path this fixture touches instead of merely
        # changing ALTITUDE_HOME after import (I-029).
        cls._config_paths = {
            name: getattr(config, name)
            for name in ("ROOT", "PROJECTS_FILE", "MONITOR_DIR", "INCIDENT_INDEX", "DIGEST_FILE")
        }
        config.ROOT = _TMP
        config.PROJECTS_FILE = _TMP / "projects.json"
        config.MONITOR_DIR = _TMP / "monitor"
        config.INCIDENT_INDEX = _TMP / "incidents.jsonl"
        config.DIGEST_FILE = _TMP / "DIGEST.md"
        cls.addClassCleanup(cls._restore_config_paths)
        config.ensure_root()
        (_TMP / "repo").mkdir(exist_ok=True)
        config.save_projects({"k": {"name": "k", "path": str(_TMP / "repo"), "stacks": ["python"]}})

    @classmethod
    def _restore_config_paths(cls):
        for name, value in cls._config_paths.items():
            setattr(config, name, value)

    def _run(self, held: bool, claude_limited: bool = False):
        calls = []
        real_c, real_x, real_route = engines.claude_print, engines.codex_exec, route.pick_l3_engine
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
            self.assertEqual(kw.get("sandbox"), "read-only")
            self.assertIn("features.hooks=true", kw["extra_config"])
            self.assertFalse(any("sandbox_" in item for item in kw["extra_config"]))
            self.assertEqual(kw["permission_role"], "l3")
            self.assertTrue(kw["bypass_hook_trust"])
            self.assertTrue(kw["generation_isolation"])
            self.assertTrue(any("hooks.PreToolUse" in item and "codex_l3_guard.py" in item
                                for item in kw["extra_config"]))
            self.assertEqual(kw["extra_env"]["ALTITUDE_PROJECT"], "k")
            self.assertEqual(kw["extra_env"]["ALTITUDE_TRIGGER"], "chat")
            self.assertEqual(Path(kw["cwd"]), _TMP / "repo")
            self.assertNotIn("ALTITUDE_ALT_BROKER_SOCKET", kw["extra_env"])
            self.assertIn("ALTITUDE_ALT_BROKER_FD", kw["extra_env"])
            self.assertIn("ALTITUDE_ALT_BROKER_LOCK_FD", kw["extra_env"])
            self.assertEqual(tuple(map(int, (kw["extra_env"]["ALTITUDE_ALT_BROKER_FD"],
                                             kw["extra_env"]["ALTITUDE_ALT_BROKER_LOCK_FD"]))),
                             kw["broker_fds"])
            self.assertIn("Current state snapshot", prompt)
            return {"text": "done on codex", "structured": None, "error": None, "usage": {"input_tokens": 1200, "output_tokens": 300}, "session_id": "codex-thread", "returncode": 0}
        engines.claude_print, engines.codex_exec = fake_claude, fake_codex
        route.pick_l3_engine = lambda **kw: {"engine": "codex" if held else "claude", "why": "Claude window exhausted" if held else "test route"}
        try:
            res = l3.turn("k", "hello", trigger="chat")
        finally:
            engines.claude_print, engines.codex_exec, route.pick_l3_engine = real_c, real_x, real_route
            if p.exists():
                p.unlink()
        return calls, res

    def test_held_window_goes_straight_to_codex(self):
        calls, res = self._run(held=True)
        self.assertEqual(calls, ["codex"])
        self.assertEqual((res["engine"], res["text"], res["session_id"]), ("codex", "done on codex", "codex-thread"))
        self.assertIn("exhausted", res["degraded"])
        inf = l3.info("k")
        self.assertEqual(inf.get("engine_last"), "codex"); self.assertGreaterEqual(inf.get("codex_turns"), 1)
        self.assertEqual(l3.chat_history("k", 5)[-1].get("engine"), "codex")
        with patch.object(monitor, "codex_context_percent", return_value=12.5):
            public = l3.public_info("k")
        self.assertEqual((public["engine"], public["session_id"], public["context_percent"], public["turns"]),
                         ("codex", "codex-thread", 12.5, inf.get("codex_turns")))
        self.assertNotIn("codex_context_percent", public)
        self.assertFalse(public["rotate_next"]); self.assertEqual(public["context_state"], "ok")

    def test_window_closing_mid_turn_reruns_on_codex(self):
        calls, res = self._run(held=False, claude_limited=True)
        self.assertEqual(calls, ["claude", "codex"])
        self.assertEqual(res["engine"], "codex")

    def test_open_window_stays_on_claude(self):
        calls, res = self._run(held=False)
        self.assertEqual(calls, ["claude"]); self.assertNotIn("engine", res)


if __name__ == "__main__":
    unittest.main()
