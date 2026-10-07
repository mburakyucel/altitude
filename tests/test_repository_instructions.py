"""Rule discovery regression: both roles/engines must reach project rules from fresh and resumed turns.

These fixtures inspect real prompt construction and native import paths, not provider adherence.
"""
import io
import json
from unittest import mock

from tests.support import AltitudeCase, REPO, git
from altitude import config, dispatch, engines, l3, platform


class _Input(io.BytesIO):
    def close(self):
        pass


class TestRepositoryInstructions(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        git("init", "-q", cwd=self.repo)
        self.patch(engines, "_codex_processes", {})

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

    def test_l2_fresh_and_resume_pass_rules_to_both_native_clis(self):
        for kind in ("shared", "agents-only", "legacy", "absent"):
            rules = self.layout(kind)
            before = {p.name: p.read_bytes() for p in self.repo.iterdir() if p.is_file()}
            for engine in config.ENGINES:
                for resume in (False, True):
                    with self.subTest(kind=kind, engine=engine, resume=resume):
                        processes = []
                        real_popen = engines.subprocess.Popen

                        def execute(cmd, **kwargs):
                            if cmd[0] == "git":
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
                        with mock.patch.object(engines, "claude_agents", return_value=[]), \
                             mock.patch.object(platform, "job_active", return_value=True), \
                             mock.patch.object(engines.subprocess, "Popen", side_effect=execute):
                            if resume:
                                result = engines.resume_l2(engine, "fixture", "session", "Continue task.", **kwargs)
                            else:
                                result = engines.start_l2(engine, "fixture", "Start task.", **kwargs)
                        self.assertEqual(result["returncode"], 0, result)
                        self.assertEqual(result["agent"]["sessionId"], "session")
                        prompt = processes[0].stdin.getvalue().decode()
                        self.assert_rules(prompt, rules)
                        # #441: every fresh/resumed owner learns where the browser keeps its sandbox, even without
                        # project rules.
                        self.assertEqual(prompt.count(engines.BROWSER_VERIFICATION_NOTE), 1)
                        for requirement in ("alt task validate", "chromiumSandbox:true", "Never disable either sandbox",
                                            "chmod/chown a SUID helper", "block with --fault",
                                            "Explicitly authorized native runtime", "actual fresh intended confined",
                                            "effective role policy", "operating-system detection",
                                            "outside-worker browser run"):
                            self.assertIn(requirement, prompt)
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
