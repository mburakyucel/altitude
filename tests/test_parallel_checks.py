"""Parallel checks retain test outcomes and wait for both required phases."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import textwrap
import time
import urllib.request

from tests.support import AltitudeCase, REPO
from altitude.land import _test_counts


class TestParallelChecks(AltitudeCase):
    def runner(self, *sources):
        directory = self.tmp / "modules"
        directory.mkdir()
        for index, source in enumerate(sources):
            (directory / f"test_{index}.py").write_text(textwrap.dedent(source))
        return subprocess.run(
            [sys.executable, str(REPO / "tests/run_parallel.py"), "--workers", "2",
             "--directory", str(directory)], capture_output=True, text=True, timeout=20)

    def test_skips_totals_and_process_isolation(self):
        source = '''
            import os, socket, time, unittest
            from pathlib import Path
            from tests.support import SUITE
            class Cases(unittest.TestCase):
                def test_isolated(self):
                    with socket.socket() as server:
                        server.bind(("127.0.0.1", 0))
                        Path(__file__).with_suffix(".ready").touch()
                        deadline = time.monotonic() + 5
                        while len(list(Path(__file__).parent.glob("*.ready"))) < 2:
                            self.assertLess(time.monotonic(), deadline)
                            time.sleep(.01)
                        print("ISOLATED", os.getpid(), SUITE, server.getsockname()[1], flush=True)
                @unittest.skip("retained skip reason")
                def test_skip(self): pass
                @unittest.expectedFailure
                def test_expected(self): self.fail("expected")
        '''
        result = self.runner(source, source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_test_counts(result.stdout + result.stderr), (2, 2, 2))
        self.assertEqual(result.stderr.count("skipped 'retained skip reason'"), 2)
        records = [line.split() for line in result.stdout.splitlines() if line.startswith("ISOLATED")]
        self.assertEqual(len({row[1] for row in records}), 2)
        self.assertEqual(len({row[2] for row in records}), 2)
        self.assertEqual(len({row[3] for row in records}), 2)

    def test_failure_and_import_error_propagate(self):
        result = self.runner('''
            import unittest
            class Cases(unittest.TestCase):
                def test_failure(self): self.fail("visible failure")
        ''', 'raise RuntimeError("visible import error")')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("visible failure", result.stderr)
        self.assertIn("visible import error", result.stderr)
        self.assertIn("FAILED (failures=1, errors=1", result.stderr)

    def test_abrupt_worker_exit_fails(self):
        result = self.runner('''
            import os, unittest
            class Cases(unittest.TestCase):
                def test_exits(self): os._exit(7)
        ''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test_exits (test_0.Cases.test_exits)", result.stderr)

    def test_browser_service_shutdown_finishes_request_thread_startup(self):
        script = textwrap.dedent('''
            import os, signal, sys, threading
            from http.server import BaseHTTPRequestHandler
            sys.path.insert(0, "web/e2e")
            from service_support import serve
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"complete")
                def log_message(self, *args): pass
            original_start = threading.Thread.start
            def interrupt_start(thread):
                # ThreadingMixIn has registered this thread but has not started it yet.
                if getattr(thread._target, "__name__", "") == "process_request_thread":
                    os.kill(os.getpid(), signal.SIGTERM)
                return original_start(thread)
            threading.Thread.start = interrupt_start
            serve(Handler)
        ''')
        process = subprocess.Popen([sys.executable, "-c", script], cwd=REPO,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(ready.select(timeout=5), "Disposable service did not start")
            address = json.loads(process.stdout.readline())
            with urllib.request.urlopen(address["url"], timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"complete")
            _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stderr, "")
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_make_overlaps_phases_preserves_order_and_propagates_each_failure(self):
        (self.tmp / "Makefile").write_text((REPO / "Makefile").read_text())
        (self.tmp / "web").mkdir()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "node").write_text("#!/bin/sh\necho 2\n")
        (bindir / "node").chmod(0o755)
        shim = f"#!{sys.executable}\n" + textwrap.dedent('''
            import os, pathlib, sys, time
            root = pathlib.Path(os.environ["CHECK_FIXTURE"])
            phase = "python" if pathlib.Path(sys.argv[0]).name == "python3" else sys.argv[1]
            (root / phase).touch()
            if phase == "python":
                assert sys.argv[-2:] == ["--workers", "2"]
                deadline = time.monotonic() + 5
                while not (root / "test").exists():
                    if time.monotonic() > deadline: sys.exit(99)
                    time.sleep(.01)
            if phase == "build": assert (root / "test").exists()
            if phase == "ui": assert (root / "build").exists()
            sys.exit(int(phase == os.environ["FAIL_PHASE"]))
        ''')
        for command in ("python3", "pnpm"):
            path = bindir / command
            path.write_text(shim)
            path.chmod(0o755)
        for failed in ("", "python", "test", "build", "ui"):
            with self.subTest(failed=failed):
                for phase in ("python", "test", "build", "ui"):
                    (self.tmp / phase).unlink(missing_ok=True)
                result = subprocess.run(
                    ["make", "check"], cwd=self.tmp,
                    env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                         "CHECK_FIXTURE": str(self.tmp), "FAIL_PHASE": failed},
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode == 0, not failed, result.stderr)
                self.assertTrue((self.tmp / "python").exists())
                self.assertTrue((self.tmp / "test").exists())
                self.assertEqual((self.tmp / "build").exists(), failed != "test")
                self.assertEqual((self.tmp / "ui").exists(), failed not in ("test", "build"))
                self.assertIn("real ", result.stderr)

    def test_interrupt_stops_both_make_branches(self):
        (self.tmp / "Makefile").write_text((REPO / "Makefile").read_text())
        (self.tmp / "web").mkdir()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "node").write_text("#!/bin/sh\necho 2\n")
        (bindir / "node").chmod(0o755)
        for command in ("python3", "pnpm"):
            path = bindir / command
            path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(f'''
                import pathlib, time
                root = pathlib.Path({str(self.tmp)!r})
                (root / "{command}.started").touch()
                try:
                    time.sleep(30)
                except KeyboardInterrupt:
                    (root / "{command}.stopped").touch()
            '''))
            path.chmod(0o755)
        process = subprocess.Popen(
            ["make", "check"], cwd=self.tmp, start_new_session=True,
            env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while len(list(self.tmp.glob("*.started"))) < 2:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            os.killpg(process.pid, signal.SIGINT)
            process.communicate(timeout=5)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(len(list(self.tmp.glob("*.stopped"))), 2)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=5)
