"""Manual installation runs on main stay outside the task PR's landing candidate.

Git and landing are real; the existing GitHub transport fixture supplies separate
repository run inventory and PR-associated check evidence. Hosted association itself
still needs a real workflow run; dispatching on a task head is deliberately unsupported.
"""
import copy
import json

from tests import test_land
from tests.support import AltitudeCase
from altitude import land, state as S


class TestInstallationCiSeparation(AltitudeCase):
    git = test_land.TestLand.git
    clone = test_land.TestLand.clone
    staged_change = test_land.TestLand.staged_change
    runner_log = test_land.TestLand.runner_log
    runner_calls = test_land.TestLand.runner_calls
    connection = staticmethod(test_land.TestCheckEvidence.connection)
    refresh = test_land.TestCheckEvidence.refresh
    contexts = test_land.TestCheckEvidence.contexts
    classify = test_land.TestCheckEvidence.classify
    configure_required_check = test_land.TestRequiredPrCheck.configure_required_check
    workflow_path = test_land.TestRequiredPrCheck.workflow_path
    source = test_land.TestCheckEvidence.source

    def setUp(self):
        super().setUp()
        test_land.TestCheckEvidence.setup_check_evidence(self)
        self.configure_required_check()
        (self.ghdir / "merge_git.txt").touch()

    def installation_run(self, status, conclusion):
        run = {
            "databaseId": 222,
            "workflowName": "Installation lifecycle",
            "event": "workflow_dispatch",
            "headBranch": "main",
            "headSha": self.base,
            "status": status,
            "conclusion": conclusion,
        }
        self.assertNotEqual(run["headSha"], self.head)
        S.write_json(self.ghdir / "runs.json", [run])
        observed = land._run(["gh", "run", "list", "--json", "headSha,status,conclusion"], self.repo)
        self.assertEqual(json.loads(observed.stdout), [run])
        return run

    def assert_installation_does_not_block(self, status, conclusion):
        self.installation_run(status, conclusion)
        self.assertEqual(self.classify(), "pass")
        before = len(self.gh_log())
        expected_tree = self.git("rev-parse", "HEAD^{tree}").strip()
        result = land.land("required PR check passed", cwd=self.repo, wait=0, merge=True)
        self.assertEqual((result["checks"], result["merged"], result["local_tests"]), ("pass", True, None))
        self.assertEqual(self.git("rev-parse", "origin/main^{tree}").strip(), expected_tree)
        self.assertEqual(self.runner_log(), [])
        calls = self.gh_log()[before:]
        self.assertFalse(any(call[0] == "run" for call in calls))
        queries = [call for call in calls if call[:2] == ["api", "graphql"]]
        self.assertTrue(queries)
        self.assertTrue(all("number=101" in call for call in queries))

    def test_failed_installation_run_on_main_does_not_block_task_landing(self):
        self.assert_installation_does_not_block("completed", "failure")

    def test_running_installation_run_on_main_does_not_block_task_landing(self):
        self.assert_installation_does_not_block("in_progress", "")

    def test_queued_installation_run_on_main_does_not_block_task_landing(self):
        self.assert_installation_does_not_block("queued", "")

    def test_installation_success_never_substitutes_for_the_required_pr_check(self):
        self.installation_run("completed", "success")
        for status, conclusion, expected in (
            ("COMPLETED", "FAILURE", "fail"),
            ("IN_PROGRESS", None, "pending"),
            ("QUEUED", None, "pending"),
            ("COMPLETED", "SKIPPED", "skipped"),
        ):
            with self.subTest(status=status, conclusion=conclusion):
                self.required_check.update(status=status, conclusion=conclusion)
                self.assertEqual(self.classify(), expected)
                result = land.land("required check remains mandatory", cwd=self.repo, wait=0, merge=True)
                self.assertEqual((result["checks"], result["merged"], result["local_tests"]),
                                 (expected, False, None))
        self.contexts().update(self.connection([]))
        S.write_json(self.ghdir / "checks.json", [])
        self.assertEqual(self.classify(), "missing")
        result = land.land("required check is absent", cwd=self.repo, wait=0, merge=True)
        self.assertEqual((result["checks"], result["merged"]), ("missing", False))
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in self.gh_log()))
        self.assertEqual(self.runner_log(), [])

    def test_dispatching_on_task_head_is_not_an_optional_check_exemption(self):
        check = copy.deepcopy(self.required_check)
        check.update(name="installation-lifecycle", isRequired=False)
        check["checkSuite"]["workflowRun"] = {
            "event": "workflow_dispatch",
            "file": {"path": ".github/workflows/installation-lifecycle.yml"},
        }
        self.contexts().update(self.connection([self.required_check, check]))
        for status, conclusion in (("COMPLETED", "FAILURE"), ("IN_PROGRESS", None),
                                   ("COMPLETED", "SKIPPED"), ("COMPLETED", "SUCCESS")):
            with self.subTest(status=status, conclusion=conclusion):
                check.update(status=status, conclusion=conclusion)
                with self.assertRaisesRegex(land.LandError, "workflow run does not belong"):
                    self.classify()
