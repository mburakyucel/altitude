"""The portable Make timer preserves real child output, failures and interrupts."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest


TIMER = Path(__file__).resolve().parents[1] / "scripts/time_command.py"


class TimeCommandTests(unittest.TestCase):
    def run_command(self, code):
        return subprocess.run([sys.executable, str(TIMER), sys.executable, "-c", code],
                              capture_output=True, text=True, timeout=10)

    def assert_timing(self, text):
        self.assertRegex(text, r"real [0-9]+\.[0-9]{2}\nuser [0-9]+\.[0-9]{2}\nsys [0-9]+\.[0-9]{2}\n\Z")

    def test_exit_and_streams(self):
        result = self.run_command('import sys; print("stdout"); print("stderr", file=sys.stderr); sys.exit(17)')
        self.assertEqual(result.returncode, 17)
        self.assertEqual(result.stdout, "stdout\n")
        self.assertTrue(result.stderr.startswith("stderr\n"))
        self.assert_timing(result.stderr)

    def test_signal_exit(self):
        for number in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=number):
                result = self.run_command(f'import os; os.kill(os.getpid(), {number})')
                self.assertEqual(result.returncode, -number)
                self.assert_timing(result.stderr)

    def test_terminal_interrupt_waits_for_command_and_prints_timing(self):
        command = [sys.executable, str(TIMER), sys.executable, "-u", "-c",
                   'import signal, time; signal.signal(signal.SIGINT, signal.SIG_DFL); print("ready"); time.sleep(30)']
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, start_new_session=True) as process:
            self.assertEqual(process.stdout.readline(), "ready\n")
            os.killpg(process.pid, signal.SIGINT)
            _, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, -signal.SIGINT)
        self.assert_timing(error)

    def test_missing_program(self):
        result = subprocess.run([sys.executable, str(TIMER), "/no-such-timed-program"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 127)
        self.assertIn("Cannot start timed command", result.stderr)

    def test_startup_interrupt_still_reaps_real_child(self):
        harness = '''
import importlib.util, os, signal, subprocess, sys
spec = importlib.util.spec_from_file_location("timer", sys.argv[1])
timer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timer)
real_popen = subprocess.Popen
def interrupted_popen(*args, **kwargs):
    child = real_popen(*args, **kwargs)
    os.kill(os.getpid(), signal.SIGINT)
    return child
timer.subprocess.Popen = interrupted_popen
sys.argv = [sys.argv[1], sys.executable, "-c",
            "import time, sys; time.sleep(.05); print('finished'); sys.exit(17)"]
sys.exit(timer.main())
'''
        result = subprocess.run([sys.executable, "-c", harness, str(TIMER)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(result.stdout, "finished\n")
        self.assert_timing(result.stderr)

    def test_inherited_descriptor_reaches_command(self):
        read_fd, write_fd = os.pipe()
        try:
            result = subprocess.run(
                [sys.executable, str(TIMER), sys.executable, "-c",
                 f"import os; os.write({write_fd}, b'inherited')"],
                pass_fds=(write_fd,), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            os.close(write_fd)
            write_fd = None
            self.assertEqual(os.read(read_fd, 100), b"inherited")
            self.assert_timing(result.stderr)
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)
