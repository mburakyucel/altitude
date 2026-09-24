"""Private review sessions share ordinary permissions without taking over L3's conversation."""
import json
import subprocess
import sys
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, l3


class TestConversationReview(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.quiet_engines()
        (self.repo / "AGENTS.md").write_text("Project instructions")

    def test_fresh_reviews_preserve_coordinator_session_and_ordinary_permissions(self):
        saved = {"sessions": {engine: {"session_id": "ordinary-chat"} for engine in config.ENGINES}}
        l3.save_info(self.project, saved)
        seen = []

        def execute(prompt, **kw):
            seen.append(kw)
            self.assertIn(str(self.repo / "AGENTS.md"), prompt)
            self.assertIn("Review only", prompt)
            self.assertIsNone(kw["resume"])
            self.assertIsNone(kw.get("effort"))
            self.assertEqual(kw["timeout"], engines.session_timeout(engine))
            self.assertTrue(kw["durable_timeout"])
            self.assertEqual(kw["model"], "configured-review-model")
            self.assertEqual(kw["extra_env"]["ALTITUDE_ACTOR"], "l3")
            self.assertTrue((kw["cwd"] / "bin" / "alt").exists())
            return {"text": "private result", "session_id": "review-session", "usage": {"input_tokens": 18},
                    "engine_model": "observed-model", "reported_cost": 0.012, "error": None}

        for engine, seam in (("claude", "claude_print"), ("codex", "codex_exec")):
            with self.subTest(engine=engine), mock.patch.object(engines, seam, side_effect=execute), \
                    mock.patch.object(l3, "turn", side_effect=AssertionError("review is not a coordinator turn")):
                result = engines.conversation_review(self.project, "Review only", engine=engine,
                                                     model="configured-review-model")
                self.assertEqual(result["text"], "private result")
                self.assertEqual(result["engine_model"], "observed-model")
                self.assertEqual(result["usage"], {"input_tokens": 18})
                self.assertEqual(result["cost"], 0.012)
                self.assertEqual(l3.info(self.project), saved)
                self.assertEqual(l3.chat_history(self.project), [])
                self.assertFalse(seen[-1]["cwd"].exists())
        native, alternate = seen
        self.assertNotEqual(native["cwd"], alternate["cwd"])
        self.assertEqual(native["tools"], l3.L3_TOOLS)
        self.assertEqual(native["allowed_tools"], engines.L3_ALLOWED_TOOLS)
        self.assertEqual((native["permission_mode"], native["permission_prompts"], native["restricted"]),
                         ("dontAsk", "none", True))
        self.assertEqual(native["add_dirs"], (self.repo, config.ROOT))
        self.assertEqual(alternate["sandbox_settings"],
                         engines.codex_l3_permissions(alternate["cwd"], project=self.project))
        self.assertTrue(alternate["ignore_user_config"])

    def test_unavailable_engine_does_not_fallback_or_claim_usage(self):
        with mock.patch.object(engines, "installation", return_value={"available": False, "why": "missing"}), \
                mock.patch.object(l3, "_l3_runtime") as runtime:
            for engine in (*config.ENGINES, "unknown"):
                result = engines.conversation_review(self.project, "review", engine=engine, model="fixed")
                self.assertTrue(result["error"])
                self.assertIsNone(result["usage"])
                self.assertIsNone(result["cost"])
            runtime.assert_not_called()

    def test_failure_and_timeout_keep_unknown_resources_and_dispose_runtime(self):
        for failure in (OSError("launch failed"), subprocess.TimeoutExpired("review", 90)):
            for engine, seam in (("claude", "claude_print"), ("codex", "codex_exec")):
                with self.subTest(engine=engine, failure=failure), \
                        mock.patch.object(engines, seam, side_effect=failure) as execute:
                    result = engines.conversation_review(self.project, "review", engine=engine, model="fixed")
                    self.assertTrue(result["error"])
                    self.assertIsNone(result["cost"])
                    self.assertIsNone(result["usage"])
                    self.assertFalse(execute.call_args.kwargs["cwd"].exists())

    def test_review_native_deadline_stops_whole_tree_for_either_engine(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine), mock.patch.object(engines.subprocess, "Popen",
                    side_effect=OSError("fixture launch intercepted")) as popen, \
                    mock.patch.object(l3, "_l3_env", return_value={}):
                result = engines.conversation_review(self.project, "review", engine=engine, model="fixed")
                self.assertIn("intercepted", result["error"])
                command = popen.call_args.args[0]
                self.assertEqual(command[0], engines.SYSTEMD_RUN_BIN)
                for option in (f"--property=RuntimeMaxSec={engines.session_timeout(engine)}", "--property=KillMode=control-group",
                               "--property=TimeoutStopSec=5", "--property=SendSIGKILL=yes"):
                    self.assertIn(option, command)

    def test_reported_cost_and_actual_model_come_from_native_evidence(self):
        fixture = self.tmp / "reviewer-fixture"
        self.patch(config, "CLAUDE_BIN", str(fixture))
        for reported in (None, 0, 0.012):
            events = [{"type": "assistant", "message": {"model": "observed-review-model"}},
                      {"type": "result", "result": "private answer", "session_id": "review-session",
                       "usage": {"input_tokens": 11},
                       **({"total_cost_usd": reported} if reported is not None else {})}]
            fixture.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n"
                               f"print({chr(10).join(map(json.dumps, events))!r})\n")
            fixture.chmod(0o700)
            with self.subTest(reported=reported), mock.patch.object(engines, "_codex_service_command",
                                                                   side_effect=lambda unit, cmd, env, **kw: cmd):
                result = engines.conversation_review(self.project, "review", engine="claude", model="fixed")
                self.assertIsNone(result["error"])
                self.assertEqual(result["cost"], reported)
                self.assertEqual(result["engine_model"], "observed-review-model")
                self.assertEqual(result["session_id"], "review-session")
                self.assertEqual(result["usage"], {"input_tokens": 11})

    def test_review_uses_final_result_after_commentary_and_tools(self):
        final = '{"findings": []}'
        events = [
            {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "I will inspect."}}},
            {"type": "assistant", "message": {"model": "observed-model", "content": [
                {"type": "tool_use", "name": "Read", "input": {"path": "AGENTS.md"}}]}},
            {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": final}}},
            {"type": "result", "result": final, "session_id": "review-with-tools"},
        ]
        fixture = self.tmp / "reviewer-fixture"
        fixture.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n"
                           f"print({chr(10).join(map(json.dumps, events))!r})\n")
        fixture.chmod(0o700)
        self.patch(config, "CLAUDE_BIN", str(fixture))
        with mock.patch.object(engines, "_codex_service_command", side_effect=lambda unit, cmd, env, **kw: cmd):
            review = engines.conversation_review(self.project, "review", engine="claude", model="fixed")
        self.assertEqual(json.loads(review["text"]), {"findings": []})
        ordinary = engines.claude_print("ordinary", cwd=self.repo)
        self.assertEqual(ordinary["text"], "I will inspect." + final)
        self.assertEqual(ordinary["final_text"], final)
