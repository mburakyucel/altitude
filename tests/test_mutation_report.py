"""The mutation run: its report groups mutmut's verdicts by module and function, on fictional `mutmut results`
output, and it stops only the processes of its own Python that lost their place below mutmut, on real ones."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from scripts import mutation

RESULTS = """\
    altitude.door.x__bounded__mutmut_1: killed
    altitude.door.x__bounded__mutmut_2: survived
    altitude.door.x__bounded__mutmut_3: survived
    altitude.door.x_decode__mutmut_1: no tests
    altitude.land.xǁLandingǁmerge__mutmut_4: survived
    altitude.land.x_wait__mutmut_1: timeout
    altitude.tasks.x__move__mutmut_1: killed
    altitude.tasks.x_new__mutmut_1: not checked
    altitude.engines.x_run__mutmut_1: survived
"""


class TestMutationReport(unittest.TestCase):
    def test_the_selection_names_existing_files(self):
        # A renamed module or test file would otherwise surface only when someone next starts a run.
        repo = Path(mutation.__file__).resolve().parents[1]
        for path in [*mutation.MODULES, *mutation.TESTS]:
            self.assertTrue((repo / path).is_file(), path)

    def test_functions_are_named_as_written(self):
        self.assertEqual(mutation.function_of("altitude.door.x__bounded__mutmut_2"), "_bounded")
        self.assertEqual(mutation.function_of("altitude.land.xǁLandingǁmerge__mutmut_4"), "Landing.merge")

    def test_only_the_selected_functions_are_counted(self):
        found = mutation.classify(RESULTS)
        self.assertEqual(found["altitude/door.py"], {
            "killed": ["altitude.door.x__bounded__mutmut_1"],
            "survived": ["altitude.door.x__bounded__mutmut_2", "altitude.door.x__bounded__mutmut_3"],
            "no tests": ["altitude.door.x_decode__mutmut_1"]})
        # Of tasks.py only the transitions run; mutants of other modules are never reported.
        self.assertEqual(found["altitude/tasks.py"], {"killed": ["altitude.tasks.x__move__mutmut_1"]})
        self.assertEqual(found["altitude/state.py"], {})
        self.assertNotIn("altitude/engines.py", found)

    def test_the_report_lists_counts_then_survivors_by_function(self):
        diffs = {"altitude.door.x__bounded__mutmut_2": "-a\n+b\n", "altitude.door.x__bounded__mutmut_3": "-c\n+d\n",
                 "altitude.land.xǁLandingǁmerge__mutmut_4": "-e\n+f\n"}
        report = mutation.render(mutation.classify(RESULTS), diffs, "# Mutation run")
        self.assertIn("| `altitude/door.py` | 4 | 1 | 2 | 1 | 0 | 0 |", report)
        self.assertIn("| `altitude/land.py` | 2 | 0 | 1 | 0 | 1 | 0 |", report)
        self.assertIn("### `_bounded`: 2 survived\n\n```diff\n-a\n+b\n```\n\n```diff\n-c\n+d\n```", report)
        self.assertIn("No selected test reaches: `decode` (1).", report)
        self.assertIn("### `Landing.merge`: 1 survived", report)
        self.assertIn("Timed out or otherwise unsettled: `altitude.land.x_wait__mutmut_1`.", report)
        self.assertIn("## `altitude/tasks.py`\n\nEvery mutant was killed.", report)
        self.assertIn("## `altitude/state.py`\n\nNo mutants.", report)


class TestStrays(unittest.TestCase):
    def test_only_orphans_of_the_scratch_python_are_strays(self):
        with tempfile.TemporaryDirectory() as folder:
            venv = Path(folder) / "venv"
            (venv / "bin").mkdir(parents=True)
            python = venv / "bin" / "python"
            python.symlink_to(sys.executable)
            sleep = "import time; time.sleep(60)"
            spawn = (f"import subprocess, sys; print(subprocess.Popen([sys.argv[1], '-c', {sleep!r}], "
                     "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid, flush=True)")
            # mutmut's stand-in keeps one child below it; another child's parent exits and leaves it orphaned.
            root = subprocess.Popen([python, "-c", f"{spawn}; import time; time.sleep(60)", python],
                                    stdout=subprocess.PIPE, text=True)
            below = int(root.stdout.readline())
            orphan = int(subprocess.run([python, "-c", spawn, python], check=True, capture_output=True,
                                        text=True).stdout)
            # The same interpreter started from elsewhere is not the scratch environment's.
            elsewhere = subprocess.Popen([sys.executable, "-c", sleep])
            try:
                deadline = time.monotonic() + 10
                while mutation.strays(root.pid, venv) != {orphan} and time.monotonic() < deadline:
                    time.sleep(0.1)
                self.assertEqual(mutation.strays(root.pid, venv), {orphan})
                mutation.stop({orphan})
                deadline = time.monotonic() + 10
                while mutation.strays(root.pid, venv) and time.monotonic() < deadline:
                    time.sleep(0.1)
                self.assertEqual(mutation.strays(root.pid, venv), set())
                self.assertIsNone(elsewhere.poll())
            finally:
                mutation.stop({below, orphan, root.pid, elsewhere.pid})
                root.wait()
                elsewhere.wait()


if __name__ == "__main__":
    unittest.main()
