"""A project agent run's suggested tasks: the `Suggest:` blocks of its answer made plain and bounded, L3's check before
anything is shown, the deadline that sets an unfinished check aside, the three waiting per agent, the presses and
typed answers on agent refs, the one task each makes and its origin, Needs you and the run page, and what the next
run reads of them. Projects are fictional; the run's engine is scripted at the engine seam."""
from datetime import timedelta

from tests.test_project_agents import NOW, AgentCase
from altitude import improve, l3, project_agents as A, server, suggestions, tasks as T

FOUND = """Found: the uploader retries forever.

The retry loop in uploader.py has no limit.

Suggest: Add a test for the uploader's retry limit
A case beside the other retry tests in tests/test_uploader.py that
checks the limit of 5, so the next change to it fails a test.

Suggest: Cap the uploader's retries at 5
Stop after five tries so a dead host fails fast.

Checked with the fixture host."""


class SuggestionCase(AgentCase):
    def setUp(self):
        super().setUp()
        self.launching()
        self.id = self.agent(name="Review each merge", tell="never")

    def finished(self, answer=FOUND, at=NOW) -> dict:
        self.change("run", agent=self.id, now=at)
        A.start_next(self.project, at)
        self.ended(answer)
        A.reconcile(self.project, at + timedelta(minutes=5))
        return self.runs()[-1]

    def checks(self) -> list[dict]:
        return [r for r in l3._queue_rows(l3.queue_path(self.project)) if r["trigger"] == "improvement-check"]

    def task_turns(self) -> list[dict]:
        return [r for r in l3._queue_rows(l3.queue_path(self.project)) if r["trigger"] == "improvement-task"]

    def posted(self, keep=(1,), drop=((2, "already covered by #214"),), at=NOW) -> dict:
        run = self.finished(at=at)
        suggestions.post(self.project, f"agent:{self.id}/{run['n']}", list(keep), list(drop),
                         now=at + timedelta(minutes=20))
        return self.runs()[-1]

    def card(self, n=1, now=NOW + timedelta(hours=1)) -> dict:
        return A.run_view(self.project, self.id, n, now=now)["suggested"]

    def operator(self, text: str, hours: float = 1) -> str:
        turn = f"{abs(hash((text, hours))) % 16**12:012x}"
        l3.chat_log(self.project, "user", text, trigger="chat", turn_id=turn, at=(NOW + timedelta(hours=hours)).isoformat())
        return turn


class TestAnswers(SuggestionCase):
    def test_suggest_blocks_leave_the_report_and_become_plain_suggestions(self):
        state, summary, text, found = A.report(FOUND)
        self.assertEqual((state, summary), ("found", "the uploader retries forever."))
        self.assertEqual(text, "The retry loop in uploader.py has no limit.\n\nChecked with the fixture host.")
        self.assertEqual(found, [
            {"title": "Add a test for the uploader's retry limit",
             "explanation": "A case beside the other retry tests in tests/test_uploader.py that checks the limit of 5, "
                            "so the next change to it fails a test."},
            {"title": "Cap the uploader's retries at 5", "explanation": "Stop after five tries so a dead host fails fast."}])

    def test_suggestions_lose_links_marks_and_credentials_and_are_cut_at_a_word(self):
        _, _, _, [found] = A.report("Found: one.\n\n**Suggest:** ## Read `https://example.test/x` **now**\n"
                                    "Use ghp_abcdefghijklmnop to sign in. " + "word " * 80)
        self.assertEqual(found["title"], "Read example.test/x now")
        self.assertTrue(found["explanation"].startswith("Use [hidden] to sign in."))
        self.assertLessEqual(len(found["explanation"]), improve.EXPLANATION)
        self.assertTrue(found["explanation"].endswith("word…"))
        _, _, _, [long] = A.report("Found: one.\n\nSuggest: " + "title " * 40)
        self.assertLessEqual(len(long["title"]), improve.TITLE)

    def test_a_suggestion_keeps_hashes_and_paths_and_loses_every_link(self):
        sha, path = "0123456789abcdef0123456789abcdef01234567", "tests/fixtures/improvement/develop/brewlog"
        token = "Ab3dEf6hIj9lMn2pQr5tUv8xYz1bCd4fGh7"
        _, _, _, [found] = A.report(f"Found: one.\n\nSuggest: Revert {sha}\nIn {path}, see HTTPS://host.test/a, "
                                    f"www.host.test/b, [the run](host.test/c), mailto:me@host.test and {token}.")
        self.assertEqual(found["title"], f"Revert {sha}")
        self.assertEqual(found["explanation"],
                         f"In {path}, see host.test/a, host.test/b, the run, me@host.test and [hidden].")

    def test_only_a_run_that_found_something_suggests(self):
        for first in ("Nothing: all current.", "Couldn't finish: the host was down."):
            with self.subTest(first=first):
                self.assertEqual(A.report(f"{first}\n\nSuggest: Do a thing\nBecause.")[3], [])

    def test_a_run_keeps_only_three_and_queues_one_check_naming_the_run(self):
        four = FOUND + "".join(f"\n\nSuggest: Extra {i}\nWhy {i}." for i in range(2))
        run = self.finished(four)
        self.assertEqual([s["n"] for s in run["suggestions"]], [1, 2, 3])
        [check] = self.checks()
        self.assertEqual(run["check"], check["id"])
        self.assertIn(f"alt agent show {self.id} --run 1", check["text"])
        self.assertIn(f"alt improve post agent:{self.id}/1", check["text"])
        self.assertNotIn("retry limit", check["text"])  # the run's wording never enters the conversation
        self.assertEqual(self.card()["checking"], 3)
        self.assertEqual(A.waiting(self.project, now=NOW + timedelta(hours=1)), [])
        self.assertEqual(len(A.status(self.project, self.id, 1)["candidates"]), 3)


class TestCheck(SuggestionCase):
    def test_a_post_shows_what_l3_kept_and_folds_what_it_set_aside(self):
        self.posted()
        self.assertEqual(self.checks(), [])
        card = self.card()
        self.assertEqual((card["checking"], card["name"], card["line"], card["unfinished"]),
                         (0, "Review each merge", "Run now by you", False))
        self.assertEqual([(s["ref"], s["state"], s["waits"]) for s in card["suggestions"]],
                         [(f"agent:{self.id}/1/1", "undecided", True)])
        self.assertEqual(card["set_aside"], [{"n": 2, "title": "Cap the uploader's retries at 5",
                                              "reason": "already covered by #214"}])
        self.assertEqual(A.waiting(self.project, now=NOW + timedelta(hours=1)), [card])

    def test_a_post_names_every_number_once_and_repeats_only_itself(self):
        self.finished()
        ref = f"agent:{self.id}/1"
        for keep, drop in (([1, 1], [(2, "x")]), ([1], []), ([1], [(2, "")])):
            with self.subTest(keep=keep, drop=drop):
                with self.assertRaises(ValueError):
                    suggestions.post(self.project, ref, keep, drop, now=NOW + timedelta(minutes=20))
        first = suggestions.post(self.project, ref, [1, 2], [], now=NOW + timedelta(minutes=20))
        self.assertEqual(suggestions.post(self.project, ref, [2, 1], [], now=NOW + timedelta(minutes=30)), first)
        with self.assertRaisesRegex(ValueError, "already posted differently"):
            suggestions.post(self.project, ref, [1], [(2, "no")], now=NOW + timedelta(minutes=30))

    def test_a_check_past_its_deadline_sets_everything_aside(self):
        self.finished()
        A.tick(self.project, NOW + timedelta(hours=2))
        self.assertEqual(len(self.checks()), 1, "the check has two hours from the run's end")
        A.tick(self.project, NOW + timedelta(hours=2, minutes=6))
        self.assertEqual(self.checks(), [])
        card = self.card(now=NOW + timedelta(hours=3))
        self.assertTrue(card["unfinished"])
        self.assertEqual([(s["n"], s["reason"]) for s in card["set_aside"]], [(1, None), (2, None)])
        self.assertEqual(A.waiting(self.project, now=NOW + timedelta(hours=3)), [])
        with self.assertRaisesRegex(ValueError, "already posted differently"):
            suggestions.post(self.project, f"agent:{self.id}/1", [1], [(2, "x")], now=NOW + timedelta(hours=3))


class TestRoom(SuggestionCase):
    def test_suggestions_hold_room_for_a_week_after_their_check_unless_decided(self):
        self.posted()  # one kept, one set aside: one room left
        self.assertEqual(A.room(self.record(), self.id, NOW + timedelta(hours=1)), 1)
        later = NOW + timedelta(hours=3)
        self.assertEqual(len(self.finished(at=later)["suggestions"]), 1)
        self.change("run", agent=self.id, now=later + timedelta(hours=1))
        A.start_next(self.project, later + timedelta(hours=1))
        prompt = A.engines.start_project_agent_run.call_args_list[-1].args[2]
        self.assertIn("already has 3 suggested tasks waiting for the operator, so suggest none", prompt)
        self.ended(FOUND)
        A.reconcile(self.project, later + timedelta(hours=1, minutes=5))
        self.assertNotIn("suggestions", self.runs()[-1])
        self.assertEqual(len(self.checks()), 1, "a run with no room causes no check")
        suggestions.decide(self.project, f"agent:{self.id}/1/1", "dismiss", "a" * 32, now=later)
        self.assertEqual(A.room(self.record(), self.id, later), 1, "a decided suggestion frees its room")
        self.assertEqual(A.room(self.record(), self.id, NOW + timedelta(days=7, hours=4)), 2,
                         "the first run's set-aside one frees its room a week after the check")

    def test_the_prompt_says_the_room_and_what_became_of_earlier_suggestions(self):
        self.posted()
        suggestions.decide(self.project, f"agent:{self.id}/1/1", "accept", "a" * 32, now=NOW + timedelta(hours=1))
        self.change("run", agent=self.id, now=NOW + timedelta(hours=2))
        A.start_next(self.project, NOW + timedelta(hours=2))
        prompt = A.engines.start_project_agent_run.call_args_list[-1].args[2]
        self.assertIn("you may suggest up to 2 tasks for the operator", prompt)
        self.assertIn("- Add a test for the uploader's retry limit (accepted, task not yet made)", prompt)
        self.assertIn("- Cap the uploader's retries at 5 (set aside by L3: already covered by #214)", prompt)


class TestDecisions(SuggestionCase):
    def test_make_a_task_queues_the_agent_turn_and_the_task_shows_its_origin(self):
        self.posted()
        ref = f"agent:{self.id}/1/1"
        suggestions.decide(self.project, ref, "accept", "a" * 32, now=NOW + timedelta(hours=1))
        [turn] = self.task_turns()
        self.assertIn(f"`alt task new --title TITLE --improvement {ref}`; that is the only command", turn["text"])
        self.assertIn("read this project and the public web", turn["text"])
        self.assertNotIn("retry limit", turn["text"])
        [item] = self.card()["suggestions"]
        self.assertEqual((item["state"], item["opening"], item["waits"]), ("accepted", True, True))
        task = T.new(self.project, "Test the retry limit", "Brief.", improvement=ref)
        with self.assertRaisesRegex(T.TransitionError, "already has task"):
            T.new(self.project, "Again", "Brief.", improvement=ref)
        self.assertEqual(self.card()["suggestions"][0]["task"], task["slug"])
        self.assertEqual(A.waiting(self.project, now=NOW + timedelta(hours=1)), [])
        self.assertEqual(suggestions.origin(self.project, ref), "Review each merge")
        [row] = [t for t in server.project_view(self.project)["tasks"] if t["slug"] == task["slug"]]
        self.assertEqual(row["origin"], "Review each merge")

    def test_an_accepted_suggestion_without_its_task_waits_a_week_after_the_press(self):
        self.posted()
        pressed = NOW + timedelta(days=3)
        suggestions.decide(self.project, f"agent:{self.id}/1/1", "accept", "a" * 32, now=pressed)
        self.assertEqual(len(A.waiting(self.project, now=pressed + timedelta(days=6))), 1)
        self.assertEqual(A.waiting(self.project, now=pressed + timedelta(days=7)), [])
        self.assertEqual(self.card(now=pressed + timedelta(days=7))["suggestions"][0]["state"], "accepted")

    def test_dismiss_undo_lapse_and_a_typed_answer_on_an_agent_ref(self):
        self.posted()
        ref = f"agent:{self.id}/1/1"
        with self.assertRaisesRegex(ValueError, "was not shown"):
            suggestions.decide(self.project, f"agent:{self.id}/1/2", "accept", "b" * 32, now=NOW + timedelta(hours=1))
        suggestions.decide(self.project, ref, "dismiss", "c" * 32, now=NOW + timedelta(hours=1))
        self.assertEqual((self.card()["suggestions"][0]["undo"], A.waiting(self.project, now=NOW + timedelta(hours=1))),
                         (True, []))
        suggestions.decide(self.project, ref, "undo", "d" * 32, now=NOW + timedelta(hours=1))
        self.assertEqual(self.card()["suggestions"][0]["state"], "undecided")
        self.assertEqual(self.card(now=NOW + timedelta(days=8))["suggestions"][0]["state"], "not decided")
        said = suggestions.resolve(self.project, ref, "accepted", self.operator("Make a task for the retry-limit one"),
                                   "the retry-limit test", now=NOW + timedelta(hours=2))
        self.assertEqual(said["state"], "accepted")

    def test_refs_for_unknown_runs_are_refused(self):
        for ref in (f"agent:{self.id}/9/1", "agent:0123456789ab/1/1", f"agent:{self.id}/1"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                suggestions.decide(self.project, ref, "accept", "e" * 32, now=NOW)

    def test_a_retired_agents_suggestions_stay_decidable(self):
        self.posted()
        self.change("retire", agent=self.id, now=NOW + timedelta(hours=1))
        self.assertEqual(len(A.waiting(self.project, now=NOW + timedelta(hours=1))), 1)
        suggestions.decide(self.project, f"agent:{self.id}/1/1", "dismiss", "f" * 32, now=NOW + timedelta(hours=1))


class TestNeedsYou(SuggestionCase):
    def test_the_overview_lists_both_kinds_newest_first(self):
        self.posted()
        later = {"kind": "review", "id": "0123456789ab", "posted": (NOW + timedelta(days=1)).isoformat(),
                 "suggestions": [{"waits": True}]}
        self.patch(improve, "waiting", return_value=[later])
        listed = [c for c in server.overview()["suggestions"] if c["project"] == self.project]
        self.assertEqual([c["kind"] for c in listed], ["review", "agent"])
