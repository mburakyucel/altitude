"""Codex L2 gets no privileged writable roots; host broker owns Git and state."""
import unittest
from pathlib import Path

from altitude import dispatch


class TestCodexGitRoots(unittest.TestCase):
    def test_no_state_or_git_writable_roots_and_no_network(self):
        values = dispatch._codex_extra_config(Path("/task-worktree"), "demo", "safe")
        joined = "\n".join(values)
        self.assertIn("network_access=false", joined)
        self.assertNotIn("writable_roots", joined)
        self.assertIn("codex_guard", joined)
        self.assertIn("codex_l2_cap", joined)


if __name__ == "__main__":
    unittest.main()
