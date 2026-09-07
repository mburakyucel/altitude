"""Decisions as the cards and the page read them (SPEC.md §3.8, §3.9, §5.2 notes 3, 5, 6).

The incident class this prevents: a decision the operator cannot answer from the card. Slice 1 left the
web parsing kind prefixes out of the question text and every block offered Resume/Reject; an L3 dilemma
with two labelled options became one "Resume" button, the chosen option never reached the L2, FYIs lived
in a side file the conversation never showed, and a follow-up from a decision page could not be told from
any other chat turn.
"""
from __future__ import annotations

import json
import socket
import threading
import unittest
from unittest import mock

from tests.support import AltitudeCase, fyi_rows
from altitude import config, digest, dispatch, engines, l3, server, state as S, tasks as T

DILEMMA = ("Slice 2 is green; one spec gap needs your call before merge. "
           "Option A: the conversation scrolls as one page, so the composer sits at the end and stays reachable. "
           "Option B: keep the portrait rule everywhere, a transcript scrolling inside itself. "
           "My recommendation is A: a three-line transcript is unusable. Reply A or B.")


class TestParseDilemma(unittest.TestCase):
    def test_reads_labelled_options_and_the_recommendation_from_the_prose(self):
        parsed = T.parse_dilemma(DILEMMA)
        self.assertEqual(parsed["question"], "Slice 2 is green; one spec gap needs your call before merge.")
        self.assertEqual([(o["key"], o["label"]) for o in parsed["options"]],
                         [("A", "The conversation scrolls as one page"), ("B", "Keep the portrait rule everywhere")])
        self.assertTrue(parsed["options"][0]["text"].startswith("the conversation scrolls as one page, so"))
        self.assertEqual(parsed["recommendation"],
                         {"option": "A", "why": "My recommendation is A: a three-line transcript is unusable."})

    def test_reads_a_sentence_initial_letter_and_an_inline_recommended_mark(self):
        parsed = T.parse_dilemma("Which rule? A (recommended, already live): scroll as one page, always. "
                                 "B: keep the portrait rule everywhere.")
        self.assertEqual([(o["key"], o["label"]) for o in parsed["options"]],
                         [("A", "Scroll as one page"), ("B", "Keep the portrait rule everywhere")])
        self.assertEqual(parsed["recommendation"]["option"], "A")
        inline = T.parse_dilemma("Keep it? Option 1: keep it (recommended). Option 2: break it now.")
        self.assertEqual([o["label"] for o in inline["options"]], ["Keep it", "Break it now"])
        self.assertEqual(inline["recommendation"]["option"], "1")

    def test_a_plain_question_has_no_options_and_keeps_its_text(self):
        parsed = T.parse_dilemma("Please provide the title and body of GitHub issue #121.")
        self.assertEqual(parsed["options"], [])
        self.assertEqual(parsed["question"], "Please provide the title and body of GitHub issue #121.")
        self.assertEqual(parsed["recommendation"], {"option": None, "why": ""})

    def test_a_long_option_is_cut_at_a_word_and_a_duplicate_label_falls_back_to_its_key(self):
        long = T.parse_dilemma("Q. Option A: on a landscape phone the conversation scrolls as one and the composer "
                               "sits at the end. Option B: the same words again exactly.")
        self.assertEqual(long["options"][0]["label"], "On a landscape phone the conversation scrolls…")
        twins = T.parse_dilemma("Q. Option A: keep it. Option B: keep it.")
        self.assertEqual([o["label"] for o in twins["options"]], ["Option A", "Option B"])


class TestDecisions(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()

    def blocked(self, title: str, reason: str, *, actor: str = "l2", waiting_on: str = "burak", **fields) -> dict:
        task = T.new(self.project, title, "Do it.", actor="burak")
        task.update({"state": "running", "attempt": 1, **fields})
        S.save_task(self.project, task)
        return T.block(self.project, task["slug"], reason, actor=actor, updates={"waiting_on": waiting_on})

    def test_an_escalation_carries_kind_asker_labelled_options_and_its_window(self):
        task = self.blocked("Landscape rule", "Which rule applies in landscape?", waiting_on="l3")
        first_block = S.read_events(self.project, task["slug"])[-1]["at"]
        T.escalate(self.project, task["slug"], DILEMMA)
        [row] = T.decisions(self.project)
        self.assertEqual((row["kind"], row["asked_by"], row["title"]), ("asks", "l3", "Landscape rule"))
        self.assertEqual(row["question"], "Slice 2 is green; one spec gap needs your call before merge.")
        self.assertEqual([o["label"] for o in row["options"]],
                         ["The conversation scrolls as one page", "Keep the portrait rule everywhere"])
        self.assertEqual(row["recommendation"]["option"], "A")
        self.assertEqual(row["detail"], DILEMMA)
        self.assertEqual(row["asked"], S.read_events(self.project, task["slug"])[-1]["at"])
        self.assertEqual(row["since"], first_block, "the follow-up window opens at the first block of this decision")

    def test_an_l2_question_is_asked_by_the_l2_and_a_stop_or_fault_keeps_its_kind(self):
        asked = self.blocked("Asked", "Which colour should the dot be?")
        stopped = self.blocked("Stopped", "stopped by Burak", actor="burak")
        faulty = self.blocked("Faulty", "system fault [sandbox]: refused", actor="altd", fault="sandbox")
        rows = {row["slug"]: row for row in T.decisions(self.project)}
        self.assertEqual((rows[asked["slug"]]["kind"], rows[asked["slug"]]["asked_by"]), ("asks", "l2"))
        self.assertEqual(rows[stopped["slug"]]["kind"], "stopped")
        self.assertEqual(rows[faulty["slug"]]["kind"], "fault")
        self.assertEqual([o["label"] for o in rows[asked["slug"]]["options"]], ["Resume", "Reject"])

    def test_a_decision_window_restarts_after_the_last_recorded_decision(self):
        task = self.blocked("Twice", "First question?")
        T.decide(self.project, task["slug"], "Resume", "go on")
        T.resume(self.project, task["slug"], actor="altd")
        T.block(self.project, task["slug"], "Second question?", actor="l2", updates={"waiting_on": "burak"})
        [row] = T.decisions(self.project)
        events = S.read_events(self.project, task["slug"])
        decided = next(e["at"] for e in events if e.get("kind") == "decided")
        self.assertGreaterEqual(row["since"], decided)
        self.assertEqual(row["since"], events[-1]["at"])

    def test_decide_records_the_choice_and_the_l2_reads_it_on_resume(self):
        task = self.blocked("Landscape rule", DILEMMA)
        record = T.decide(self.project, task["slug"], "keep the portrait rule everywhere", "Portrait first.")
        self.assertEqual((record["key"], record["option"], record["note"], record["by"]),
                         ("B", "Keep the portrait rule everywhere", "Portrait first.", "burak"))
        self.assertEqual(record["message"], "Decision: B (Keep the portrait rule everywhere). Portrait first.")
        saved = S.load_task(self.project, task["slug"])["decision"]
        self.assertEqual((saved["key"], saved["option"], saved["note"]), ("B", "Keep the portrait rule everywhere", "Portrait first."))
        self.assertEqual(S.read_events(self.project, task["slug"])[-1]["kind"], "decided")
        by_index = T.decide(self.project, task["slug"], 0, "")
        self.assertEqual((by_index["key"], by_index["message"]), ("A", "Decision: A (The conversation scrolls as one page)."))
        with self.assertRaisesRegex(T.TransitionError, "option must be one of"):
            T.decide(self.project, task["slug"], "Reject", "")
        plain = self.blocked("Plain", "Which colour?")
        self.assertEqual(T.decide(self.project, plain["slug"], "resume", "Blue.")["message"], "Blue.")
        self.assertEqual(T.decide(self.project, plain["slug"], 1, "")["key"], "reject")

    def test_fyi_is_a_system_chat_row_and_the_inbox_file_is_gone(self):
        task = T.new(self.project, "Probe", "Do it.", actor="burak")
        row = T.fyi(self.project, task["slug"], "activation pending: main is ahead.", actor="altd")
        self.assertEqual((row["role"], row["trigger"], row["slug"], row["by"]), ("system", "fyi", task["slug"], "altd"))
        self.assertEqual([r["text"] for r in fyi_rows(self.project)], ["activation pending: main is ahead."])
        self.assertEqual(l3.chat_history(self.project)[-1]["text"], "activation pending: main is ahead.")
        self.assertFalse((config.project_dir(self.project) / "inbox.jsonl").exists())
        self.assertEqual(S.read_events(self.project, task["slug"])[-1]["kind"], "fyi")
        self.assertFalse(hasattr(T, "inbox") or hasattr(digest, "fyis"))
        self.assertNotIn("fyis", server.overview())
        self.assertNotIn("Recent FYIs", digest.text())

    def test_the_overview_counts_blocks_waiting_on_l3_and_names_each_queue_hold(self):
        self.blocked("On L3", "Need the lease.", waiting_on="l3")
        queued = T.new(self.project, "Waiting", "Do it.", actor="burak")
        overview = server.overview()
        counts = next(p for p in overview["projects"] if p["name"] == self.project)["counts"]
        self.assertEqual((counts["blocked"], counts["waits_l3"]), (1, 1))
        [waiting] = [w for w in overview["wip"]["waiting"] if w["slug"] == queued["slug"]]
        self.assertEqual((waiting["why"], waiting["hold"]), ("dispatch", "ready for dispatch"))
        with mock.patch.object(dispatch, "wip_hold", return_value="WIP limit: 2 running in here"):
            [held] = [w for w in digest.wip()["waiting"] if w["slug"] == queued["slug"]]
        self.assertEqual(held["hold"], "WIP limit: 2 running in here")


class TestDecisionApi(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.private_ledgers()
        self.patch(server, "log", new=lambda *_a, **_k: None)
        server.Handler._seen_clients.clear()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
        raw = json.dumps(body).encode() if body is not None else b""
        head = f"{method} {path} HTTP/1.0\r\nHost: x\r\n"
        if body is not None:
            head += f"Content-Type: application/json\r\nContent-Length: {len(raw)}\r\n"
        host, port = self.httpd.server_address
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.sendall(head.encode() + b"\r\n" + raw)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        headers, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
        if b"chunked" in headers.lower():  # the chat stream is chunked NDJSON
            body, rest = b"", payload
            while rest:
                size, _, rest = rest.partition(b"\r\n")
                length = int(size.strip() or b"0", 16)
                if not length:
                    break
                body, rest = body + rest[:length], rest[length + 2:]
            payload = body
        return int(headers.split()[1]), payload

    def blocked(self, reason: str) -> dict:
        task = T.new(self.project, "Landscape rule", "Do it.", actor="burak")
        task.update({"state": "running", "attempt": 1, "agent_id": "agent-1", "session_id": "s-1", "l2_engine": "claude"})
        S.save_task(self.project, task)
        return T.block(self.project, task["slug"], reason, actor="l2", updates={"waiting_on": "burak"})

    def test_decide_accepts_a_label_and_a_note_and_hands_the_lifecycle_the_answer(self):
        task = self.blocked(DILEMMA)
        with mock.patch.object(server, "request_daemon_task_operation") as op:
            status, payload = self.request("POST", "/api/decide", {
                "project": self.project, "slug": task["slug"], "option": "Keep the portrait rule everywhere",
                "note": "Portrait first."})
        self.assertEqual(status, 200, payload)
        body = json.loads(payload)
        self.assertEqual((body["queued"], body["decision"]["key"]), (True, "B"))
        op.assert_called_once_with(self.project, task["slug"], "resume",
                                   "Decision: B (Keep the portrait rule everywhere). Portrait first.", actor="burak")
        self.assertEqual([m["text"] for m in T.pending(self.project, task["slug"])],
                         ["Decision: B (Keep the portrait rule everywhere). Portrait first."])
        self.assertEqual(S.load_task(self.project, task["slug"])["decision"]["note"], "Portrait first.")

    def test_decide_by_index_still_rejects_and_an_unknown_option_is_refused(self):
        task = self.blocked("Which colour?")
        with mock.patch.object(server, "request_daemon_task_operation") as op:
            status, _ = self.request("POST", "/api/decide", {
                "project": self.project, "slug": task["slug"], "option": 1, "note": "Not now."})
        self.assertEqual(status, 200)
        op.assert_called_once_with(self.project, task["slug"], "reject", "Not now.", actor="burak")
        with mock.patch.object(server, "request_daemon_task_operation") as op:
            status, payload = self.request("POST", "/api/decide", {
                "project": self.project, "slug": task["slug"], "option": "Maybe"})
        self.assertEqual(status, 400)
        self.assertIn("option must be one of Resume, Reject", json.loads(payload)["error"])
        op.assert_not_called()

    def test_a_follow_up_carries_the_decision_slug_on_its_rows_and_the_active_turn(self):
        task = self.blocked(DILEMMA)
        started, release, prompts = threading.Event(), threading.Event(), []
        self.addCleanup(release.set)

        def provider(prompt, **kwargs):
            prompts.append(prompt)
            kwargs["on_start"](4242)  # the server names the turn to the page once the provider starts
            started.set()
            release.wait(10)
            return {"text": "A keeps the composer reachable.", "session_id": "s1", "context_tokens": 10,
                    "cost": 0.0, "usage": {}, "turns": 1, "error": None, "tools": []}

        result = []
        with mock.patch.object(l3, "_select", return_value={"engine": "claude", "why": "test"}), \
             mock.patch.object(engines, "claude_print", side_effect=provider), \
             mock.patch.object(server, "request_l3_drain"):
            worker = threading.Thread(target=lambda: result.append(self.request(
                "POST", "/api/chat", {"project": self.project, "text": "Why not B?", "slug": task["slug"]})), daemon=True)
            worker.start()
            self.assertTrue(started.wait(5))
            active = json.loads(self.request("GET", f"/api/chat/{self.project}")[1])["active"]
            self.assertEqual(active["slug"], task["slug"])
            release.set()
            worker.join(5)
        status, payload = result[0]
        self.assertEqual(status, 200)
        lines = [json.loads(line) for line in payload.decode().splitlines() if line.strip()]
        self.assertEqual(lines[0]["turn"]["slug"], task["slug"])
        history = l3.chat_history(self.project)
        self.assertEqual([(row["role"], row.get("slug")) for row in history[-2:]],
                         [("user", task["slug"]), ("assistant", task["slug"])])
        self.assertIn(f"follow-up on the decision waiting on task `{task['slug']}`", prompts[0])
        self.assertIn("it stays blocked until the operator chooses", prompts[0])
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "blocked", "a follow-up never decides")

    def test_a_queued_follow_up_keeps_its_slug_and_its_own_turn(self):
        task = self.blocked(DILEMMA)
        with mock.patch.object(l3, "busy", return_value=True), mock.patch.object(server, "request_l3_drain"):
            status, payload = self.request("POST", "/api/chat", {
                "project": self.project, "text": "Why not B?", "slug": task["slug"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["queued"]["slug"], task["slug"])
        l3.queue_message(self.project, "unrelated", trigger="chat", role="burak")
        with mock.patch.object(l3, "turn", return_value={"completed": True}) as turn, \
             mock.patch.object(l3, "_select", return_value={"engine": "claude", "why": "test"}):
            server.drain_l3_queue(self.project)
        self.assertEqual([(c.args[1], c.kwargs.get("slug")) for c in turn.call_args_list],
                         [("Why not B?", task["slug"]), ("unrelated", None)])

    def test_a_slug_that_is_not_a_task_identifier_is_refused(self):
        status, _ = self.request("POST", "/api/chat", {"project": self.project, "text": "hi", "slug": "../x"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
