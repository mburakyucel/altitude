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
                {"id": "q2", "revision": 1, "asked": at(3, 2.5), "text": "Merge now?", "asked_again": True}]),
            task("answered", 4, messages=[message("m1", 4, 1.2, "operator", "Fees: none.")], questions=[
                {"id": "q1", "revision": 1, "asked": at(4, 1), "text": "Fees?"},
                {"id": "q1", "revision": 2, "asked": at(4, 1.5), "text": "Which city field?"}]),
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
                         {"id": "e9", "at": at(5, 1.8), "kind": "report-superseded"},
                         {"id": "e10", "at": at(5, 1.9), "kind": "escalated"},
                         {"id": "e11", "at": at(5, 2), "kind": "fyi", "text": "noted"}],
                 reviews=[{"id": "r1", "subject": "changes", "requested": at(5, 2.1), "findings": []},
                          {"id": "r2", "subject": "proposal", "requested": at(5, 2.2), "findings": []},
                          {"id": "r3", "subject": "changes", "requested": at(5, 2.3), "findings": []}],
                 report_review=[{"summary": "kept", "disposition": "fixed"},
                                {"summary": "left", "disposition": "open"}]))
        place = lambda name: anchors[name]
        # A re-park after the operator replied is not re-asking without their input.
        self.assertEqual(found["reasked"], {"reasked": [place("reasked:q1@2")]})
        self.assertEqual(found["answered"], {})  # the operator answered between the two revisions
        busy = found["busy"]
        self.assertEqual(busy["refused"], [place("busy:e1"), place("busy:e2")])
        self.assertEqual(busy["retries"], [place("busy:e3"), place("busy:e5")])
        self.assertEqual(busy["failures"], [place("busy:e7"), place("busy:e8")])
        self.assertEqual(busy["rewritten"], [place("busy:e9")])
        self.assertEqual(busy["escalations"], [place("busy:e10")])
        self.assertEqual(busy["after_report"], [place("busy:m2")])
        self.assertEqual(len(busy["review_rounds"]), 1)  # a second changes review; a proposal review is its own first
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

    def test_counted_events_show_what_they_recorded(self):
        project, _ = self.load([task("rewritten", 2, events=[
            {"id": "e1", "at": at(2, 1), "kind": "ci-recheck", "reason": "Lint job hit an unpinned rule"},
            {"id": "e2", "at": at(2, 2), "kind": "report-superseded", "unmerged": [143]},
            {"id": "e3", "at": at(2, 3), "kind": "escalated"}])])
        text = improve.prompt(project, end=NOW)["text"]
        self.assertRegex(text, r"- retries \[A\d+\]: Lint job hit an unpinned rule\n")
        self.assertRegex(text, r"- rewritten \[A\d+\]: the replaced report named unmerged PR #143\n")
        self.assertNotRegex(text, r"- escalations \[")

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
                                              self.anchors)["dropped"][0]["why"], "it contains a path or credential")
        for broken in (None, [], {"answer": []}, {"suggestions": "none"}):
            with self.assertRaisesRegex(ValueError, "not usable"):
                improve.validate(broken, self.anchors)


def stream(*events) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


INIT = {"type": "system", "subtype": "init", "tools": ["StructuredOutput"], "mcp_servers": [],
        "model": "claude-sonnet-5-5"}
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
        return self.patch(engines.subprocess, "run", side_effect=run)

    def call(self, prompt="# Project orchard", **kw):
        return engines.improvement_review(prompt, "Instructions", improve.SCHEMA, model=kw.pop("model", self.model),
                                          timeout=kw.pop("timeout", 60))

    def test_a_call_is_tool_free_bounded_and_records_its_usage(self):
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
        self.assertEqual(kw["timeout"], 60)
        self.assertNotIn("--add-dir", cmd)
        self.assertIsNone(result["error"])
        self.assertEqual(result["answer"], ANSWER)
        self.assertEqual((result["tokens"], result["over_limit"], result["cost"]), (9810, False, 0.05))
        self.assertEqual(result["engine_model"], self.model)

    def test_any_tool_server_or_missing_start_record_fails_closed(self):
        for init in ({**INIT, "tools": ["StructuredOutput", "Bash"]}, {**INIT, "mcp_servers": [{"name": "x"}]},
                     {**INIT, "model": None}, None):
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
        with mock.patch.object(engines, "usage_hold", return_value=at(-1)):
            self.assertEqual(self.call()["error"], f"{self.model} is unavailable")
        self.assertEqual(self.calls, [])

    def test_timeouts_limits_and_unusable_answers_end_the_call(self):
        self.scripted(raises=subprocess.TimeoutExpired("claude", 60))
        self.assertEqual(self.call()["error"], "it timed out")
        self.scripted(stream(INIT, {**RESULT, "structured_output": None}))
        self.assertEqual(self.call()["error"], "its answer was not usable")
        record = self.patch(engines, "record_usage_limit")
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
    def test_every_scenario_loads_with_a_consistent_key(self):
        for half in improve_eval.HALVES:
            with self.subTest(half=half):
                self.patch(config, "ROOT", self.tmp / half)
                self.assertEqual(improve_eval.check(half), [])

    def test_matching_is_one_to_one_by_most_shared_planted_places(self):
        patterns = [{"id": "P1", "places": {"a", "b", "c"}}, {"id": "P2", "places": {"c", "d", "e"}}]
        suggestions = [{"n": 1, "anchors": ["a", "c"]}, {"n": 2, "anchors": ["a", "b", "c"]},
                       {"n": 3, "anchors": ["d", "x"]}]
        self.assertEqual(improve_eval.match(suggestions, patterns), {2: "P1"})
        suggestions.append({"n": 4, "anchors": ["c", "d", "e"]})
        self.assertEqual(improve_eval.match(suggestions, patterns), {2: "P1", 4: "P2"})
