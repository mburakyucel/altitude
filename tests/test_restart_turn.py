"""An L3 session rotation is durable before a conversational turn starts."""
import unittest

from tests.support import AltitudeCase
from altitude import engines, l3, state as S


class TestRotationIsPersistedBeforeTheTurn(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.quiet_engines()
        self.register(self.project, l3_engine="claude")

    def test_a_restart_mid_turn_does_not_rotate_the_same_session_again(self):
        l3.save_info(self.project, {
            "sessions": {"claude": {"session_id": "e9aa9612", "rotate_next": True, "context_percent": 91.0, "turns": 12}},
            "session_id": "e9aa9612", "engine_last": "claude",
        })
        seen: dict = {}

        def crash(prompt, **kwargs):
            seen["resume"] = kwargs.get("resume")
            seen["on_disk"] = l3.info(self.project)
            raise RuntimeError("altd restarted mid-turn")

        def ok(prompt, **kwargs):
            seen["resume_after_restart"] = kwargs.get("resume")
            return {
                "text": "hello", "session_id": "f00d1234", "usage": {}, "context_tokens": 1000,
                "cost": 0.1, "turns": 1, "structured": None, "error": None, "tools": [], "limited": None,
            }

        self.patch(engines, "claude_print", new=crash)
        with self.assertRaises(RuntimeError):
            l3.turn(self.project, "the turn the restart cut short")
        self.patch(engines, "claude_print", new=ok)
        l3.turn(self.project, "the first turn after the restart")

        self.assertIsNone(seen["resume"])
        self.assertIsNone(seen["on_disk"]["session_id"])
        self.assertFalse(seen["on_disk"]["rotate_next"])
        self.assertEqual(seen["on_disk"]["rotated_from"], "e9aa9612")
        self.assertIsNone(seen["resume_after_restart"])
        rotations = [event for event in S.read_project_log(self.project)
                     if event.get("kind") == "l3-rotate" and event.get("old") == "e9aa9612"]
        self.assertEqual(len(rotations), 1)
        self.assertEqual(l3.info(self.project)["session_id"], "f00d1234")
        self.assertEqual(l3.info(self.project)["turns"], 1)


if __name__ == "__main__":
    unittest.main()
