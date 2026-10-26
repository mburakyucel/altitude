"""Issue #902: automatic review reflects current prerequisites without mutating authority."""
import copy
import http.client
import json
import threading
from unittest import mock

from tests.support import AltitudeCase, add_worktree, git, make_repo
from altitude import config, reviews, server, state as S, tasks as T, verify


class TestMergeReviewReadiness(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        make_repo(self.repo)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.patch(server, "log", lambda *_a, **_k: None)

    def request(self, method, path, body=None):
        client = http.client.HTTPConnection(*self.httpd.server_address, timeout=10)
        try:
            client.request(method, path, json.dumps(body) if body is not None else None,
                           {"Content-Type": "application/json"})
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def held(self):
        slug = T.new(self.project, "Fictional feature", "Review the delivered feature.")["slug"]
        worktree = add_worktree(self.repo, slug)
        T.dispatch(self.project, slug, attempt=1, session_id="fictional-session", agent_id="fictional-owner",
                   worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        T.set_hold_merge(self.project, slug, "Operator reviews the finished feature")
        task = S.load_task(self.project, slug)
        task.update(state="reported", prs=[42], delivery={"number": 42, "at": S.now(),
                    "head": git("rev-parse", "HEAD", cwd=worktree).strip()})
        S.save_task(self.project, task)
        return task

    def review(self, identity="fixture-review", **fields):
        return {"id": identity, "subject": "changes", "state": "completed", "reconciled": {"at": S.now()},
                "dispositions": [], "result": {"findings": []},
                "message": {"id": identity, "role": "system", "at": S.now(), "text": "Fictional saved review."},
                **fields}

    def assert_no_review(self, task):
        before = copy.deepcopy(task)
        self.assertIsNone(T.review_pr(self.project, task))
        self.assertIsNone(T.review_row(self.project, task))
        self.assertNotIn("review", [row["kind"] for row in T.decisions(self.project)])
        self.assertNotIn("review PR", T.wait_label(self.project, task) or "")
        self.assertEqual(S.load_task(self.project, task["slug"]), before)

    def test_blocked_and_reported_open_finding_and_l3_wait_have_no_automatic_review(self):
        task = self.held()
        task["reviews"] = [self.review(dispositions=[{"finding_id": "F1", "disposition": "open"}])]
        for state in ("blocked", "reported"):
            task.update(state=state, waiting_on="l3", blocked_reason="Waiting for the shared API delivery.")
            S.save_task(self.project, task)
            with self.subTest(state=state):
                self.assert_no_review(task)
                self.assertEqual(T.block_status(self.project, task)[0], "waiting-l3")
                code, view = self.request("GET", f"/api/task/{self.project}/{task['slug']}")
                self.assertEqual(code, 200, view)
                self.assertEqual(view["waiting_on"], "l3")
                self.assertEqual(view["review"]["subjects"]["changes"]["open"], 1)
                self.assertEqual(view["hold_merge"], task["hold_merge"])

    def test_each_review_prerequisite_suppresses_and_resolved_or_skipped_reviews_allow_review(self):
        task = self.held()
        blocked = [self.review(state=state) for state in ("requested", "running", "failed", "cancelled")]
        blocked += [self.review(reconciled=None),
                    self.review(dispositions=[{"finding_id": "F1", "disposition": "open"}]),
                    self.review(subject="proposal", dispositions=[{"finding_id": "F1", "disposition": "open"}])]
        for record in blocked:
            with self.subTest(record=record):
                task["reviews"] = [record, self.review("additional-clear")]
                S.save_task(self.project, task)
                self.assert_no_review(task)
        for record in (self.review(), self.review(state="withdrawn"),
                       self.review(dispositions=[{"finding_id": "F1", "disposition": "fixed"}]),
                       self.review(dispositions=[{"finding_id": "F1", "disposition": "dismissed"}])):
            task["reviews"] = [record]
            S.save_task(self.project, task)
            with self.subTest(record=record):
                self.assertEqual(T.review_pr(self.project, task), 42)

    def test_l3_wait_alone_suppresses_and_independent_operator_question_remains_answerable(self):
        task = self.held()
        task.update(state="running", waiting_on="l3")
        S.save_task(self.project, task)
        task = T.block(self.project, task["slug"], "Waiting for the shared API delivery.", actor="l2")
        prerequisite = task["questions"][-1]
        self.assert_no_review(task)
        T.escalate(self.project, task["slug"], "Which rollout region?", questions={"questions": [
            {"question": "Which rollout region?", "review": "none", "options": [
                {"key": "west", "label": "West", "text": "Use the west region."}], "recommended_key": "west"}]})
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual(saved["waiting_on"], T.OPERATOR_MESSAGE_ROLE)
        self.assertEqual(saved["questions"][0], prerequisite)
        self.assert_no_review(saved)
        [row] = T.decisions(self.project)
        self.assertEqual(row["question"], "Which rollout region?")
        answer = T.accept_question(self.project, task["slug"], row["id"], row["revision"], "west")
        self.assertEqual(answer["response"]["text"], "Use the west region.")
        self.assertEqual(S.load_task(self.project, task["slug"])["questions"][0], prerequisite)

    def test_explicit_pr_question_retains_its_options_with_open_findings(self):
        task = self.held()
        task.update(state="running", waiting_on=T.OPERATOR_MESSAGE_ROLE,
                    reviews=[self.review(dispositions=[{"finding_id": "F1", "disposition": "open"}])])
        S.save_task(self.project, task)
        T.block(self.project, task["slug"], "Review the rollout choice for this PR.", actor="l2", questions={"questions": [
            {"question": "May the rollout begin after the API work?", "review": "changes", "options": [
                {"key": "approve", "label": "Approve after API", "text": "Approve the rollout after the API work."}],
             "recommended_key": "approve"}]})
        [row] = T.decisions(self.project)
        self.assertEqual((row["kind"], row["pr"], row["review"]["open"]), ("asks", 42, 1))
        self.assertEqual(row["options"][0]["label"], "Approve after API")
        answer = T.accept_question(self.project, task["slug"], row["id"], row["revision"], "approve")
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual(answer["response"]["text"], "Approve the rollout after the API work.")
        self.assertEqual(saved["hold_merge"], task["hold_merge"])
        self.assertEqual(saved["reviews"], task["reviews"])

    def park(self, slug, reason, question=None):
        task = S.load_task(self.project, slug)
        if task["state"] != "running":
            T.resume(self.project, slug)
        T.take_inbox(self.project, slug)  # the owner read the operator's words at its checkpoint
        if question is None:
            task = S.load_task(self.project, slug)
            S.write_json(S.task_dir(self.project, slug) / "report.json", {
                "landed": {"prs": [{"number": 42, "merged": False}], "main_runs": [], "deploy": "not-applicable"},
                "review": [], "blocked": ""})
            return T.report(self.project, slug, {"verdict": "ok", "owner": T.report_owner(task),
                                                 "delivery": task["delivery"], "prs": task["prs"]})
        T.block(self.project, slug, reason, actor="l2", updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE},
                questions={"questions": [question]})
        return S.load_task(self.project, slug)["questions"][-1]

    def answer(self, slug, question, text):
        row = T.message(self.project, slug, T.OPERATOR_MESSAGE_ROLE, text, question_id=question["id"],
                        revision=question["revision"])
        T.resume(self.project, slug)
        T.resolve_question(self.project, slug, question["id"], question["revision"], row["id"],
                           disposition="answered", reason=f"The operator said: {text}", expected_attempt=1)

    def surfaces(self, slug):
        task = S.load_task(self.project, slug)
        code, view = self.request("GET", f"/api/project/{self.project}")  # Needs you and the task page's turn rows
        self.assertEqual(code, 200, view)
        rows = [row for row in view["decisions"] if row["slug"] == slug]
        return T.review_pr(self.project, task), [row["kind"] for row in rows], T.wait_label(self.project, task)

    def test_an_answered_pr_question_keeps_the_card_away_until_a_new_version(self):
        # Reported 2026-10-25: the operator turned a held PR's design down through its merge question; the owner's
        # next question, about the same PR's design and marked "none", showed with Approve merge directly under it.
        task = self.held()
        slug, name = task["slug"], config.operator_label()
        self.assertEqual(self.surfaces(slug), (42, ["review"], f"{name}'s turn · review PR #42"),
                         "an owner that never asked gets the card")
        merge = self.park(slug, "Merge the feature?", {
            "question": "Merge the feature (PR #42)?", "review": "changes", "recommended_key": "approve", "options": [
                {"key": "approve", "label": "Approve merge", "text": "Merge the feature as shown."},
                {"key": "changes", "label": "Request changes", "text": "Hold the feature for changes."}]})
        self.answer(slug, merge, "Its menu and sheet feel off; the chat should offer it instead.")
        design = self.park(slug, "How should the feature start?", {
            "question": "How should the feature start?", "review": "none", "recommended_key": "chat", "options": [
                {"key": "chat", "label": "Suggest in chat", "text": "Offer it in the conversation."},
                {"key": "menu", "label": "Keep the menu", "text": "Keep the menu item."}]})
        self.assertEqual(self.surfaces(slug), (None, ["asks"], f"{name}'s turn · 1 question"))
        [row] = T.decisions(self.project)
        self.assertEqual(row["question"], "How should the feature start?")
        self.answer(slug, design, "Offer it in the conversation.")
        self.park(slug, "Rebuilding the start in the conversation.")
        self.assertEqual(self.surfaces(slug)[:2], (None, []), "the same version asks for nothing after the answer")
        self.assertNotIn("review PR", T.wait_label(self.project, S.load_task(self.project, slug)) or "")
        saved = S.load_task(self.project, slug)
        self.assertEqual((saved["hold_merge"], saved["prs"]), (task["hold_merge"], [42]))

        # Something new to review brings the card back: a new head, another PR, a new hold. The task's log orders
        # it against the answer, so one recorded in the answer's own whole second counts on the side it happened.
        events = S.read_events(self.project, slug)
        index = next(i for i, event in enumerate(events) if event.get("question_id") == merge["id"])
        second = events[index]["at"]
        for event in ({"kind": "delivery", "number": 42, "head": "b" * 40, "at": second},
                      {"kind": "pr-adopted", "number": 43, "at": second},
                      {"kind": "hold-merge", "why": "Operator reviews the rebuilt feature", "at": second}):
            with self.subTest(event=event["kind"]):
                self.assertFalse(T._answered_pr(self.project, saved, [*events, event]))
                self.assertTrue(T._answered_pr(self.project, saved, [*events[:index], event, *events[index:]]))
        # Only the operator's own answer or redirection counts; a withdrawal or L3's settlement does not.
        for change, answered in (({"disposition": "superseded"}, True), ({"disposition": "withdrawn", "by": "l2"}, False),
                                 ({"by": "l3", "l3_authority": "Recorded authority."}, False)):
            with self.subTest(change=change):
                questions = [{**q, "resolution": {**q["resolution"], **change}} if q["status"] == "resolved" else q
                             for q in saved["questions"] if q["id"] in (merge["id"], design["id"])]
                self.assertEqual(T._answered_pr(self.project, {**saved, "questions": questions}, events), answered)
        self.assertTrue(T._answered_pr(self.project, saved, events))

        # The operator naming the PR in chat brings the card back once its owner stops again.
        with mock.patch.object(verify, "gh", return_value={"state": "OPEN"}):
            T.message(self.project, slug, T.OPERATOR_MESSAGE_ROLE, "PR #42 is ready for another look.")
        self.assertEqual(self.surfaces(slug)[:2], (None, []), "the owner holds the turn it was handed")
        self.park(slug, "Reported after the operator's note.")
        self.assertEqual(self.surfaces(slug), (42, ["review"], f"{name}'s turn · review PR #42"))

    def approval_body(self, task):
        return {"project": self.project, "slug": task["slug"], "text": "Approved: merge PR #42.",
                "review_pr": 42, "review_hold_id": task.get("hold_merge_id")}

    def test_stale_generated_approval_is_refused_without_wake_or_any_record_change(self):
        original = self.held()
        body = self.approval_body(original)
        blockers = [{"reviews": [self.review(dispositions=[{"finding_id": "F1", "disposition": "open"}])]},
                    {"waiting_on": "l3"}, {"hold_merge_id": "renewed-hold"}, {"hold_merge": None},
                    {"state": "running"}, {"fault": "fictional-fault"}, {"prs": [43]}]
        for fields in blockers:
            task = {**original, **fields}
            S.save_task(self.project, task)
            events = S.read_events(self.project, task["slug"])
            messages = T.task_messages(self.project, task["slug"])
            with self.subTest(fields=fields), mock.patch.object(server, "request_task_resume") as wake:
                code, response = self.request("POST", "/api/l2/message", body)
                self.assertEqual(code, 409, response)
                self.assertIn("not ready", response["error"])
                wake.assert_not_called()
                self.assertEqual(S.load_task(self.project, task["slug"]), task)
                self.assertEqual(S.read_events(self.project, task["slug"]), events)
                self.assertEqual(T.task_messages(self.project, task["slug"]), messages)

    def test_ready_generated_approval_sends_the_operator_message_and_preserves_hold(self):
        task = self.held()
        task["state"] = "blocked"
        S.save_task(self.project, task)
        with mock.patch.object(server, "request_task_resume") as wake:
            code, response = self.request("POST", "/api/l2/message", self.approval_body(task))
        wake.assert_called_once_with(self.project, task["slug"])
        self.assertEqual(code, 200, response)
        self.assertEqual(response["message"]["text"], "Approved: merge PR #42.")
        self.assertEqual(response["message"]["role"], T.OPERATOR_MESSAGE_ROLE)
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual(saved["hold_merge"], task["hold_merge"])
        self.assert_no_review(saved)
