"""An L3 session rotation is durable before a conversational turn starts."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="altitude-restart-turn-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, engines, l3, state as S  # noqa: E402

PROJECT = "restartdup"


def register() -> None:
    config.ensure_root()
    projects = config.load_projects()
    projects[PROJECT] = {"name": PROJECT, "path": config.ROOT.as_posix(), "l3_engine": "claude"}
    config.save_projects(projects)


class TestRotationIsPersistedBeforeTheTurn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register()

    def test_a_restart_mid_turn_does_not_rotate_the_same_session_again(self):
        l3.save_info(PROJECT, {
            "session_id": "e9aa9612", "rotate_next": True, "context_percent": 91.0, "turns": 12,
        })
        seen: dict = {}
        original = engines.claude_print

        def crash(prompt, **kwargs):
            seen["resume"] = kwargs.get("resume")
            seen["on_disk"] = l3.info(PROJECT)
            raise RuntimeError("altd restarted mid-turn")

        def ok(prompt, **kwargs):
            seen["resume_after_restart"] = kwargs.get("resume")
            return {
                "text": "hello", "session_id": "f00d1234", "usage": {}, "context_tokens": 1000,
                "cost": 0.1, "turns": 1, "structured": None, "error": None, "tools": [], "limited": None,
            }

        try:
            engines.claude_print = crash
            with self.assertRaises(RuntimeError):
                l3.turn(PROJECT, "the turn the restart cut short")
            engines.claude_print = ok
            l3.turn(PROJECT, "the first turn after the restart")
        finally:
            engines.claude_print = original

        self.assertIsNone(seen["resume"])
        self.assertIsNone(seen["on_disk"]["session_id"])
        self.assertFalse(seen["on_disk"]["rotate_next"])
        self.assertEqual(seen["on_disk"]["rotated_from"], "e9aa9612")
        self.assertIsNone(seen["resume_after_restart"])
        rotations = [event for event in S.read_project_log(PROJECT)
                     if event.get("kind") == "l3-rotate" and event.get("old") == "e9aa9612"]
        self.assertEqual(len(rotations), 1)
        self.assertEqual(l3.info(PROJECT)["session_id"], "f00d1234")
        self.assertEqual(l3.info(PROJECT)["turns"], 1)


if __name__ == "__main__":
    unittest.main()
