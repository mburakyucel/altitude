"""Setup instructions follow the newest release, so publishing one needs no documentation edit."""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BLOCK = re.compile(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", re.M | re.S)
TAG = re.compile(r"\bv\d+\.\d+\.\d+")


class SetupInstructionsTest(unittest.TestCase):
    def test_command_blocks_name_no_release(self):
        for name in ("README.md", "docs/SETUP.md"):
            blocks = BLOCK.findall((REPO / name).read_text())
            self.assertTrue(blocks, name)
            for block in blocks:
                self.assertNotRegex(block, TAG, f"{name} names a release; resolve the latest or write <tag>")


if __name__ == "__main__":
    unittest.main()
