"""A queued system notification that every provider refuses before any output stays queued with its identity."""
from datetime import datetime, timedelta, timezone

from tests.support import AltitudeCase
from altitude import config, engines, l3, route


class TestQueuedNotifications(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.mode = "ok"
        self.prompts = []

        def execute(text, **kwargs):
            self.prompts.append(text)
            if self.mode == "limited":
                until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
                return {"text": "", "session_id": "", "error": "fixture allowance exhausted", "usage": {},
                        "limited": {"scope": "engine", "why": "fixture allowance exhausted", "until": until},
                        "safe_to_retry": True, "tools": []}
            if self.mode == "expired-limit" and "Fictional operator question" not in text:
                until = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
                return {"text": "", "session_id": "", "error": "fixture refusal", "usage": {},
                        "limited": {"scope": "engine", "why": "fixture refusal", "until": until},
                        "safe_to_retry": True, "tools": []}
            if self.mode == "after-output":
                return {"text": "Partial answer.", "session_id": "", "error": "fixture stream broke", "usage": {},
                        "safe_to_retry": False, "tools": [{"name": "shell", "command": "alt task show fictional"}]}
            sid = kwargs.get("resume") or "fixture-session"
            return {"text": "Question relayed.", "session_id": sid, "reported_session_id": sid,
                    "usage": {"input_tokens": 10}, "context_tokens": 10, "error": None, "tools": []}

        for seam in ("claude_print", "codex_turn"):
            self.patch(engines, seam, side_effect=execute)

    def queue(self) -> list[dict]:
        return l3._queue_rows(l3.queue_path(self.project))

    def recover(self) -> None:
        for engine in config.ENGINES:
            route.note_rejection({"engine": engine, "model": None}, {
                "scope": "engine", "why": "fixture recovered",
                "until": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()})

    def later(self) -> None:
        """The retained notification's retry time passes."""
        rows = [{**row, "retry_at": "2000-01-01T00:00:00+00:00"} for row in self.queue()]
        l3._write_queue(l3.queue_path(self.project), rows)

    def turns(self, role: str) -> int:
        return sum(row["role"] == role and row["trigger"] == "block" for row in l3.chat_history(self.project, None))

    def test_refused_notification_stays_queued_until_l3_is_available_then_is_delivered_once(self):
        row = l3.queue_message(self.project, "Fictional task asks which mirror goes first.", trigger="block",
                               slug="fictional-mirror")
        self.mode = "limited"

        result = l3.deliver_queued(self.project)

        self.assertTrue(result["undelivered"])
        self.assertEqual([(item["id"], item["trigger"], item["slug"]) for item in self.queue()],
                         [(row["id"], "block", "fictional-mirror")])
        attempts = len(self.prompts)
        for _ in range(5):
            self.assertIsNone(l3.deliver_queued(self.project), "an unavailable L3 is not retried")
        self.assertEqual(len(self.prompts), attempts)
        self.assertEqual(self.turns("user"), 1)

        self.recover()
        self.mode = "ok"
        self.assertIsNone(l3.deliver_queued(self.project), "the retained notification waits for its retry time")
        self.later()
        l3.deliver_queued(self.project)
        l3.deliver_queued(self.project)

        self.assertEqual(self.queue(), [])
        self.assertEqual(self.turns("assistant"), 1)
        self.assertEqual(self.prompts[-1].count("Fictional task asks which mirror goes first."), 1)

    def test_a_turn_that_produced_output_is_not_replayed(self):
        l3.queue_message(self.project, "Fictional incident changed.", trigger="incident")
        self.mode = "after-output"

        result = l3.deliver_queued(self.project)

        self.assertFalse(result.get("undelivered"))
        self.assertEqual(self.queue(), [])
        self.assertEqual(len(self.prompts), 1)

    def test_refused_operator_chat_is_not_requeued_because_the_operator_sees_retry(self):
        l3.queue_message(self.project, "Fictional operator question", trigger="chat", role=config.OPERATOR_ACTOR)
        self.mode = "limited"

        l3.deliver_queued(self.project)

        self.assertEqual(self.queue(), [])
        errors = [row for row in l3.chat_history(self.project, None) if row["role"] == "error"]
        self.assertTrue(errors)

    def test_refusal_that_leaves_routing_available_waits_instead_of_looping(self):
        row = l3.queue_message(self.project, "Fictional restart inventory.", trigger="restart")
        chat = l3.queue_message(self.project, "Fictional operator question", trigger="chat", role=config.OPERATOR_ACTOR)
        self.mode = "expired-limit"

        def attempts() -> int:
            return sum("Fictional restart inventory." in prompt for prompt in self.prompts)

        self.assertTrue(l3.deliver_queued(self.project)["undelivered"])
        refused = attempts()
        for _ in range(10):
            if not l3.deliver_queued(self.project):
                break

        self.assertEqual(attempts(), refused, "one refused turn, then the notification waits for its retry time")
        self.assertEqual([(item["id"], item["refusals"]) for item in self.queue()], [(row["id"], 1)])
        self.assertEqual(sum(row["role"] == "user" and row["trigger"] == "chat"
                             for row in l3.chat_history(self.project, None)), 1, "the operator message behind it ran")
        self.assertNotIn(chat["id"], [item["id"] for item in self.queue()])

        self.later()
        self.mode = "ok"
        l3.deliver_queued(self.project)
        self.assertEqual(self.queue(), [])
        self.assertEqual(sum(row["role"] == "assistant" and row["trigger"] == "restart"
                             for row in l3.chat_history(self.project, None)), 1)
