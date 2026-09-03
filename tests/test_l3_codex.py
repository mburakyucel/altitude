"""L3 preserves one resumable conversation per provider and never replays a partial turn."""
import unittest
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, l3


class TestL3Sessions(AltitudeCase):
    @staticmethod
    def choice(engine):
        return {"engine": engine, "why": f"test chose {engine}", "quota": {}}

    def test_codex_second_turn_resumes_the_same_thread(self):
        calls = []

        def fake_codex(prompt, **kwargs):
            calls.append((prompt, kwargs.get("resume")))
            sid = kwargs.get("resume") or "cx-1"
            return {"text": "codex answer", "session_id": sid, "reported_session_id": sid,
                    "error": None, "usage": {"input_tokens": 1200, "cached_input_tokens": 900}, "returncode": 0}

        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", side_effect=fake_codex):
            first = l3.turn(self.project, "first")
            second = l3.turn(self.project, "second")

        self.assertEqual((first["session_id"], second["session_id"]), ("cx-1", "cx-1"))
        self.assertIsNone(calls[0][1]); self.assertEqual(calls[1][1], "cx-1")
        self.assertIn("Engine: Codex", calls[0][0])
        self.assertNotIn("# You are the L3", calls[1][0], "persona is not replayed into a resumed transcript")
        self.assertEqual(l3.info(self.project)["sessions"]["codex"]["usage"]["cached_input_tokens"], 900)

    def test_codex_l3_runs_the_shared_persona_from_a_scratch_directory(self):
        seen = {}

        def fake_codex(prompt, **kwargs):
            seen.update(kwargs, prompt=prompt)
            return {"text": "coordinated", "session_id": "cx-state", "reported_session_id": "cx-state",
                    "error": None, "usage": {"input_tokens": 100}, "returncode": 0}

        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", side_effect=fake_codex):
            out = l3.turn(self.project, "coordinate this")

        self.assertEqual(Path(seen["cwd"]), config.project_dir(self.project) / "l3-codex-runtime")
        self.assertTrue(seen["prompt"].startswith((config.PERSONAS / "l3.md").read_text()), "one persona per role")
        self.assertEqual(seen["extra_env"]["ALTITUDE_ACTOR"], "l3")
        self.assertNotIn("schema", seen)
        self.assertEqual((out["completed"], out["text"]), (True, "coordinated"))

    def test_alternating_providers_preserves_both_session_ids(self):
        choices = [self.choice("claude"), self.choice("codex"), self.choice("claude")]
        claude_resumes = []

        def fake_claude(prompt, **kwargs):
            claude_resumes.append(kwargs.get("resume"))
            return {"text": "claude answer", "session_id": kwargs.get("resume") or "cl-1", "usage": {},
                    "context_tokens": 100, "cost": 0.0, "turns": 1, "structured": None, "error": None,
                    "tools": [], "limited": None}

        def fake_codex(_prompt, **kwargs):
            sid = kwargs.get("resume") or "cx-1"
            return {"text": "codex answer", "session_id": sid, "reported_session_id": sid, "error": None,
                    "usage": {"input_tokens": 100}, "returncode": 0}

        with mock.patch.object(l3, "_select", side_effect=choices), \
             mock.patch.object(engines, "claude_print", side_effect=fake_claude), \
             mock.patch.object(engines, "codex_exec", side_effect=fake_codex):
            l3.turn(self.project, "one"); l3.turn(self.project, "two"); l3.turn(self.project, "three")

        self.assertEqual(claude_resumes, [None, "cl-1"])
        sessions = l3.info(self.project)["sessions"]
        self.assertEqual(sessions["claude"]["session_id"], "cl-1")
        self.assertEqual(sessions["codex"]["session_id"], "cx-1")

    def test_codex_first_does_not_migrate_its_thread_into_claude(self):
        choices = [self.choice("codex"), self.choice("claude")]

        def fake_codex(_prompt, **_kwargs):
            return {"text": "codex answer", "session_id": "cx-1", "reported_session_id": "cx-1",
                    "error": None, "usage": {"input_tokens": 100}, "returncode": 0}

        def fake_claude(_prompt, **kwargs):
            self.assertIsNone(kwargs.get("resume"))
            return {"text": "claude answer", "session_id": "cl-1", "usage": {},
                    "context_tokens": 100, "cost": 0.0, "turns": 1, "structured": None, "error": None,
                    "tools": [], "limited": None}

        with mock.patch.object(l3, "_select", side_effect=choices), \
             mock.patch.object(engines, "codex_exec", side_effect=fake_codex), \
             mock.patch.object(engines, "claude_print", side_effect=fake_claude):
            l3.turn(self.project, "one"); l3.turn(self.project, "two")

        sessions = l3.info(self.project)["sessions"]
        self.assertEqual(sessions["codex"]["session_id"], "cx-1")
        self.assertEqual(sessions["claude"]["session_id"], "cl-1")

    def test_codex_resume_rejects_a_different_thread(self):
        l3.save_info(self.project, {"sessions": {"codex": {"session_id": "cx-1"}}, "engine_last": "codex"})
        result = {"text": "wrong thread answer", "session_id": "cx-2", "reported_session_id": "cx-2",
                  "error": None, "usage": {"input_tokens": 100}, "returncode": 0}
        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", return_value=result):
            out = l3.turn(self.project, "continue")
        self.assertFalse(out["completed"])
        self.assertIn("different thread", out["error"])
        self.assertEqual(l3.info(self.project)["sessions"]["codex"]["session_id"], "cx-1")

    def test_partial_limited_claude_turn_is_not_replayed_on_codex(self):
        result = {"text": "I already changed state", "session_id": "cl-1", "usage": {},
                  "context_tokens": 100, "cost": 0.0, "turns": 1, "structured": None,
                  "error": "usage limit", "tools": ["Bash"], "limited": "2099-01-01T00:00:00+00:00"}
        with mock.patch.object(l3, "_select", return_value=self.choice("claude")), \
             mock.patch.object(engines, "claude_print", return_value=result), \
             mock.patch.object(engines, "codex_exec") as codex:
            out = l3.turn(self.project, "do one thing")
        codex.assert_not_called()
        self.assertEqual(out["engine"], "claude")


if __name__ == "__main__":
    unittest.main()
