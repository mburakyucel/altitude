"""Issue #267: inspect real L3 launch prompts, replacing only external engine execution/routing."""
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, l3, state as S


class TestL3Continuity(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.minute = 0
        self.at = "2030-01-01T00:00:00+00:00"
        self.patch(S, "now", new=lambda: self.at)

    def turn(self, prompt, engine, *, project=None, trigger="chat"):
        self.minute += 1
        self.at = f"2030-01-01T00:{self.minute:02d}:00+00:00"
        seen = []

        def execute(text, **kwargs):
            seen.append((text, kwargs.get("resume")))
            sid = kwargs.get("resume") or f"session-{engine}-{self.minute}"
            return {"text": f"Answer to: {prompt}", "session_id": sid, "reported_session_id": sid,
                    "usage": {"input_tokens": 10}, "context_tokens": 10, "error": None,
                    "tools": [{"name": "shell", "command": "tool-evidence-must-not-replay"}]}

        seam = "claude_print" if engine == "claude" else "codex_exec"
        with mock.patch.object(l3, "_select", return_value={"engine": engine, "why": "fixture"}), \
             mock.patch.object(engines, seam, side_effect=execute):
            result = l3.turn(project or self.project, prompt, trigger=trigger)
        self.assertTrue(result.get("completed"), result)
        self.assertEqual(len(seen), 1)
        return seen[0]

    def test_same_provider_fresh_causes_restore_discussion_then_resume_natively(self):
        for engine in config.ENGINES:
            for cause in ("reset", "context", "confinement", "missing-session"):
                with self.subTest(engine=engine, cause=cause):
                    project = f"{engine}-{cause}"
                    self.register(project)
                    discussion = "Pre-approved issues need a separate task-intake proposal."
                    self.turn(discussion, engine, project=project)
                    inf = l3.info(project)
                    session = inf["sessions"][engine]
                    self.assertTrue(session["last_turn"])
                    if cause == "reset":
                        l3.reset(project)
                    else:
                        key, value = {"context": ("context_percent", 100),
                                      "confinement": ("confinement_version", 0),
                                      "missing-session": ("session_id", None)}[cause]
                        session[key] = value
                        l3.save_info(project, inf)

                    followup = "Please propose how to record this mechanism."
                    text, resume = self.turn(followup, engine, project=project)
                    self.assertIsNone(resume)
                    self.assertIn("preserve upstream references as full URLs or owner/repo#number", text)
                    self.assertIn("Recent human conversation", text)
                    self.assertIn("historical context", text)
                    self.assertIn(f"- user: {discussion}", text)
                    self.assertIn(f"- assistant: Answer to: {discussion}", text)
                    self.assertLess(text.index(f"- user: {discussion}"), text.index("- assistant:"))
                    self.assertEqual(text.count(followup), 1)
                    self.assertNotIn("Cross-provider chat", text)
                    self.assertNotIn("tool-evidence-must-not-replay", text)

                    saved_sid = l3.info(project)["sessions"][engine]["session_id"]
                    text, resume = self.turn("Continue the proposal.", engine, project=project)
                    self.assertEqual(resume, saved_sid)
                    self.assertIn("preserve upstream references as full URLs or owner/repo#number", text)
                    self.assertNotIn("Recent human conversation", text)
                    self.assertNotIn("Cross-provider chat", text)
                    self.assertNotIn(discussion, text)
                    self.assertNotIn(followup, text)

    def test_fresh_provider_then_cross_provider_resume_injects_each_exchange_once(self):
        for first, second in (("claude", "codex"), ("codex", "claude")):
            with self.subTest(first=first):
                project = f"handoff-{first}"
                self.register(project)
                text, resume = self.turn("First discussion.", first, project=project)
                self.assertIsNone(resume)
                self.assertNotIn("Recent human conversation", text, "an empty history adds no block")
                first_sid = l3.info(project)["sessions"][first]["session_id"]

                text, resume = self.turn("Intervening discussion.", second, project=project)
                self.assertIsNone(resume)
                self.assertIn("Recent human conversation", text)
                self.assertNotIn("Cross-provider chat", text)
                self.assertEqual(text.count("First discussion."), 2, "one user and one assistant row")
                self.assertEqual(text.count("Intervening discussion."), 1)

                text, resume = self.turn("Back to the first provider.", first, project=project)
                self.assertEqual(resume, first_sid)
                self.assertIn("Cross-provider chat", text)
                self.assertNotIn("Recent human conversation", text)
                self.assertNotIn("First discussion.", text, "native history is not replayed")
                self.assertEqual(text.count("Intervening discussion."), 2)
                self.assertEqual(text.count("Back to the first provider."), 1)
                self.assertLess(text.index("- user:"), text.index("- assistant:"))

                text, resume = self.turn("Another native follow-up.", first, project=project)
                self.assertEqual(resume, first_sid)
                self.assertNotIn("Cross-provider chat", text, "the watermark advances after handoff")
                self.assertNotIn("Intervening discussion.", text)

    def test_latest_twenty_human_rows_precede_current_turn_despite_system_traffic(self):
        for index in range(24):
            meta = ({"trigger": "chat"}, {}, {"trigger": None}, {"trigger": ""})[index % 4]
            l3.chat_log(self.project, "user" if index % 2 == 0 else "assistant", f"human-{index:02d}",
                        engine=config.ENGINES[index % 2], **meta)
            for trigger in ("report-landed", "restart", "start", "incident", "blocked", "recovery"):
                for role in ("user", "assistant"):
                    l3.chat_log(self.project, role, "server-chatter", trigger=trigger)
        for index in range(65):
            l3.chat_log(self.project, "system", "system-fyi", trigger="fyi")
        for role in ("error", "tool"):
            l3.chat_log(self.project, role, "non-conversation-evidence", trigger="chat")

        text, resume = self.turn("current-turn-only", "claude")
        self.assertIsNone(resume)
        for index in range(4):
            self.assertNotIn(f"human-{index:02d}", text)
        expected = [f"- {'user' if index % 2 == 0 else 'assistant'}: human-{index:02d}"
                    for index in range(4, 24)]
        self.assertEqual([line for line in text.splitlines() if line.startswith(("- user:", "- assistant:"))],
                         expected)
        self.assertNotIn("server-chatter", text)
        self.assertNotIn("system-fyi", text)
        self.assertNotIn("non-conversation-evidence", text)
        self.assertEqual(text.count("current-turn-only"), 1)
        self.assertTrue(text.endswith("current-turn-only"))

    def test_pre_output_fallback_excludes_current_row_tagged_with_rejected_engine(self):
        self.turn("Native discussion.", "claude")
        self.at = "2030-01-01T00:02:00+00:00"
        rejected = {"session_id": None, "text": "", "error": "model unavailable",
                    "rejection": {"scope": "model", "why": "fixture"}, "safe_to_retry": True}
        accepted = {"session_id": "session-claude-1", "text": "Proposal recorded.", "usage": {}}
        choices = [{"engine": engine, "why": "fixture"} for engine in ("codex", "claude")]
        with mock.patch.object(l3, "_select", side_effect=choices), \
             mock.patch.object(engines, "codex_exec", return_value=rejected), \
             mock.patch.object(engines, "claude_print", return_value=accepted) as execute:
            result = l3.turn(self.project, "Please record that proposal.")
        self.assertTrue(result["completed"])
        text = execute.call_args.args[0]
        self.assertEqual(execute.call_args.kwargs["resume"], "session-claude-1")
        self.assertEqual(text.count("Please record that proposal."), 1)
        self.assertNotIn("Cross-provider chat", text)
        self.assertNotIn("Native discussion.", text)

    def test_text_bound_is_explicit_and_history_is_project_local_even_on_server_turn(self):
        other = "other-project"
        self.register(other)
        for project in (self.project, other):
            l3.chat_log(project, "user", f"only-in-{project}", trigger="chat")
        for size in (799, 800, 801):
            l3.chat_log(self.project, "assistant", str(size)[0] * size, trigger="chat")

        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                text, resume = self.turn("current-restart-notice", engine, trigger="restart")
                self.assertIsNone(resume)
                self.assertIn(f"only-in-{self.project}", text)
                self.assertNotIn("only-in-other-project", text)
                self.assertIn("latest 20 messages", text)
                self.assertIn("800 characters", text)
                self.assertIn("- assistant: " + "7" * 799 + "\n", text)
                self.assertIn("- assistant: " + "8" * 800 + "\n", text)
                self.assertIn("- assistant: " + "8" * 800 + " [truncated]\n", text)
                self.assertNotIn("8" * 801, text)
                self.assertEqual(text.count("current-restart-notice"), 1)
                self.assertIn("[altitude] End historical context.", text)

        text, _ = self.turn("other-project-current", "claude", project=other)
        self.assertIn("only-in-other-project", text)
        self.assertNotIn(f"only-in-{self.project}", text)
