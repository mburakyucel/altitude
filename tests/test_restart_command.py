"""The operator restart stays one guarded, repository-owned command."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import REPO, AltitudeCase
from altitude import config, dispatch, state as S, tasks as T

SCRIPT = REPO / "scripts" / "restart_altitude.py"


def load_script():
    spec = importlib.util.spec_from_file_location("restart_altitude", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class TestRestartCommand(AltitudeCase):
    def test_make_exposes_one_operator_command(self):
        makefile = (REPO / "Makefile").read_text()
        restart = makefile.split("restart:", 1)[1].split("\ninstall-service:", 1)[0]
        self.assertIn("python3 scripts/restart_altitude.py", restart)
        self.assertIn("`make restart`", (REPO / "docs" / "OPERATIONS.md").read_text())
        helper = SCRIPT.read_text()
        self.assertIn("fetch_and_require_exact_base", helper)
        self.assertIn("require_idle()", helper)
        self.assertIn('unit_environment().get("ALTITUDE_HOME"', helper)

    def test_generated_web_bundle_is_runtime_state_not_tracked_source(self):
        ignored = (REPO / ".gitignore").read_text().splitlines()
        self.assertIn("web/dist/", ignored)
        self.assertIn("web/.dist-*/", ignored)
        tracked = subprocess.run(
            ["git", "ls-files", "web/dist"], cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        self.assertEqual(tracked, [])

    def test_bundle_validation_rejects_a_missing_bundle(self):
        restart = load_script()
        with self.assertRaises(restart.RestartError):
            restart.validate_bundle(REPO / "tests" / "does-not-exist")

    def test_failed_restart_restores_previous_bundle(self):
        restart = load_script()
        with tempfile.TemporaryDirectory(prefix="altitude-restart-test-") as tmp:
            web = Path(tmp)
            dist, staging = web / "dist", web / ".dist-next-test"
            dist.mkdir(); (dist / "version").write_text("old")
            staging.mkdir(); (staging / "version").write_text("new")
            attempts = 0

            def restart_unit():
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise restart.RestartError("simulated restart failure")

            with mock.patch.object(restart, "WEB", web), mock.patch.object(restart, "DIST", dist), \
                    mock.patch.object(restart, "unit_properties", return_value={"MainPID": "10"}), \
                    mock.patch.object(restart, "restart_unit", side_effect=restart_unit), \
                    mock.patch.object(restart, "wait_healthy") as healthy, \
                    mock.patch.object(restart, "diagnostics"):
                with self.assertRaisesRegex(restart.RestartError, "recovery API/UI health verified"):
                    restart.publish_and_restart(staging)
            healthy.assert_called_once_with(10)
            self.assertEqual((dist / "version").read_text(), "old")
            self.assertFalse(staging.exists())

    def test_activation_and_rollback_verify_health_from_each_new_process(self):
        restart = load_script()
        for activation_fails, recovery_fails in ((False, False), (True, False), (True, True)):
            with self.subTest(activation_fails=activation_fails, recovery_fails=recovery_fails), \
                    tempfile.TemporaryDirectory() as tmp:
                web = Path(tmp)
                dist, staging = web / "dist", web / ".dist-next-test"
                dist.mkdir(); (dist / "version").write_text("old")
                staging.mkdir(); (staging / "version").write_text("new")
                pid = 10
                observed = []
                failure = restart.RestartError("candidate API failed")

                def restart_unit():
                    nonlocal pid
                    pid += 10

                def healthy(previous):
                    observed.append((previous, pid, (dist / "version").read_text()))
                    self.assertNotEqual(previous, pid)
                    if len(observed) == 1 and activation_fails:
                        raise failure
                    if len(observed) == 2 and recovery_fails:
                        raise restart.RestartError("recovery UI failed")

                with mock.patch.object(restart, "WEB", web), mock.patch.object(restart, "DIST", dist), \
                        mock.patch.object(restart, "unit_properties", side_effect=lambda: {"MainPID": str(pid)}), \
                        mock.patch.object(restart, "restart_unit", side_effect=restart_unit), \
                        mock.patch.object(restart, "wait_healthy", side_effect=healthy), \
                        mock.patch.object(restart, "diagnostics"):
                    if activation_fails:
                        with self.assertRaises(restart.RestartError) as caught:
                            restart.publish_and_restart(staging)
                        self.assertIs(caught.exception.__cause__, failure)
                        self.assertIn("candidate API failed", str(caught.exception))
                        self.assertIn("recovery UI failed" if recovery_fails else "recovery API/UI health verified",
                                      str(caught.exception))
                    else:
                        restart.publish_and_restart(staging)
                self.assertEqual(observed, [(10, 20, "new"), (20, 30, "old")] if activation_fails
                                 else [(10, 20, "new")])
                self.assertFalse(staging.exists())
                self.assertFalse((web / ".dist-previous").exists())
                self.assertEqual((dist / "version").read_text(), "old" if activation_fails else "new")

    def test_failed_recovery_restart_retains_both_errors_and_the_prior_bundle(self):
        restart = load_script()
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp)
            dist, staging = web / "dist", web / ".dist-next-test"
            dist.mkdir(); (dist / "version").write_text("old")
            staging.mkdir(); (staging / "version").write_text("new")
            with mock.patch.object(restart, "WEB", web), mock.patch.object(restart, "DIST", dist), \
                    mock.patch.object(restart, "unit_properties", return_value={"MainPID": "10"}), \
                    mock.patch.object(restart, "restart_unit", side_effect=[restart.RestartError("bootstrap failed"),
                                                                           restart.RestartError("recovery failed")]), \
                    mock.patch.object(restart, "wait_healthy") as healthy, \
                    mock.patch.object(restart, "diagnostics"):
                with self.assertRaisesRegex(restart.RestartError, "bootstrap failed.*recovery failed"):
                    restart.publish_and_restart(staging)
            healthy.assert_not_called()
            self.assertEqual((dist / "version").read_text(), "old")
            self.assertFalse(staging.exists())

    def test_first_activation_failure_does_not_claim_a_prior_bundle_or_attempt_empty_recovery(self):
        restart = load_script()
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp)
            staging = web / ".dist-next-test"
            staging.mkdir(); (staging / "version").write_text("new")
            with mock.patch.object(restart, "WEB", web), mock.patch.object(restart, "DIST", web / "dist"), \
                    mock.patch.object(restart, "unit_properties", return_value={"MainPID": "10"}), \
                    mock.patch.object(restart, "restart_unit", side_effect=restart.RestartError("bootstrap failed")) as start, \
                    mock.patch.object(restart, "diagnostics"):
                with self.assertRaisesRegex(restart.RestartError, "no prior web bundle.*recovery not attempted"):
                    restart.publish_and_restart(staging)
            self.assertEqual(start.call_count, 1)
            self.assertFalse((web / "dist").exists())
            self.assertFalse(staging.exists())

    def test_health_requires_new_running_process_valid_api_and_spa_shell(self):
        restart = load_script()
        for pid, api, page, failure in (("20", b'{"projects":[]}', b'<div id="root"></div>', None),
                                        ("10", b'{"projects":[]}', b'<div id="root"></div>', "PID 10"),
                                        ("20", b'{"projects":null}', b'<div id="root"></div>', "invalid payload"),
                                        ("20", b'{"projects":[]}', b'not the app', "SPA shell")):
            with self.subTest(pid=pid, api=api, page=page), \
                    mock.patch.object(restart, "unit_properties", return_value={"MainPID": pid,
                                                                               "ActiveState": "active"}), \
                    mock.patch.object(restart, "fetch", side_effect=lambda path: api if path == "/api/overview" else page) as fetch, \
                    mock.patch.object(restart.time, "monotonic", side_effect=[0, 0, 46]), \
                    mock.patch.object(restart.time, "sleep"):
                if failure:
                    with self.assertRaisesRegex(restart.RestartError, failure):
                        restart.wait_healthy(10)
                else:
                    restart.wait_healthy(10)
                    self.assertEqual([call.args[0] for call in fetch.call_args_list], ["/api/overview", "/"])

    def test_the_checkout_is_checked_against_the_branch_the_service_runs(self):
        restart = load_script()
        for environment, branch in (("", "main"), ("ALTITUDE_SOURCE_BRANCH=trial", "trial")):
            unit = {"WorkingDirectory": str(restart.ROOT),
                    "Environment": f"ALTITUDE_HOME={config.ROOT} {environment}".strip()}
            with self.subTest(branch=branch), mock.patch.object(restart, "unit_properties", return_value=unit), \
                    mock.patch.object(restart.git_policy, "fetch_and_require_exact_base") as fetch:
                restart.require_deployed_checkout()
                fetch.assert_called_once_with(restart.ROOT, branch)

    def test_decision_11_script_allows_running_and_blocked_workers_and_waiting_reports(self):
        restart = load_script()
        self.private_ledgers()
        for state in ("running", "blocked", "reported"):
            task = T.new(self.project, state, "request", actor="burak")
            task.update(state=state, agent_id="detached")
            S.save_task(self.project, task)
        with mock.patch.object(restart, "unit_properties", return_value={"ActiveState": "active"}), \
                mock.patch.object(restart, "fetch", return_value=b'{"restart":{"waiting_for":[]}}'):
            restart.require_idle()
        self.assertTrue(config.restart_in_progress())
        pending = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING)
        pending["requested_at"] = "2026-09-07T08:00:00+00:00"
        S.write_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, pending)
        with mock.patch.object(restart, "unit_properties", return_value={"ActiveState": "inactive"}):
            restart.require_idle()
        self.assertEqual(S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING)["requested_at"],
                         pending["requested_at"])

    def test_script_rechecks_dispatch_claim_l3_and_report_verification(self):
        restart = load_script()
        self.private_ledgers()
        task = T.new(self.project, "launching", "request", actor="burak")
        with mock.patch.object(restart, "unit_properties", return_value={"ActiveState": "inactive"}):
            for field in ("dispatching", "resume_claim"):
                task[field] = "claim"; S.save_task(self.project, task)
                with self.assertRaisesRegex(restart.RestartError, "active"):
                    restart.require_idle()
                task.pop(field); S.save_task(self.project, task)
            with config.restart_lock():
                with self.assertRaisesRegex(restart.RestartError, "active"):
                    restart.require_idle()
        with mock.patch.object(restart, "unit_properties", return_value={"ActiveState": "active"}), \
                mock.patch.object(restart, "fetch", return_value=b'{"restart":{"waiting_for":["L3"]}}'):
            with self.assertRaisesRegex(restart.RestartError, "L3"):
                restart.require_idle()


    def test_a_failing_unit_reports_its_own_reason_at_once_and_lifts_the_hold(self):
        """I-20260924-205802: a 10-second failure surfaced only at altd's ten-minute grace timeout."""
        restart = load_script()
        flag = config.MONITOR_DIR / dispatch.RESTART_PENDING
        failure = restart.RestartError("cannot prove Altitude is quiet: /api/overview is unreachable: timed out")
        with mock.patch.object(restart, "require_deployed_checkout", side_effect=failure), \
                mock.patch.object(restart.incidents, "system_fault") as fault:
            self.assertEqual(restart.main(), 1)  # nothing requested: a hand run reports only on its terminal
            fault.assert_not_called()
            self.assertIsNone(S.read_json(flag))
            S.write_json(flag, {"files": ["web/src/styles.css"], "requested_at": S.now(), "unit": "u"})
            self.assertTrue(config.restart_in_progress())
            self.assertEqual(restart.main(), 1)
            self.assertEqual(restart.main(), 1)  # an already failed record is reported once
        fault.assert_called_once_with("restart", f"restart failed: {failure}")
        pending = S.read_json(flag)
        self.assertEqual(pending["error"], str(failure))
        self.assertFalse(config.restart_in_progress())


    def test_verification_failure_after_the_replacement_cleared_the_record_is_still_reported(self):
        restart = load_script()
        flag = config.MONITOR_DIR / dispatch.RESTART_PENDING
        S.write_json(flag, {"files": ["altitude/server.py"], "requested_at": S.now(), "unit": "u"})

        def replacement_starts_then_fails(_staging):
            flag.unlink()  # the replacement daemon clears the record once it binds
            raise restart.RestartError("restart verification failed; restored the prior web bundle: unhealthy")

        with mock.patch.object(restart, "require_deployed_checkout"), \
                mock.patch.object(restart, "build_bundle", return_value=self.tmp / "staging"), \
                mock.patch.object(restart, "require_idle"), \
                mock.patch.object(restart, "publish_and_restart", side_effect=replacement_starts_then_fails), \
                mock.patch.object(restart.incidents, "system_fault") as fault:
            self.assertEqual(restart.main(), 1)
        fault.assert_called_once()
        self.assertIn("unhealthy", fault.call_args.args[1])
        self.assertIsNone(S.read_json(flag))  # a running replacement's open entry is not reclosed


if __name__ == "__main__":
    unittest.main()
