"""The mutation run: its report groups mutmut's verdicts by module and function, on fictional `mutmut results`
output, and it stops only the processes naming its scratch folder that lost their place below mutmut, on a
fictional process listing."""
from pathlib import Path
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


# Fictional `processes()` output in ps's layout: right-aligned ids, then the command with its spaces.
LISTING = """\
    1     0 /sbin/init
  500   480 python3 -m mutmut run
  501   500 python3 /tmp/scratch/bin/alt task status
  502   501 python3 /tmp/scratch/hooks/guard.py
  600     1 python3 /tmp/scratch/bin/alt task reply
  601   600 python3 /tmp/scratch/hooks/guard.py
  700     1 python3 -c import time; time.sleep(60)
  701   500 python3 -c import time; time.sleep(60)
  900     1
"""


class TestStrays(unittest.TestCase):
    def test_only_orphans_naming_the_scratch_folder_are_strays(self):
        # mutmut's descendants stay; an orphan naming the scratch folder and its own child are strays, while an
        # orphan running something else is not the run's.
        self.assertEqual(mutation.strays(500, Path("/tmp/scratch"), LISTING), {600, 601})

    def test_once_mutmut_ends_its_descendants_are_strays_too(self):
        # mutmut's children pass to init when it ends, as the run's final sweep finds them.
        ended = LISTING.replace("  500   480 python3 -m mutmut run\n", "").replace("  501   500", "  501     1")
        self.assertEqual(mutation.strays(500, Path("/tmp/scratch"), ended), {501, 502, 600, 601})


if __name__ == "__main__":
    unittest.main()
