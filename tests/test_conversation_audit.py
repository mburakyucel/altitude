"""Finite audit journeys use real conversation/task storage and deterministic reviewer replies."""
import json
import threading
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.support import AltitudeCase
from altitude import audit, config, engines, l3, project_setup, server, state as S, tasks


class TestConversationAudit(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        self.patch(config, "CONVERSATION_AUDIT_PROJECT", self.project)
        self.now = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
        self.patch(S, "now", new=lambda: self.now.isoformat())
        self.reviewer = self.patch(engines, "conversation_review", return_value={
            "text": '{"findings": []}', "usage": {"input_tokens": 20}, "cost": None,
            "engine_model": "observed-review-model", "session_id": "review-session", "error": None})

    def start(self):
        return audit.configure(self.project, "start", actor=tasks.OPERATOR_MESSAGE_ROLE, reason="approved pilot")

    def exchange(self, identity, *, age=60, reply=True, trigger="chat", text="Please follow through", named=()):
        at = self.now - timedelta(minutes=age)
        l3.chat_log(self.project, "user", text, at=at.isoformat(), turn_id=identity, trigger=trigger)
        if reply:
            l3.chat_log(self.project, "assistant", "I will inspect", at=(at + timedelta(seconds=1)).isoformat(),
                        turn_id=identity, trigger=trigger, tasks=list(named))

    def batch(self, prefix="batch", count=4, **kw):
        for index in range(count):
            self.exchange(f"{prefix}-{index}", **kw)

    def finding(self, source, status="unresolved", category="missed_action"):
        return {"category": category, "status": status, "expected": "Take the authorized next step",
                "observed": "Explained but did not act", "impact": "Operator repeats the request",
                "uncertainty": "Only supplied evidence was inspected", "corrections": "", "owner": "",
                "sources": [source]}

    def chat(self, text="Continue ordinary work", trigger="chat"):
        def execute(prompt, **kw):
            return {"text": "ordinary answer", "session_id": kw.get("resume") or "ordinary-session",
                    "context_tokens": 10, "usage": {}, "cost": 0, "error": None, "tools": []}
        with mock.patch.object(engines, "claude_print", side_effect=execute) as call:
            result = l3.turn(self.project, text, trigger=trigger, engine="claude", model="fixture")
        self.assertTrue(result["completed"], result)
        return call.call_args.args[0]

    def test_minimum_activity_and_maturity_include_unanswered_exchanges(self):
        self.start()
        audit.run(self.project)
        self.batch(count=3)
        audit.run(self.project)
        self.exchange("unanswered", age=29, reply=False)
        audit.run(self.project)
        self.reviewer.assert_not_called()
        self.now += timedelta(minutes=1)
        audit.run(self.project)
        self.reviewer.assert_called_once()
        record = audit.status(self.project)
        self.assertEqual(record["attempts"][0]["model"], "observed-review-model")
        self.assertIsNone(record["attempts"][0]["cost"])
        evidence = S.read_json(config.project_dir(self.project) / "audits" / record["attempts"][0]["id"] / "packet.json")
        unanswered = next(e for e in evidence["selected"] if e["id"] == "unanswered")
        self.assertTrue(unanswered["incomplete"])
        self.assertEqual(evidence["coverage"]["eligible"], 4)
        audit.run(self.project)
        self.reviewer.assert_called_once()

    def test_twelve_hour_cadence_four_new_exchanges_and_fourteen_attempt_budget(self):
        self.start()
        for attempt in range(14):
            if attempt:
                self.now += timedelta(hours=12)
            self.batch(prefix=f"attempt-{attempt}")
            audit.run(self.project)
            self.assertEqual(self.reviewer.call_count, attempt + 1)
            audit.run(self.project)
            self.assertEqual(self.reviewer.call_count, attempt + 1)
        self.assertEqual(audit.status(self.project)["status"], "complete")
        self.batch(prefix="excess")
        self.now += timedelta(hours=12)
        audit.run(self.project)
        self.assertEqual(self.reviewer.call_count, 14)

    def test_new_activity_waits_twelve_hours_and_pilot_expires_after_seven_days(self):
        self.start()
        self.batch()
        audit.run(self.project)
        self.batch(prefix="next")
        self.now += timedelta(hours=12, seconds=-1)
        audit.run(self.project)
        self.reviewer.assert_called_once()
        self.now += timedelta(seconds=1)
        audit.run(self.project)
        self.assertEqual(self.reviewer.call_count, 2)
        self.now += timedelta(days=7)
        self.batch(prefix="after-expiry")
        audit.run(self.project)
        self.assertEqual(audit.status(self.project)["status"], "expired")
        self.assertEqual(self.reviewer.call_count, 2)
        with self.assertRaisesRegex(ValueError, "cannot renew"):
            self.start()

    def test_recency_cutoff_and_nonconversation_events_are_excluded(self):
        self.exchange("before-cutoff", age=1800)
        self.exchange("current")
        self.exchange("system-task-event", trigger="task-report")
        evidence = audit.packet(self.project, [], self.now)
        self.assertEqual([e["id"] for e in evidence["selected"]], ["current"])
        self.now += timedelta(days=3)
        self.exchange("new")
        evidence = audit.packet(self.project, [], self.now)
        self.assertEqual([e["id"] for e in evidence["selected"]], ["new"])

    def test_packet_volume_and_oversized_evidence_report_incomplete_coverage(self):
        self.batch(count=25)
        evidence = audit.packet(self.project, [], self.now)
        self.assertEqual(len(evidence["selected"]), 20)
        self.assertEqual(evidence["coverage"]["omitted_exchanges"], 5)
        self.assertLessEqual(len(audit.prompt(self.project, evidence).encode()), 65536)
        self.exchange("oversized", text="x" * 70000)
        selected_ids = [e["id"] for e in evidence["selected"]]
        oversized = audit.packet(self.project, selected_ids + [f"batch-{i}" for i in range(20, 25)], self.now)
        self.assertEqual(oversized["selected"], [])
        self.assertEqual(oversized["coverage"]["omitted_exchanges"], 1)
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            audit.prompt(self.project, {"evidence": "x" * 70000})

    def test_oversized_exchanges_do_not_count_toward_four_new_reviewed_exchanges(self):
        self.start()
        self.batch(count=3, text="x" * 70000)
        self.exchange("fits")
        audit.run(self.project)
        self.reviewer.assert_not_called()
        self.batch(prefix="fits-too", count=3)
        audit.run(self.project)
        self.reviewer.assert_called_once()

    def test_bounded_history_tail_retains_original_byte_sources_and_skips_partial_append(self):
        history = config.project_dir(self.project) / "chat.jsonl"
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text(json.dumps({"at": self.now.isoformat(), "role": "system", "text": "x" * (2 * 1024 * 1024)}) + "\n")
        self.batch()
        with history.open("a") as stream:
            stream.write('{"at": "concurrent incomplete append')
        evidence = audit.packet(self.project, [], self.now)
        self.assertTrue(evidence["coverage"]["history_tail_truncated"])
        self.assertEqual(len(evidence["selected"]), 4)
        first = evidence["selected"][0]["messages"][0]
        with history.open("rb") as stream:
            stream.seek(int(first["source"].split("#byte=")[1]))
            original = json.loads(stream.readline())
        self.assertEqual(original["turn_id"], first["turn_id"])
        self.assertEqual(original["text"], first["text"])

    def test_later_corrections_and_removed_task_message_metadata_are_preserved(self):
        task = tasks.new(self.project, "Repair the fictional issue", "Investigate the assigned defect")
        removed = {"id": "removed-decision", "at": self.now.isoformat(), "role": tasks.OPERATOR_MESSAGE_ROLE,
                   "text": "Superseded request"}
        task["message_deliveries"] = {removed["id"]: {"state": "removed", "at": self.now.isoformat()}}
        task["questions"] = [{"id": "question-one", "revision": 2, "status": "resolved",
                              "question": "Proceed with the repair?", "message": {
                                  "id": "question-anchor", "at": self.now.isoformat(), "role": "l2",
                                  "text": "Proceed with the repair?"},
                              "resolution": {"disposition": "answered", "message_id": "operator-source",
                                             "source": "task", "text": "Repair approved"}}]
        S.save_task(self.project, task)
        (S.task_dir(self.project, task["slug"]) / "conversation.jsonl").write_text(json.dumps(removed) + "\n")
        self.batch(named=[task["slug"]])
        self.exchange("later-correction", age=2, text="The task already owns this correction")
        evidence = audit.packet(self.project, [], self.now)
        self.assertEqual(evidence["later_context"][0]["turn_id"], "later-correction")
        self.assertEqual(evidence["tasks"][0]["slug"], task["slug"])
        message = next(row for row in evidence["tasks"][0]["messages"] if row["id"] == removed["id"])
        self.assertEqual(message["removed_at"], self.now.isoformat())
        self.assertEqual(message["id"], removed["id"])
        self.assertIn(removed["id"], message["source"])
        decision = evidence["tasks"][0]["decisions"][0]
        self.assertEqual(decision["revision"], 2)
        self.assertEqual(decision["resolution"]["message_id"], "operator-source")

    def test_related_task_limit_does_not_admit_unrelated_later_conversations(self):
        related = [tasks.new(self.project, f"Related repair {i}", "Assigned repair") for i in range(5)]
        unrelated = tasks.new(self.project, "A unrelated repair", "Separate work")
        self.batch(named=[t["slug"] for t in related])
        self.exchange("new-unrelated", age=1, named=[unrelated["slug"]])
        evidence = audit.packet(self.project, [], self.now)
        selected = {task["slug"] for task in evidence["tasks"]}
        self.assertEqual(len(selected), 4)
        self.assertNotIn(unrelated["slug"], selected)

    def test_only_unresolved_findings_are_supplied_once_and_duplicates_stay_quiet(self):
        self.start()
        self.batch()
        evidence = audit.packet(self.project, [], self.now)
        source = evidence["selected"][0]["messages"][0]["source"]
        rows = [self.finding(source), self.finding(source, "already_owned"),
                self.finding(source, "legitimate_wait", "unowned_wait")]
        self.reviewer.return_value["text"] = json.dumps({"findings": rows})
        audit.run(self.project)
        record = audit.status(self.project)
        record["attempts"].append({"id": "duplicate", "status": "complete", "findings": [rows[0].copy()]})
        S.write_json(audit.path(self.project), record)
        self.assertNotIn("Private conversation-review candidates", self.chat(trigger="task-report"))
        prompt = self.chat()
        self.assertIn("Private conversation-review candidates", prompt)
        self.assertEqual(prompt.count('"observed": "Explained but did not act"'), 1)
        self.assertNotIn('"status": "already_owned"', prompt)
        self.assertNotIn('"status": "legitimate_wait"', prompt)
        self.assertNotIn("Private conversation-review candidates", self.chat("Another ordinary request"))

    def test_bad_findings_and_failed_reviewer_pause_without_unchanged_retry(self):
        self.start()
        self.batch()
        self.reviewer.return_value = {"text": "", "error": "native review timed out"}
        audit.run(self.project)
        record = audit.status(self.project)
        self.assertEqual(record["status"], "paused")
        self.assertEqual(record["attempts"][0]["status"], "failed")
        self.now += timedelta(hours=12)
        audit.run(self.project)
        self.reviewer.assert_called_once()
        self.assertIn("Continue ordinary work", self.chat())
        evidence = audit.packet(self.project, [], self.now)
        for text in ('{"findings": [{}]}', json.dumps({"findings": [self.finding("invented-source")]}), "x" * 8193):
            with self.subTest(text=text[:50]), self.assertRaises(ValueError):
                audit.findings(text, evidence)

    def test_uncertain_interrupted_attempt_is_paused_without_replay(self):
        record = self.start()
        record["attempts"] = [{"id": "interrupted", "at": (self.now - timedelta(minutes=3)).isoformat(),
                               "status": "running"}]
        S.write_json(audit.path(self.project), record)
        self.batch()
        audit.run(self.project)
        self.assertEqual(audit.status(self.project)["status"], "paused")
        audit.run(self.project)
        self.reviewer.assert_not_called()

    def test_unavailable_pinned_engine_keeps_attempt_budget_and_never_falls_back(self):
        self.start()
        self.batch()
        with mock.patch.object(engines, "installation", return_value={"available": False, "why": "missing fixture"}):
            audit.run(self.project)
        record = audit.status(self.project)
        self.assertEqual(record["attempts"], [])
        self.assertIn("missing fixture", record["unavailable"])
        self.reviewer.assert_not_called()

    def test_background_attempts_coalesce_and_ordinary_chat_continues_during_review(self):
        self.start()
        self.batch()
        entered, release = threading.Event(), threading.Event()
        result = dict(self.reviewer.return_value)
        def review(*args, **kw):
            entered.set()
            if not release.wait(10):
                raise AssertionError("test did not release reviewer")
            return result
        self.reviewer.side_effect = review
        key = f"audit:{self.project}"
        # A saved setup failure stays idle until explicit repair; audit/chat remain independent.
        project_setup.save(self.project, operation={"state": "failed", "error": "fixture setup wait"})
        try:
            server.tick_project(self.project)
            self.assertTrue(entered.wait(5))
            server.tick_project(self.project)
            self.assertFalse(server.spawn(key, audit.run, self.project))
            audit.run(self.project)
            self.assertIn("Continue ordinary work", self.chat())
            self.reviewer.assert_called_once()
        finally:
            release.set()
            server._bg[key].join(10)
        self.assertFalse(server._bg[key].is_alive())
        self.assertEqual(len(audit.status(self.project)["attempts"]), 1)

    def test_corrupt_private_audit_evidence_cannot_block_ordinary_chat(self):
        for content in ("{bad json", '{"status":"active"}', '{"status":"stopped","attempts":[null]}'):
            audit.path(self.project).parent.mkdir(parents=True, exist_ok=True)
            audit.path(self.project).write_text(content)
            with self.subTest(content=content):
                audit.run(self.project)
                self.reviewer.assert_not_called()
                self.assertIn("Continue ordinary work", self.chat())


class TestAuditCLI(AltitudeCase):
    def test_operator_start_stop_and_owner_project_inspection_boundaries(self):
        self.register(config.CONVERSATION_AUDIT_PROJECT)
        project = config.CONVERSATION_AUDIT_PROJECT
        command = ("--project", project, "audit")
        started = self.alt(*command, "start", "--reason", "approved finite pilot")
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertEqual(json.loads(started.stdout)["status"], "active")
        for actor in ("l2", "l3"):
            refused = self.alt(*command, "stop", "--reason", "not operator authority",
                               env={"ALTITUDE_ACTOR": actor, "ALTITUDE_PROJECT": project})
            self.assertNotEqual(refused.returncode, 0)
        own = self.alt(*command, "status", env={"ALTITUDE_ACTOR": "l2", "ALTITUDE_PROJECT": project})
        self.assertEqual(own.returncode, 0, own.stderr)
        foreign = self.alt(*command, "status", env={"ALTITUDE_ACTOR": "l2", "ALTITUDE_PROJECT": self.project})
        self.assertNotEqual(foreign.returncode, 0)
        self.assertIn("launch project", foreign.stderr)
        stopped = self.alt(*command, "stop", "--reason", "pilot concluded")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(json.loads(stopped.stdout)["status"], "stopped")
        refused = self.alt(*command, "start", "--reason", "reset budget")
        self.assertNotEqual(refused.returncode, 0)
        foreign = self.alt("--project", self.project, "audit", "start", "--reason", "outside pilot")
        self.assertNotEqual(foreign.returncode, 0)
