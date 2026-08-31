"""The dispatch brief template renders completely with build_brief's keyword set."""
import os
import re
import string
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-brief-render-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, tasks  # noqa: E402

PROJECT = "brief-render-template-test"
REPO = _TMP / "repo"
EXPECTED_FIELDS = {
    "approval_note",
    "branch",
    "class",
    "engine_line",
    "l1_in_flight",
    "leases",
    "max_turns",
    "merge_policy",
    "model",
    "never_list",
    "paths",
    "project",
    "proposal",
    "repo",
    "report_schema",
    "slug",
    "subagent_launches",
    "task_dir",
    "title",
    "verification",
}
UNFORMATTED_FIELD = r"\{[A-Za-z_][A-Za-z0-9_]*\}"


class TestBriefRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        REPO.mkdir(exist_ok=True)
        projects = config.load_projects()
        projects[PROJECT] = {"name": PROJECT, "path": str(REPO), "stacks": ["python"]}
        config.save_projects(projects)

        default_task = tasks.new(
            PROJECT,
            "Brief render default policy fixture",
            "S",
            "Render a dispatch brief from a safe request.",
        )
        held_task = tasks.new(
            PROJECT,
            "Brief render held policy fixture",
            "S",
            "Render a held dispatch brief from a safe request.",
            hold_merge="requires a maintainer release",
        )
        cls.default_rendered = dispatch.build_brief(PROJECT, default_task["slug"])
        cls.held_rendered = dispatch.build_brief(PROJECT, held_task["slug"])

    def assert_no_unformatted_field(self, rendered):
        self.assertIsNone(re.search(UNFORMATTED_FIELD, rendered))

    def test_template_fields_match_dispatcher_keywords(self):
        template = (config.TEMPLATES / "brief.md").read_text()
        fields = {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(template)
            if field_name is not None
        }
        self.assertEqual(
            EXPECTED_FIELDS,
            fields,
            "Adding or renaming a brief placeholder also requires updating "
            "dispatch.build_brief's kwargs.",
        )

    def test_real_render_is_nonempty_and_fully_formatted(self):
        self.assertTrue(self.default_rendered.strip())
        self.assert_no_unformatted_field(self.default_rendered)

    def test_both_merge_policy_branches_render(self):
        self.assertNotIn("Held for Burak", self.default_rendered)
        self.assertIn("Held for Burak", self.held_rendered)
        self.assertIn("requires a maintainer release", self.held_rendered)
        self.assert_no_unformatted_field(self.default_rendered)
        self.assert_no_unformatted_field(self.held_rendered)

    def test_ledger_guarantees_survive_rendering(self):
        # R-014's test-command sentence is not in the template yet; assert it here when it lands.
        for rendered in (self.default_rendered, self.held_rendered):
            with self.subTest(held="Held for Burak" in rendered):
                self.assertIn("never merge around the hold", rendered)
                self.assertIn("full local test suite on merged `main`", rendered)


if __name__ == "__main__":
    unittest.main()
