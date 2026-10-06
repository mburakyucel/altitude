"""Real guest command/log/artifact mechanics; only native account/GUI launch is replaced."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from scripts import validation_guest as guest


class ValidationGuestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input"
        self.candidate = self.input / "candidate"
        (self.candidate / "web").mkdir(parents=True)
        self.results = self.root / "results"
        self.results.mkdir()
        self.work = self.root / "work"
        self.store = self.root / "store"
        self.store.mkdir()
        (self.store / "package").write_text("offline package")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "pnpm").write_text("#!/bin/sh\nprintf 'offline install\\n'\nexit 0\n")
        (self.bin / "pnpm").chmod(0o755)
        for file in ("package.json", "pnpm-lock.yaml"):
            (self.candidate / "web" / file).write_text("fixture dependency declaration")
        self.config = {"version": 1, "user": "fixture", "path": f"{self.bin}:/usr/bin:/bin",
                       "store": str(self.store), "browsers": str(self.root / "browsers"),
                       "fingerprints": {f"web/{name}": guest.fingerprint(self.candidate / "web" / name)
                                        for name in ("package.json", "pnpm-lock.yaml")}}
        self.account = {"name": "fixture", "uid": os.getuid(), "gid": os.getgid(), "home": str(self.root / "home")}
        self.launched = []

    def run_guest(self, code):
        (self.input / "request.json").write_text(json.dumps({"argv": [sys.executable, "-c", code],
                                                           "run_id": "fictional-run", "commit": "a" * 40, "tree": "b" * 40}))

        def launch(argv, *, cwd, env, account, output):
            self.launched.append((argv, env))
            return subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=subprocess.STDOUT, start_new_session=True)

        with (mock.patch.object(guest.platform, "validation_guest_account", return_value=self.account),
              mock.patch.object(guest.platform, "validation_host_identity", return_value={"os": "macos", "version": "fixture"}),
              mock.patch.object(guest.platform, "validation_guest_launch", side_effect=launch)):
            return guest.run(self.config, input_path=self.input, results=self.results, work=self.work)

    def test_command_exit_log_artifact_and_clean_environment_are_recorded(self):
        with mock.patch.dict(os.environ, {"SECRET_FIXTURE": "must not propagate", "ALTITUDE_SOURCE_BRANCH": "unrelated"}):
            receipt = self.run_guest("import os,pathlib,sys; print('candidate output'); "
                                     "assert 'SECRET_FIXTURE' not in os.environ; assert 'ALTITUDE_SOURCE_BRANCH' not in os.environ; "
                                     "pathlib.Path(os.environ['RESULTS'],'evidence.txt').write_text('observed'); sys.exit(7)")
        self.assertEqual((receipt["exit"], receipt["ended"]), (7, "exit"))
        self.assertIsNone(receipt["error"])
        self.assertEqual(json.loads((self.results / "receipt.json").read_text()), receipt)
        self.assertEqual((self.results / "artifacts" / "evidence.txt").read_text(), "observed")
        self.assertIn("candidate output", (self.results / "output.log").read_text())
        self.assertEqual(self.launched[0][0][:4], ["pnpm", "install", "--offline", "--frozen-lockfile"])
        env = self.launched[1][1]
        self.assertEqual(env["ALTITUDE_VALIDATION"], "1")
        self.assertEqual(env["PIP_NO_INDEX"], "1")
        self.assertEqual(env["COREPACK_ENABLE_NETWORK"], "0")
        self.assertNotEqual(env["RESULTS"], str(self.results))
        self.assertEqual((self.store / "package").read_text(), "offline package")

    def test_dependency_change_never_executes_candidate(self):
        (self.candidate / "web" / "pnpm-lock.yaml").write_text("changed")
        receipt = self.run_guest("raise AssertionError('must not execute')")
        self.assertEqual(receipt["ended"], "unavailable")
        self.assertIn("template refresh needed", receipt["error"])
        self.assertEqual(self.launched, [])

    def test_missing_offline_dependency_cannot_report_a_pass(self):
        (self.bin / "pnpm").write_text("#!/bin/sh\nexit 1\n")
        receipt = self.run_guest("raise AssertionError('must not execute')")
        self.assertIsNone(receipt["exit"])
        self.assertEqual(receipt["ended"], "unavailable")
        self.assertEqual(len(self.launched), 1)

    def test_candidate_stdout_is_pipe_and_cannot_truncate_supervisor_log(self):
        receipt = self.run_guest("import os,stat; assert stat.S_ISFIFO(os.fstat(1).st_mode); "
                                 "print('pipe output',flush=True)")
        self.assertEqual(receipt["exit"], 0)
        self.assertIn("offline install", (self.results / "output.log").read_text())

    def test_symlink_artifact_is_an_explicit_incomplete_result(self):
        receipt = self.run_guest("import os,pathlib; pathlib.Path(os.environ['RESULTS'],'escape').symlink_to('/etc/passwd')")
        self.assertIsNone(receipt["exit"])
        self.assertIn("link or special file", receipt["error"])
        self.assertFalse((self.results / "artifacts" / "escape").exists())

    def test_invalid_artifact_names_preserve_collectable_log_and_receipt(self):
        receipt = self.run_guest("import os,pathlib; print('output before artifact failure'); "
                                 "root=pathlib.Path(os.environ['RESULTS']); "
                                 "(root/'good').write_text('partial'); (root/'.git').write_text('unsafe')")
        self.assertIsNone(receipt["exit"])
        self.assertIn("unsafe transfer path", receipt["error"])
        self.assertFalse((self.results / "artifacts").exists())
        result = guest.payload.collect_results(self.results)
        destination = self.root / "returned"
        guest.payload.restore_results(result, destination)
        self.assertIn("output before artifact failure", (destination / "output.log").read_text())
        self.assertEqual(json.loads((destination / "receipt.json").read_text()), receipt)

    def test_artifact_overflow_preserves_log_and_explicit_failure(self):
        with mock.patch.object(guest, "EVIDENCE_LIMIT", 65536 + 1024):
            receipt = self.run_guest("import os,pathlib; print('diagnostic before overflow'); "
                                     "root=pathlib.Path(os.environ['RESULTS']); "
                                     "(root/'good').write_text('partial'); (root/'huge').write_bytes(b'x'*2048)")
        self.assertIsNone(receipt["exit"])
        self.assertIn("evidence budget", receipt["error"])
        self.assertFalse((self.results / "artifacts").exists())
        result = guest.payload.collect_results(self.results)
        self.assertEqual(set(result["entries"]), {"output.log", "receipt.json"})
        self.assertIn("diagnostic before overflow", (self.results / "output.log").read_text())

    def test_artifact_count_reserves_log_receipt_and_directory_entries(self):
        with mock.patch.object(guest.payload, "ENTRY_LIMIT", 4):
            receipt = self.run_guest("import os,pathlib; root=pathlib.Path(os.environ['RESULTS']); "
                                     "(root/'one').touch(); (root/'two').touch()")
            self.assertIsNone(receipt["exit"])
            self.assertIn("file count limit", receipt["error"])
            self.assertEqual(set(guest.payload.collect_results(self.results)["entries"]),
                             {"output.log", "receipt.json"})

    def test_log_flood_stops_with_no_success_receipt(self):
        with mock.patch.object(guest, "LOG_LIMIT", 1024):
            receipt = self.run_guest("import os; os.write(1,b'x'*10000)")
        self.assertIsNone(receipt["exit"])
        self.assertEqual(receipt["ended"], "output-limit")
        self.assertEqual((self.results / "output.log").stat().st_size, 1024)

    def test_result_receipt_prevents_second_execution(self):
        self.run_guest("pass")
        with self.assertRaisesRegex(RuntimeError, "already run"):
            self.run_guest("raise AssertionError('must not execute')")

    def test_artifact_budget_prevents_oversized_import(self):
        source, target = self.root / "artifacts", self.root / "imported"
        source.mkdir()
        (source / "too-large").write_bytes(b"x" * 10)
        with self.assertRaisesRegex(RuntimeError, "evidence budget"):
            guest.export_artifacts(source, target, 9)
        self.assertFalse((target / "too-large").exists())

    def test_timeout_terminates_and_reaps_command(self):
        processes = []

        def launch(argv, **kwargs):
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            processes.append(process)
            return process

        with (mock.patch.object(guest.platform, "validation_guest_launch", side_effect=launch),
              (self.root / "log").open("wb") as log):
            result = guest.execute([sys.executable, "-c", "import time; time.sleep(30)"], cwd=self.root,
                                   env={}, account={}, log=log, deadline=time.monotonic() + 0.05)
        self.assertEqual(result, (None, "timeout"))
        self.assertIsNotNone(processes[0].poll())


if __name__ == "__main__":
    unittest.main()
