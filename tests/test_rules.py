"""Tests for altitude.rules: ledger parsing, section compilation, id allocation, entry rendering."""
import tempfile
import unittest
from pathlib import Path

from altitude import rules, config


class TestParseLedgerReal(unittest.TestCase):
    """parse_ledger against the real checked-in global ledger."""

    def setUp(self):
        self.path = config.RULES / "global" / "RULES.md"
        self.entries = rules.parse_ledger(self.path)

    def test_exactly_seven_entries(self):
        self.assertEqual(len(self.entries), 7)

    def test_ids_in_order(self):
        self.assertEqual([e["id"] for e in self.entries],
                          [f"R-{i:03d}" for i in range(1, 8)])

    def test_every_entry_has_all_fields_title_and_file(self):
        for e in self.entries:
            for field in rules.FIELDS:
                self.assertIn(field, e, f"{e.get('id')} missing field {field!r}")
                self.assertIsInstance(e[field], str)
                self.assertTrue(e[field], f"{e.get('id')} has empty {field!r}")
            self.assertTrue(e.get("title"))
            self.assertEqual(e["file"], str(self.path))


class TestParseLedgerMissing(unittest.TestCase):
    def test_missing_path_returns_empty_list(self):
        self.assertEqual(rules.parse_ledger(Path("/nonexistent/does/not/exist/RULES.md")), [])


class TestParseLedgerInline(unittest.TestCase):
    """parse_ledger against a small hand-written ledger covering both heading dash forms,
    a known field, an unknown field (must be dropped), and a prose line (ignored)."""

    def test_inline_ledger(self):
        content = (
            "# scratch ledger\n"
            "\n"
            "## R-001 — First Rule\n"
            "- scope: global\n"
            "- foo: bar\n"
            "Some prose line that should be ignored.\n"
            "\n"
            "## S-002 - Second Rule\n"
            "- where: personas/l1.md\n"
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "RULES.md"
            path.write_text(content)
            entries = rules.parse_ledger(path)

        self.assertEqual(len(entries), 2)

        first, second = entries
        self.assertEqual(first["id"], "R-001")
        self.assertEqual(first["title"], "First Rule")
        self.assertEqual(first["scope"], "global")
        self.assertNotIn("foo", first)

        self.assertEqual(second["id"], "S-002")
        self.assertEqual(second["title"], "Second Rule")
        self.assertEqual(second["where"], "personas/l1.md")


class TestCompileSection(unittest.TestCase):
    def _rules(self):
        return [
            {"id": "R-001", "text": "text one", "status": "active"},
            {"id": "R-002", "text": "text two", "status": "probation"},
            {"id": "R-003", "text": "text three", "status": "retired"},
            {"id": "R-004", "text": "text four"},  # no status key -> treated as active
            {"id": "R-005", "text": "", "status": "active"},  # empty text -> excluded
        ]

    def test_compiled_section_shape(self):
        out = rules.compile_section(self._rules(), "My Heading")
        lines = out.split("\n")
        self.assertEqual(lines[0], "## My Heading")
        self.assertEqual(lines[1], "")
        self.assertIn("- [R-001] text one", out)
        self.assertIn("- [R-002] text two", out)
        self.assertIn("- [R-004] text four", out)
        self.assertNotIn("R-003", out)
        self.assertNotIn("R-005", out)
        self.assertTrue(out.endswith("\n"))

    def test_empty_rules_list(self):
        self.assertEqual(rules.compile_section([], "x"), "")

    def test_only_retired_rule(self):
        only_retired = [{"id": "R-003", "text": "text three", "status": "retired"}]
        self.assertEqual(rules.compile_section(only_retired, "x"), "")


class TestNextId(unittest.TestCase):
    def test_real_global_ledger(self):
        self.assertEqual(rules.next_id(config.RULES / "global" / "RULES.md"), "R-008")

    def test_missing_path(self):
        self.assertEqual(rules.next_id(Path("/nonexistent/does/not/exist/RULES.md")), "R-001")

    def test_temp_ledger_default_and_custom_prefix(self):
        content = (
            "## R-002 — Some Rule\n"
            "- text: hi\n"
            "\n"
            "## S-005 — Another Rule\n"
            "- text: hi\n"
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "RULES.md"
            path.write_text(content)
            self.assertEqual(rules.next_id(path), "R-003")
            self.assertEqual(rules.next_id(path, prefix="S"), "S-006")


class TestRenderEntryRoundTrip(unittest.TestCase):
    def test_round_trip(self):
        fields = dict(
            scope="global",
            where="personas/l1.md",
            origin="design 2026-08-29",
            prevents="something bad",
            effect="something good",
            status="probation",
            text="Do the thing carefully.",
        )
        rendered = rules.render_entry("R-042", "some title", **fields)

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "RULES.md"
            path.write_text(rendered)
            entries = rules.parse_ledger(path)

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["id"], "R-042")
        self.assertEqual(entry["title"], "some title")
        for k, v in fields.items():
            self.assertEqual(entry[k], v)

    def test_none_field_omitted(self):
        rendered = rules.render_entry(
            "R-042", "some title",
            scope="global", where="w", origin="o", prevents="p",
            effect=None, status="probation", text="t",
        )
        self.assertNotIn("effect", rendered)

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "RULES.md"
            path.write_text(rendered)
            entries = rules.parse_ledger(path)

        self.assertEqual(len(entries), 1)
        self.assertNotIn("effect", entries[0])

    def test_rendered_text_shape(self):
        rendered = rules.render_entry("R-042", "some title", text="t")
        self.assertTrue(rendered.startswith("## R-042 — some title"))
        self.assertTrue(rendered.endswith("\n"))
        self.assertFalse(rendered.endswith("\n\n"))


if __name__ == "__main__":
    unittest.main()
