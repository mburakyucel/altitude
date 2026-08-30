"""Tests for altitude.rules: ledger parsing, section compilation, id allocation, entry rendering."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from altitude import config, dispatch, improve, rules, state as S


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


class TestNextIdPending(unittest.TestCase):
    """next_id + write_pending: reserve ids for un-merged pending drafts (I-003)."""

    def test_missing_ledger_two_consecutive_allocations_get_distinct_ids(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d) / "RULES.md"
            pending = Path(d) / "rules-pending"

            rid1 = rules.next_id(ledger, pending=pending)
            self.assertEqual(rid1, "R-001")
            rules.write_pending(pending, rid1, "draft one")

            rid2 = rules.next_id(ledger, pending=pending)
            self.assertEqual(rid2, "R-002")

    def test_write_pending_does_not_clobber_reserved_draft(self):
        with tempfile.TemporaryDirectory() as d:
            pending = Path(d) / "rules-pending"
            rules.write_pending(pending, "R-001", "original")

            with self.assertRaises(FileExistsError):
                rules.write_pending(pending, "R-001", "clobber attempt")

            self.assertEqual((pending / "R-001.md").read_text(), "original")

    def test_ledger_and_pending_both_respected_prefixes_do_not_cross_contaminate(self):
        content = "## R-002 — Some Rule\n- text: hi\n"
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d) / "RULES.md"
            ledger.write_text(content)
            pending = Path(d) / "rules-pending"
            pending.mkdir()
            (pending / "R-005.md").write_text("draft")
            (pending / "S-009.md").write_text("draft")

            self.assertEqual(rules.next_id(ledger, prefix="R", pending=pending), "R-006")
            self.assertEqual(rules.next_id(ledger, prefix="S", pending=pending), "S-010")

    def test_no_pending_arg_behaves_as_before(self):
        content = "## R-002 — Some Rule\n- text: hi\n"
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d) / "RULES.md"
            ledger.write_text(content)
            self.assertEqual(rules.next_id(ledger), "R-003")
            self.assertEqual(rules.next_id(ledger, pending=None), "R-003")


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


class TestProposeRulePaths(unittest.TestCase):
    def propose(self, *, mechanism="rule", where="", scope="project"):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            home, repo, source = root / "home", root / "repo", root / "source"
            (repo / "docs").mkdir(parents=True)
            (repo / "docs" / "RULES.md").write_text("# Rules\n")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_alpha.py").write_text("")
            (repo / "tests" / "test_beta.py").write_text("")
            incident_dir = home / "demo" / "incidents"
            incident_dir.mkdir(parents=True)
            (incident_dir / "I-062.md").write_text("# I-062\n")
            projects = home / "projects.json"
            projects.write_text(json.dumps({
                "demo": {"path": str(repo), "stacks": []},
                "altitude": {"path": str(repo), "stacks": []},
            }))
            incident_index = home / "incidents.jsonl"

            with (
                patch.object(config, "ROOT", home),
                patch.object(config, "PROJECTS_FILE", projects),
                patch.object(config, "INCIDENT_INDEX", incident_index),
                patch.object(config, "REPO", source),
                patch.object(config, "RULES", source / "rules"),
                patch.object(improve, "matches_elsewhere", return_value=[{"id": "I-061"}]),
            ):
                result = improve.propose_rule(
                    "demo", incident="I-062", title="carry apply paths", text="Keep apply tasks leased.",
                    mechanism=mechanism, where=where, scope=scope, stack="python" if scope == "stack" else None,
                )
                task = S.load_task(result["target_project"], result["task"])
                expanded = dispatch.task_paths(result["target_project"], task)
        return result, task, expanded

    def test_apply_task_carries_ledger_incident_and_where_paths(self):
        _, task, _ = self.propose(
            where="CLAUDE.md; personas/l2.md section Flow; templates/brief.md, altitude/land.py",
        )

        self.assertEqual(task["paths"], [
            "docs/RULES.md",
            "docs/incidents/I-062.md",
            "CLAUDE.md",
            "personas/l2.md",
            "templates/brief.md",
            "altitude/land.py",
        ])

    def test_prose_where_still_carries_rule_mechanism_target(self):
        _, task, _ = self.propose(where="L2 persona: the stage plan and merge-policy step for L tasks")
        self.assertEqual(task["paths"], ["docs/RULES.md", "docs/incidents/I-062.md", "CLAUDE.md"])

    def test_glob_is_resolved_to_concrete_repo_paths(self):
        _, task, _ = self.propose(where="tests/test_*.py")
        self.assertEqual(task["paths"], [
            "docs/RULES.md", "docs/incidents/I-062.md", "CLAUDE.md",
            "tests/test_alpha.py", "tests/test_beta.py",
        ])

    def test_brace_group_is_preserved_for_dispatch_expansion(self):
        _, task, expanded = self.propose(where="docs/{ROLES,ARCHITECTURE}.md")
        self.assertIn("docs/{ROLES,ARCHITECTURE}.md", task["paths"])
        self.assertEqual(expanded[-2:], ["docs/ROLES.md", "docs/ARCHITECTURE.md"])

    def test_sentence_period_is_not_stored_as_part_of_path(self):
        _, task, _ = self.propose(where="CLAUDE.md and docs/ROLES.md.")
        self.assertEqual(task["paths"][-1], "docs/ROLES.md")

    def test_empty_where_adds_each_mechanism_target(self):
        expected = {
            "rule": ["CLAUDE.md"],
            "skill": [".claude/skills/"],
            "instruction": ["CLAUDE.md"],
            "incident-only": [],
        }
        for mechanism, target in expected.items():
            with self.subTest(mechanism=mechanism):
                _, task, _ = self.propose(mechanism=mechanism)
                self.assertEqual(task["paths"], ["docs/RULES.md", "docs/incidents/I-062.md", *target])

    def test_stack_and_global_ledgers_are_relative_to_altitude_repo(self):
        expected = {"stack": "rules/stacks/python/RULES.md", "global": "rules/global/RULES.md"}
        for scope, ledger in expected.items():
            with self.subTest(scope=scope):
                result, task, _ = self.propose(scope=scope)
                self.assertEqual(result["target_project"], "altitude")
                self.assertEqual(task["paths"][0], ledger)
                self.assertFalse(any(Path(path).is_absolute() for path in task["paths"]))


class TestTaskPathsCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home, self.repo = root / "home", root / "repo"
        self.home.mkdir()
        self.repo.mkdir()
        (self.home / "projects.json").write_text(json.dumps({"demo": {"path": str(self.repo), "stacks": []}}))
        self.cli = Path(__file__).resolve().parent.parent / "bin" / "alt"
        self.env = {**os.environ, "ALTITUDE_HOME": str(self.home), "ALTITUDE_PROJECT": "demo"}

    def write_task(self, slug, state, **extra):
        directory = self.home / "demo" / "tasks" / slug
        directory.mkdir(parents=True)
        task = {"slug": slug, "title": slug, "class": "S", "state": state, "paths": [], **extra}
        (directory / "status.json").write_text(json.dumps(task))
        return directory / "status.json"

    def set_paths(self, slug, paths):
        return subprocess.run([sys.executable, str(self.cli), "task", "paths", slug, paths],
                              cwd=self.repo, env=self.env, capture_output=True, text=True)

    def test_sets_paths_on_requested_task(self):
        status = self.write_task("requested-task", "requested")
        result = self.set_paths("requested-task", "altitude/improve.py, tests/test_rules.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(status.read_text())["paths"], ["altitude/improve.py", "tests/test_rules.py"])

    def test_sets_paths_on_blocked_task_without_disturbing_resume(self):
        status = self.write_task("blocked-task", "blocked", resume_after="2026-08-30T12:00:00+00:00")
        self.write_task("running-task", "running", paths=["tests/test_land.py"],
                        updated="2026-08-30T11:00:00+00:00")
        result = self.set_paths("blocked-task", "altitude/land.py,tests/test_land.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        task = json.loads(status.read_text())
        self.assertEqual(task["paths"], ["altitude/land.py", "tests/test_land.py"])
        self.assertEqual(task["resume_after"], "2026-08-30T12:00:00+00:00")
        with (
            patch.object(config, "ROOT", self.home),
            patch.object(config, "PROJECTS_FILE", self.home / "projects.json"),
            patch.object(dispatch.engines, "usage_hold", return_value=None),
        ):
            repaired = S.load_task("demo", "blocked-task")
            self.assertEqual(dispatch.task_paths("demo", repaired), ["altitude/land.py", "tests/test_land.py"])
            self.assertEqual(dispatch.wip_hold("demo", repaired),
                             "file lease: `running-task` is running on tests/test_land.py")


class TestAuditInput(unittest.TestCase):
    def test_incident_join_uses_rule_scope_and_id(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "repo"
            project_ledger = repo / "docs" / "RULES.md"
            project_ledger.parent.mkdir(parents=True)
            project_ledger.write_text(rules.render_entry(
                "R-003", "project rule", scope="project", status="active", text="project text",
            ))

            rules_root = root / "rules"
            global_ledger = rules_root / "global" / "RULES.md"
            global_ledger.parent.mkdir(parents=True)
            global_ledger.write_text(rules.render_entry(
                "R-003", "global rule", scope="global", status="active", text="global text",
            ))

            incident_index = root / "incidents.jsonl"
            incidents = [
                {"project": "demo", "id": "I-001", "scope": "project", "rule": "R-003"},
                {"project": "demo", "id": "I-002", "scope": "global", "rule": "R-003"},
            ]
            incident_index.write_text("\n".join(json.dumps(row) for row in incidents) + "\n")

            with (
                patch.object(config, "project", return_value={"path": str(repo), "stacks": []}),
                patch.object(config, "RULES", rules_root),
                patch.object(config, "INCIDENT_INDEX", incident_index),
            ):
                audited = improve.audit_input("demo")["rules"]

        by_scope = {r["scope"]: r["incidents"] for r in audited if r["id"] == "R-003"}
        self.assertEqual(by_scope, {"global": ["I-002"], "project": ["I-001"]})

    def test_uncontested_id_keeps_incidents_filed_under_another_scope(self):
        """A rule only one ledger carries keeps its origin incident after promotion, when the incident row still
        records the scope it was filed under."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "repo"
            project_ledger = repo / "docs" / "RULES.md"
            project_ledger.parent.mkdir(parents=True)
            project_ledger.write_text("# none\n")

            rules_root = root / "rules"
            global_ledger = rules_root / "global" / "RULES.md"
            global_ledger.parent.mkdir(parents=True)
            global_ledger.write_text(rules.render_entry(
                "R-007", "promoted rule", scope="global", status="active", text="global text",
            ))

            incident_index = root / "incidents.jsonl"
            incidents = [
                {"project": "demo", "id": "I-010", "scope": "project", "rule": "R-007"},
                {"project": "demo", "id": "I-011", "rule": "R-007"},
            ]
            incident_index.write_text("\n".join(json.dumps(row) for row in incidents) + "\n")

            with (
                patch.object(config, "project", return_value={"path": str(repo), "stacks": []}),
                patch.object(config, "RULES", rules_root),
                patch.object(config, "INCIDENT_INDEX", incident_index),
            ):
                audited = improve.audit_input("demo")["rules"]

        found = [r for r in audited if r["id"] == "R-007"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["incidents"], ["I-010", "I-011"])


if __name__ == "__main__":
    unittest.main()
