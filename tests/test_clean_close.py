"""A mechanically clean report closes without spending an L3 report-landed turn."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-clean-close-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, improve, l3, server, state as S, tasks as T, verify  # noqa: E402

PROJECT = "cleanclose"
HEAD_SHA = "b" * 40
BASE_SHA = "a" * 40
MERGE_SHA = "c" * 40
RUN_ID = 47001


def register() -> None:
    """Register this test project under whichever ROOT is live during suite discovery."""
    config.ensure_root()
    config.save_projects({PROJECT: {"name": PROJECT, "path": config.ROOT.as_posix(), "stacks": ["python"]}})


class TestCleanClose(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register()

    def setUp(self):
        (config.MONITOR_DIR / "recovery-breaker.json").unlink(missing_ok=True)

    def _report(self) -> dict:
        return {
            "landed": {
                "prs": [{"number": 47, "title": "Clean close", "merged": True, "merge_sha": MERGE_SHA}],
                "main_runs": [{"id": str(RUN_ID), "conclusion": "success"}],
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
        injected = {"problems": [], "signals": []}
        if change:
            change(report, injected)
        directory = S.task_dir(PROJECT, slug)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "report.md").write_text("report\n")
        S.write_json(directory / "report.json", report)
        (directory / "progress.md").write_text("complete\n")
        dispatch_id = f"{slug}-1"
        task = {"slug": slug, "title": slug, "class": cls, "state": "reported", "created": S.now(),
                "updated": S.now(), "l3_handled": None, "spend": {}, "prs": [47],
                "dispatch_id": dispatch_id, "attempt": 1}
        if hold_merge:
            task["hold_merge"] = "always-list: release"
        S.save_task(PROJECT, task)
        self._write_merge_record(slug, dispatch_id)
        verdict = self._verify_report(slug)
        verdict["problems"].extend(injected["problems"])
        verdict["signals"].extend(injected["signals"])
        task["verified"] = verdict
        S.save_task(PROJECT, task)
        if live_state:
            live = dict(task)
            live["state"] = live_state
            S.save_task(PROJECT, live)
        return task, verdict

    def _write_merge_record(self, slug: str, dispatch_id: str) -> None:
        S.write_json(S.task_dir(PROJECT, slug) / "merge-request-47.json", {
            "version": 1, "project": PROJECT, "slug": slug, "pr": 47,
            "generation": f"merge-{slug}", "dispatch_id": dispatch_id, "task_attempt": 1,
            "branch": f"worktree-{slug}", "base": "main", "base_sha": BASE_SHA,
            "head_sha": HEAD_SHA, "state": "merged",
            "result": {"merged": True, "head_sha": HEAD_SHA, "base_sha": BASE_SHA,
                       "merge_sha": MERGE_SHA, "gate_mode": "github-actions",
                       "candidate_gate": None},
        })

    @staticmethod
    def _github(args, cwd):
        if args[:2] == ["pr", "view"]:
            return {"number": 47, "state": "MERGED", "mergedAt": S.now(),
                    "mergeCommit": {"oid": MERGE_SHA}, "headRefName": "worktree-clean",
                    "headRefOid": HEAD_SHA}
        if args[:2] == ["run", "view"]:
            return {"databaseId": RUN_ID, "headSha": MERGE_SHA,
                    "status": "completed", "conclusion": "success"}
        raise AssertionError(args)

    def _verify_report(self, slug: str) -> dict:
        with mock.patch.object(verify, "gh", side_effect=self._github), \
             mock.patch.object(verify, "_seen_tags", return_value=set()):
            return verify._verify(PROJECT, slug)

    def _run(self, task: dict, verdict: dict) -> tuple[list, list]:
        turns = []
        logs = []
        original_turn, original_log = l3.turn, server.log
        l3.turn = lambda project, header, trigger, **kw: turns.append((project, header, trigger)) or {}
        server.log = lambda message: logs.append(message)
        try:
            server.report_turn(PROJECT, task, verdict)
        finally:
            l3.turn, server.log = original_turn, original_log
        return turns, logs


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
                (config.MONITOR_DIR / "recovery-breaker.json").unlink(missing_ok=True)
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


    def test_main_runs_must_be_present_well_shaped_and_successful(self):
        cases = (
            ("missing", []),
            ("failed", [{"id": "run-red", "conclusion": "failure"}]),
            ("invented-numeric", [{"id": "99999", "conclusion": "success"}]),
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
                self.assertEqual(S.load_task(PROJECT, task["slug"])["state"], "reported")

    def test_local_suite_and_generic_check_proof_cannot_clean_close(self):
        task, _ = self._task_and_verdict("local-suite")
        path = S.task_dir(PROJECT, task["slug"])
        report = S.read_json(path / "report.json")
        report["landed"]["main_runs"] = []
        S.write_json(path / "report.json", report)
        rec = S.read_json(path / "merge-request-47.json")
        gate = {"sandboxed": True, "passed": True, "base_sha": BASE_SHA,
                "head_sha": HEAD_SHA, "tests": 733, "skipped": 0, "expected_failures": 0}
        rec["result"].update({"gate_mode": "local-suite", "candidate_gate": gate})
        S.write_json(path / "merge-request-47.json", rec)
        verdict = self._verify_report(task["slug"])
        self.assertEqual(verdict["verdict"], "contradicted")
        self.assertIn(verify.TRUSTED_REMOTE_PENDING, verdict["problems"])
        turns, _ = self._run(task, verdict)
        self.assertEqual(len(turns), 1)
        self.assertEqual(S.load_task(PROJECT, task["slug"])["state"], "reported")

        invented, _ = self._task_and_verdict("local-suite-invented-run")
        idir = S.task_dir(PROJECT, invented["slug"])
        ireport = S.read_json(idir / "report.json")
        irec = S.read_json(idir / "merge-request-47.json")
        irec["result"].update({"gate_mode": "local-suite", "candidate_gate": gate})
        S.write_json(idir / "merge-request-47.json", irec)
        rejected = self._verify_report(invented["slug"])
        self.assertEqual(rejected["verdict"], "contradicted")
        self.assertIn(verify.TRUSTED_REMOTE_PENDING, rejected["problems"])
        self.assertIn("local-suite landings must report zero GitHub main runs", rejected["problems"])
        turns, _ = self._run(invented, rejected)
        self.assertEqual(len(turns), 1)

    def test_clean_close_rejects_stale_dispatch_and_head_provenance(self):
        for name, mutation in (
                ("dispatch", lambda rec: rec.update(dispatch_id="old-dispatch")),
                ("head", lambda rec: rec.update(head_sha="d" * 40))):
            with self.subTest(name=name):
                task, _ = self._task_and_verdict(f"stale-provenance-{name}")
                path = S.task_dir(PROJECT, task["slug"]) / "merge-request-47.json"
                rec = S.read_json(path)
                mutation(rec)
                S.write_json(path, rec)
                verdict = self._verify_report(task["slug"])
                self.assertEqual(verdict["verdict"], "contradicted")
                turns, _ = self._run(task, verdict)
                self.assertEqual(len(turns), 1)

        task, _ = self._task_and_verdict("stale-provenance-merge-sha")
        report_path = S.task_dir(PROJECT, task["slug"]) / "report.json"
        report = S.read_json(report_path)
        report["landed"]["prs"][0]["merge_sha"] = "d" * 40
        S.write_json(report_path, report)
        verdict = self._verify_report(task["slug"])
        self.assertEqual(verdict["verdict"], "contradicted")
        self.assertTrue(any("merge SHA" in problem for problem in verdict["problems"]))

    def test_github_main_run_must_be_available_completed_success_on_exact_merge(self):
        cases = (
            ("unavailable", None, "unavailable"),
            ("wrong-id", {"databaseId": 99999, "headSha": MERGE_SHA,
                           "status": "completed", "conclusion": "success"}, "id does not match"),
            ("pending", {"databaseId": RUN_ID, "headSha": MERGE_SHA,
                         "status": "in_progress", "conclusion": None}, "not completed"),
            ("failed", {"databaseId": RUN_ID, "headSha": MERGE_SHA,
                        "status": "completed", "conclusion": "failure"}, "did not conclude success"),
            ("wrong-head", {"databaseId": RUN_ID, "headSha": "d" * 40,
                            "status": "completed", "conclusion": "success"},
             "does not exactly match"),
        )
        for name, run_info, expected in cases:
            with self.subTest(name=name):
                task, _ = self._task_and_verdict(f"github-run-{name}")

                def github(args, cwd):
                    return self._github(args, cwd) if args[:2] == ["pr", "view"] else run_info

                with mock.patch.object(verify, "gh", side_effect=github), \
                     mock.patch.object(verify, "_seen_tags", return_value=set()):
                    verdict = verify._verify(PROJECT, task["slug"])
                self.assertEqual(verdict["verdict"], "contradicted")
                self.assertTrue(any(expected in problem for problem in verdict["problems"]),
                                verdict["problems"])




    def test_unknown_review_dispositions_fail_closed(self):
        for index, disposition in enumerate((None, "accepted", "")):
            with self.subTest(disposition=disposition):
                task, verdict = self._task_and_verdict(
                    f"review-{index}",
                    change=lambda report, _verdict: report["review"][0].update(disposition=disposition))

                turns, _ = self._run(task, verdict)

                self.assertEqual(len(turns), 1)
                self.assertEqual(S.load_task(PROJECT, task["slug"])["state"], "reported")

    def test_on_disk_report_is_the_only_clean_close_source(self):
        task, verdict = self._task_and_verdict(
            "on-disk-decision", change=lambda report, verified: report["decisions"].append("choose"))
        verdict["report"] = {}

        turns, _ = self._run(task, verdict)

        self.assertEqual(len(turns), 1)
        self.assertEqual(S.load_task(PROJECT, task["slug"])["state"], "reported")
if __name__ == "__main__":
    unittest.main()
