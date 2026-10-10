"""A queued turn that every provider refuses before any output stays queued with its identity: a system notification
keeps its place, and an operator chat keeps its turn and is answered first beneath its message. A notification that
later state already answers never runs."""
from datetime import datetime, timedelta, timezone

from tests.support import AltitudeCase
from altitude import config, engines, l3, route, state as S, tasks as T


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

    def ask(self, title: str, question: str, slug: str | None = None) -> dict:
        """A fictional owner blocks on one question; returns its queued block notification."""
        if slug is None:
            task = T.new(self.project, title, "Fictional brief.")
            task.update(state="running", attempt=1, agent_id="owner", session_id="conversation")
            S.save_task(self.project, task)
            slug = task["slug"]
        else:
            T.resume(self.project, slug)
        T.block(self.project, slug, question, actor="l2", expected_attempt=1,
                updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, tell_l3=True)
        return next(row for row in self.queue() if row.get("slug") == slug)

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
        row = self.ask("Mirror order", "Which mirror goes first?")
        self.mode = "limited"

        result = l3.deliver_queued(self.project)

        self.assertTrue(result["undelivered"])
        self.assertEqual([(item["id"], item["trigger"], item["slug"]) for item in self.queue()],
                         [(row["id"], "block", row["slug"])])
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
        self.assertEqual(self.prompts[-1].count(row["text"]), 1)
        self.assertIsNone(l3.deliver_queued(self.project))
        self.assertEqual(self.turns("assistant"), 1, "an open question still arrives once")

    def dropped(self) -> list[tuple]:
        return [(event["message_id"], event["slug"]) for event in S.read_project_log(self.project, limit=0)
                if event["kind"] == "l3-notice-dropped"]

    def test_notification_answered_while_l3_was_unavailable_is_dropped_not_delivered(self):
        self.mode = "limited"
        row = self.ask("Mirror order", "Which mirror goes first?")
        self.assertTrue(l3.deliver_queued(self.project)["undelivered"])
        question = next(q for q in S.load_task(self.project, row["slug"])["questions"] if q["status"] == "open")
        message = T.message(self.project, row["slug"], "burak", "The west mirror goes first.")
        T.resolve_question(self.project, row["slug"], question["id"], question["revision"], message["id"],
                           expected_attempt=1, disposition="answered", reason="The west mirror goes first.")
        attempts = len(self.prompts)

        self.recover()
        self.mode = "ok"
        self.assertIsNone(l3.deliver_queued(self.project))

        self.assertEqual((self.queue(), len(self.prompts), self.turns("assistant")), ([], attempts, 0))
        self.assertEqual(self.dropped(), [(row["id"], row["slug"])])

    def test_notification_whose_task_ended_is_dropped_before_an_engine_is_available(self):
        self.patch(engines, "remove_l2_worker", return_value="Fixture worker stopped.")
        self.mode = "limited"
        row = self.ask("Mirror order", "Which mirror goes first?")
        self.assertTrue(l3.deliver_queued(self.project)["undelivered"])
        T.reject(self.project, row["slug"], "Fictional request withdrawn.")  # also archives the task

        self.assertIsNone(l3.deliver_queued(self.project), "no engine is eligible")
        self.assertEqual(self.queue(), [])
        self.assertEqual(self.dropped(), [(row["id"], row["slug"])])

    def test_newer_notification_replaces_the_older_for_its_task(self):
        first = self.ask("Mirror order", "Which mirror goes first?")
        other = self.ask("Cache size", "How large may the cache grow?")
        second = self.ask("Mirror order", "Which mirror goes first, given the outage?", slug=first["slug"])
        self.assertIn("revision 2", second["text"])

        self.assertEqual([row["id"] for row in self.queue()], [other["id"], second["id"]])
        l3.deliver_queued(self.project)
        l3.deliver_queued(self.project)

        self.assertEqual(self.queue(), [])
        self.assertEqual(self.turns("assistant"), 2)
        self.assertNotIn("revision 1", self.prompts[-1])
        self.assertEqual(self.prompts[-1].count(second["text"]), 1)

    def test_newer_restart_notice_replaces_the_older(self):
        chat = l3.queue_message(self.project, "Fictional operator question", trigger="chat", role=config.OPERATOR_ACTOR)
        for inventory in ("first", "second", "third"):
            notice = l3.queue_message(self.project, f"Fictional {inventory} restart inventory.", trigger="restart")
        self.assertEqual(notice["position"], 2)
        self.assertEqual([row["id"] for row in self.queue()], [chat["id"], notice["id"]])

        l3.deliver_queued(self.project)
        l3.deliver_queued(self.project)

        self.assertEqual(self.queue(), [])
        self.assertEqual([sum(f"Fictional {inventory} restart inventory." in prompt for prompt in self.prompts)
                          for inventory in ("first", "second", "third")], [0, 0, 1])

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
        later = self.ask("Decision", "Fictional task asks for a decision.")
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

    def test_direct_chats_without_an_engine_are_kept_in_send_order_and_answered_together(self):
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
        for _ in range(2):
            l3.deliver_queued(self.project)

        self.assertEqual(self.queue(), [])
        self.assertEqual(self.rows_of(first["turn_id"]), [("user", "chat")])
        self.assertEqual(self.rows_of(second["turn_id"]), [("user", "chat"), ("assistant", "chat")])
        self.assertEqual([self.prompts[-2].count(text) for text in ("Fictional direct question", "Fictional follow-up")],
                         [1, 1])
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
        other = l3.queue_message(self.project, "Fictional operator question elsewhere", trigger="chat",
                                 role=config.OPERATOR_ACTOR, slug="fictional-task")
        l3.send_now(self.project, other["id"])
        l3.deliver_queued(self.project)
        self.assertTrue(self.prompts[-1].endswith("End historical context.\n\nFictional operator question elsewhere"),
                        "another conversation's group leaves the kept chat waiting")
        self.assertEqual([row["id"] for row in self.queue()], [kept["id"], notice["id"]])

        self.mode = "ok"
        self.later()
        l3.deliver_queued(self.project)
        self.assertIn("Fictional refused chat", self.prompts[-1])
        self.assertEqual(self.rows_of(turn_id), [("user", "chat"), ("assistant", "chat")])
        self.assertEqual([row["id"] for row in self.queue()], [notice["id"]])

    def kept_then_newer(self) -> tuple[dict, list[dict]]:
        """One chat every option refuses stays kept under its turn, waiting for its retry time, and two newer
        messages queue behind it."""
        self.mode = "expired-limit"
        l3.queue_message(self.project, "Kept fictional chat", trigger="chat", role=config.OPERATOR_ACTOR)
        self.assertTrue(l3.deliver_queued(self.project)["undelivered"])
        [kept] = self.queue()
        self.assertIn("retry_at", kept)
        self.mode = "ok"
        newer = [l3.queue_message(self.project, text, trigger="chat", role=config.OPERATOR_ACTOR)
                 for text in ("First newer chat", "Second newer chat")]
        return kept, newer

    def clear(self) -> None:
        for name in ("l3-queue.jsonl", "chat.jsonl"):
            (config.project_dir(self.project) / name).unlink(missing_ok=True)

    def users(self) -> list[tuple]:
        return [(row["text"], row["turn_id"], row.get("queue_ids")) for row in self.history() if row["role"] == "user"]

    def assert_answered_once(self, kept: dict, newer: list[dict]) -> None:
        texts = [kept["text"], *(row["text"] for row in newer)]
        self.assertEqual([self.prompts[-1].count(text) for text in texts], [1] * len(texts))
        self.assertLess(self.prompts[-1].index(texts[0]), self.prompts[-1].index(texts[-1]))
        self.assertEqual(self.queue(), [])
        self.assertIsNone(l3.deliver_queued(self.project))
        for row in (kept, *newer):
            self.assertEqual(l3.send_now(self.project, row["id"])["status"], "delivered")

    def test_send_now_delivers_a_kept_chat_with_the_newer_messages_once_in_order(self):
        for index in (0, 1, 2):
            with self.subTest(pressed=index):
                self.clear()
                kept, newer = self.kept_then_newer()
                l3.send_now(self.project, [kept, *newer][index]["id"])
                self.assertTrue(all(row["send_now"] and "retry_at" not in row for row in self.queue()))
                attempts = len(self.prompts)

                result = l3.deliver_queued(self.project)

                self.assertEqual(len(self.prompts), attempts + 1, "one turn takes the whole group")
                users = self.users()
                self.assertEqual(users[0], (kept["text"], kept["turn_id"], [kept["id"]]), "the kept bubble is not relogged")
                self.assertEqual([user[0::2] for user in users[1:]], [(row["text"], [row["id"]]) for row in newer])
                self.assertEqual(users[-1][1], result["turn_id"])
                self.assertNotEqual(users[1][1], users[2][1], "each newer message keeps its own bubble")
                self.assertEqual(self.rows_of(kept["turn_id"]), [("user", "chat")])
                self.assertEqual(self.rows_of(result["turn_id"]), [("user", "chat"), ("assistant", "chat")])
                self.assertEqual(self.history()[-1]["role"], "assistant", "the reply follows the whole group")
                self.assert_answered_once(kept, newer)

    def test_a_kept_chat_and_the_newer_messages_drain_as_one_group_once_its_retry_time_passes(self):
        kept, newer = self.kept_then_newer()
        attempts = len(self.prompts)
        self.assertIsNone(l3.deliver_queued(self.project), "the newer messages wait behind the kept chat")
        self.assertEqual(len(self.prompts), attempts)

        self.later()
        result = l3.deliver_queued(self.project)

        self.assertEqual(len(self.prompts), attempts + 1)
        self.assertEqual(self.users(), [(kept["text"], kept["turn_id"], [kept["id"]]),
                                        ("First newer chat\n\nSecond newer chat", result["turn_id"],
                                         [row["id"] for row in newer])])
        self.assertEqual(self.rows_of(result["turn_id"]), [("user", "chat"), ("assistant", "chat")])
        self.assert_answered_once(kept, newer)

    def test_a_kept_and_newer_group_interrupted_again_keeps_every_identity_and_runs_once(self):
        for send_now in (True, False):
            with self.subTest(send_now=send_now):
                self.clear()
                kept, newer = self.kept_then_newer()
                if send_now:
                    l3.send_now(self.project, newer[0]["id"])
                else:
                    self.later()
                self.mode = "limited"
                interrupted = l3.deliver_queued(self.project)
                self.assertTrue(interrupted["undelivered"])
                [retained] = self.queue()
                self.assertEqual((retained["id"], retained["turn_id"], retained["queue_ids"]),
                                 (kept["id"], interrupted["turn_id"], [kept["id"], *(row["id"] for row in newer)]))
                self.assertNotIn("send_now", retained)
                logged = self.users()
                self.assertEqual(logged[-1][1], interrupted["turn_id"], "the retained turn is the last new bubble's")
                latest = l3.queue_message(self.project, "Latest newer chat", trigger="chat", role=config.OPERATOR_ACTOR)
                self.recover()
                self.mode = "ok"

                result = l3.deliver_queued(self.project)

                self.assertEqual(self.users(), [*logged, ("Latest newer chat", result["turn_id"], [latest["id"]])])
                self.assertEqual(self.rows_of(interrupted["turn_id"]), [("user", "chat")])
                self.assert_answered_once(kept, [*newer, latest])

    def two_kept_then_newer(self) -> tuple[list[dict], dict]:
        """Two kept chats, the second still waiting for its retry time, and a newer message behind them."""
        kept = []
        for name, retry_at in (("first", None), ("second", "2099-01-01T00:00:00+00:00")):
            row = l3.queue_message(self.project, f"Kept {name} fictional chat", trigger="chat", role=config.OPERATOR_ACTOR)
            row = {**row, "turn_id": f"kept-{name}", "queue_ids": [row["id"]], **({"retry_at": retry_at} if retry_at else {})}
            l3.chat_log(self.project, "user", row["text"], trigger="chat", turn_id=row["turn_id"], queue_ids=[row["id"]])
            kept.append(row)
        l3._write_queue(l3.queue_path(self.project), kept)
        newer = l3.queue_message(self.project, "Newer fictional chat", trigger="chat", role=config.OPERATOR_ACTOR)
        for row in kept:
            row.pop("position", None)
        return kept, newer

    def test_several_kept_chats_and_a_newer_message_run_as_one_group_after_every_retry_time(self):
        kept, newer = self.two_kept_then_newer()
        self.assertIsNone(l3.deliver_queued(self.project), "a ready kept chat waits for the one behind it")
        self.assertEqual(self.prompts, [])

        self.later()
        result = l3.deliver_queued(self.project)

        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(self.users()[-1], ("Newer fictional chat", result["turn_id"], [newer["id"]]))
        self.assertEqual([len(self.rows_of(row["turn_id"])) for row in kept], [1, 1], "kept bubbles are not relogged")
        self.assert_answered_once(kept[0], [kept[1], newer])

    def test_send_now_keeps_several_kept_chats_and_a_newer_message_together_through_a_refusal(self):
        kept, newer = self.two_kept_then_newer()
        l3.send_now(self.project, newer["id"])
        self.mode = "limited"
        interrupted = l3.deliver_queued(self.project)
        self.assertTrue(interrupted["undelivered"])
        [retained] = self.queue()
        self.assertEqual((retained["id"], retained["turn_id"], retained["queue_ids"]),
                         (kept[0]["id"], interrupted["turn_id"], [kept[0]["id"], kept[1]["id"], newer["id"]]))
        self.recover()
        self.mode = "ok"

        result = l3.deliver_queued(self.project)

        self.assertEqual(result["turn_id"], interrupted["turn_id"], "the retry answers beneath the newer message")
        self.assertEqual(self.rows_of(result["turn_id"]), [("user", "chat"), ("assistant", "chat")])
        self.assert_answered_once(kept[0], [kept[1], newer])

    def test_a_direct_turns_kept_message_leads_newer_messages_without_repeating_in_history(self):
        self.mode = "limited"
        direct = l3.turn(self.project, "Kept fictional direct chat", trigger="chat")
        newer = l3.queue_message(self.project, "Newer fictional chat", trigger="chat", role=config.OPERATOR_ACTOR)
        self.recover()
        self.mode = "ok"

        result = l3.deliver_queued(self.project)

        self.assertEqual(self.users(), [("Kept fictional direct chat", direct["turn_id"], None),
                                        ("Newer fictional chat", result["turn_id"], [newer["id"]])])
        self.assert_answered_once(direct["queued"], [newer])

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
