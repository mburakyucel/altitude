"""Persona activation through real dispatch/turn construction, with no provider or service execution."""
import io
import json
import subprocess
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, make_repo
from altitude import config, dispatch, engines, l3, state as S, tasks as T


class _BytesInput(io.BytesIO):
    def close(self):
        pass  # Keep the submitted prompt inspectable after the adapter closes stdin.


class _TextInput(io.StringIO):
    def close(self):
        pass


class _ProviderProcess:
    """Capture the provider boundary and return only deterministic session initialization/output."""

    def __init__(self, command, kwargs, session):
        self.command, self.session = command, session
        self.pid, self.returncode, self.alive = 4242, 0, True
        self.persona_path = (Path(command[command.index("--append-system-prompt-file") + 1])
                             if "--append-system-prompt-file" in command else None)
        self.persona = self.persona_path.read_text() if self.persona_path else None
        self.synchronous = kwargs.get("text", False)
        self.stdin = _TextInput() if self.synchronous else _BytesInput()
        if config.CLAUDE_BIN in command:
            events = [{"type": "system", "subtype": "init", "session_id": session},
                      {"type": "result", "session_id": session, "result": "Fixture answer", "usage": {}}]
        else:
            events = [{"type": "thread.started", "thread_id": session},
                      {"type": "item.completed", "item": {"type": "agent_message", "text": "Fixture answer"}},
                      {"type": "turn.completed", "usage": {}}]
        self.output = "".join(json.dumps(event) + "\n" for event in events)
        if self.synchronous:
            self.stdout, self.stderr = io.StringIO(self.output), io.StringIO("")
        else:
            kwargs["stdout"].write(self.output.encode())

    @property
    def prompt(self):
        text = self.stdin.getvalue()
        return text if isinstance(text, str) else text.decode()

    def communicate(self, text, timeout=None):
        self.stdin.write(text)
        self.alive = False
        return self.output, ""

    def poll(self):
        return None if self.alive else self.returncode

    def wait(self, timeout=None):
        self.alive = False
        return self.returncode

    def kill(self):
        self.alive = False


class PersonaLoading(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        make_repo(self.repo)
        self.personas = self.tmp / "personas"
        self.personas.mkdir()
        for role in ("l2", "l3"):
            (self.personas / f"{role}.md").write_text((config.PERSONAS / f"{role}.md").read_text())
        self.patch(config, "PERSONAS", self.personas)
        self.patch(engines, "_codex_processes", {})
        self.patch(engines, "_unit_active", return_value=False)
        self.patch(engines, "window_hold", return_value=None)
        self.processes = []
        real_popen = subprocess.Popen

        def popen(command, **kwargs):
            if config.CLAUDE_BIN not in command and config.CODEX_BIN not in command:
                return real_popen(command, **kwargs)  # Real local Git fixtures still validate task provenance.
            session = (command[command.index("--resume") + 1] if "--resume" in command else
                       command[-2] if "resume" in command else f"fixture-session-{len(self.processes)}")
            process = _ProviderProcess(command, kwargs, session)
            self.processes.append(process)
            return process

        self.patch(engines.subprocess, "Popen", side_effect=popen)

    def assert_persona(self, process, engine, role, text, *, resumed):
        if engine == "claude":
            self.assertEqual(process.persona_path, self.personas / f"{role}.md")
            self.assertEqual(process.persona, text, "the current file is supplied at this invocation")
            self.assertEqual("--resume" in process.command, resumed)
        else:
            self.assertIsNone(process.persona_path)
            self.assertEqual("resume" in process.command, resumed)
            if resumed:
                self.assertNotIn(text, process.prompt, "existing threads do not reload the persona")
                self.assertNotIn("FIXTURE PERSONA UPDATE", process.prompt)
            else:
                self.assertTrue(process.prompt.startswith(text + "\n\n"), "fresh threads get the current persona")

    def test_l2_dispatch_and_resume_use_authoritative_persona_with_explicit_activation_boundary(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine), mock.patch.object(dispatch.route, "pick_engine",
                    return_value={"engine": engine, "why": "fixture routing"}):
                persona = self.personas / "l2.md"
                original = persona.read_text()
                task = T.new(self.project, f"Persona {engine}", "Inspect the fictional task.")
                slug = task["slug"]
                dispatch.run(self.project, slug)
                initial = self.processes[-1]
                self.assert_persona(initial, engine, "l2", original, resumed=False)

                updated = original + "\nFIXTURE PERSONA UPDATE: current owner instructions.\n"
                persona.write_text(updated)
                initial.alive = False
                T.block(self.project, slug, "Wait for task context.", actor="l2", updates={"waiting_on": "l3"})
                T.message(self.project, slug, "l3", "Continue the existing task with this sourced context.")
                dispatch.resume(self.project, slug)
                resumed = self.processes[-1]
                self.assert_persona(resumed, engine, "l2", updated, resumed=True)
                self.assertEqual(resumed.session, initial.session)
                self.assertIn("Continue the existing task with this sourced context.", resumed.prompt)
                self.assertEqual(S.load_task(self.project, slug)["attempt"], 1)

                next_task = T.new(self.project, f"Current persona {engine}", "Inspect another fictional task.")
                dispatch.run(self.project, next_task["slug"])
                self.assert_persona(self.processes[-1], engine, "l2", updated, resumed=False)
                self.assertNotEqual(self.processes[-1].session, initial.session)
                persona.write_text(original)

    def test_l3_turn_resume_and_fresh_rotation_read_persona_at_the_supported_boundary(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine), mock.patch.object(l3, "_select",
                    return_value={"engine": engine, "why": "fixture routing"}):
                persona = self.personas / "l3.md"
                original = persona.read_text()
                self.assertTrue(l3.turn(self.project, "Inspect project direction.")["completed"])
                initial = self.processes[-1]
                self.assert_persona(initial, engine, "l3", original, resumed=False)

                updated = original + "\nFIXTURE PERSONA UPDATE: current coordinator instructions.\n"
                persona.write_text(updated)
                self.assertTrue(l3.turn(self.project, "Discuss another project detail.")["completed"])
                resumed = self.processes[-1]
                self.assert_persona(resumed, engine, "l3", updated, resumed=True)
                self.assertEqual(resumed.session, initial.session)

                info = l3.info(self.project)
                info["sessions"][engine]["context_percent"] = 100
                l3.save_info(self.project, info)
                self.assertTrue(l3.turn(self.project, "Continue after context rotation.")["completed"])
                self.assert_persona(self.processes[-1], engine, "l3", updated, resumed=False)
                self.assertNotEqual(self.processes[-1].session, initial.session)
                persona.write_text(original)
