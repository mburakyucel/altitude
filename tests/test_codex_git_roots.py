"""Codex L2 gets no privileged writable roots; host broker owns Git and state."""
import unittest
from pathlib import Path

from altitude import dispatch


class TestCodexGitRoots(unittest.TestCase):
    def test_no_state_or_git_writable_roots_and_no_network(self):
        values = dispatch._codex_extra_config(Path("/task-worktree"), "demo", "safe")
        joined = "\n".join(values)
        # Network denial is mandatory in engines' generated 0.151 permission
        # profile.  A legacy sandbox override would disable that profile.
        self.assertNotIn("network_access", joined)
        self.assertNotIn("sandbox_", joined)
        self.assertNotIn("writable_roots", joined)
        self.assertIn("codex_guard", joined)
        self.assertIn("codex_l2_cap", joined)


if __name__ == "__main__":
    unittest.main()
