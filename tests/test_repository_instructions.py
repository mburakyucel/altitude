"""Rule discovery regression: both roles/engines must reach project rules from fresh and resumed turns.

These fixtures inspect real prompt construction and native import paths, not provider adherence.
"""
import io
import json
import unittest
from unittest import mock

from tests.support import AltitudeCase, REPO, git
from altitude import config, dispatch, engines, l3, platform


class _Input(io.BytesIO):
    def close(self):
        pass


class TestRepositoryInstructions(AltitudeCase):
    host = "linux"  # systemd fixtures

    def setUp(self):
        super().setUp()
        self.private_ledgers()
        git("init", "-q", cwd=self.repo)
        self.patch(engines.common, "_worker_processes", {})

    def layout(self, kind):
        for name in ("AGENTS.md", "CLAUDE.md"):
            (self.repo / name).unlink(missing_ok=True)
        name = "CLAUDE.md" if kind == "legacy" else "AGENTS.md"
        if kind != "absent":
            (self.repo / name).write_text("# Fictional project\n- Never publish fixture records.\n")
        if kind == "shared":
            (self.repo / "CLAUDE.md").write_text("@AGENTS.md\n")
        return self.repo / name if kind != "absent" else None

    def assert_rules(self, prompt, rules):
        if rules:
            self.assertIn(f"Before proceeding, read the repository rules at `{rules}`", prompt)
            self.assertIn("references/imports", prompt)
            self.assertIn("applicable directory instructions", prompt)
        else:
            self.assertNotIn("[altitude] Before proceeding", prompt)
        self.assertNotIn(str(REPO / "AGENTS.md"), prompt)
        self.assertNotIn("Never publish fixture records", prompt, "rules remain in the repository")

    def assert_resume(self, prompt, rules):
        # Session analysis of 2026-10-13: owners kept every resume's full rule, browser and helper notes through
        # compaction and reread the rules each time. A resume carries one line pointing back to current paths.
        line, _ = prompt.split("\n\n", 1)
        self.assertTrue(line.startswith("[altitude] Resumed session: "))
        self.assertEqual(prompt.count("[altitude]"), 1)
        self.assertNotIn("\n", line)
        if rules:
            self.assertIn(f"the repository rules at `{rules}`", line)
        else:
            self.assertNotIn("repository rules", line)
        self.assertIn(f"your role at `{config.PERSONAS / 'l2.md'}`", line)
        self.assertIn(f"`{config.PERSONAS / 'l1.md'}`", line)
        self.assertIn("instead of rereading them in full", line)
        self.assertIn("browser verification rules before a browser check", line)

    def test_resume_text_is_one_line_naming_current_rules_role_and_helper_persona(self):
        rules = self.layout("shared")
        persona, helper = config.PERSONAS / "l2.md", config.PERSONAS / "l1.md"
        tail = ("Consult these as needed instead of rereading them in full, and reread any part no longer in your "
                "context, such as the role's browser verification rules before a browser check.\n\n")
        self.assertEqual(engines.common.owner_prompt(self.repo, persona, resume=True), (
            f"[altitude] Resumed session: the repository rules at `{rules}` and your role at `{persona}` still "
            f"apply, and native helper assignments now name `{helper}`. " + tail))
        self.layout("absent")
        self.assertEqual(engines.common.owner_prompt(self.repo, persona, resume=True), (
            f"[altitude] Resumed session: your role at `{persona}` still applies, and native helper assignments "
            f"now name `{helper}`. " + tail))

    def test_owner_persona_carries_the_browser_and_helper_rules(self):
        # #441: every owner learns where the browser keeps its sandbox, even without project rules; the persona
        # reaches fresh and resumed owners without a per-launch note.
        persona = " ".join((config.PERSONAS / "l2.md").read_text().split())
        for requirement in ("`alt task validate`", "chromiumSandbox:true", "Keep worker/runner confinement",
                            "no ad hoc sandbox-bypass flags", "no chmod/chown of a SUID helper",
                            "fails its required preflight, fault with the evidence",
                            "explicitly approved project's local-fictional harness", "Mac validation Seatbelt runner",
                            "not native-worker, deployed/private-content", "Record actual runner identity",
                            "outside-worker browser run", "`launchOptions` or sandbox flags",
                            "You are an L1 helper. Read `<l1.md>` before working."):
            self.assertIn(requirement, persona)

    def test_l2_fresh_and_resume_pass_rules_to_both_native_clis(self):
        for kind in ("shared", "agents-only", "legacy", "absent"):
            rules = self.layout(kind)
            before = {p.name: p.read_bytes() for p in self.repo.iterdir() if p.is_file()}
            for engine in config.ENGINES:
                for resume in (False, True):
                    with self.subTest(kind=kind, engine=engine, resume=resume):
                        processes = []
                        real_popen = engines.common.subprocess.Popen

                        def execute(cmd, **kwargs):
                            if cmd[-len(engines.common._driver_command()):] != engines.common._driver_command():
                                return real_popen(cmd, **kwargs)
                            event = ({"type": "thread.started", "thread_id": "session"} if engine == "codex"
                                     else {"type": "system", "subtype": "init", "session_id": "session"})
                            kwargs["stdout"].write((json.dumps(event) + "\n").encode())
                            proc = mock.Mock(pid=4242, stdin=_Input())
                            proc.poll.return_value = None
                            processes.append(proc)
                            self.assertEqual(kwargs["cwd"], str(self.repo))
                            return proc

                        kwargs = dict(cwd=self.repo, persona=config.PERSONAS / "l2.md", model=None,
                                      settings=self.tmp / "settings.json", job_root=self.tmp / "jobs",
                                      extra_env={"ALTITUDE_TASK": "fixture"})
                        with mock.patch.object(engines.claude, "claude_agents", return_value=[]), \
                             mock.patch.object(platform, "job_active", return_value=True), \
                             mock.patch.object(engines.common.subprocess, "Popen", side_effect=execute):
                            if resume:
                                result = engines.resume_l2(engine, "fixture", "session", "Continue task.", **kwargs)
                            else:
                                result = engines.start_l2(engine, "fixture", "Start task.", **kwargs)
                        self.assertEqual(result["returncode"], 0, result)
                        self.assertEqual(result["agent"]["sessionId"], "session")
                        spec = json.loads(processes[0].stdin.getvalue())
                        self.assertEqual((spec["engine"], spec["cwd"]), (engine, str(self.repo)))
                        prompt = spec["input"][0]["text"]
                        if resume:
                            self.assert_resume(prompt, rules)
                        else:
                            self.assert_rules(prompt, rules)
                            self.assertIn(f'You are an L1 helper. Read `{config.PERSONAS / "l1.md"}`', prompt)
                        self.assertTrue(prompt.endswith("Continue task." if resume else "Start task."))
            self.assertEqual(before, {p.name: p.read_bytes() for p in self.repo.iterdir() if p.is_file()})

    def test_l3_fresh_and_resume_read_checkout_rules_from_scratch_for_both_engines(self):
        for kind in ("shared", "agents-only", "legacy", "absent"):
            rules = self.layout(kind)
            for engine in config.ENGINES:
                project = f"{kind}-{engine}"
                self.register(project)
                seam = "codex_exec" if engine == "codex" else "claude_print"
                for resume in (False, True):
                    with self.subTest(kind=kind, engine=engine, resume=resume):
                        def execute(prompt, **kwargs):
                            self.assert_rules(prompt, rules)
                            self.assertNotEqual(kwargs["cwd"], self.repo)
                            self.assertFalse(self.repo in kwargs["cwd"].parents)
                            self.assertEqual(kwargs["resume"], "session" if resume else None)
                            self.assertTrue(prompt.endswith("Current request."))
                            return {"text": "Fixture answer", "session_id": "session", "usage": {}}

                        with mock.patch.object(l3, "_select", return_value={"engine": engine, "why": "fixture"}), \
                             mock.patch.object(engines, seam, side_effect=execute) as launch:
                            result = l3.turn(project, "Current request.", trigger="restart" if resume else "chat")
                        self.assertTrue(result["completed"], result)
                        launch.assert_called_once()

    def test_shared_native_import_resolves_and_boundaries_come_from_authoritative_rules(self):
        reference = (REPO / "CLAUDE.md").read_text().strip()
        self.assertEqual(reference, "@AGENTS.md")
        target = REPO / reference[1:]
        self.assertTrue(target.is_file())
        self.assertIn("## Boundaries", target.read_text())
        for kind in ("shared", "agents-only", "legacy"):
            with self.subTest(kind=kind):
                rules = self.layout(kind)
                self.assertEqual(engines.repository_rules(self.repo), rules)
        self.layout("absent")
        self.assertIsNone(engines.repository_rules(self.repo))

    def test_next_turn_resolves_migrated_rules_instead_of_caching_the_legacy_path(self):
        legacy = self.layout("legacy")
        self.assertIn(str(legacy), l3._header(self.project, "chat", False))
        shared = self.layout("shared")
        self.assertIn(str(shared), l3._header(self.project, "chat", False))
        self.assertNotIn(str(legacy), engines.repository_rule_prompt(self.repo))


class TestBrowserSpecs(unittest.TestCase):
    def test_e2e_specs_leave_launch_and_sandbox_options_to_the_shared_configs(self):
        # PR #702: a spec's own chromiumSandbox override failed the required check's harness launch.
        files = sorted((REPO / "web" / "e2e").rglob("*.ts"))
        self.assertTrue(files)
        for path in files:
            text = path.read_text(encoding="utf-8")
            for option in ("launchOptions", "chromiumSandbox"):
                self.assertNotIn(option, text, f"{path.relative_to(REPO)}: web/playwright*.config.ts owns {option}")
