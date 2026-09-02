"""The dormant prompt derivation retains the exact host and role constraints."""
import os
import tempfile
import unittest

os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-helper-prompt-")

from altitude import l1


class TestHelperPrompt(unittest.TestCase):
    def test_implementer_prompt_has_one_host_note_and_bounded_footer(self):
        prompt = l1._prompt("implementer", "change one thing", ["altitude/l1.py"])  # noqa: SLF001
        self.assertEqual(prompt.count(l1.CODEX_PATCH_NOTE), 1)
        self.assertIn("Do not commit, open a PR, or merge", prompt)
        self.assertTrue(prompt.endswith("Your sublease is: ['altitude/l1.py']."))

    def test_reviewer_prompt_is_read_only_and_has_no_sublease(self):
        prompt = l1._prompt("reviewer", "inspect it", [])  # noqa: SLF001
        self.assertEqual(prompt.count(l1.CODEX_PATCH_NOTE), 1)
        self.assertIn("Do not edit, open a PR, or merge", prompt)
        self.assertNotIn("Your sublease", prompt)


if __name__ == "__main__":
    unittest.main()
