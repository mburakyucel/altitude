"""The improvement review: exact signals, a bounded prompt with code-placed citations, an untrusted answer, and a
tool-free reviewer call that fails closed. Records are fictional scenarios loaded as ordinary task folders."""
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, improve, improve_eval, state as S

NOW = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)


def at(days: float, hours: float = 0) -> str:
    """A time `days` before NOW, plus `hours`."""
    return (NOW - timedelta(days=days) + timedelta(hours=hours)).isoformat()


def task(slug: str, days: float, **fields) -> dict:
    return {"slug": slug, "title": f"Fictional {slug}", "state": "done", "created": at(days),
            "reported": at(days, 5), "finished": at(days, 6), "verdict": "delivered", "tokens": 1000, **fields}


def message(mid: str, days: float, hours: float, role: str, text: str) -> dict:
    return {"id": mid, "at": at(days, hours), "role": role, "text": text}


class ImprovementCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(config, "ROOT", self.tmp / "home")
        self.patch(config, "RUNTIME_ROOT", (self.tmp / "home").resolve())

    def load(self, tasks: list[dict], **extra) -> tuple[str, dict]:
        scenario = {"project": "orchard", "now": NOW.isoformat(), "tasks": tasks, **extra}
        return "orchard", improve_eval.load(scenario)


class TestSignals(ImprovementCase):
    def signals(self, *tasks) -> dict:
        project, anchors = self.load(list(tasks))
        items, _ = improve.finished(project, NOW, NOW - improve.FIRST_WINDOW)
        return {item["task"]["slug"]: improve.signals(project, item) for item in items}, anchors

    def test_each_signal_counts_exactly_its_predicate(self):
        question = "Which dock list should the export use?"
        found, anchors = self.signals(
            task("reasked", 3, messages=[message("m1", 3, 2, "operator", "The live one.")], questions=[
                {"id": "q1", "revision": 1, "asked": at(3, 1), "text": question,
                 "resolution": {"disposition": "superseded", "at": at(3, 1.5), "by": "l2"}},
                {"id": "q1", "revision": 2, "asked": at(3, 1.5), "text": question,
                 "resolution": {"disposition": "answered", "at": at(3, 2), "by": "operator", "message": "m1"}},
                {"id": "q2", "revision": 1, "asked": at(3, 2.5), "text": "Merge now?"}]),
            task("answered", 4, messages=[message("m1", 4, 1.2, "operator", "Fees: none."),
                                          message("m2", 4, 2.2, "l3", "Use the shared list.")], questions=[
                {"id": "q1", "revision": 1, "asked": at(4, 1), "text": "Fees?"},
                {"id": "q1", "revision": 2, "asked": at(4, 1.5), "text": "Which city field?"},
                {"id": "q2", "revision": 1, "asked": at(4, 2), "audience": "l3", "text": "Which depot list?"},
                {"id": "q2", "revision": 2, "asked": at(4, 2.5), "audience": "l3", "text": "Old exports too?"}]),
            task("busy", 5, messages=[message("m1", 5, 4, "l2", "Delivered."),
                                      message("m2", 5, 5.5, "operator", "Send me a sample?")],
                 events=[{"id": "e1", "at": at(5, 1), "kind": "merge-approval-refused"},
                         {"id": "e2", "at": at(5, 1.1), "kind": "grant-refused"},
                         {"id": "e3", "at": at(5, 1.2), "kind": "ci-recheck"},
                         {"id": "e4", "at": at(5, 1.3), "kind": "delivery", "number": 7},
                         {"id": "e5", "at": at(5, 1.4), "kind": "delivery", "number": 7},
                         {"id": "e6", "at": at(5, 1.5), "kind": "delivery", "number": 8},
                         {"id": "e7", "at": at(5, 1.6), "kind": "fault", "fault": "worker", "reason": "lost"},
                         {"id": "e8", "at": at(5, 1.7), "kind": "resume-failed"},
                         {"id": "e9", "at": at(5, 1.8), "kind": "report-superseded", "contradicted": "PR #8 is open"},
                         {"id": "e12", "at": at(5, 1.85), "kind": "report-superseded"},
                         {"id": "e10", "at": at(5, 1.9), "kind": "escalated"},
                         {"id": "e11", "at": at(5, 2), "kind": "fyi", "text": "noted"}],
                 reviews=[{"id": "r1", "subject": "changes", "requested": at(5, 2.1), "findings": []},
                          {"id": "r2", "subject": "proposal", "requested": at(5, 2.2), "findings": []},
                          {"id": "r3", "subject": "changes", "requested": at(5, 2.3), "findings": []},
                          {"id": "r4", "subject": "changes", "requested": at(5, 2.35), "state": "failed"},
                          {"id": "r5", "subject": "changes", "requested": at(5, 2.4), "additional": True},
                          {"id": "r6", "subject": "changes", "requested": at(5, 2.45), "findings": []}],
                 report_review=[{"summary": "kept", "disposition": "fixed"},
                                {"summary": "left", "disposition": "open"}]))
        place = lambda name: anchors[name]
        self.assertEqual(found["reasked"], {"reasked": [place("reasked:q1@2")]})
        self.assertEqual(found["answered"], {})  # the operator, then L3, replied before each new revision
        busy = found["busy"]
        self.assertEqual(busy["refused"], [place("busy:e1"), place("busy:e2")])
        # Pushes are ordinary work, and so is a report superseded by a follow-up that verification never contradicted.
        self.assertEqual(busy["retries"], [place("busy:e3")])
        self.assertEqual(busy["failures"], [place("busy:e7"), place("busy:e8")])
        self.assertEqual(busy["rewritten"], [place("busy:e9")])
        self.assertEqual(busy["escalations"], [place("busy:e10")])
        self.assertEqual(busy["after_report"], [place("busy:m2")])
        # The third completed changes review: a proposal review, a failed one and a review of new material do not count.
        self.assertEqual(busy["review_rounds"], [place("busy:r6")])
        self.assertEqual(busy["open_findings"], ["busy/report/1"])
        self.assertEqual(set(busy), {"refused", "retries", "failures", "rewritten", "escalations", "after_report",
                                     "review_rounds", "open_findings"})

    def test_window_starts_at_the_cutoff_and_reads_thirty_days_at_most(self):
        project, _ = self.load([task("recent", 2), task("month-old", 25), task("too-old", 35)])
        items, older = improve.finished(project, NOW, NOW - timedelta(days=60))
        self.assertEqual([item["task"]["slug"] for item in items], ["month-old", "recent"])
        self.assertEqual(older, 1)
        with mock.patch.object(improve, "CUTOFF", NOW - timedelta(days=10)):
            items, older = improve.finished(project, NOW, NOW - timedelta(days=60))
        self.assertEqual([item["task"]["slug"] for item in items], ["recent"])
        self.assertEqual(older, 2)


class TestPrompt(ImprovementCase):
    def test_prompt_is_bounded_and_every_citation_is_a_place_the_code_put_there(self):
        long = "word " * 400
        tasks = [task(f"task-{n:02d}", 1 + n * 0.4, title="T" * 300, messages=[
            message(f"m{i}", 1 + n * 0.4, 0.1 * i, "operator" if i % 2 else "l2", f"{i} {long}") for i in range(20)],
            questions=[{"id": f"q{i}", "revision": 1, "asked": at(1 + n * 0.4, 0.05 * i), "text": long}
                       for i in range(6)],
            events=[{"id": f"e{i}", "at": at(1 + n * 0.4, 0.3), "kind": "ci-recheck"} for i in range(4)])
            for n in range(30)]
        chat = [{"id": f"c{i}", "at": at(1, 0.01 * i), "role": "operator" if i % 2 else "l3", "text": long,
                 "turn": f"t{i // 2}"} for i in range(200)]
        project, anchors = self.load(tasks, chat=chat, current=[{"slug": "open-work", "title": "Open work",
                                                                   "state": "running"}],
                                     earlier=[{"title": "Pin the linter", "outcome": "dismissed"}])
        built = improve.prompt(project, end=NOW)
        text = built["text"]
        self.assertLessEqual(len(text.encode()), improve.PROMPT_BYTES)
        self.assertEqual(built["tasks"], 30)
        self.assertGreater(built["left_out"], 0)
        self.assertEqual(built["sheets"] + built["left_out"], 30)
        self.assertIn("# Tasks that did not fit", text)
        self.assertIn("Open work", text)
        self.assertIn('"Pin the linter": dismissed', text)
        for sheet in text.split("\n\n## ")[1:]:
            self.assertLessEqual(len(sheet.split("\n\n#")[0].encode()), improve.SHEET_BYTES + 3)
        self.assertNotIn(long[:improve.QUOTE + 10], text)
        cited = {alias for group in improve.ALIASES.findall(text) for alias in group.split(", ")}
        self.assertEqual(cited, set(built["anchors"]))
        # Places outside the scenario's own ids are the question messages Altitude adds to each conversation.
        extra = set(built["anchors"].values()) - set(anchors.values())
        self.assertTrue(extra and all(re.fullmatch(r"task-\d+/m/q\d-1", a) for a in extra), sorted(extra)[:5])

    def test_a_full_sheet_keeps_the_operators_words_and_whole_lines(self):
        """The 2026-10-14 review read 112 operator task messages and quoted 4: one line per question revision and the
        review findings filled each sheet, its quotes were dropped first and the rest was cut mid-line."""
        messages = [message(f"m{i}", 2, 0.1 * i, "operator" if i % 2 else "l2", f"Reply {i}: " + "detail " * 60)
                    for i in range(24)]
        questions = [{"id": f"q{i // 6}", "revision": i % 6 + 1, "asked": at(2, 0.05 * i),
                      "text": "Which layout should the courier board use? " + "context " * 30} for i in range(18)]
        reviews = [{"id": f"r{r}", "subject": "changes", "requested": at(2, 3 + r),
                    "findings": [{"id": f"F{j}", "title": f"Finding {j} " + "x" * 70} for j in range(10)]}
                   for r in range(3)]
        project, _ = self.load([task("talkative", 2, messages=messages, questions=questions, reviews=reviews)])
        sheet = improve.prompt(project, end=NOW)["text"].split("\n\n## ")[1].split("\n\n# ")[0]
        self.assertLessEqual(len(("## " + sheet).encode()), improve.SHEET_BYTES)
        self.assertGreaterEqual(len(re.findall(r'operator \[A\d+\] \w{3} \d\d \d\d:\d\d: "', sheet)), 2)
        # One line per question, naming every revision: they show which revisions followed a reply.
        self.assertEqual(len(re.findall(r"^- Question \[A\d+(?:, A\d+){5}\] revs 1–6 ", sheet, re.M)), 3)
        self.assertRegex(sheet.splitlines()[-1], r"^- Not shown for room: \d+ operator quotes, 3 reviews$")

    def test_signal_details_share_the_sheet_bound_after_the_operators_words(self):
        """A heavily signalled task's event details once came before any budget, so its sheet could pass the bound
        with every quote dropped; and quotes past the fourth pair were counted as left out for room."""
        kinds = {"ci-recheck": "reason", "resume-failed": "error", "merge-approval-refused": "reason"}
        events = [{"id": f"e{k}{i}", "at": at(2, 0.1 * i), "kind": kind, field: f"{kind} {i}: " + "why " * 40}
                  for k, (kind, field) in enumerate(kinds.items()) for i in range(8)]
        messages = [message(f"m{i}", 2, 0.1 * i, "operator" if i % 2 else "l2", f"Note {i} on the layout.")
                    for i in range(24)]
        project, _ = self.load([task("noisy", 2, messages=messages, events=events)])
        sheet = "## " + improve.prompt(project, end=NOW)["text"].split("\n\n## ")[1].split("\n\n# ")[0]
        self.assertLessEqual(len(sheet.encode()), improve.SHEET_BYTES)
        # The two most telling quotes come before the details; the others of PAIRS take the room left.
        quoted = len(re.findall(r'operator \[A\d+\] \w{3} \d\d \d\d:\d\d: "', sheet))
        self.assertIn(quoted, (2, 3))
        self.assertRegex(sheet, r"\n- retries \[A\d+\]: ci-recheck 0: ")
        self.assertRegex(sheet.splitlines()[-1], rf"^- Not shown for room: {improve.PAIRS - quoted} operator quotes, "
                                                 r"\d+ signal details$")

    def test_counted_events_show_what_they_recorded(self):
        project, _ = self.load([task("rewritten", 2, events=[
            {"id": "e1", "at": at(2, 1), "kind": "ci-recheck", "reason": "Lint job hit an unpinned rule"},
            {"id": "e2", "at": at(2, 2), "kind": "report-superseded", "contradicted": "PR #143 is not merged"},
            {"id": "e3", "at": at(2, 3), "kind": "escalated", "by": "l3", "question": "Pin or float the chart library?"},
            {"id": "e4", "at": at(2, 4), "kind": "escalated", "by": "l3"}],
            messages=[message("m1", 2, 3.1, "l3", "Handed to the operator."),
                      message("m2", 2, 3.5, "operator", "That is the owner's call; pin it.")])])
        text = improve.prompt(project, end=NOW)["text"]
        self.assertRegex(text, r"- retries \[A\d+\]: Lint job hit an unpinned rule\n")
        self.assertRegex(text, r"- rewritten \[A\d+\]: verification found: PR #143 is not merged\n")
        # An escalation shows the question handed on and the answer it got, never its own actor's note.
        self.assertRegex(text, r'- escalations \[A\d+\]: Pin or float the chart library\?; '
                               r'next operator \[A\d+\]: "That is the owner\'s call; pin it."\n')
        self.assertEqual(len(re.findall(r"- escalations \[", text)), 1)

    def test_the_operators_quotes_are_the_messages_that_matter(self):
        """Sheets once quoted a task's earliest four operator messages, mostly answers its question lines already
        link, and left out where the operator stepped in later."""
        messages, questions = [], []
        for i in range(8):  # eight answers to the owner's questions first
            questions.append({"id": f"q{i}", "revision": 1, "asked": at(3, 0.1 * i), "text": f"Choice {i}?",
                              "resolution": {"disposition": "answered", "at": at(3, 0.1 * i + 0.05),
                                             "by": "operator", "message": f"a{i}"}})
            messages.append(message(f"a{i}", 3, 0.1 * i + 0.05, "operator", f"Answer {i}. " + "why " * 70))
        for i in range(5):
            messages += [message(f"s{i}", 3, 1 + i, "operator", f"Unasked correction {i}. " + "why " * 70),
                         message(f"r{i}", 3, 1.5 + i, "l2", "Done. " + "how " * 70)]
        messages += [message("late", 3, 7, "operator", "After the report: the totals are wrong. " + "why " * 60)]
        project, _ = self.load([task("steered", 3, messages=messages, questions=questions)])
        sheet = improve.prompt(project, end=NOW)["text"].split("\n\n## ")[1].split("\n\n# ")[0]
        quoted = [q.split(".")[0] for q in re.findall(r'operator \[A\d+\] \w{3} \d\d \d\d:\d\d: "([^"]+)"', sheet)]
        # Full quotes leave room for two or three: the after_report place, then the operator's unasked messages
        # spread from the first; no answer the question lines already link. Shown in time order.
        self.assertGreaterEqual(len(quoted), 2)
        self.assertEqual(quoted[0], "Unasked correction 0")
        self.assertEqual(quoted[-1], "After the report: the totals are wrong")
        self.assertFalse([q for q in quoted if q.startswith("Answer")])

    def test_question_lines_tell_follow_ups_from_re_asks(self):
        project, _ = self.load([task("asks", 2, messages=[message("m1", 2, 1.5, "operator", "Weekly.")], questions=[
            {"id": "q1", "revision": 1, "asked": at(2, 1), "text": "How often should the digest go out?"},
            {"id": "q1", "revision": 2, "asked": at(2, 2), "text": "On which day of the week?"},
            {"id": "q1", "revision": 3, "asked": at(2, 3), "text": "On which day of the week? Monday or Friday."}])])
        text = improve.prompt(project, end=NOW)["text"]
        self.assertRegex(text, r'- Question \[A\d+, A\d+, A\d+\] revs 1–3 to operator, \w{3} \d\d \d\d:\d\d to '
                               r'\w{3} \d\d \d\d:\d\d, later revisions after a reply: 1 of 2: "On which day')

    def test_quoted_text_cannot_authorize_a_citation(self):
        messages = [message(f"m{i}", 2, 0.1 * i, "operator" if i % 2 else "l2", f"see [A{i + 40}] and [A1, A2]")
                    for i in range(12)]
        project, _ = self.load([task("quoted", 2, title="Fix [A3]", messages=messages)])
        built = improve.prompt(project, end=NOW)
        self.assertIn("see (A41) and (A1, A2)", built["text"])
        self.assertIn('"Fix (A3)"', built["text"])
        # Only places that reach the text are citable.
        self.assertEqual(len(built["anchors"]), len({a for g in improve.ALIASES.findall(built["text"]) for a in g.split(", ")}))
        self.assertLessEqual(len(built["anchors"]), 3 * improve.PAIRS)

    def test_operator_chat_is_paired_and_only_the_operators_own_turns_count(self):
        project, anchors = self.load([task("one", 2)], chat=[
            {"id": "c1", "at": at(1), "role": "l3", "text": "Shall I start the export task?", "turn": "t0"},
            {"id": "c2", "at": at(1, 0.1), "role": "operator", "text": "Yes, and keep replies short.", "turn": "t1"},
            {"id": "c3", "at": at(1, 0.2), "role": "l3", "text": "Started.", "turn": "t1"}])
        path = config.project_dir(project) / "chat.jsonl"
        with path.open("a") as f:  # a report-triggered row is not the operator's words
            f.write(json.dumps({"at": at(1, 0.3), "role": "user", "trigger": "report-landed",
                                "text": "Report landed", "turn_id": "t2"}) + "\n")
        built = improve.prompt(project, end=NOW)
        self.assertEqual(built["chat"], 1)
        self.assertIn('"Yes, and keep replies short."', built["text"])
        self.assertNotIn("Report landed", built["text"])
        self.assertIn(anchors["chat:c2"], built["anchors"].values())


class TestValidate(ImprovementCase):
    anchors = {"A1": "one/m/m1", "A2": "one/m/m2", "A3": "chat/0"}

    def suggestion(self, **fields) -> dict:
        return {"target": "agents", "title": "Owners re-ask answered questions", "explanation": "They do.",
                "where": "The owner persona", "signal": "reasked", "anchors": ["A1", "A2"],
                "uncertainty": "Two tasks only.", **fields}

    def test_untrusted_answers_keep_only_valid_suggestions_with_canonical_places(self):
        result = improve.validate({"suggestions": [
            self.suggestion(anchors=["A1", "A9", "A3", "A1"]),
            self.suggestion(anchors=["A1", "A9"]),
            self.suggestion(target="everything"),
            self.suggestion(where="Edit /home/someone/.config"),
            self.suggestion(signal="invented"),
            self.suggestion(title="x" * 91)]}, self.anchors)
        self.assertEqual([s["anchors"] for s in result["suggestions"]], [["one/m/m1", "chat/0"]])
        self.assertEqual([d["why"] for d in result["dropped"]], [
            "fewer than two citations from its prompt", "no valid target", "more than three suggestions"])
        self.assertEqual(improve.validate({"suggestions": []}, self.anchors), {"suggestions": [], "dropped": []})
        for unsafe in ("token ghp_abcdefghijkl", "see ~/.ssh", "-----BEGIN KEY"):
            self.assertEqual(improve.validate({"suggestions": [self.suggestion(explanation=unsafe)]},
                                              self.anchors)["dropped"][0]["why"], "it contains a link, path or credential")
        for broken in (None, [], {"answer": []}, {"suggestions": "none"}):
            with self.assertRaisesRegex(ValueError, "not usable"):
                improve.validate(broken, self.anchors)


def stream(*events) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


INIT = {"type": "system", "subtype": "init", "tools": ["StructuredOutput"], "mcp_servers": [],
        "model": "claude-sonnet-5-5", "apiKeySource": "none"}
ANSWER = {"suggestions": []}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.05, "structured_output": ANSWER,
          "usage": {"input_tokens": 10, "cache_creation_input_tokens": 9000, "output_tokens": 800}}


class TestReviewerCall(AltitudeCase):
    model = "claude-sonnet-5-5"

    def setUp(self):
        super().setUp()
        self.quiet_engines()
        self.calls = []

    def scripted(self, stdout: str = "", returncode: int = 0, raises=None):
        def run(cmd, **kw):
            self.calls.append((cmd, kw))
            self.assertEqual(list(Path(kw["cwd"]).iterdir()), [])
            if raises:
                raise raises
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")
        return self.patch(engines.common.subprocess, "run", side_effect=run)

    def call(self, prompt="# Project orchard", **kw):
        return engines.improvement_review(prompt, "Instructions", improve.SCHEMA, model=kw.pop("model", self.model),
                                          timeout=kw.pop("timeout", 60))

    def test_a_call_is_tool_free_bounded_and_records_its_usage(self):
        self.setenv("ANTHROPIC_API_KEY", "fictional-key")
        self.setenv("ANTHROPIC_BASE_URL", "https://proxy.invalid")
        self.scripted(stream(INIT, {"type": "assistant", "message": {"model": self.model, "content": []}}, RESULT))
        result = self.call()
        cmd, kw = self.calls[0]
        for flag in ("--safe-mode", "--no-session-persistence", "--strict-mcp-config"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(cmd[cmd.index("--model") + 1], self.model)
        self.assertEqual(json.loads(cmd[cmd.index("--json-schema") + 1]), improve.SCHEMA)
        self.assertEqual(cmd[cmd.index("--system-prompt") + 1], "Instructions")
        self.assertEqual(float(cmd[cmd.index("--max-budget-usd") + 1]), engines.improvement_budget(self.model))
        self.assertEqual(engines.improvement_budget(self.model), 0.268)  # 74,000 in and 12,000 out at its rates
        self.assertEqual(kw["input"], "# Project orchard")
        self.assertEqual(kw["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "12000")
        self.assertFalse([k for k in kw["env"] if k.startswith("ANTHROPIC_")])
        self.assertEqual(kw["timeout"], 60)
        self.assertNotIn("--add-dir", cmd)
        self.assertIsNone(result["error"])
        self.assertEqual(result["answer"], ANSWER)
        self.assertEqual((result["tokens"], result["over_limit"], result["cost"]), (9810, False, 0.05))
        self.assertEqual(result["engine_model"], self.model)

    def test_any_tool_server_api_key_or_missing_start_record_fails_closed(self):
        for init in ({**INIT, "tools": ["StructuredOutput", "Bash"]}, {**INIT, "mcp_servers": [{"name": "x"}]},
                     {**INIT, "model": None}, {**INIT, "apiKeySource": "ANTHROPIC_API_KEY"},
                     {k: v for k, v in INIT.items() if k != "apiKeySource"}, None):
            with self.subTest(init=init):
                self.scripted(stream(*(e for e in (init, RESULT) if e)))
                result = self.call()
                self.assertEqual(result["error"], "it could not run isolated")
                self.assertIsNone(result["answer"])

    def test_refusals_happen_before_any_launch(self):
        self.scripted(raises=AssertionError("launched"))
        self.assertEqual(self.call(model="unpriced-model")["error"], "no usage figures for unpriced-model")
        self.assertEqual(self.call(prompt="x" * engines.IMPROVEMENT_SENT_BYTES)["error"],
                         "its prompt exceeds the usage limit")
        with mock.patch.object(engines.common, "usage_hold", return_value=at(-1)):
            self.assertEqual(self.call()["error"], "Claude Sonnet 5.5 is unavailable")
        self.assertEqual(self.calls, [])

    def test_timeouts_limits_and_unusable_answers_end_the_call(self):
        self.scripted(raises=subprocess.TimeoutExpired("claude", 60))
        self.assertEqual(self.call()["error"], "it timed out")
        self.scripted(stream(INIT, {**RESULT, "structured_output": None}))
        self.assertEqual(self.call()["error"], "its answer was not usable")
        record = self.patch(engines.common, "record_usage_limit")
        self.scripted(stream(INIT, {"type": "assistant", "message": {"model": "<synthetic>", "content": [
            {"type": "text", "text": "You've hit your usage limit · resets 8pm (UTC)"}]}}, RESULT))
        self.assertEqual(self.call()["error"], "it went over its usage limit")
        record.assert_called_once()
        self.scripted(stream(INIT, {**RESULT, "usage": {"input_tokens": 80_000, "output_tokens": 12_000}}))
        self.assertTrue(self.call()["over_limit"])

    def test_review_validates_the_answer_against_its_own_prompt(self):
        built = {"text": "# Project orchard", "anchors": {"A1": "one/m/m1", "A2": "one/m/m2"}}
        answer = {"suggestions": [{"target": "project", "title": "Pin the linter", "explanation": "CI reruns.",
                                   "where": "CI workflow", "signal": "retries", "anchors": ["A1", "A2"],
                                   "uncertainty": "None."}]}
        self.scripted(stream(INIT, {**RESULT, "structured_output": answer}))
        result = improve.review(built, model=self.model)
        self.assertEqual(result["suggestions"][0]["anchors"], ["one/m/m1", "one/m/m2"])
        self.assertNotIn("answer", result["call"])
        self.scripted(stream(INIT, {**RESULT, "structured_output": {"other": 1}}))
        self.assertEqual(improve.review(built, model=self.model)["call"]["error"], "its answer was not usable")


class TestEval(ImprovementCase):
    def test_the_offline_eval_finds_every_signal_kind_and_nothing_on_ordinary_work(self):
        result = improve_eval.offline()
        self.assertEqual(result["problems"], [])
        for kind, row in result["kinds"].items():
            with self.subTest(kind=kind):
                self.assertGreater(row["planted"], 0)
                self.assertEqual((row["fired"], row["reached"], row["ordinary_fired"]), (row["planted"], row["planted"], 0))
        self.assertEqual(result["patterns_reached"], result["patterns"])

    def test_the_offline_eval_reports_a_key_that_does_not_fit_its_records(self):
        name, scenario, key = next(s for s in improve_eval.scenarios("heldback")
                                   if len(s[2].get("patterns", [])) > 1 and s[2]["patterns"][0]["signal"] != "escalations")
        key["patterns"][0]["signal"] = "escalations"
        key["patterns"][1]["anchors"] = ["no-such-task:m1"]
        with mock.patch.object(improve_eval, "scenarios", lambda group: [(name, scenario, key)] if group == "heldback" else []):
            problems = improve_eval.offline()["problems"]
        self.assertEqual(len(problems), 1)
        self.assertIn(f"{name} P1: fewer than two places fire escalations", problems[0])
        self.assertIn(f"{name} P2: unknown anchors ['no-such-task:m1']", problems[0])

    def test_only_the_full_held_back_matrix_can_pass_a_model(self):
        out = self.tmp / "eval"
        names = [name for name, _, _ in improve_eval.scenarios("heldback")]
        answer = {"call": {"error": None, "tokens": 10}, "suggestions": [], "dropped": [], "matched": {}}

        def score(scenarios, repeats, complete=True, sets=("heldback",)):
            self.patch(config, "ROOT", self.tmp / f"home-{len(scenarios)}-{repeats}-{complete}")
            self.patch(config, "RUNTIME_ROOT", (self.tmp / f"home-{len(scenarios)}-{repeats}-{complete}").resolve())
            out.mkdir(exist_ok=True)
            S.write_json(out / "run.json", {"sets": list(sets), "models": ["sonnet"], "repeats": repeats,
                                            "scenarios": scenarios,
                                            "complete": complete, "stopped": None, "spent": 0, "ceiling": 1})
            (out / "calls.jsonl").write_text("".join(json.dumps({**answer, "scenario": n, "model": "sonnet",
                                                                  "repeat": r}) + "\n"
                                                     for n in scenarios for r in range(1, repeats + 1)))
            S.write_json(out / "grading-key.json", {})
            S.write_json(out / "grades.json", {})
            return improve_eval.score(out)

        develop = [name for name, _, _ in improve_eval.scenarios("develop")]
        for partial in (score(names[:1], 3), score(names, 1), score(names, 3, complete=False),
                        score(develop + names, 3, sets=("develop", "heldback"))):
            self.assertFalse(partial["full_matrix"])
            self.assertFalse(partial["models"]["sonnet"]["passes"])
            self.assertIsNone(partial["recommended"])
        self.assertTrue(score(names, 3)["full_matrix"])

    def test_matching_is_one_to_one_by_most_shared_planted_places(self):
        patterns = [{"id": "P1", "places": {"a", "b", "c"}}, {"id": "P2", "places": {"c", "d", "e"}}]
        suggestions = [{"n": 1, "anchors": ["a", "c"]}, {"n": 2, "anchors": ["a", "b", "c"]},
                       {"n": 3, "anchors": ["d", "x"]}]
        self.assertEqual(improve_eval.match(suggestions, patterns), {2: "P1"})
        suggestions.append({"n": 4, "anchors": ["c", "d", "e"]})
        self.assertEqual(improve_eval.match(suggestions, patterns), {2: "P1", 4: "P2"})
