"""A mechanically clean report closes without spending an L3 report-landed turn."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="altitude-clean-close-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, l3, server, state as S, tasks as T  # noqa: E402

PROJECT = "cleanclose"


def register() -> None:
    """Register this test project under whichever ROOT is live during suite discovery."""
    config.ensure_root()
    config.save_projects({PROJECT: {"name": PROJECT, "path": config.ROOT.as_posix(), "stacks": ["python"]}})


class TestCleanClose(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register()

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

    def _task_and_verdict(self, slug: str, *, cls: str = "S", change=None, hold_merge=False,
                          live_state: str | None = None) -> tuple[dict, dict]:
        report = self._report()
        verdict = {"verdict": "ok", "problems": [], "signals": [], "spend": {}, "prs": [47]}
        if change:
            change(report, verdict)
        verdict["report"] = {key: report[key] for key in ("blocked", "decisions", "fyi", "follow_ups", "deviations")}
        directory = S.task_dir(PROJECT, slug)
        directory.mkdir(parents=True, exist_ok=True)
        S.write_json(directory / "report.json", report)
        task = {"slug": slug, "title": slug, "class": cls, "state": "reported", "created": S.now(),
                "updated": S.now(), "verified": verdict, "l3_handled": None, "spend": {}, "prs": [47]}
        if hold_merge:
            task["hold_merge"] = "always-list: release"
        S.save_task(PROJECT, task)
        if live_state:
            live = dict(task)
            live["state"] = live_state
            S.save_task(PROJECT, live)
        return task, verdict

    def _run(self, task: dict, verdict: dict) -> tuple[list, list]:
        turns = []
        logs = []
        original_turn, original_log = l3.turn, server.log
        l3.turn = lambda project, header, trigger: turns.append((project, header, trigger)) or {}
        server.log = lambda message: logs.append(message)
        try:
            server.report_turn(PROJECT, task, verdict)
        finally:
            l3.turn, server.log = original_turn, original_log
        return turns, logs

    def test_clean_report_closes_without_an_l3_turn_and_posts_one_fyi(self):
        task, verdict = self._task_and_verdict("clean")
        before = len(T.inbox(PROJECT, limit=1000))

        turns, logs = self._run(task, verdict)

        self.assertEqual(turns, [])
        closed = S.load_task(PROJECT, "clean")
        self.assertEqual(closed["state"], "done")
        self.assertIsNotNone(closed["l3_handled"])
        items = T.inbox(PROJECT, limit=1000)[before:]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["by"], "altd")
        text = items[0]["text"]
        for expected in ("closed by altd without an L3 turn", "verifier verdict ok",
                         "task class S", "hold_merge unset", "PRs merged: PR #47 (Clean close)",
                         "main runs: run-47: success", "deploy: healthy",
                         "no decisions, blocked items, FYIs, follow-ups, or post-mortem signals"):
            self.assertIn(expected, text)
        digest = (S.task_dir(PROJECT, "clean") / "digest.md").read_text()
        for expected in ("PR #47 (Clean close)", "run-47: success", "Deploy: healthy",
                         "Review findings: 1 fixed, 1 dismissed"):
            self.assertIn(expected, digest)
        self.assertTrue(any("clean report closed by altd" in line for line in logs))

    def test_each_nonclean_signal_keeps_the_existing_l3_turn(self):
        cases = {
            "problem": ("S", lambda report, verdict: verdict["problems"].append("contradiction"), {}),
            "decisions": ("S", lambda report, verdict: report["decisions"].append({"question": "choose", "options": ["a"]}), {}),
            "blocked": ("S", lambda report, verdict: report.update(blocked="waiting"), {}),
            "fyi": ("S", lambda report, verdict: report["fyi"].append("route this"), {}),
            "signals": ("S", lambda report, verdict: verdict["signals"].append("one deviation"), {}),
            "class-l": ("L", None, {}),
            "deploy": ("S", lambda report, verdict: report["landed"].update(deploy="failed: unhealthy"), {}),
            "deploy-missing": ("S", lambda report, verdict: report["landed"].pop("deploy"), {}),
            "deploy-empty": ("S", lambda report, verdict: report["landed"].update(deploy=""), {}),
            "hold-merge": ("S", None, {"hold_merge": True}),
            "unmerged-pr": ("S", lambda report, verdict: report["landed"]["prs"][0].update(merged=False), {}),
            "follow-ups": ("S", lambda report, verdict: report["follow_ups"].append("fix the flaky test"), {}),
            "state-blocked": ("S", None, {"live_state": "blocked"}),
        }
        for name, (cls, change, task_options) in cases.items():
            with self.subTest(name=name):
                task, verdict = self._task_and_verdict(f"dirty-{name}", cls=cls, change=change, **task_options)
                expected_state = S.load_task(PROJECT, task["slug"])["state"]
                before = len(T.inbox(PROJECT, limit=1000))

                turns, logs = self._run(task, verdict)

                self.assertEqual(len(turns), 1)
                self.assertEqual(turns[0][0], PROJECT)
                self.assertEqual(turns[0][2], "report-landed")
                self.assertEqual(S.load_task(PROJECT, task["slug"])["state"], expected_state)
                self.assertEqual(len(T.inbox(PROJECT, limit=1000)), before)
                self.assertFalse(any("clean report closed by altd" in line for line in logs))

    def test_on_disk_report_is_the_only_clean_close_source(self):
        task, verdict = self._task_and_verdict(
            "on-disk-decision", change=lambda report, verified: report["decisions"].append("choose"))
        verdict["report"] = {}

        turns, _ = self._run(task, verdict)

        self.assertEqual(len(turns), 1)
        self.assertEqual(S.load_task(PROJECT, task["slug"])["state"], "reported")


if __name__ == "__main__":
    unittest.main()
