"""A mechanically clean report closes without spending an L3 report-landed turn."""
import unittest

from tests.support import AltitudeCase, fyi_rows
from altitude import incidents, l3, server, state as S, tasks as T


class TestCleanClose(AltitudeCase):
    def _report(self) -> dict:
        return {
            "landed": {
                "prs": [{"number": 47, "title": "Clean close", "merged": True, "merge_sha": "abc123"}],
                "main_runs": [{"id": "run-47", "conclusion": "success"}],
                "deploy": "healthy",
            },
            "review": [
                {"tag": "fixed", "severity": "major", "summary": "fixed", "disposition": "fixed", "reason": "done"},
                {"tag": "dismissed", "severity": "minor", "summary": "dismissed", "disposition": "dismissed", "reason": "not relevant"},
            ],
            "deviations": [],
            "decisions": [],
            "fyi": [],
            "blocked": "",
            "follow_ups": [],
            "spend": {"turns": 8, "subagent_launches": 2, "retries": 0, "model_tiers": "coding", "reverts": 0},
            "roadmap_complete": True,
        }

    def _task_and_verdict(self, slug: str, *, change=None, hold_merge=False,
                          live_state: str | None = None) -> tuple[dict, dict]:
        report = self._report()
        verdict = {"verdict": "ok", "problems": [], "signals": [], "spend": {}, "prs": [47]}
        if change:
            change(report, verdict)
        verdict["report"] = {key: report[key] for key in ("blocked", "decisions", "fyi", "follow_ups", "deviations")}
        directory = S.task_dir(self.project, slug)
        directory.mkdir(parents=True, exist_ok=True)
        S.write_json(directory / "report.json", report)
        task = {"slug": slug, "title": slug, "state": "reported", "created": S.now(),
                "updated": S.now(), "verified": verdict, "l3_handled": None, "spend": {}, "prs": [47]}
        if hold_merge:
            task["hold_merge"] = "always-list: release"
        S.save_task(self.project, task)
        if live_state:
            live = dict(task)
            live["state"] = live_state
            S.save_task(self.project, live)
        return task, verdict

    def _run(self, task: dict, verdict: dict) -> tuple[list, list]:
        turns: list = []
        logs: list = []
        self.patch(l3, "turn", new=lambda project, header, trigger: turns.append((project, header, trigger)) or {})
        self.patch(server, "log", new=logs.append)
        server.report_turn(self.project, task, verdict)
        return turns, logs

    def _faults(self) -> list:
        faults: list = []
        self.patch(incidents, "system_fault", new=lambda *args, **kwargs: faults.append((args, kwargs)))
        return faults

    def test_clean_report_closes_without_an_l3_turn_and_posts_one_fyi(self):
        task, verdict = self._task_and_verdict("clean")
        before = len(fyi_rows(self.project))

        turns, logs = self._run(task, verdict)

        self.assertEqual(turns, [])
        closed = S.load_task(self.project, "clean")
        self.assertEqual(closed["state"], "done")
        self.assertIsNotNone(closed["l3_handled"])
        items = fyi_rows(self.project)[before:]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["by"], "altd")
        text = items[0]["text"]
        for expected in ("closed by altd without an L3 turn", "verifier verdict ok",
                         "hold_merge unset", "PRs merged: PR #47 (Clean close)",
                         "main runs: run-47: success", "deploy: healthy",
                         "no decisions, blocked items, FYIs, follow-ups, or post-mortem signals"):
            self.assertIn(expected, text)
        digest = (S.task_dir(self.project, "clean") / "digest.md").read_text()
        for expected in ("PR #47 (Clean close)", "run-47: success", "Deploy: healthy",
                         "Review findings: 1 fixed, 1 dismissed"):
            self.assertIn(expected, digest)
        self.assertTrue(any("clean report closed by altd" in line for line in logs))

    def test_each_nonclean_signal_keeps_the_existing_l3_turn(self):
        cases = {
            "problem": (lambda report, verdict: verdict["problems"].append("contradiction"), {}),
            "decisions": (lambda report, verdict: report["decisions"].append({"question": "choose", "options": ["a"]}), {}),
            "blocked": (lambda report, verdict: report.update(blocked="waiting"), {}),
            "fyi": (lambda report, verdict: report["fyi"].append("route this"), {}),
            "signals": (lambda report, verdict: verdict["signals"].append("one deviation"), {}),
            "deploy": (lambda report, verdict: report["landed"].update(deploy="failed: unhealthy"), {}),
            "deploy-missing": (lambda report, verdict: report["landed"].pop("deploy"), {}),
            "deploy-empty": (lambda report, verdict: report["landed"].update(deploy=""), {}),
            "hold-merge": (None, {"hold_merge": True}),
            "unmerged-pr": (lambda report, verdict: report["landed"]["prs"][0].update(merged=False), {}),
            "follow-ups": (lambda report, verdict: report["follow_ups"].append("fix the flaky test"), {}),
            "state-blocked": (None, {"live_state": "blocked"}),
        }
        for name, (change, task_options) in cases.items():
            with self.subTest(name=name):
                task, verdict = self._task_and_verdict(f"dirty-{name}", change=change, **task_options)
                expected_state = S.load_task(self.project, task["slug"])["state"]
                before = len(fyi_rows(self.project))

                turns, logs = self._run(task, verdict)

                self.assertEqual(len(turns), 1)
                self.assertEqual(turns[0][0], self.project)
                self.assertEqual(turns[0][2], "report-landed")
                self.assertEqual(S.load_task(self.project, task["slug"])["state"], expected_state)
                self.assertEqual(len(fyi_rows(self.project)), before)
                self.assertFalse(any("clean report closed by altd" in line for line in logs))

    def test_unfinished_l3_report_turn_remains_retryable(self):
        task, verdict = self._task_and_verdict(
            "route-hold", change=lambda report, _verdict: report["fyi"].append("needs coordinator"))
        logs: list = []
        self.patch(l3, "turn", new=lambda *_args, **_kwargs: {
            "completed": False, "error": "engine hold: no engine available", "limited": None,
        })
        self.patch(server, "log", new=logs.append)

        server.report_turn(self.project, task, verdict)

        self.assertIsNone(S.load_task(self.project, task["slug"])["l3_handled"])
        self.assertTrue(any("report turn unfinished" in line for line in logs))

    def test_malformed_report_shapes_fail_closed_and_not_applicable_closes(self):
        cases = (
            ("top-level-list", lambda report: [], False, False),
            ("landed-string", lambda report: {**report, "landed": "merged"}, False, False),
            ("prs-dict", lambda report: {
                **report, "landed": {**report["landed"], "prs": {"47": {"merged": True}}}}, False, False),
            ("deploy-number", lambda report: {
                **report, "landed": {**report["landed"], "deploy": 47}}, False, False),
            ("corrupt-json", None, False, True),
            ("deploy-not-applicable", lambda report: {
                **report, "landed": {**report["landed"], "deploy": "not-applicable"}}, True, False),
        )
        faults = self._faults()
        for name, build_report, closes, corrupt in cases:
            with self.subTest(name=name):
                task, verdict = self._task_and_verdict(f"shape-{name}")
                report_path = S.task_dir(self.project, task["slug"]) / "report.json"
                if corrupt:
                    S.atomic_write(report_path, "{not json\n")
                else:
                    S.write_json(report_path, build_report(self._report()))

                turns, _ = self._run(task, verdict)

                self.assertEqual(len(turns), 0 if closes else 1)
                self.assertEqual(S.load_task(self.project, task["slug"])["state"], "done" if closes else "reported")
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0][0][0], "report-json")
        self.assertEqual(faults[0][1], {"project": self.project, "task": "shape-corrupt-json",
                                      "expected_owner": T.report_owner(S.load_task(self.project, "shape-corrupt-json"))})

    def test_main_runs_must_be_present_well_shaped_and_successful(self):
        cases = (
            ("missing", []),
            ("failed", [{"id": "run-red", "conclusion": "failure"}]),
            ("missing-id", [{"conclusion": "success"}]),
            ("missing-conclusion", [{"id": "run-unknown"}]),
        )
        for name, runs in cases:
            with self.subTest(name=name):
                task, verdict = self._task_and_verdict(
                    f"main-run-{name}", change=lambda report, _verdict: report["landed"].update(main_runs=runs))

                turns, _ = self._run(task, verdict)

                self.assertEqual(len(turns), 1)
                self.assertEqual(turns[0][2], "report-landed")
                self.assertEqual(S.load_task(self.project, task["slug"])["state"], "reported")

    def test_missing_or_corrupt_live_status_falls_through_without_escaping(self):
        faults = self._faults()
        for name, corrupt in (("missing", False), ("corrupt", True)):
            with self.subTest(name=name):
                task, verdict = self._task_and_verdict(f"live-status-{name}")
                status_path = S.status_path(self.project, task["slug"])
                if corrupt:
                    S.atomic_write(status_path, "{not json\n")
                else:
                    status_path.unlink()
                try:
                    turns, logs = self._run(task, verdict)
                finally:
                    S.save_task(self.project, task)

                self.assertEqual(len(turns), 1)
                self.assertEqual(turns[0][2], "report-landed")
                self.assertTrue(any("report turn unfinished" in line for line in logs))
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0][0][0], "task-json")
        self.assertEqual(faults[0][1], {"project": self.project, "task": "live-status-corrupt"})

    def test_live_hold_merge_value_controls_clean_close_and_its_fyi(self):
        held_task, held_verdict = self._task_and_verdict("live-hold")
        live = S.load_task(self.project, held_task["slug"])
        live["hold_merge"] = "always-list: live hold"
        S.save_task(self.project, live)
        before = len(fyi_rows(self.project))

        held_turns, _ = self._run(held_task, held_verdict)

        self.assertEqual(len(held_turns), 1)
        self.assertEqual(S.load_task(self.project, held_task["slug"])["state"], "reported")
        self.assertEqual(len(fyi_rows(self.project)), before)

        stale_task, stale_verdict = self._task_and_verdict("stale-caller-hold")
        stale_task["hold_merge"] = "stale caller snapshot"
        before = len(fyi_rows(self.project))

        stale_turns, _ = self._run(stale_task, stale_verdict)

        self.assertEqual(stale_turns, [])
        self.assertEqual(S.load_task(self.project, stale_task["slug"])["state"], "done")
        items = fyi_rows(self.project)[before:]
        self.assertEqual(len(items), 1)
        self.assertIn("hold_merge unset", items[0]["text"])
        self.assertNotIn("stale caller snapshot", items[0]["text"])

    def test_blocked_transition_during_done_falls_through_to_l3(self):
        """A block that lands inside the close window leaves the task blocked and hands L3 the turn."""
        task, verdict = self._task_and_verdict("blocked-during-done")
        before = len(fyi_rows(self.project))
        original_done = T.done

        def block_then_done(project, slug, **kwargs):
            T.block(project, slug, "blocked in the post-lock window")
            return original_done(project, slug, **kwargs)

        self.patch(T, "done", new=block_then_done)

        turns, logs = self._run(task, verdict)

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0][2], "report-landed")
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "blocked")
        self.assertEqual(len(fyi_rows(self.project)), before)
        self.assertFalse(any("clean report closed by altd" in line for line in logs))

    def test_unknown_review_dispositions_fail_closed(self):
        for index, disposition in enumerate((None, "accepted", "")):
            with self.subTest(disposition=disposition):
                task, verdict = self._task_and_verdict(
                    f"review-{index}",
                    change=lambda report, _verdict: report["review"][0].update(disposition=disposition))

                turns, _ = self._run(task, verdict)

                self.assertEqual(len(turns), 1)
                self.assertEqual(S.load_task(self.project, task["slug"])["state"], "reported")

    def test_on_disk_report_is_the_only_clean_close_source(self):
        task, verdict = self._task_and_verdict(
            "on-disk-decision", change=lambda report, verified: report["decisions"].append("choose"))
        verdict["report"] = {}

        turns, _ = self._run(task, verdict)

        self.assertEqual(len(turns), 1)
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "reported")


if __name__ == "__main__":
    unittest.main()
