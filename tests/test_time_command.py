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
