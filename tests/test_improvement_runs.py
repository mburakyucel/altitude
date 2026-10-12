"""Improvement review runs on the operator's request: start authority and refusals, the reviewer thread, L3's check
and Altitude's post, typed decisions, deadlines, stop, and one task per accepted suggestion. Projects are
fictional records; the reviewer is scripted at the engine seam."""
import http.client
import json
import threading
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, improve, improve_eval, l3, route, server, state as S, tasks as T

# The coordinator verbs start a review at the clock's time, so the fictional work finished just before it.
NOW = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=1)


def at(days: float, hours: float = 0) -> str:
    return (NOW - timedelta(days=days) + timedelta(hours=hours)).isoformat()


def finished_task(n: int, days: float) -> dict:
    return {"slug": f"dock-{n}", "title": f"Fictional dock task {n}", "state": "done", "created": at(days),
            "reported": at(days, 5), "finished": at(days, 6), "verdict": "delivered", "tokens": 1000,
            "messages": [{"id": f"m{n}", "at": at(days, 7), "role": "operator", "text": f"Why again, {n}?"}]}


def suggestion(target="agents", title="Owners re-ask settled merge questions", **fields) -> dict:
    return {"target": target, "title": title, "explanation": "Owners ask again after an answer. Cite it instead.",
            "where": "personas/l2.md decisions", "signal": "after_report", "uncertainty": "Two tasks only.", **fields}


class RunCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.quiet_engines()
        self.patch(improve.github_intake, "project_repo", return_value=("mburakyucel", "altitude"))
        self.usage = self.patch(route, "_usage", return_value={"claude": (40.0, 10.0), "codex": (None, None)})
        self.answer = {"suggestions": []}
        self.calls = []

        def review(text, instructions, schema, *, model, timeout):
            self.calls.append(text)
            aliases = [a for group in improve.ALIASES.findall(text) for a in group.split(", ")]
            answer = json.loads(json.dumps(self.answer).replace('"CITE"', json.dumps(aliases[:3])[1:-1]))
            return {"model": model, "engine_model": model, "usage": {"output_tokens": 5}, "cost": 0.01,
                    "tokens": 900, "over_limit": False, "answer": answer, "error": None}
        self.engine = self.patch(engines, "improvement_review", side_effect=review)

    def agree(self):
        """The operator's Turn on, as the settings sheet records it."""
        with S.project_lock(self.project):
            record = improve._load(self.project)
            improve._agree_locked(record, improve.terms(self.project)["version"], NOW.isoformat())
            S.write_json(improve._path(self.project), record)

    def load(self, count=6, *, agree=True):
        improve_eval.load({"project": self.project, "now": NOW.isoformat(),
                           "tasks": [finished_task(n, 1 + n) for n in range(1, count + 1)]})
        if agree:
            self.agree()

    def operator(self, text: str, hours: float = 0) -> str:
        turn = f"{abs(hash((text, hours))) % 16**12:012x}"
        l3.chat_log(self.project, "user", text, trigger="chat", turn_id=turn, at=(NOW + timedelta(hours=hours)).isoformat())
        return turn

    def started(self, **answer) -> dict:
        self.load()
        if answer:
            self.answer = answer
        receipt = improve.start(self.project, self.operator("Get improvement suggestions"), now=NOW)
        improve.execute(self.project, receipt["run"])
        return improve._find(improve._load(self.project), receipt["run"])

    def chat(self, trigger="improvement") -> list[dict]:
        return [r for r in l3.chat_history(self.project, None) if r.get("trigger") == trigger]

    def queued(self) -> list[dict]:
        return [r for r in l3._queue_rows(l3.queue_path(self.project)) if r["trigger"] == "improvement-check"]

    def posted(self) -> dict:
        run = self.started(suggestions=[suggestion(anchors=["CITE"]),
                                        suggestion(target="project", title="Docks list rebuilt by hand", anchors=["CITE"])])
        improve.post(self.project, run["id"], [1], [(2, "being handled")], now=NOW)
        return run


class TestStart(RunCase):
    def test_a_chat_request_claims_one_review_and_asking_again_returns_it(self):
        self.load()
        message = self.operator("Get improvement suggestions")
        receipt = improve.start(self.project, message, now=NOW)
        self.assertEqual((receipt["state"], receipt["tasks"]), ("reading", 6))
        self.assertEqual(receipt["deadline"], (NOW + timedelta(hours=2)).isoformat())
        self.assertEqual(improve.start(self.project, message, now=NOW), {**receipt, "replayed": True})
        with self.assertRaisesRegex(ValueError, "already running"):
            improve.start(self.project, self.operator("Again please", 1), now=NOW)

    def test_refusals_name_their_reason_and_claim_nothing(self):
        self.load(count=4)
        cases = [("4 tasks finished since", {}),
                 ("weekly allowance is inside Altitude's reserve", {"claude": (90.0, 10.0)}),
                 ("short-term allowance is inside", {"claude": (40.0, 95.0)}),
                 ("allowance is not known", {"claude": (None, 10.0)})]
        for why, usage in cases:
            with self.subTest(why=why):
                self.usage.return_value = {"codex": (None, None), **({"claude": (40.0, 10.0)} | usage)}
                with self.assertRaisesRegex(ValueError, why):
                    improve.start(self.project, self.operator(why), now=NOW)
        self.usage.return_value = {"claude": (40.0, 10.0), "codex": (None, None)}
        with mock.patch.object(improve.github_intake, "project_repo", return_value=("someone", "orchard")):
            with self.assertRaisesRegex(ValueError, "only the Altitude project"):
                improve.start(self.project, self.operator("other"), now=NOW)
        with mock.patch.object(config, "activation_pending", return_value=True):
            with self.assertRaisesRegex(ValueError, "restarts soon"):
                improve.start(self.project, self.operator("restart"), now=NOW)
        self.assertEqual(improve._load(self.project)["runs"], [])

    def test_the_window_advances_after_a_review_that_ended_with_or_without_suggestions(self):
        run = self.started()
        self.assertEqual(run["state"], "nothing")
        with self.assertRaisesRegex(ValueError, "0 tasks finished since"):
            improve.start(self.project, self.operator("Again", 1), now=NOW + timedelta(hours=1))

    def test_a_failed_review_leaves_the_window_where_it_was(self):
        self.engine.side_effect = lambda *a, **k: {"model": k["model"], "answer": None, "error": "it timed out"}
        run = self.started()
        self.assertEqual((run["state"], run["reason"]), ("failed", "it timed out"))
        self.assertEqual(self.chat()[-1]["text"], "Improvement suggestions failed · it timed out.")
        again = improve.start(self.project, self.operator("Again", 1), now=NOW + timedelta(hours=1))
        self.assertEqual(again["tasks"], 6)


class TestReading(RunCase):
    def test_nothing_to_suggest_ends_with_its_line_and_keeps_the_private_record(self):
        run = self.started()
        line = self.chat()[-1]
        self.assertRegex(line["text"], r"^Improvement suggestions of \w{3} \d+ · 6 tasks from .+ · nothing to suggest\.$")
        self.assertEqual((line["role"], line["improvement"]), ("assistant", run["id"]))
        folder = improve.run_dir(self.project, run["id"])
        self.assertIn("# Project", (folder / "prompt.txt").read_text())
        self.assertEqual(S.read_json(folder / "answer.json"), {"answer": {"suggestions": []}})
        self.assertEqual(run["call"]["tokens"], 900)

    def test_suggestions_queue_one_check_turn_that_names_the_run_only(self):
        run = self.started(suggestions=[suggestion(anchors=["CITE"])])
        self.assertEqual(run["state"], "checking")
        [row] = self.queued()
        self.assertEqual(row["id"], run["check"])
        self.assertIn(f"alt improve status --run {run['id']}", row["text"])
        self.assertNotIn("re-ask", row["text"])  # the unchecked wording stays out of the conversation
        self.assertEqual(self.chat(), [])

    def test_an_error_while_reading_fails_the_run_and_is_raised_for_a_fault(self):
        self.load()
        receipt = improve.start(self.project, self.operator("Get improvement suggestions"), now=NOW)
        with mock.patch.object(improve, "prompt", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                improve.execute(self.project, receipt["run"])
        run = improve._find(improve._load(self.project), receipt["run"])
        self.assertEqual(run["state"], "failed")
        self.assertEqual(self.chat()[-1]["text"], "Improvement suggestions failed · Altitude hit an error; a fault is filed.")

    def test_a_restart_holding_the_quiet_point_fails_the_run_before_reading(self):
        self.load()
        receipt = improve.start(self.project, self.operator("Get improvement suggestions"), now=NOW)
        with config.restart_lock(exclusive=True):
            improve.execute(self.project, receipt["run"])
        self.assertEqual(self.engine.call_count, 0)
        self.assertEqual(improve._find(improve._load(self.project), receipt["run"])["reason"],
                         "it was interrupted by a restart")

    def test_a_review_stopped_while_reading_discards_its_answer(self):
        self.load()
        self.answer = {"suggestions": [suggestion(anchors=["CITE"])]}
        receipt = improve.start(self.project, self.operator("Get improvement suggestions"), now=NOW)
        message = self.operator("Stop the suggestions", 0.5)
        stopped = improve.stop(self.project, message)
        self.assertEqual(stopped["state"], "stopped")
        self.assertEqual(improve.stop(self.project, message), stopped)
        improve.execute(self.project, receipt["run"])
        run = improve._find(improve._load(self.project), receipt["run"])
        self.assertEqual((run["state"], run["suggestions"]), ("stopped", []))
        self.assertFalse((improve.run_dir(self.project, run["id"]) / "answer.json").exists())
        self.assertEqual((self.queued(), [r["text"] for r in self.chat()]), ([], ["Improvement suggestions stopped by you."]))
        with self.assertRaisesRegex(ValueError, "No improvement review is running"):
            improve.stop(self.project, self.operator("Stop", 1))


class TestPosting(RunCase):
    def checking(self) -> dict:
        return self.started(suggestions=[
            suggestion(anchors=["CITE"]),
            suggestion(target="project", title="Docks list is rebuilt by hand", anchors=["CITE"]),
            suggestion(title="[Click](https://example.invalid) **now** `rm` /etc/x #12", anchors=["CITE"])])

    def test_post_writes_the_message_with_run_numbers_task_links_and_drops(self):
        run = self.checking()
        self.assertEqual([s["n"] for s in run["suggestions"]], [1, 2])  # 3 had a link: validation dropped it
        receipt = improve.post(self.project, run["id"], [2], [(1, "already handled by an open task")], now=NOW)
        self.assertEqual((receipt["state"], receipt["post"]["shown"]), ("posted", [2]))
        [row] = self.chat()
        head, body, dropped = row["text"].split("\n\n")
        self.assertRegex(head, r"^Improvement suggestions of \w{3} \d+ · 6 tasks from ")
        lines = body.split("\n")
        self.assertEqual(lines[0], f"**2. Docks list is rebuilt by hand** · {self.project}")
        self.assertEqual(lines[1], "Owners ask again after an answer. Cite it instead.")
        self.assertRegex(lines[2], rf"^Seen in \[Fictional dock task \d\]\(/projects/{self.project}/tasks/dock-\d\)")
        self.assertEqual(dropped, "Dropped 1: already handled by an open task")
        self.assertEqual((row["turn_id"], self.queued()), (receipt["post"]["row"], []))

    def test_the_posted_message_follows_its_saved_receipt_and_appears_once(self):
        run = self.checking()
        with mock.patch.object(l3, "chat_log", side_effect=OSError("disk full")), self.assertRaises(OSError):
            improve.post(self.project, run["id"], [1, 2], [], now=NOW)
        saved = improve._find(improve._load(self.project), run["id"])
        self.assertEqual((saved["state"], saved["post"]["row"], self.chat()), ("posted", run["id"], []))
        self.assertEqual(improve.post(self.project, run["id"], [1, 2], [], now=NOW)["post"]["row"], run["id"])
        improve.reconcile(self.project, NOW)
        [row] = self.chat()  # the replay posted it; the tick found nothing left
        self.assertEqual((row["turn_id"], row["improvement"]), (run["id"], run["id"]))
        record = improve._load(self.project)
        record["runs"][-1]["line"] = row["text"]  # the chat row was written, the record not yet re-saved
        improve._save(self.project, record)
        self.assertEqual(len(self.chat()), 1)
        self.assertNotIn("line", improve._find(improve._load(self.project), run["id"]))

    def test_the_window_reads_as_days(self):
        day = lambda d, h=12: datetime(2026, 10, d, h, tzinfo=timezone.utc).astimezone().replace(hour=h).isoformat()
        self.assertEqual(improve._span(day(12, 9), day(12, 15)), "Oct 12")
        self.assertEqual(improve._span(day(5), day(12)), "Oct 5–12")
        self.assertEqual(improve._span(datetime(2026, 9, 28, 12).astimezone().isoformat(), day(5)), "Sep 28–Oct 5")

    def test_posted_text_is_plain_prose(self):
        self.assertEqual(improve._plain("~~~ js\n- ~~~ - code"), "js - ~~~ - code")
        self.assertEqual(improve._plain("about ~5 tasks"), "about ~5 tasks")
        plain = improve._plain("[Click](https://x) **now** `rm` /etc/x #12\n- item")
        self.assertEqual(plain, "(Click)(https:/\u2215x) \u2217\u2217now\u2217\u2217 'rm' \u2215etc/x #\u206012 - item")
        self.assertEqual(improve._plain("1. listed"), "listed")

    def test_the_same_post_returns_its_receipt_and_a_different_one_is_refused(self):
        run = self.checking()
        first = improve.post(self.project, run["id"], [1, 2], [], now=NOW)
        self.assertEqual(improve.post(self.project, run["id"], [2, 1], [], now=NOW), first)
        with self.assertRaisesRegex(ValueError, "already posted differently"):
            improve.post(self.project, run["id"], [1], [(2, "dup")], now=NOW)
        self.assertEqual(len(self.chat()), 1)

    def test_post_names_every_number_once_with_reasons(self):
        run = self.checking()
        for keep, drop, why in (([1], [], "exactly once"), ([1, 2], [(2, "x")], "exactly once"),
                                ([1], [(2, "")], "reason of 1"), ([1, 3], [(2, "x")], "exactly once")):
            with self.subTest(keep=keep, drop=drop), self.assertRaisesRegex(ValueError, why):
                improve.post(self.project, run["id"], keep, drop, now=NOW)

    def test_dropping_every_candidate_ends_with_nothing_to_suggest(self):
        run = self.checking()
        improve.post(self.project, run["id"], [], [(1, "one incident"), (2, "being handled")], now=NOW)
        [row] = self.chat()
        self.assertTrue(row["text"].endswith("· nothing to suggest.\n\nDropped 1: one incident\n\nDropped 2: being handled"))
        self.assertEqual(improve._find(improve._load(self.project), run["id"])["state"], "nothing")

    def test_post_is_refused_for_stopped_late_and_ended_runs(self):
        run = self.checking()
        with self.assertRaisesRegex(ValueError, "past its deadline"):
            improve.post(self.project, run["id"], [1, 2], [], now=NOW + timedelta(hours=3))
        improve.stop(self.project, self.operator("Stop the suggestions", 0.5))
        self.assertEqual(self.queued(), [])
        with self.assertRaisesRegex(ValueError, "was stopped; nothing can be posted"):
            improve.post(self.project, run["id"], [1, 2], [], now=NOW)

    def test_the_deadline_ends_an_unchecked_run_and_its_queued_turn(self):
        run = self.checking()
        improve.reconcile(self.project, NOW + timedelta(hours=1))
        self.assertEqual(len(self.queued()), 1)
        improve.reconcile(self.project, NOW + timedelta(hours=2, seconds=1))
        ended = improve._find(improve._load(self.project), run["id"])
        self.assertEqual((ended["state"], ended["reason"]), ("failed", "L3 did not finish checking"))
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.chat()[-1]["text"], "Improvement suggestions failed · L3 did not finish checking.")

    def test_the_tick_posts_an_ending_a_restart_interrupted(self):
        run = self.checking()
        with mock.patch.object(l3, "chat_log", side_effect=OSError("disk full")), self.assertRaises(OSError):
            improve.post(self.project, run["id"], [], [(1, "handled"), (2, "handled")], now=NOW)
        self.assertEqual(self.chat(), [])
        improve.reconcile(self.project, NOW)
        improve.reconcile(self.project, NOW)
        [row] = self.chat()
        self.assertTrue(row["text"].endswith("nothing to suggest.\n\nDropped 1: handled\n\nDropped 2: handled"))

    def test_a_reading_run_past_its_deadline_was_interrupted(self):
        self.load()
        receipt = improve.start(self.project, self.operator("Get improvement suggestions"), now=NOW)
        improve.reconcile(self.project, NOW + timedelta(hours=2, seconds=1))
        self.assertEqual(improve._find(improve._load(self.project), receipt["run"])["reason"],
                         "it was interrupted by a restart")


class TestDecisions(RunCase):
    def test_typed_answers_follow_the_decision_table(self):
        run = self.posted()
        ref = f"{run['id']}/1"
        dismiss = self.operator("Dismiss the first one", 1)
        self.assertEqual(improve.resolve(self.project, ref, "dismissed", dismiss, "not now", now=NOW)["state"], "dismissed")
        self.assertEqual(improve.resolve(self.project, ref, "dismissed", dismiss, "not now", now=NOW)["state"], "dismissed")
        with self.assertRaisesRegex(ValueError, "is dismissed; it cannot become dismissed"):
            improve.resolve(self.project, ref, "dismissed", self.operator("Dismiss again", 2), "again", now=NOW)
        accept = self.operator("Actually make a task for it", 3)
        self.assertEqual(improve.resolve(self.project, ref, "accepted", accept, "owner rules only", now=NOW)["state"],
                         "accepted")
        with self.assertRaisesRegex(ValueError, "is accepted; it cannot become dismissed"):
            improve.resolve(self.project, ref, "dismissed", self.operator("No, drop it", 4), "drop", now=NOW)
        decisions = improve._find(improve._load(self.project), run["id"])["decisions"]
        self.assertEqual([(d["from"], d["to"], d["message"]) for d in decisions],
                         [("undecided", "dismissed", dismiss), ("dismissed", "accepted", accept)])

    def test_a_reversal_needs_the_operators_newer_message(self):
        run = self.posted()
        ref = f"{run['id']}/1"
        later, dismiss = self.operator("Make a task for it", 2), self.operator("Dismiss the first one", 3)
        improve.resolve(self.project, ref, "dismissed", dismiss, "not now", now=NOW)
        for message in (dismiss, later):
            with self.assertRaisesRegex(ValueError, "a change needs the operator's newer message"):
                improve.resolve(self.project, ref, "accepted", message, "owner rules only", now=NOW)
        self.assertEqual(improve.resolve(self.project, ref, "accepted", self.operator("Actually do it", 4), "all",
                                         now=NOW)["state"], "accepted")

    def test_answers_need_a_shown_number_and_a_later_operator_message(self):
        run = self.posted()
        later = self.operator("Make a task for both", 1)
        with self.assertRaisesRegex(ValueError, "was not shown"):
            improve.resolve(self.project, f"{run['id']}/2", "accepted", later, "both", now=NOW)
        earlier = self.operator("Earlier words", -1)
        with self.assertRaisesRegex(ValueError, "after the suggestions"):
            improve.resolve(self.project, f"{run['id']}/1", "accepted", earlier, "x", now=NOW)
        with self.assertRaisesRegex(ValueError, "after the suggestions"):
            improve.resolve(self.project, f"{run['id']}/1", "accepted", "0123456789ab", "x", now=NOW)

    def test_a_week_without_a_decision_lapses_and_only_acceptance_remains(self):
        run = self.posted()
        week = NOW + timedelta(days=7)
        self.assertEqual(improve.status(self.project, now=week)["runs"][0]["suggestions"][0]["state"], "not decided")
        self.assertEqual(improve.status(self.project, now=week)["runs"][0]["suggestions"][1]["state"],
                         "dropped by L3: being handled")
        with self.assertRaisesRegex(ValueError, "is not decided; it cannot become dismissed"):
            improve.resolve(self.project, f"{run['id']}/1", "dismissed", self.operator("Dismiss", 200), "x", now=week)
        self.assertEqual(improve.resolve(self.project, f"{run['id']}/1", "accepted", self.operator("Make it", 201),
                                         "all of it", now=week)["state"], "accepted")

    def test_an_accepted_suggestion_makes_exactly_one_task(self):
        run = self.posted()
        ref = f"{run['id']}/1"
        with self.assertRaisesRegex(T.TransitionError, "is not accepted"):
            T.new(self.project, "Cite answered questions", "Brief.", improvement=ref)
        improve.resolve(self.project, ref, "accepted", self.operator("Make a task", 1), "owner rules only", now=NOW)
        task = T.new(self.project, "Cite answered questions", "Brief.", improvement=ref)
        self.assertEqual(task["improvement"], ref)
        with self.assertRaisesRegex(T.TransitionError, f"already has task {task['slug']}"):
            T.new(self.project, "Cite answered questions again", "Brief.", improvement=ref)
        self.assertEqual(improve.status(self.project)["runs"][0]["suggestions"][0]["task"], task["slug"])

    def test_later_reviews_see_earlier_outcomes(self):
        run = self.posted()
        improve.resolve(self.project, f"{run['id']}/1", "dismissed", self.operator("Dismiss it", 1), "not wanted", now=NOW)
        self.assertEqual(improve.earlier(self.project, NOW),
                         [{"title": "Owners re-ask settled merge questions", "state": "dismissed"},
                          {"title": "Docks list rebuilt by hand", "state": "dropped by L3: being handled"}])


class TestCard(RunCase):
    """The web card and Needs you: presses with request ids, the task turn, Try again and what each view shows."""
    def tasks(self) -> list[dict]:
        return [r for r in l3._queue_rows(l3.queue_path(self.project)) if r["trigger"] == "improvement-task"]

    def test_the_card_shows_kept_suggestions_with_their_places_count_and_details(self):
        run = self.posted()
        [card] = improve.view(self.project, now=NOW)
        self.assertEqual((card["id"], card["state"], card["tasks"]), (run["id"], "posted", 6))
        [shown] = card["suggestions"]
        self.assertEqual((shown["ref"], shown["target"], shown["state"], shown["task"], shown["opening"]),
                         (f"{run['id']}/1", "How agents work", "undecided", None, False))
        self.assertTrue(shown["seen"] and all(place["title"].startswith("Fictional dock task") for place in shown["seen"]))
        self.assertEqual(shown["count"], "Your messages after a report in 6 of 6 tasks")
        details = dict(card["details"])
        self.assertTrue(details["Read"].startswith("6 tasks that finished "))
        self.assertEqual((details["L3 dropped"], details["Model"], details["Usage"]),
                         ("Suggestion 2: being handled", "Claude Sonnet 5.5", "900 of its 86,000-token limit"))
        self.assertEqual(improve.waiting(self.project, now=NOW), [card])

    def test_running_and_ending_lines(self):
        self.load()
        self.answer = {"suggestions": [suggestion(anchors=["CITE"])]}
        receipt = improve.start(self.project, self.operator("Get improvement suggestions"), now=NOW)
        self.assertRegex(improve.view(self.project)[0]["line"], r"^Improvement suggestions · reading 6 tasks from ")
        improve.execute(self.project, receipt["run"])
        self.assertEqual(improve.view(self.project)[0]["line"], "Improvement suggestions · L3 is checking 1")
        request = "0f" * 16
        with self.assertRaisesRegex(ValueError, "That review already ended"):
            improve.stop(self.project, request, "0" * 12)  # a press from the line of an earlier review
        self.assertEqual(improve.stop(self.project, request, receipt["run"])["state"], "stopped")
        self.assertEqual(improve.stop(self.project, request, receipt["run"])["state"], "stopped")
        self.assertEqual((self.queued(), [r["text"] for r in self.chat()]),
                         ([], ["Improvement suggestions stopped by you."]))
        self.assertNotIn("line", improve.view(self.project)[0])
        self.assertEqual(improve.waiting(self.project), [])

    def test_make_a_task_queues_one_turn_and_the_card_follows_it(self):
        run = self.posted()
        ref = f"{run['id']}/1"
        accepted = improve.decide(self.project, ref, "accept", "a" * 32, now=NOW)
        self.assertEqual(improve.decide(self.project, ref, "accept", "a" * 32, now=NOW), accepted)
        [turn] = self.tasks()
        self.assertIn(f"--improvement {ref}", turn["text"])
        self.assertNotIn("Owners re-ask", turn["text"])  # the reviewer's wording never enters the conversation
        [card] = improve.view(self.project, now=NOW)
        self.assertEqual((card["suggestions"][0]["state"], card["suggestions"][0]["opening"]), ("accepted", True))
        self.assertEqual(len(improve.waiting(self.project, now=NOW)), 1)  # until its task exists
        with self.assertRaisesRegex(ValueError, "L3 is still opening its task"):
            improve.retry(self.project, ref, "b" * 32, now=NOW)
        with self.assertRaisesRegex(ValueError, "This suggestion is accepted now"):
            improve.decide(self.project, ref, "dismiss", "c" * 32, now=NOW)
        # L3's turn ended without a task: Try again queues the same single turn once.
        l3.unqueue_locked(self.project, turn["id"])
        self.assertFalse(improve.view(self.project, now=NOW)[0]["suggestions"][0]["opening"])
        improve.retry(self.project, ref, "d" * 32, now=NOW)
        improve.retry(self.project, ref, "d" * 32, now=NOW)
        self.assertEqual(len(self.tasks()), 1)
        task = T.new(self.project, "Cite answered questions", "Brief.", improvement=ref)
        self.assertEqual(improve.view(self.project, now=NOW)[0]["suggestions"][0]["task"], task["slug"])
        self.assertEqual(improve.waiting(self.project, now=NOW), [])
        with self.assertRaisesRegex(ValueError, "already has its task"):
            improve.retry(self.project, ref, "e" * 32, now=NOW)

    def test_two_try_again_presses_at_once_queue_one_turn(self):
        run = self.posted()
        ref = f"{run['id']}/1"
        improve.decide(self.project, ref, "accept", "a" * 32, now=NOW)
        [turn] = self.tasks()
        l3.unqueue_locked(self.project, turn["id"])
        pending, raced = l3.pending, []

        def racing(project, row_id):
            # The other press runs between this press's read and its lock.
            if not raced:
                raced.append(row_id)
                improve.retry(project, ref, "b" * 32, now=NOW)
            return pending(project, row_id)
        self.patch(l3, "pending", racing)
        with self.assertRaisesRegex(ValueError, "L3 is still opening its task"):
            improve.retry(self.project, ref, "c" * 32, now=NOW)
        self.assertEqual(len(self.tasks()), 1)

    def test_a_request_id_makes_one_press_across_runs_and_actions(self):
        self.posted()
        record = improve._load(self.project)
        first = record["runs"][-1]
        second = {**first, "id": "1" * 12, "decisions": []}
        record["runs"].append(second)
        improve._save(self.project, record)
        improve.decide(self.project, f"{first['id']}/1", "dismiss", "a" * 32, now=NOW)
        for again in (lambda: improve.decide(self.project, f"{second['id']}/1", "dismiss", "a" * 32, now=NOW),
                      lambda: improve.decide(self.project, f"{first['id']}/1", "undo", "a" * 32, now=NOW),
                      lambda: improve.retry(self.project, f"{first['id']}/1", "a" * 32, now=NOW)):
            with self.assertRaisesRegex(ValueError, "already saved for another change"):
                again()
        self.assertEqual(improve.view(self.project, now=NOW)[1]["suggestions"][0]["state"], "undecided")

    def test_the_task_turn_running_counts_as_opening(self):
        run = self.posted()
        ref = f"{run['id']}/1"
        improve.decide(self.project, ref, "accept", "a" * 32, now=NOW)
        [turn] = self.tasks()
        l3.unqueue_locked(self.project, turn["id"])
        l3._active[self.project] = {"id": "0badc0ffee00", "trigger": "improvement-task", "queue_ids": [turn["id"]]}
        self.addCleanup(l3._active.pop, self.project, None)
        self.assertTrue(improve.view(self.project, now=NOW)[0]["suggestions"][0]["opening"])
        typed = self.operator("Make a task for the second", 1)
        self.assertFalse(l3.pending(self.project, typed))
        l3._active[self.project] = {"id": typed, "trigger": "chat"}
        self.assertTrue(l3.pending(self.project, typed))

    def test_dismiss_and_undo_until_it_lapses(self):
        run = self.posted()
        ref = f"{run['id']}/1"
        improve.decide(self.project, ref, "dismiss", "a" * 32, now=NOW)
        [card] = improve.view(self.project, now=NOW)
        self.assertEqual((card["suggestions"][0]["state"], card["suggestions"][0]["undo"]), ("dismissed", True))
        self.assertEqual(improve.waiting(self.project, now=NOW), [])
        with self.assertRaisesRegex(ValueError, "already saved for another change"):
            improve.decide(self.project, ref, "undo", "a" * 32, now=NOW)
        self.assertEqual(improve.decide(self.project, ref, "undo", "b" * 32, now=NOW)["state"], "undecided")
        self.assertEqual(len(improve.waiting(self.project, now=NOW)), 1)
        improve.decide(self.project, ref, "dismiss", "c" * 32, now=NOW)
        week = NOW + timedelta(days=7)
        self.assertFalse(improve.view(self.project, now=week)[0]["suggestions"][0]["undo"])
        with self.assertRaisesRegex(ValueError, "This suggestion is dismissed now"):
            improve.decide(self.project, ref, "undo", "d" * 32, now=week)
        # A typed answer still accepts it, and an Undo a week later reads as not decided.
        improve.decide(self.project, ref, "undo", "e" * 32, now=NOW + timedelta(days=6))
        self.assertEqual(improve.view(self.project, now=week)[0]["suggestions"][0]["state"], "not decided")
        self.assertEqual(improve.waiting(self.project, now=week), [])
        with self.assertRaisesRegex(ValueError, "This suggestion is not decided now"):
            improve.decide(self.project, ref, "accept", "f" * 32, now=week)
        self.assertEqual(improve.resolve(self.project, ref, "accepted", self.operator("Make it after all", 200),
                                         "all of it", now=week)["state"], "accepted")

    def test_presses_reach_the_record_through_the_web(self):
        run = self.posted()
        drains = self.patch(server, "request_l3_drain")
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        def press(status=200, **body):
            connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
            connection.request("POST", "/api/improve", body=json.dumps({"project": self.project, **body}),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            self.assertEqual(response.status, status, payload)
            return payload

        ref, request = f"{run['id']}/1", "6f1c" * 8
        self.assertEqual(press(action="accept", ref=ref, request_id=request)["state"], "accepted")
        drains.assert_called_once_with(self.project)
        self.assertEqual(press(action="accept", ref=ref, request_id=request)["state"], "accepted")
        self.assertEqual(len(self.tasks()), 1)
        self.assertIn("is accepted now", press(409, action="dismiss", ref=ref, request_id="7" * 32)["error"])
        self.assertIn("Unsupported", press(409, action="delete", ref=ref, request_id="7" * 32)["error"])
        self.assertIn("badly formed", press(409, action="dismiss", ref=ref, request_id="no")["error"])
        self.assertIn("That review already ended", press(409, action="stop", ref=run["id"], request_id="8" * 32)["error"])
        overview = server.overview()
        self.assertEqual([(row["project"], row["id"]) for row in overview["suggestions"]], [(self.project, run["id"])])
        self.assertEqual(server.project_view(self.project)["improvements"][0]["id"], run["id"])


class TestCoordinatorVerbs(RunCase):
    def verb(self, *args: str) -> dict:
        result = server._l3_verb_request(self.project, {"kind": "alt", "args": ["improve", *args]})
        return json.loads(result["stdout"])

    def test_starting_needs_the_chat_turn_of_the_operators_own_message(self):
        self.load()
        with self.assertRaisesRegex(ValueError, "while L3 answers the operator's chat message"):
            self.verb("run")
        l3._active[self.project] = {"id": "0badc0ffee00", "trigger": "block", "started_at": S.now()}
        self.addCleanup(l3._active.pop, self.project, None)
        with self.assertRaisesRegex(ValueError, "while L3 answers the operator's chat message"):
            self.verb("run")
        l3._active[self.project] = {"id": "0badc0ffee00", "trigger": "chat", "started_at": S.now()}
        with self.assertRaisesRegex(ValueError, "operator's own chat message"):
            self.verb("run")
        message = self.operator("Get improvement suggestions")
        l3._active[self.project]["id"] = message
        with mock.patch.object(server, "spawn") as spawn:
            receipt = self.verb("run")
            self.assertEqual(receipt["state"], "reading")
            spawn.assert_called_once_with(f"improve:{self.project}", improve.execute, self.project, receipt["run"])
            self.assertTrue(self.verb("run")["replayed"])
            spawn.assert_called_once()
        self.assertEqual(self.verb("status")["runs"][0]["run"], receipt["run"])

    def test_posting_needs_no_operator_message_and_the_cli_reads_status_only(self):
        run = self.started(suggestions=[suggestion(anchors=["CITE"])])
        self.assertEqual(self.verb("status", "--run", run["id"])["runs"][0]["candidates"][0]["n"], 1)
        self.assertEqual(self.verb("post", run["id"], "--keep", "1")["state"], "posted")
        result = self.alt("improve", "post", run["id"], "--keep", "1", env={"ALTITUDE_PROJECT": self.project})
        self.assertIn("only through the project's coordinator socket", result.stderr)
        self.assertNotEqual(result.returncode, 0)
