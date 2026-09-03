"""The dispatch brief template renders completely with build_brief's keyword set."""
import re
import string
import unittest

from tests.support import AltitudeCase
from altitude import config, dispatch, tasks

EXPECTED_FIELDS = {
    "branch",
    "completion_contract",
    "conversation_contract",
    "engine",
    "leases",
    "merge_policy",
    "model",
    "never_list",
    "paths",
    "project",
    "request",
    "repo",
    "publication_contract",
    "slug",
    "task_dir",
    "title",
}
UNFORMATTED_FIELD = r"\{[A-Za-z_][A-Za-z0-9_]*\}"


class TestBriefRender(AltitudeCase):
    def setUp(self):
        super().setUp()
        default_task = tasks.new(
            self.project,
            "Brief render default policy fixture",
            "Render a dispatch brief from a safe request.",
        )
        held_task = tasks.new(
            self.project,
            "Brief render held policy fixture",
            "Render a held dispatch brief from a safe request.",
            hold_merge="requires a maintainer release",
        )
        self.default_rendered = dispatch.build_brief(self.project, default_task["slug"])
        self.held_rendered = dispatch.build_brief(self.project, held_task["slug"])

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

    def test_merge_guarantees_survive_rendering(self):
        for rendered in (self.default_rendered, self.held_rendered):
            with self.subTest(held="Held for Burak" in rendered):
                self.assertIn("never merge around it", rendered)
                self.assertIn("full local test suite on the exact merge candidate", rendered)

    def test_direct_execution_and_delegation_survive_rendering(self):
        rendered = self.default_rendered
        self.assertIn("Implement directly", rendered)
        self.assertIn("your engine's own subagents", rendered)
        self.assertIn("Direct implementation is normal", rendered)
        self.assertIn("Review is optional", rendered)
        self.assertIn("alt task reply", rendered)


if __name__ == "__main__":
    unittest.main()
