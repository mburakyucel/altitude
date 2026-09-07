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

    def test_live_model_is_saved_before_turn_finishes_and_replaced_on_resume(self):
        def fake_codex(prompt, **kwargs):
            model = "second-model" if kwargs["resume"] else "first-model"
            metadata = {"session_id": "cx-1", "engine_model": model, "engine_reasoning_effort": "high"}
            kwargs["on_session"](metadata)
            live = l3.info(self.project)
            self.assertEqual(live["engine_model"], model)
            self.assertEqual(live["sessions"]["codex"]["engine_reasoning_effort"], "high")
            return {"text": "answer", "reported_session_id": "cx-1", **metadata}

        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", side_effect=fake_codex):
            l3.turn(self.project, "first")
            l3.turn(self.project, "second")
        self.assertEqual(l3.info(self.project)["sessions"]["codex"]["engine_model"], "second-model")

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
            runtime = Path(kwargs["cwd"])
            seen["runtime_was_real"] = runtime.is_dir() and not runtime.is_symlink()
            return {"text": "coordinated", "session_id": "cx-state", "reported_session_id": "cx-state",
                    "error": None, "usage": {"input_tokens": 100}, "returncode": 0}

        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", side_effect=fake_codex):
            out = l3.turn(self.project, "coordinate this")

        runtime = Path(seen["cwd"])
        self.assertEqual(runtime.parent, config.project_dir(self.project),
                         "altd creates the fresh runtime inside this project's state directory")
        self.assertTrue(runtime.name.startswith("l3-codex-") and seen["runtime_was_real"],
                        "the daemon handoff never trusts a reusable or symlinked runtime path")
        self.assertFalse(runtime.exists(), "the fresh runtime is removed after the Codex turn")
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
        l3.save_info(self.project, {"sessions": {"codex": {"session_id": "cx-1",
            "confinement_version": l3.L3_CONFINEMENT_VERSION}}, "engine_last": "codex"})
        result = {"text": "wrong thread answer", "session_id": "cx-2", "reported_session_id": "cx-2",
                  "error": None, "usage": {"input_tokens": 100}, "returncode": 0}
        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", return_value=result):
            out = l3.turn(self.project, "continue")
        self.assertFalse(out["completed"])
        self.assertIn("different thread", out["error"])
        self.assertEqual(l3.info(self.project)["sessions"]["codex"]["session_id"], "cx-1")

    def test_sept7_legacy_codex_session_rotates_once_onto_coordinator_mcp(self):
        l3.save_info(self.project, {"sessions": {"codex": {"session_id": "legacy-shell"}},
                                   "engine_last": "codex"})
        result = {"text": "coordinator connected", "session_id": "mcp-thread", "reported_session_id": "mcp-thread",
                  "error": None, "usage": {"input_tokens": 100}, "returncode": 0}
        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", return_value=result) as execute:
            first = l3.turn(self.project, "check coordinator access")
            second = l3.turn(self.project, "check again")
        self.assertTrue(first["completed"] and second["completed"])
        self.assertEqual([call.kwargs["resume"] for call in execute.call_args_list], [None, "mcp-thread"])
        session = l3.info(self.project)["sessions"]["codex"]
        self.assertEqual(session["rotated_from"], "legacy-shell")
        self.assertEqual(session["confinement_version"], l3.L3_CONFINEMENT_VERSION)

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

    def test_turn_engine_pins_the_turn_over_project_pin_and_quota(self):
        seen = {}

        def pick(role, *, forced=None, current=None):
            seen.update({"forced": forced, "current": current})
            return self.choice(forced or "claude")

        with mock.patch.object(config, "project", return_value={"l3_engine": "claude"}), \
             mock.patch.object(engines, "usage_hold", return_value="2099-01-01T00:00:00+00:00"), \
             mock.patch.object(l3.route, "pick_engine", side_effect=pick):
            choice = l3._select(self.project, "codex")
        self.assertEqual(seen["forced"], "codex")
        self.assertEqual(choice["engine"], "codex")
        self.assertEqual(choice["why"], "chosen by Burak for this turn")

    def test_route_learns_the_engine_that_ran_last_and_the_project_pin(self):
        seen = {}

        def pick(role, *, forced=None, current=None):
            seen.update({"forced": forced, "current": current})
            return self.choice(forced or current or "claude")

        l3.save_info(self.project, {"engine_last": "codex"})
        with mock.patch.object(engines, "usage_hold", return_value=None), \
             mock.patch.object(l3.route, "pick_engine", side_effect=pick):
            self.assertEqual(l3._select(self.project)["engine"], "codex")
            self.assertEqual(seen, {"forced": None, "current": "codex"})
            config.set_l3_engine(self.project, "claude")
            self.assertEqual(l3._select(self.project)["engine"], "claude")
            self.assertEqual(seen["forced"], "claude")
            config.set_l3_engine(self.project, None)
            l3._select(self.project)
            self.assertIsNone(seen["forced"]); self.assertNotIn("l3_engine", config.project(self.project))

    def test_pinned_claude_turn_never_falls_back_to_codex(self):
        limited = {"text": "", "session_id": "", "usage": {}, "context_tokens": 0, "cost": 0.0, "turns": 0,
                   "structured": None, "error": "usage limit", "tools": [],
                   "limited": "2099-01-01T00:00:00+00:00"}
        with mock.patch.object(l3, "_select", return_value=self.choice("claude")), \
             mock.patch.object(engines, "claude_print", return_value=limited), \
             mock.patch.object(engines, "codex_exec") as codex:
            out = l3.turn(self.project, "hello", engine="claude")
        codex.assert_not_called()
        self.assertEqual(out["engine"], "claude")


if __name__ == "__main__":
    unittest.main()
