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
import time
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

    def test_an_escalation_carries_its_explicit_recommendation_and_durable_anchor(self):
        task = self.blocked("Landscape rule", "Which rule applies in landscape?", waiting_on="l3")
        first_question = task["questions"][-1]
        T.escalate(self.project, task["slug"], DILEMMA)
        [row] = T.decisions(self.project)
        self.assertEqual((row["kind"], row["asked_by"], row["title"]), ("asks", "l3", "Landscape rule"))
        self.assertEqual(row["question"], "Slice 2 is green; one spec gap needs your call before merge.")
        self.assertEqual(row["recommendation"]["label"], "The conversation scrolls as one page")
        self.assertEqual(row["detail"], DILEMMA)
        self.assertEqual((row["id"], row["revision"]), (first_question["id"], 2))
        anchor = next(m for m in T.task_messages(self.project, task["slug"]) if m["id"] == row["anchor_id"])
        self.assertEqual((anchor["role"], anchor["text"]), ("l3", DILEMMA))
        self.assertEqual(T.pending(self.project, task["slug"])[0]["id"], anchor["id"])
        self.assertFalse(S.load_task(self.project, task["slug"]).get("resume_after"))

    def test_an_l2_question_is_asked_by_the_l2_and_a_stop_or_fault_keeps_its_kind(self):
        asked = self.blocked("Asked", "Which colour should the dot be?")
        stopped = self.blocked("Stopped", "stopped by Burak", actor="burak")
        faulty = self.blocked("Faulty", "system fault [sandbox]: refused", actor="altd", fault="sandbox")
        rows = {row["slug"]: row for row in T.decisions(self.project)}
        self.assertEqual((rows[asked["slug"]]["kind"], rows[asked["slug"]]["asked_by"]), ("asks", "l2"))
        self.assertEqual(rows[stopped["slug"]]["kind"], "stopped")
        self.assertEqual(rows[faulty["slug"]]["kind"], "fault")
        self.assertIsNone(rows[asked["slug"]]["recommendation"])
        for operational in (stopped, faulty):
            self.assertIsNone(rows[operational["slug"]]["recommendation"])
            self.assertNotIn("options", rows[operational["slug"]])

    def test_a_new_question_has_a_new_identity_after_the_prior_question_is_resolved(self):
        task = self.blocked("Twice", "First question?")
        question = task["questions"][-1]
        answer = T.message(self.project, task["slug"], "burak", "go on")
        T.resolve_question(self.project, task["slug"], question["id"], 1, answer["id"],
                           disposition="answered", reason="Continue", expected_attempt=1)
        T.resume(self.project, task["slug"], actor="altd")
        T.block(self.project, task["slug"], "Second question?", actor="l2", updates={"waiting_on": "burak"})
        [row] = T.decisions(self.project)
        self.assertNotEqual(row["id"], question["id"])
        self.assertEqual(row["revision"], 1)
        self.assertEqual(T.question_views(self.project, task["slug"])[0]["status"], "resolved")

    def test_a_reply_hands_the_turn_back_until_the_owner_parks_again(self):
        task = self.blocked("Rollback", "How long should the old index stay?")
        slug, name = task["slug"], config.operator_label()
        [row] = T.decisions(self.project)
        self.assertEqual(T.wait_label(self.project, S.load_task(self.project, slug)), f"{name}'s turn · 1 question")
        T.message(self.project, slug, "burak", "Could we roll back after day seven?")
        self.assertEqual(T.decisions(self.project), [], "sending anything hands the turn back")
        self.assertEqual(T.wait_label(self.project, S.load_task(self.project, slug)), f"L2 replying to {name}")
        self.assertEqual(T.question_views(self.project, slug)[-1]["status"], "open", "the record stays open")
        T.resume(self.project, slug)
        T.block(self.project, slug, "How long should the old index stay?", actor="l2")
        [again] = T.decisions(self.project)
        self.assertEqual((again["id"], again["revision"], again["asked_again"]), (row["id"], 1, True))
        T.message(self.project, slug, "burak", "What does fourteen days cost?")
        T.resume(self.project, slug)
        T.block(self.project, slug, "Keep it seven or fourteen days? Fourteen costs 2 GB more.", actor="l2")
        [revised] = T.decisions(self.project)
        self.assertEqual((revised["id"], revised["revision"]), (row["id"], 2))
        self.assertNotIn("asked_again", revised, "a revised question is asked for the first time")

    def test_a_fault_park_after_a_reply_keeps_the_turn_with_the_owner(self):
        # Only the owner's own park or report asks again; a fault is L3's to repair, not the operator's turn.
        task = self.blocked("Rollback", "How long should the old index stay?")
        slug = task["slug"]
        T.message(self.project, slug, "burak", "Could we roll back after day seven?")
        T.resume(self.project, slug)
        T.block(self.project, slug, "system fault [worker:x]: died", actor="altd", updates={"fault": "worker:x"})
        self.assertEqual(T.decisions(self.project), [])
        self.assertNotIn("asked_again", S.load_task(self.project, slug)["questions"][-1])
        self.assertEqual(T.wait_label(self.project, S.load_task(self.project, slug)), "paused · fault worker:x")

    def test_a_held_review_ready_delivery_asks_for_review_beside_a_later_question(self):
        # #419: a later, unrelated proposal must not hide the merge decision, and neither reads "paused".
        task = self.blocked("Checkout fix", "Build the export next?")
        slug, name = task["slug"], config.operator_label()
        held = S.load_task(self.project, slug)
        held.update(hold_merge="Operator review before merge", prs=[42], delivery={"number": 42, "head": "a" * 40, "at": S.now()})
        S.save_task(self.project, held)
        rows = T.decisions(self.project)
        self.assertEqual([row["kind"] for row in rows], ["asks", "review"])
        self.assertEqual({k: rows[1][k] for k in ("pr", "question", "detail")},
                         {"pr": 42, "question": "Review PR #42 before merge",
                          "detail": "Operator review before merge"})
        for blocker in ({"fault": "worker:x"}, {"stop_id": "operator-stop"}):
            with self.subTest(blocker=blocker):
                S.save_task(self.project, {**S.load_task(self.project, slug), **blocker})
                self.assertNotIn("review", [row["kind"] for row in T.decisions(self.project)])
                S.save_task(self.project, {k: v for k, v in S.load_task(self.project, slug).items() if k not in blocker})
        self.assertEqual(T.wait_label(self.project, S.load_task(self.project, slug)), f"{name}'s turn · 1 question · review PR #42")
        T.message(self.project, slug, "burak", "I will review PR #42 shortly.")
        self.assertEqual(T.decisions(self.project), [])
        T.resume(self.project, slug)
        self.assertEqual(T.decisions(self.project), [], "a running owner is working, not waiting for review")
        T.take_inbox(self.project, slug)  # the owner read the approval at its checkpoint
        T.report(self.project, slug, {"verdict": "ok", "delivery": S.load_task(self.project, slug)["delivery"]})
        self.assertEqual([row["kind"] for row in T.decisions(self.project)], ["review"])

    def test_a_pr_question_is_the_single_surface_and_the_card_returns_without_it(self):
        # One PR merge decision appears once: the owner's question with quick options replaces the generated
        # card, through a revision too. Freeform questions also supply one response surface;
        # unrelated questions and withdrawal leave the fallback available.
        url = "https://example.com/atlas/pull/42"
        approve = lambda text: {"questions": [{"question": text, "recommended_key": "approve", "options": [
            {"key": "approve", "label": "Approve merge", "text": "Approved: merge PR #42."},
            {"key": "changes", "label": "Request changes", "text": "Hold PR #42 for changes."}]}]}
        task = self.blocked("Checkout fix", f"Merge the checkout fix? {url}")
        slug, name = task["slug"], config.operator_label()
        held = S.load_task(self.project, slug)
        held.update(hold_merge="Operator review before merge", prs=[42], delivery={"number": 42, "head": "a" * 40, "at": S.now()})
        S.save_task(self.project, held)
        self.assertEqual([row["kind"] for row in T.decisions(self.project)], ["asks"],
                         "a freeform question and an Approve card must not compete for the same PR")
        self.assertEqual(T.wait_label(self.project, S.load_task(self.project, slug)),
                         f"{name}'s turn · 1 question")
        T.resume(self.project, slug)
        T.block(self.project, slug, "Merge the checkout fix?", actor="l2", updates={"waiting_on": "burak"},
                questions=approve(f"Merge the checkout fix? {url}"))
        self.assertEqual([row["kind"] for row in T.decisions(self.project)], ["asks"])
        self.assertEqual(T.wait_label(self.project, S.load_task(self.project, slug)), f"{name}'s turn · 1 question")
        question = S.load_task(self.project, slug)["questions"][-1]
        T.resume(self.project, slug)
        T.block(self.project, slug, "Merge the checkout fix now?", actor="l2", updates={"waiting_on": "burak"},
                questions={"questions": [{**approve(f"Merge the checkout fix now that the copy is clearer? {url}")["questions"][0],
                                          "id": question["id"]}]})
        [row] = T.decisions(self.project)
        self.assertEqual((row["kind"], row["revision"]), ("asks", question["revision"] + 1))
        T.resume(self.project, slug)
        T.block(self.project, slug, "Build the export next? Merge PR #420 elsewhere first.", actor="l2",
                updates={"waiting_on": "burak"})
        self.assertEqual([row["kind"] for row in T.decisions(self.project)], ["asks", "review"],
                         "a question about another PR does not answer this review")
        question = S.load_task(self.project, slug)["questions"][-1]
        T.resolve_question(self.project, slug, question["id"], question["revision"], None, disposition="withdrawn",
                           reason="Asked in the review instead.", expected_attempt=S.load_task(self.project, slug)["attempt"])
        self.assertEqual([row["kind"] for row in T.decisions(self.project)], ["review"])

    def test_submitted_pr_question_keeps_one_surface_until_owner_resolution(self):
        # The freeform answer is delivered, not classified as merge permission. An independent
        # question stays usable; resolving discussion restores the held review when still needed.
        task = self.blocked("Checkout review", "Choose the checkout rollout details.")
        slug = task["slug"]
        held = S.load_task(self.project, slug)
        held.update(hold_merge="Operator review before merge", prs=[42],
                    delivery={"number": 42, "head": "a" * 40, "at": S.now()})
        S.save_task(self.project, held)
        original = held["questions"][-1]
        T.resume(self.project, slug)
        T.block(self.project, slug, "Choose the checkout rollout details.", actor="l2", questions={"questions": [
            {"id": original["id"], "question": "Who announces PR #42 to the release team?"},
            {"question": "Which backup region should we use?"}]})
        task = S.load_task(self.project, slug)
        question, independent = T.question_group_view(self.project, task)["questions"]
        self.assertEqual([row.get("id") for row in T.decisions(self.project)], [question["id"], independent["id"]])
        self.assertFalse(T.asks_merge(task, question), "freeform input does not authorize a changes-review exception")
        response = T.accept_question(self.project, slug, question["id"], question["revision"], text="The release team.")
        T.resume(self.project, slug)
        T.take_inbox(self.project, slug)
        T.block(self.project, slug, "Choose the checkout rollout details.", actor="l2")
        self.assertEqual([row["id"] for row in T.decisions(self.project)], [independent["id"]])
        with mock.patch.object(server.monitor, "sessions", return_value=[]):
            view = server.task_view(self.project, slug)
        self.assertEqual(view["question_group"]["questions"][0]["response"], response["response"])
        self.assertIsNone(T.review_row(self.project, S.load_task(self.project, slug)))
        T.resolve_question(self.project, slug, question["id"], question["revision"], response["response"]["message_id"],
                           disposition="answered", reason="The release team announces it; merge approval is still needed.",
                           expected_attempt=1)
        self.assertEqual([row["kind"] for row in T.decisions(self.project)], ["asks", "review"])
        current = S.load_task(self.project, slug)
        self.assertEqual(current["hold_merge"], held["hold_merge"])
        self.assertIsNone(current.get("merge_approval"))
        self.assertIsNone(T.approved_pr(self.project, current), "a PR mention never turns an unrelated answer into approval")
        T.accept_question(self.project, slug, independent["id"], independent["revision"], text="West.")

    def test_an_approved_pr_waiting_on_a_dependency_is_not_asked_again(self):
        # #451: approving the PR once is enough while the owner waits on something else, including after
        # routine integration gives it a new head; a later word about the PR or a new hold asks again,
        # and faults keep their label.
        task = self.blocked("Export fix", "CI export access is missing.", waiting_on="l3")
        slug, name = task["slug"], config.operator_label()
        held = S.load_task(self.project, slug)
        held.update(hold_merge="Operator review before merge", prs=[42], delivery={"number": 42, "head": "a" * 40, "at": "2026-01-01T00:00:00+00:00"})
        S.save_task(self.project, held)
        self.assertEqual([row["kind"] for row in T.decisions(self.project)], ["review"])
        def approve_and_park(text):
            T.message(self.project, slug, "burak", text)
            T.resume(self.project, slug)
            T.take_inbox(self.project, slug)
            T.block(self.project, slug, "CI export access is still missing.", actor="l2", updates={"waiting_on": "l3"})
        time.sleep(1.1)  # the task's hold starts at creation; records keep whole seconds
        approve_and_park("Approved: merge PR #42.")
        self.assertEqual(T.decisions(self.project), [])
        self.assertEqual(T.wait_label(self.project, S.load_task(self.project, slug)), "waiting on L3 · PR #42 approved")
        approve_and_park("Wait, only merge PR #42 after the export lands.")
        self.assertEqual([row["kind"] for row in T.decisions(self.project)], ["review"], "a later word about the PR re-asks")
        approve_and_park("Approved: merge PR #42 at aaaaaaa.")
        self.assertEqual(T.decisions(self.project), [], "an earlier card's approval still counts")
        moved = S.load_task(self.project, slug)
        moved["delivery"] = {"number": 42, "head": "b" * 40, "at": "2026-01-01T00:00:00+00:00"}
        S.save_task(self.project, moved)
        self.assertEqual(T.decisions(self.project), [], "routine integration keeps the approval")
        self.assertEqual(T.wait_label(self.project, S.load_task(self.project, slug)), "waiting on L3 · PR #42 approved")
        time.sleep(1.1)  # records keep whole seconds
        T.set_hold_merge(self.project, slug, "Operator review after the export change", actor="l3")
        self.assertEqual([row["kind"] for row in T.decisions(self.project)], ["review"], "a new hold asks again")
        time.sleep(1.1)
        approve_and_park("Approved: merge PR #42.")
        S.save_task(self.project, {**S.load_task(self.project, slug), "fault": "worker:x"})
        self.assertEqual(T.wait_label(self.project, S.load_task(self.project, slug)), "paused · fault worker:x")

    def test_daemon_park_never_invents_operator_attention(self):
        for fields in ({}, {"waiting_on": None}):
            with self.subTest(fields=fields):
                task = T.new(self.project, f"Park {len(S.list_tasks(self.project))}", "Wait for the landing window.")
                T.block(self.project, task["slug"], "Landing window pending", updates=fields)
                self.assertEqual(T.decisions(self.project), [])
                row = next(row for row in digest.queue_status()["waiting"] if row["slug"] == task["slug"])
                self.assertEqual((row["kind"], row["reason"]), ("paused", "paused: Landing window pending"))

    def test_resolved_coordination_question_then_report_park_is_paused(self):
        task = self.blocked("Landing window", "May I land now?", waiting_on="l3")
        question = task["questions"][-1]
        answer = T.message(self.project, task["slug"], "l3", "Wait for the preceding delivery.")
        T.resolve_question(self.project, task["slug"], question["id"], 1, answer["id"],
                           disposition="answered", reason="Landing order recorded", expected_attempt=1)
        T.resume(self.project, task["slug"])
        T.block(self.project, task["slug"], "Landing window pending", actor="altd")
        self.assertEqual(T.decisions(self.project), [])
        [row] = digest.queue_status()["waiting"]
        self.assertEqual((row["kind"], row["reason"]), ("paused", "paused: Landing window pending"))

    def test_accept_records_the_explicit_recommendation_once_and_survives_delivery(self):
        task = self.blocked("Landscape rule", DILEMMA)
        question = task["questions"][-1]
        record = T.accept_question(self.project, task["slug"], question["id"], 1)
        self.assertEqual(record["response"]["text"], question["recommendation"]["text"])
        self.assertIsNone(record["resolution"])
        self.assertEqual(T.decisions(self.project), [])
        [answer] = T.pending(self.project, task["slug"])
        self.assertEqual((answer["role"], answer["id"]), ("burak", record["response"]["message_id"]))
        self.assertEqual(T.accept_question(self.project, task["slug"], question["id"], 1), record)
        self.assertEqual(T.take_inbox(self.project, task["slug"]), [answer])
        T.accept_question(self.project, task["slug"], question["id"], 1)
        self.assertEqual(T.pending(self.project, task["slug"]), [])
        self.assertEqual(len(T.task_messages(self.project, task["slug"])), 2)
        plain = self.blocked("Plain", "Which colour?")
        with self.assertRaisesRegex(T.TransitionError, "no explicit recommendation"):
            T.accept_question(self.project, plain["slug"], plain["questions"][-1]["id"], 1)

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

    def test_only_explicit_l3_fyis_record_heads_up_selection_without_changing_task_state(self):
        task = T.new(self.project, "Recovery owner", "Do it.", actor="burak")
        automatic = T.fyi(self.project, task["slug"], "Activation pending.")
        self.assertEqual(automatic["by"], "altd")
        self.assertFalse(automatic["heads_up"])
        for actor in ("altd", "l2", "burak", "l3"):
            with self.subTest(actor=actor):
                row = T.fyi(self.project, task["slug"], "  An owner is investigating the build.  ", actor=actor)
                self.assertEqual(row["heads_up"], actor == "l3")
                self.assertEqual(row["by"], actor)
                self.assertEqual(row["text"], "An owner is investigating the build.")
                self.assertEqual(l3.chat_history(self.project)[-1], row)
        project_row = T.fyi(self.project, None, "Deliveries are waiting on the build.", actor="l3")
        self.assertTrue(project_row["heads_up"])
        self.assertIsNone(project_row["slug"])
        self.assertEqual(S.load_task(self.project, task["slug"]), task)
        self.assertEqual(T.decisions(self.project), [])

    def test_the_overview_counts_blocks_waiting_on_l3_and_names_each_queue_hold(self):
        self.quiet_engines()
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
        head = f"{method} {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n"
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

    def test_decide_accepts_the_exact_recommendation_and_requests_an_ordinary_wake(self):
        task = self.blocked(DILEMMA)
        question = task["questions"][-1]
        with mock.patch.object(server, "request_task_resume") as op:
            status, payload = self.request("POST", "/api/decide", {
                "project": self.project, "slug": task["slug"], "question_id": question["id"], "revision": 1})
        self.assertEqual(status, 200, payload)
        body = json.loads(payload)
        self.assertTrue(body["queued"])
        self.assertEqual(body["response"]["text"], question["recommendation"]["text"])
        self.assertIsNone(body["question"]["resolution"])
        op.assert_called_once_with(self.project, task["slug"])
        self.assertEqual(T.pending(self.project, task["slug"])[0]["id"], body["response"]["message_id"])
        self.assertEqual(S.load_task(self.project, task["slug"])["questions"][-1]["status"], "open")

    def test_legacy_unfenced_options_and_absent_recommendations_are_refused(self):
        task = self.blocked("Which colour?")
        with mock.patch.object(server, "request_daemon_task_operation") as op:
            status, _ = self.request("POST", "/api/decide", {
                "project": self.project, "slug": task["slug"], "option": 1, "note": "Not now."})
        self.assertEqual(status, 409)
        op.assert_not_called()
        with mock.patch.object(server, "request_daemon_task_operation") as op:
            status, payload = self.request("POST", "/api/decide", {
                "project": self.project, "slug": task["slug"], "question_id": task["questions"][-1]["id"], "revision": 1})
        self.assertEqual(status, 409)
        self.assertIn("no explicit recommendation", json.loads(payload)["error"])
        op.assert_not_called()

    def test_custom_plain_answer_api_preserves_context_and_retries_without_resolution(self):
        task = self.blocked("Which team owns the rollout?")
        question = task["questions"][-1]
        body = {"project": self.project, "slug": task["slug"], "question_id": question["id"],
                "revision": 1, "text": "Search team"}
        with mock.patch.object(server, "request_task_resume") as wake:
            for malformed in ({**body, "text": "  "}, {**body, "text": None}, {**body, "option_key": None}):
                status, _ = self.request("POST", "/api/decide", malformed)
                self.assertEqual(status, 409)
            self.assertEqual(T.pending(self.project, task["slug"]), [])
            wake.assert_not_called()
            status, raw = self.request("POST", "/api/decide", body)
            self.assertEqual(status, 200, raw)
            sent = json.loads(raw)
            status, raw = self.request("POST", "/api/decide", body)
            self.assertEqual(status, 200, raw)
            self.assertEqual(json.loads(raw)["response"], sent["response"])
        self.assertEqual(sent["response"]["text"], "Search team")
        self.assertEqual(sent["question"]["status"], "open")
        self.assertIsNone(sent["question"]["resolution"])
        [message] = T.pending(self.project, task["slug"])
        self.assertEqual(message["question_refs"], [{"id": question["id"], "revision": 1}])
        self.assertEqual(message["text"], "Which team owns the rollout?\nSearch team")
        self.assertEqual(T.decisions(self.project), [])
        T.resolve_question(self.project, task["slug"], question["id"], 1, message["id"],
                           disposition="answered", reason="The search team owns rollout", expected_attempt=1)
        self.assertEqual(S.load_task(self.project, task["slug"])["questions"][-1]["status"], "resolved")

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
        self.assertIn(f"project conversation concerns task `{task['slug']}`", prompts[0])
        self.assertIn("original project conversation turn id", prompts[0])
        self.assertIn("A follow-up does not authorize implementation or close a question", prompts[0])
        self.assertNotIn("chooses an option", prompts[0])
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
