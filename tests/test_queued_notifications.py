"""A queued turn that every provider refuses before any output stays queued with its identity: a system notification
keeps its place, and an operator chat keeps its turn and is answered first beneath its message."""
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

        for seam in ("claude_print", "codex_exec"):
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
        self.assertNotIn("retry_at", self.queue()[0], "an engine hold waits for an engine, not for a retry time")
        attempts = len(self.prompts)
        for _ in range(5):
            self.assertIsNone(l3.deliver_queued(self.project), "an unavailable L3 is not retried")
        self.assertEqual(len(self.prompts), attempts)
        self.assertEqual(self.turns("user"), 1)

        self.recover()
        self.mode = "ok"
        l3.deliver_queued(self.project)

        self.assertEqual(self.queue(), [], "delivered as soon as L3 is available")
        self.assertEqual(self.turns("assistant"), 1)
        self.assertEqual(self.prompts[-1].count("Fictional task asks which mirror goes first."), 1)

    def test_a_turn_that_produced_output_is_not_replayed(self):
        l3.queue_message(self.project, "Fictional incident changed.", trigger="incident")
        self.mode = "after-output"

        result = l3.deliver_queued(self.project)

        self.assertFalse(result.get("undelivered"))
        self.assertEqual(self.queue(), [])
        self.assertEqual(len(self.prompts), 1)

    def history(self) -> list[dict]:
        return l3.chat_history(self.project, None)

    def rows_of(self, turn_id: str) -> list[tuple]:
        return [(row["role"], row["trigger"]) for row in self.history() if row.get("turn_id") == turn_id]

    def test_refused_operator_chat_keeps_its_turn_and_is_answered_before_later_notifications(self):
        l3.queue_message(self.project, "Fictional operator question", trigger="chat", role=config.OPERATOR_ACTOR)
        self.mode = "limited"

        result = l3.deliver_queued(self.project)

        self.assertTrue(result["undelivered"])
        turn_id = result["turn_id"]
        [kept] = self.queue()
        self.assertEqual((kept["turn_id"], kept["trigger"], kept["role"], kept["text"]),
                         (turn_id, "chat", config.OPERATOR_ACTOR, "Fictional operator question"))
        self.assertNotIn("retry_at", kept)
        self.assertEqual(self.rows_of(turn_id), [("user", "chat")], "the message stays without an error row")
        self.assertFalse(l3.drop_queued(self.project, kept["id"]), "a kept message is already in the conversation")
        later = l3.queue_message(self.project, "Fictional task asks for a decision.", trigger="block",
                                 slug="fictional-later")
        attempts = len(self.prompts)
        self.assertIsNone(l3.deliver_queued(self.project), "an unavailable L3 is not retried")
        self.assertEqual(len(self.prompts), attempts)

        self.recover()
        self.mode = "ok"
        l3.deliver_queued(self.project)

        self.assertIn("Fictional operator question", self.prompts[-1])
        self.assertNotIn("Fictional task asks for a decision.", self.prompts[-1])
        self.assertEqual(self.rows_of(turn_id), [("user", "chat"), ("assistant", "chat")])
        self.assertEqual([row["id"] for row in self.queue()], [later["id"]])
        l3.deliver_queued(self.project)
        self.assertEqual(self.queue(), [])
        self.assertEqual(sum(row["role"] == "user" and row["text"] == "Fictional operator question"
                             for row in self.history()), 1)

    def test_direct_chat_without_an_engine_is_kept_in_send_order_and_answered_under_each_turn(self):
        self.mode = "limited"
        first = l3.turn(self.project, "Fictional direct question", trigger="chat")
        attempts = len(self.prompts)
        notice = l3.queue_message(self.project, "Fictional restart inventory.", trigger="restart")
        second = l3.turn(self.project, "Fictional follow-up", trigger="chat")

        self.assertEqual(len(self.prompts), attempts, "no engine is eligible, so no provider runs")
        self.assertEqual([(row["id"], row["turn_id"]) for row in (first["queued"], second["queued"])],
                         [(first["turn_id"],) * 2, (second["turn_id"],) * 2])
        self.assertEqual([row["id"] for row in self.queue()], [first["turn_id"], second["turn_id"], notice["id"]])
        self.assertFalse([row for row in self.history() if row["role"] == "error"])

        self.recover()
        self.mode = "ok"
        for _ in range(3):
            l3.deliver_queued(self.project)

        self.assertEqual(self.queue(), [])
        for result in (first, second):
            self.assertEqual(self.rows_of(result["turn_id"]), [("user", "chat"), ("assistant", "chat")])
        self.assertIn("Fictional direct question", self.prompts[-3])
        self.assertIn("Fictional follow-up", self.prompts[-2])
        self.assertIn("Fictional restart inventory.", self.prompts[-1])

    def test_send_now_runs_a_kept_chat_without_waiting_for_its_retry_time(self):
        self.mode = "expired-limit"
        l3.queue_message(self.project, "Fictional refused chat", trigger="chat", role=config.OPERATOR_ACTOR)
        turn_id = l3.deliver_queued(self.project)["turn_id"]
        [kept] = self.queue()
        self.assertIn("retry_at", kept)
        self.mode = "ok"

        l3.send_now(self.project, kept["id"])
        l3.deliver_queued(self.project)

        self.assertEqual(self.queue(), [])
        self.assertEqual(self.rows_of(turn_id), [("user", "chat"), ("assistant", "chat")])
        self.assertEqual(l3.send_now(self.project, kept["id"])["status"], "delivered")

    def test_refused_send_now_group_keeps_every_member_out_of_retry_handoff(self):
        texts = ["First unique instruction", "Middle unique instruction", "Last unique instruction"]
        group = [l3.queue_message(self.project, text, trigger="chat", role=config.OPERATOR_ACTOR)
                 for text in texts]
        l3.send_now(self.project, group[-1]["id"])
        self.mode = "limited"
        self.assertTrue(l3.deliver_queued(self.project)["undelivered"])
        self.recover()
        self.assertTrue(l3.deliver_queued(self.project)["undelivered"])
        self.assertEqual(self.queue()[0]["queue_ids"], [row["id"] for row in group])
        self.recover()
        self.mode = "ok"
        l3.deliver_queued(self.project)
        self.assertEqual([self.prompts[-1].count(text) for text in texts], [1, 1, 1])
        self.assertEqual(self.queue(), [])
        self.assertEqual([row["text"] for row in self.history() if row["role"] == "user"], texts)

    def test_kept_chat_waiting_for_its_retry_time_is_overtaken_only_by_send_now(self):
        self.mode = "expired-limit"
        l3.queue_message(self.project, "Fictional refused chat", trigger="chat", role=config.OPERATOR_ACTOR)
        turn_id = l3.deliver_queued(self.project)["turn_id"]
        [kept] = self.queue()
        self.assertEqual((kept["turn_id"], kept["refusals"]), (turn_id, 1), "routing stayed available: it waits")
        notice = l3.queue_message(self.project, "Fictional restart inventory.", trigger="restart")
        attempts = len(self.prompts)

        self.assertIsNone(l3.deliver_queued(self.project), "system work does not overtake the operator's message")
        self.assertEqual(len(self.prompts), attempts)
        urgent = l3.queue_message(self.project, "Fictional operator question now", trigger="chat",
                                  role=config.OPERATOR_ACTOR)
        l3.send_now(self.project, urgent["id"])
        l3.deliver_queued(self.project)
        self.assertIn("Fictional operator question now", self.prompts[-1])
        self.assertEqual([row["id"] for row in self.queue()], [kept["id"], notice["id"]])

        self.mode = "ok"
        self.later()
        l3.deliver_queued(self.project)
        self.assertIn("Fictional refused chat", self.prompts[-1])
        self.assertEqual(self.rows_of(turn_id), [("user", "chat"), ("assistant", "chat")])
        self.assertEqual([row["id"] for row in self.queue()], [notice["id"]])

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
