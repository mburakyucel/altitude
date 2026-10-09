"""A pending report waits for an available L3 without flooding the conversation, and human chat stays in view."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tests.support import AltitudeCase
from altitude import config, engines, l3, route, server, state as S


class TestReportRetry(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.clock = [1000.0]
        self.patch(server, "time", new=SimpleNamespace(monotonic=lambda: self.clock[0]))
        self.patch(server, "_report_retries", new={})
        self.raised = []

        def spawn(_key, fn, *args):
            try:
                fn(*args)
            except RuntimeError as exc:  # server.spawn records a background failure and continues
                self.raised.append(str(exc))
            return True

        self.patch(server, "spawn", side_effect=spawn)
        self.patch(server, "ensure_l3_verb_broker")
        self.patch(server, "request_l3_drain")
        self.logs = []
        self.patch(server, "log", new=self.logs.append)
        self.prompts = []

        def execute(text, **kwargs):
            self.prompts.append(text)
            if "Report landed" in text.rsplit("[altitude] End historical context.", 1)[-1] and self.provider_fault:
                if self.provider_fault == "raise":
                    raise RuntimeError("fixture provider crash")
                return {"text": "", "session_id": "", "error": "fixture provider fault", "usage": {}}
            sid = kwargs.get("resume") or "fixture-session"
            return {"text": "Handled.", "session_id": sid, "reported_session_id": sid,
                    "usage": {"input_tokens": 10}, "context_tokens": 10, "error": None, "tools": []}

        self.provider_fault = False
        for seam in ("claude_print", "codex_exec"):
            self.patch(engines, seam, side_effect=execute)
        self.slug = "fictional-report"
        directory = S.task_dir(self.project, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        S.write_json(directory / "report.json", {"landed": {"prs": []}, "blocked": "Needs a fictional decision",
                                                 "fyi": [], "decisions": [], "follow_ups": [], "review": []})
        S.save_task(self.project, {
            "slug": self.slug, "title": "Fictional report", "state": "blocked", "created": S.now(),
            "updated": S.now(), "attempt": 1, "session_id": "fixture-owner", "l3_handled": None,
            "blocked_reason": "Needs a fictional decision",
            "verified": {"verdict": "blocked", "attempt": 1, "problems": [], "signals": [], "spend": {}, "prs": [],
                         "report": {"blocked": "Needs a fictional decision"}}})

    def availability(self, until: datetime) -> None:
        for engine in config.ENGINES:
            route.note_rejection({"engine": engine}, {"scope": "engine", "why": "fixture allowance exhausted",
                                                      "until": until.isoformat()})

    def rows(self) -> list[dict]:
        return l3.chat_history(self.project, None)

    def task(self) -> dict:
        return S.load_task(self.project, self.slug)

    def test_unavailable_l3_leaves_report_pending_without_chat_rows_then_handles_it_once(self):
        l3.chat_log(self.project, "user", "Fictional operator question", trigger="chat")
        self.availability(datetime.now(timezone.utc) + timedelta(hours=1))
        before = self.rows()

        for _ in range(50):
            server.resume_stranded_reports(self.project)
            self.clock[0] += 30

        self.assertEqual(self.rows(), before)
        self.assertEqual(self.prompts, [])
        self.assertIsNone(self.task()["l3_handled"])
        self.assertEqual(self.task()["state"], "blocked")
        self.assertTrue(any("report waits for L3: " in line for line in self.logs))

        self.availability(datetime.now(timezone.utc) - timedelta(minutes=1))
        for _ in range(5):
            server.resume_stranded_reports(self.project)

        added = self.rows()[len(before):]
        self.assertEqual([(row["role"], row["trigger"]) for row in added],
                         [("user", "report-landed"), ("assistant", "report-landed")])
        self.assertEqual(len(self.prompts), 1)
        self.assertIsNotNone(self.task()["l3_handled"])
        self.assertEqual(self.task()["state"], "blocked", "handling a report never completes a blocked task")

    def test_failed_report_turn_backs_off_and_a_changed_report_is_handled_at_once(self):
        self.provider_fault = True

        def attempts() -> int:
            return sum(row["role"] == "user" and row["trigger"] == "report-landed" for row in self.rows())

        for _ in range(10):
            server.resume_stranded_reports(self.project)
        self.assertEqual(attempts(), 1)
        self.assertEqual([row["role"] for row in self.rows()], ["user", "error"])

        self.clock[0] += 61
        for _ in range(10):
            server.resume_stranded_reports(self.project)
        self.assertEqual(attempts(), 2)
        self.clock[0] += 61
        server.resume_stranded_reports(self.project)
        self.assertEqual(attempts(), 2, "the second failure waits longer")
        self.assertIsNone(self.task()["l3_handled"])

        l3.queue_message(self.project, "Fictional operator follow-up", trigger="chat", role=config.OPERATOR_ACTOR)
        l3.deliver_queued(self.project)
        human = [row for row in self.rows() if row["trigger"] == "chat"]
        self.assertEqual([(row["role"], row["text"]) for row in human],
                         [("user", "Fictional operator follow-up"), ("assistant", "Handled.")])

        with S.project_lock(self.project):
            task = self.task()
            task["session_id"] = "fixture-new-owner"
            S.save_task(self.project, task)
        self.provider_fault = False
        server.resume_stranded_reports(self.project)
        self.assertEqual(attempts(), 3, "a changed report does not inherit the previous backoff")
        self.assertIsNotNone(self.task()["l3_handled"])
        self.assertEqual(server._report_retries, {})

    def test_history_limit_keeps_human_conversation_through_a_system_burst(self):
        for index in range(3):
            l3.chat_log(self.project, "user", f"human-{index}", trigger="chat")
            l3.chat_log(self.project, "assistant", f"answer-{index}", trigger="chat")
        for index in range(150):
            l3.chat_log(self.project, "user", f"report-{index}", trigger="report-landed", turn_id=f"t{index}")
            l3.chat_log(self.project, "error", "fixture hold", trigger="report-landed", turn_id=f"t{index}")
        l3.chat_log(self.project, "system", "fixture heads-up", trigger="fyi")
        path = config.project_dir(self.project) / "chat.jsonl"
        raw = path.read_text()

        history = l3.chat_state(self.project, 60)["history"]

        texts = [row["text"] for row in history]
        self.assertEqual(texts[:6], ["human-0", "answer-0", "human-1", "answer-1", "human-2", "answer-2"])
        self.assertEqual(len(history), 66)
        self.assertEqual(texts[-1], "fixture heads-up")
        self.assertEqual(sum(row["trigger"] != "chat" for row in history), 60)
        self.assertEqual(l3.chat_history(self.project, 0), [])
        self.assertEqual(len(l3.chat_history(self.project, None)), 307)
        self.assertEqual(path.read_text(), raw, "reading history never rewrites the saved log")
        self.assertEqual(json.loads(raw.splitlines()[0])["text"], "human-0")

    def test_a_report_turn_that_raises_backs_off_like_a_failed_one(self):
        self.provider_fault = "raise"

        for _ in range(10):
            server.resume_stranded_reports(self.project)

        self.assertEqual(self.raised, ["fixture provider crash"])
        self.assertEqual(sum(row["role"] == "user" for row in self.rows()), 1)
        self.assertIsNone(self.task()["l3_handled"])
        self.clock[0] += 61
        server.resume_stranded_reports(self.project)
        self.assertEqual(len(self.raised), 2)

    def test_engine_exhaustion_at_admission_writes_no_report_rows(self):
        self.availability(datetime.now(timezone.utc) + timedelta(hours=1))
        # The report passed any earlier look at availability; admission under the turn lock decides.
        result = server.server_l3_turn(self.project, "Report landed for fictional-report.", trigger="report-landed")

        self.assertTrue(result["held"])
        self.assertEqual(self.rows(), [])
        chat = l3.turn(self.project, "Fictional operator question", trigger="chat")
        self.assertIn("engine hold", chat["error"])
        self.assertEqual([row["role"] for row in self.rows()], ["user", "error"], "human chat still reports the hold")
