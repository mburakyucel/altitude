"""Real task/HTTP/Git/evidence flow with only remote transport and native jobs replaced."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from tests.support import AltitudeCase, make_repo
from altitude import dispatch, engines, platform, server, state as S, tasks as T, terminal, validation
from altitude import validation_remote as remote

VM = r'''
import json, os, pathlib, subprocess, sys
area = pathlib.Path(sys.argv[1])
request = json.loads((area / 'input/request.json').read_text())
results = area / 'results'
results.mkdir()
artifacts = results / 'artifacts'
artifacts.mkdir()
with (results / 'output.log').open('wb') as log:
    result = subprocess.run(request['argv'], cwd=area / 'input/candidate',
                            env={'PATH': os.defpath, 'RESULTS': str(artifacts)},
                            stdout=log, stderr=subprocess.STDOUT)
receipt = {key: request[key] for key in ('run_id', 'commit', 'tree')}
receipt.update(exit=result.returncode, ended='exit', error=None,
               guest={'os': 'macos', 'version': 'fixture', 'chip': 'fixture'})
(results / 'receipt.json').write_text(json.dumps(receipt))
'''


class RemoteValidationTests(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.runner = self.tmp / "runner"
        self.patch(validation, "home", return_value=self.runner)
        self.patch(terminal, "owner_connection", return_value=True)
        self.slug = T.new(self.project, "Native validation", "One bounded Mac run")["slug"]
        T.dispatch(self.project, self.slug, attempt=1, session_id="session", agent_id="agent",
                   worktree=str(self.repo), branch="work")
        root = dispatch.l2_job_root(self.project, self.slug)
        root.mkdir(parents=True)
        S.write_json(root / "agent.json", {"id": "agent", "engine": "claude", "unit": engines._claude_unit("agent")})
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.broker = remote.Broker(self.tmp / "mac", self.tmp / "template", ["fictional-broker"])
        self.alive = set()
        self.launches = []
        self.requests = []
        self.execute = True
        self.patch(remote, "POLL_SECONDS", .01)
        self.patch(remote, "POLL_MAX_SECONDS", .04)

        def launch(worker, ident, area):
            self.alive.add(ident)
            self.launches.append(ident)

        self.patch(platform, "validation_broker_launch", side_effect=launch)
        self.patch(platform, "validation_broker_active", side_effect=lambda ident: ident in self.alive)
        self.patch(platform, "validation_broker_stop", side_effect=self.alive.discard)
        self.patch(platform, "validation_vm_cleanup", side_effect=lambda area: self.alive.discard(area.name))
        self.patch(platform, "validation_vm_prepare", side_effect=lambda template, area: {
            "argv": [sys.executable, "-c", VM, str(area)], "host": {"os": "macos", "chip": "fixture"},
            "template": "fixture-template"})
        self.transport = self.patch(platform, "validation_remote_request", side_effect=self.forward)

    def forward(self, request):
        self.requests.append(request)
        ident = request["run_id"]
        if request["operation"] in {"status", "cancel"} and ident in self.alive and self.execute:
            self.broker.work(ident)
        return self.broker.dispatch(request)

    def validate(self, code="print('observed')", status=200, **options):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=10)
        try:
            body = {"project": self.project, "slug": self.slug, "attempt": "1", "target": "macos",
                    "command": [sys.executable, "-c", code], **options}
            connection.request("POST", "/api/task/validate", json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            value = json.loads(response.read())
            self.assertEqual(response.status, status, value)
            return value
        finally:
            connection.close()

    def rows(self):
        return [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl").read_text().splitlines()]

    def test_exact_revision_command_exit_log_artifacts_and_single_task_record(self):
        (self.repo / "untracked-private").write_text("must remain local")
        result = self.validate("import pathlib,os,subprocess; "
                               "assert not pathlib.Path('untracked-private').exists(); "
                               "print(subprocess.check_output(['git','rev-parse','HEAD']).decode()); "
                               "pathlib.Path(os.environ['RESULTS'],'proof.txt').write_text('observed')")
        self.assertEqual(result["exit"], 0, result)
        self.assertIn(result["commit"], result["output"])
        self.assertEqual((Path(result["results"]) / "artifacts/proof.txt").read_text(), "observed")
        self.assertEqual(Path(result["log"]).read_text(), result["output"])
        [row] = self.rows()
        self.assertEqual((row["target"], row["purpose"], row["exit"]), ("macos", "validation", 0))
        self.assertEqual(row["host"]["chip"], "fixture")
        self.assertEqual(row["guest"]["version"], "fixture")
        self.assertTrue(row["cleanup"])
        self.assertFalse(remote._pending().exists())
        self.assertFalse((self.broker.home / "active.json").exists())
        self.assertFalse((self.broker._area(row["run_id"]) / "evidence.json").exists())
        self.assertEqual(len(self.launches), 1)

    def test_nonzero_command_result_is_not_transport_unavailable(self):
        result = self.validate("import sys; print('test failure'); sys.exit(7)")
        self.assertEqual((result["exit"], result["ended"]), (7, "exit"))
        self.assertIn("test failure", result["output"])

    def test_full_executor_window_leaves_time_for_upload_result_and_acknowledgement(self):
        from unittest import mock
        clock = [0.0]
        duration = []

        def transfer(request):
            clock[0] += platform.VALIDATION_REQUEST_SECONDS
            if request["operation"] == "submit":
                duration.append(request["duration"])
            elif request["operation"] == "status":
                clock[0] += duration[0]  # Executor uses its full preparation/cleanup window.
            return self.forward(request)

        self.transport.side_effect = transfer
        self.patch(remote, "time", mock.Mock(wraps=time, monotonic=lambda: clock[0]))
        result = self.validate()
        self.assertEqual(result["exit"], 0, result)
        self.assertGreater(duration[0], remote.CLEANUP_SECONDS)
        self.assertLess(duration[0], remote.TIMEOUT)
        self.assertLessEqual(clock[0], remote.TIMEOUT)
        self.assertFalse(remote._pending().exists())
        self.assertEqual([r["operation"] for r in self.requests], ["submit", "status", "result", "result"])

    def test_invalid_artifacts_keep_protected_log_and_receipt_without_claiming_pass(self):
        result = self.validate("import os,pathlib; print('useful test log'); "
                               "pathlib.Path(os.environ['RESULTS'],'invalid-link').symlink_to('/unrelated')")
        self.assertIsNone(result["exit"])
        self.assertIn("artifacts are incomplete or invalid", result["error"])
        self.assertIn("useful test log", result["output"])
        self.assertTrue((Path(result["results"]) / "receipt.json").is_file())
        self.assertFalse((Path(result["results"]) / "artifacts").exists())
        self.assertTrue(result["cleanup"])

    def test_missing_relay_records_explicit_unavailable_without_reserving_a_run(self):
        self.transport.side_effect = lambda request: {"status": "unavailable", "accepted": False}
        result = self.validate()
        self.assertIsNone(result["exit"])
        self.assertEqual(result["ended"], "unavailable")
        self.assertIn("no passing result", result["error"])
        self.assertFalse(remote._pending().exists())
        self.assertEqual(self.rows()[0]["ended"], "unavailable")

    def test_unreachable_mac_preflight_returns_without_submission_polling_or_pending_identity(self):
        import tempfile
        from unittest import mock
        from scripts import validation_relay as relay
        from tests.test_validation_relay import CONFIG
        probes = []

        def unreachable(argv, **kwargs):
            import io
            request = platform.validation_frame_read(io.BytesIO(kwargs['input']))
            probes.append(request)
            self.assertEqual(request['operation'], 'status')
            self.assertEqual(kwargs['timeout'], 15)
            raise subprocess.TimeoutExpired('private fixture transport', 15)

        self.patch(relay, 'subprocess', mock.Mock(wraps=subprocess, run=unreachable,
                                               TimeoutExpired=subprocess.TimeoutExpired))

        def through_relay(request):
            with tempfile.TemporaryFile() as output:
                relay.forward(CONFIG, request, output)
                output.seek(0)
                return platform.validation_frame_read(output)

        self.transport.side_effect = through_relay
        result = self.validate()
        self.assertEqual(result['ended'], 'unavailable')
        self.assertIsNone(result['exit'])
        self.assertFalse(remote._pending().exists())
        self.assertEqual(self.launches, [])
        self.assertEqual(len(probes), 1)
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(self.rows()[0]['ended'], 'unavailable')

    def test_uncertain_submission_is_cancelled_and_recovered_without_duplicate_submission(self):
        def lost_reply(request):
            if request["operation"] == "submit":
                self.broker.dispatch(request)
                remote.stop_all()
            return {"status": "unavailable"}

        self.transport.side_effect = lost_reply
        first = self.validate()
        self.assertIsNone(first["exit"])
        self.assertTrue(remote._pending().exists())
        self.transport.side_effect = self.forward
        remote.reconcile()
        self.assertFalse(remote._pending().exists())
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.requests[0]["operation"], "cancel")
        # Recovery preserves the original uncertain outcome; later evidence is separate.
        self.assertIsNone(self.rows()[0]["exit"])
        recovered = S.task_dir(self.project, self.slug) / "validation/1-recovered"
        self.assertTrue((recovered / "output.log").is_file())

    def test_switch_cancellation_stays_unconfirmed_until_remote_cleanup(self):
        self.execute = False

        def switch_off(request):
            result = self.forward(request)
            if request["operation"] == "submit":
                remote.stop_all()
            return result

        self.transport.side_effect = switch_off
        result = self.validate()
        self.assertIsNone(result["exit"])
        self.assertTrue(remote._pending().exists())
        self.assertEqual([r["operation"] for r in self.requests], ["submit", "cancel"])
        remote.reconcile()
        self.assertTrue(remote._pending().exists())
        self.alive.clear()  # Native supervisor has stopped, including every VM descendant.
        remote.reconcile()
        self.assertFalse(remote._pending().exists())

    def test_wrong_owner_options_and_attempt_never_reach_transport(self):
        self.validate(status=403, attempt="2")
        self.validate(status=400, kvm=True)
        self.validate(status=400, publish=8080)
        self.validate(status=400, target="other")
        self.patch(terminal, "owner_connection", return_value=False)
        self.validate(status=403)
        self.transport.assert_not_called()

    def test_switch_off_cancels_pending_identity_without_an_active_caller(self):
        def lost_reply(request):
            if request["operation"] == "submit":
                self.broker.dispatch(request)
                remote.stop_all()
            return {"status": "unavailable"}

        self.transport.side_effect = lost_reply
        self.validate()
        self.assertTrue(remote._pending().exists())
        self.transport.side_effect = self.forward
        worker = remote.stop_all()
        self.assertIsNotNone(worker)
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(remote._pending().exists())
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.requests[0]["operation"], "cancel")
        self.assertIsNone(self.rows()[0]["exit"])
        self.assertTrue((S.task_dir(self.project, self.slug) / "validation/1-recovered/output.log").is_file())

    def test_unreachable_idle_cancellation_retains_identity_and_releases_lock(self):
        def lost_reply(request):
            if request["operation"] == "submit":
                self.broker.dispatch(request)
                remote.stop_all()
            return {"status": "unavailable"}

        self.transport.side_effect = lost_reply
        self.validate()
        self.transport.reset_mock()
        worker = remote.stop_all()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(remote._pending().exists())
        self.assertEqual(self.transport.call_args.args[0]["operation"], "cancel")
        self.transport.side_effect = self.forward
        remote.reconcile()
        self.assertFalse(remote._pending().exists())

    def test_mismatched_remote_identity_never_becomes_a_pass(self):
        def corrupt(request):
            response = self.forward(request)
            if request["operation"] == "result":
                response["commit"] = "0" * 40
            return response

        self.transport.side_effect = corrupt
        result = self.validate()
        self.assertIsNone(result["exit"])
        self.assertTrue(remote._pending().exists())

    def test_cleanup_failure_closes_remote_admission(self):
        self.patch(platform, "validation_vm_cleanup", side_effect=RuntimeError("fixture cleanup failure"))
        result = self.validate()
        self.assertIsNone(result["exit"])
        self.assertTrue(remote._pending().exists())
        self.assertTrue((self.broker.home / "active.json").exists())
        self.validate(status=400)
        self.assertEqual(len(self.launches), 1)

    def test_duplicate_remote_submission_keeps_one_identity_and_supervisor(self):
        self.execute = False

        def twice(request):
            response = self.forward(request)
            if request["operation"] == "submit":
                self.assertEqual(self.broker.dispatch(request), response)
                remote.stop_all()
            return response

        self.transport.side_effect = twice
        self.validate()
        self.assertEqual(len(self.launches), 1)

    def test_lost_acknowledgement_retries_evidence_without_duplicate_command_or_event(self):
        def lost_ack(request):
            if "received" in request:
                return {"status": "unavailable"}
            return self.forward(request)

        self.transport.side_effect = lost_ack
        result = self.validate()
        self.assertEqual(result["exit"], 0)
        self.assertTrue(remote._pending().exists())
        remote.reconcile()  # Re-read and verify the already published complete artifact tree.
        self.assertTrue(remote._pending().exists())
        self.transport.side_effect = self.forward
        remote.reconcile()
        self.assertFalse(remote._pending().exists())
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.rows()[0]["exit"], 0)
        self.assertEqual(len([e for e in S.read_events(self.project, self.slug)
                              if e["kind"] == "validation-reconciled"]), 1)

    def test_recovery_retries_after_log_publication_failure(self):
        original = remote.os.replace
        failed = []

        def interrupt(source, destination, **kwargs):
            if str(destination).endswith('.log') and not failed:
                failed.append(True)
                raise OSError('fixture interruption between artifact and log publication')
            return original(source, destination, **kwargs)

        self.patch(remote.os, "replace", side_effect=interrupt)
        result = self.validate()
        self.assertIsNone(result["exit"])
        self.assertTrue(remote._pending().exists())
        remote.reconcile()
        self.assertFalse(remote._pending().exists())
        self.assertEqual(len(self.launches), 1)
        self.assertIsNone(self.rows()[0]["exit"])

    def test_changed_published_evidence_refuses_reconciliation(self):
        def lost_ack(request):
            return {"status": "unavailable"} if "received" in request else self.forward(request)

        self.transport.side_effect = lost_ack
        result = self.validate()
        (Path(result["results"]) / "output.log").write_text("changed after delivery")
        self.transport.side_effect = self.forward
        remote.reconcile()
        self.assertTrue(remote._pending().exists())
        self.assertEqual(len(self.launches), 1)

    def admitted(self):
        candidate = remote.payload.export(self.repo)
        return self.broker.dispatch({"operation": "submit", "run_id": "a" * 32,
                                    "argv": [sys.executable, "-c", "print('observed')"],
                                    "payload": candidate, "duration": 3600})

    def test_new_identity_status_does_not_reap_an_unrelated_expired_run(self):
        from unittest import mock
        record = self.admitted()
        with mock.patch.object(self.broker, '_reap', side_effect=RuntimeError('slow native cleanup')) as reap:
            absent = self.broker.dispatch({'operation': 'status', 'run_id': 'b' * 32})
            self.assertEqual(absent, {'run_id': 'b' * 32, 'status': 'absent'})
            reap.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, 'slow native cleanup'):
                self.broker.dispatch({'operation': 'status', 'run_id': record['run_id']})

    def test_transient_status_and_result_failures_recover_the_same_run(self):
        lost = {"status", "result"}
        def transient(request):
            if request["operation"] in lost:
                lost.remove(request["operation"])
                # Refusal of this poll proves nothing about the original submission.
                return {"status": "unavailable", "accepted": False}
            return self.forward(request)
        self.transport.side_effect = transient
        result = self.validate()
        self.assertEqual(result["exit"], 0, result)
        self.assertEqual(len(self.launches), 1)
        self.assertFalse(remote._pending().exists())
        self.assertFalse(lost)

    def test_absent_cancel_tombstone_refuses_a_delayed_submission(self):
        cancelled = self.broker.dispatch({"operation": "cancel", "run_id": "a" * 32})
        self.assertEqual(cancelled, {"run_id": "a" * 32, "status": "cancelled", "accepted": False})
        self.assertEqual(self.admitted(), cancelled)
        self.assertFalse(self.launches)
        self.assertFalse((self.broker.home / "active.json").exists())

    def test_executor_duration_uses_its_own_clock(self):
        self.patch(remote.time, "time", return_value=1000)
        record = self.admitted()
        self.assertEqual(record["expires"], 4600)
        self.assertEqual(record["status"], "running")

    def test_cleanup_budget_stays_inside_the_one_hour_supervisor_limit(self):
        self.admitted()
        area = self.broker._area("a" * 32)
        record = remote.read_json(area / "record.json")
        record["expires"] = time.time() + remote.CLEANUP_SECONDS - 1
        remote.write_json(area / "record.json", record)
        prepare = self.patch(platform, "validation_vm_prepare")
        self.broker.work("a" * 32)
        prepare.assert_not_called()
        result = self.broker.dispatch({"operation": "status", "run_id": "a" * 32})
        self.assertEqual(result["ended"], "timeout")
        self.assertTrue(result["cleanup"])

    def test_cancellation_before_boot_does_not_launch_a_vm(self):
        self.admitted()
        self.broker.dispatch({"operation": "cancel", "run_id": "a" * 32})
        prepare = self.patch(platform, "validation_vm_prepare")
        self.broker.work("a" * 32)
        prepare.assert_not_called()
        result = self.broker.dispatch({"operation": "status", "run_id": "a" * 32})
        self.assertEqual(result["ended"], "cancelled")
        self.assertTrue(result["cleanup"])

    def test_active_vm_cancellation_records_output_and_confirmed_cleanup(self):
        self.admitted()
        area = self.broker._area("a" * 32)
        command = [sys.executable, "-c", "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
                   "p.mkdir(); (p/'output.log').write_text('guest is active'); time.sleep(5)", str(area / "results")]
        self.patch(platform, "validation_vm_prepare", return_value={"argv": command, "host": {}, "template": "fixture"})
        worker = threading.Thread(target=self.broker.work, args=("a" * 32,))
        worker.start()
        try:
            deadline = time.monotonic() + 3
            while not (area / "results/output.log").exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue((area / "results/output.log").is_file())
            self.broker.dispatch({"operation": "cancel", "run_id": "a" * 32})
        finally:
            worker.join(timeout=7)
        self.assertFalse(worker.is_alive())
        result = self.broker.dispatch({"operation": "result", "run_id": "a" * 32})
        self.assertEqual(result["ended"], "cancelled")
        self.assertTrue(result["cleanup"])
        self.assertIn("evidence", result)

    def test_expired_or_dead_job_is_stopped_before_cleanup_and_never_relaunched(self):
        self.admitted()
        area = self.broker._area("a" * 32)
        record = remote.read_json(area / "record.json")
        record["expires"] = int(time.time()) - 1
        remote.write_json(area / "record.json", record)
        result = self.broker.dispatch({"operation": "status", "run_id": "a" * 32})
        self.assertEqual(result["ended"], "timeout")
        self.assertTrue(result["cleanup"])
        self.assertFalse(self.alive)
        self.assertFalse((area / "input").exists())
        self.assertEqual(len(self.launches), 1)
