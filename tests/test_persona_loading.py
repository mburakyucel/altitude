"""Persona activation through real dispatch/turn construction, with no provider or service execution."""
import io
import json
import subprocess
import threading
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, make_repo
from altitude import config, dispatch, engines, l3, platform, state as S, tasks as T


class _BytesInput(io.BytesIO):
    """A worker job's launch input; closing it hands the spec to the fake engine."""
    def __init__(self, ended):
        super().__init__()
        self.ended = ended

    def close(self):
        self.ended()  # Keep the submitted spec inspectable after the adapter closes stdin.


class _TextInput(io.StringIO):
    """Stdin a fake engine reads to its end; the spec stays inspectable after the writer closes it."""
    def __init__(self, ended):
        super().__init__()
        self.ended = ended

    def close(self):
        self.ended()


class _Output:
    """A synchronous turn's output, available once its launch input has arrived."""
    def __init__(self, process):
        self.process = process

    def __iter__(self):
        self.process.started.wait(10)
        return iter(io.StringIO(self.process.output))

    def close(self):
        pass


class _ProviderProcess:
    """Capture driver launch input or coordinator exec stdin, returning deterministic session/output."""

    def __init__(self, command, kwargs, sequence):
        self.kwargs, self.sequence = kwargs, sequence
        self.exec_command = (command[command.index(config.CODEX_BIN):] if config.CODEX_BIN in command else None)
        self.pid, self.returncode, self.alive = 4242, 0, True
        self.started = threading.Event()
        self.synchronous = kwargs.get("text", False)
        self.stdin = self.received = (_TextInput if self.synchronous else _BytesInput)(self.start)
        if self.synchronous:
            self.stdout, self.stderr = _Output(self), io.StringIO("")

    def start(self):
        if self.started.is_set():
            return
        text = self.received.getvalue()
        self.spec = None if self.exec_command else json.loads(text if isinstance(text, str) else text.decode())
        command = self.command = self.exec_command or self.spec["command"]
        self.persona_path = (Path(command[command.index("--append-system-prompt-file") + 1])
                             if "--append-system-prompt-file" in command else None)
        self.persona = self.persona_path.read_text() if self.persona_path else None
        if command[0] == config.CLAUDE_BIN:
            session = self.session = (command[command.index("--resume") + 1] if "--resume" in command
                                      else f"fixture-session-{self.sequence}")
            events = [{"type": "system", "subtype": "init", "session_id": session},
                      {"type": "result", "session_id": session, "result": "Fixture answer", "usage": {}}]
        else:
            resume = (command[-2] if "resume" in command else None) if self.exec_command else self.spec["resume"]
            session = self.session = resume or f"fixture-session-{self.sequence}"
            events = [{"type": "thread.started", "thread_id": session},
                      {"type": "item.completed", "item": {"type": "agent_message", "text": "Fixture answer"}},
                      {"type": "turn.completed", "usage": {}}]
        self.output = "".join(json.dumps(event) + "\n" for event in events)
        if not self.synchronous:  # the launcher has closed its copy of the job's output file by now
            with open(self.kwargs["stdout"].name, "ab") as stdout:
                stdout.write(self.output.encode())
        self.started.set()

    @property
    def prompt(self):
        return self.received.getvalue() if self.exec_command else self.spec["input"][0]["text"]

    def communicate(self, timeout=None):
        self.started.wait(10)
        self.alive = False
        return self.output, ""

    def poll(self):
        return None if self.alive else self.returncode

    def wait(self, timeout=None):
        self.started.wait(10)
        self.alive = False
        return self.returncode

    def kill(self):
        self.alive = False


class PersonaLoading(AltitudeCase):
    host = "linux"  # systemd fixtures

    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        make_repo(self.repo)
        self.personas = self.tmp / "personas"
        self.personas.mkdir()
        for role in ("l1", "l2", "l3"):
            (self.personas / f"{role}.md").write_text((config.PERSONAS / f"{role}.md").read_text())
        self.patch(config, "PERSONAS", self.personas)
        self.patch(engines, "_codex_processes", {})
        self.patch(platform, "job_active", return_value=False)
        self.patch(engines, "window_hold", return_value=None)
        self.processes = []
        real_popen = subprocess.Popen

        def popen(command, **kwargs):
            if config.CODEX_BIN not in command and command[-len(engines._driver_command()):] != engines._driver_command():
                return real_popen(command, **kwargs)  # Real local Git fixtures still validate task provenance.
            process = _ProviderProcess(command, kwargs, len(self.processes))
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
            if role == "l3":
                self.assertEqual(process.command[:2], [config.CODEX_BIN, "exec"])
                self.assertIn("--ignore-user-config", process.command)
                self.assertEqual("resume" in process.command, resumed)
            else:
                self.assertEqual(process.command[:2], [config.CODEX_BIN, "app-server"])
                self.assertEqual(process.spec["resume"] is not None, resumed)
            if resumed:
                self.assertNotIn(text, process.prompt, "existing threads do not reload the persona")
                self.assertNotIn("FIXTURE PERSONA UPDATE", process.prompt)
            else:
                self.assertTrue(process.prompt.startswith(text + "\n\n"), "fresh threads get the current persona")

    def assert_helper_reference(self, process, repo):
        path = self.personas / "l1.md"
        self.assertEqual(process.prompt.count(str(path)), 1)
        self.assertIn(f'You are an L1 helper. Read `{path}` before working.', process.prompt)
        self.assertIn(f'Your assigned repository is `{repo.resolve()}`.', process.prompt)
        self.assertNotIn(path.read_text(), process.prompt, "only the helper needs the shared persona contents")
        self.assertNotIn(path.read_text(), process.persona or "")

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
                self.assert_helper_reference(initial, Path(S.load_task(self.project, slug)["worktree"]))

                updated = original + "\nFIXTURE PERSONA UPDATE: current owner instructions.\n"
                persona.write_text(updated)
                initial.alive = False
                T.block(self.project, slug, "Wait for task context.", actor="l2", updates={"waiting_on": "l3"})
                T.message(self.project, slug, "l3", "Continue the existing task with this sourced context.")
                dispatch.resume(self.project, slug)
                resumed = self.processes[-1]
                self.assert_persona(resumed, engine, "l2", updated, resumed=True)
                self.assert_helper_reference(resumed, Path(S.load_task(self.project, slug)["worktree"]))
                self.assertEqual(resumed.session, initial.session)
                self.assertIn("Continue the existing task with this sourced context.", resumed.prompt)
                self.assertEqual(S.load_task(self.project, slug)["attempt"], 1)

                next_task = T.new(self.project, f"Current persona {engine}", "Inspect another fictional task.")
                dispatch.run(self.project, next_task["slug"])
                self.assert_persona(self.processes[-1], engine, "l2", updated, resumed=False)
                self.assertNotEqual(self.processes[-1].session, initial.session)
                persona.write_text(original)

    def test_l3_turn_resume_and_fresh_rotation_read_persona_at_the_supported_boundary(self):
        # Regression: asking permission for an already-authorized administrative transfer.
        # Capture the authoritative instructions alongside this context; no fixture can prove
        # that a future model will choose the instructed action or perform a real handoff.
        handoff_context = (
            "Recorded operator direction fixture-direction authorizes moving the remaining validation "
            "scope to the existing continuation task. Preserve its acceptance and source evidence, "
            "the security-review merge hold and original blocked owner session. A proposed provider "
            "change remains an unanswered operator choice; the administrative handoff is already authorized."
        )
        for engine in config.ENGINES:
            with self.subTest(engine=engine), mock.patch.object(l3, "_select",
                    return_value={"engine": engine, "why": "fixture routing"}):
                persona = self.personas / "l3.md"
                original = persona.read_text()
                self.assertTrue(l3.turn(self.project, handoff_context)["completed"])
                initial = self.processes[-1]
                self.assert_persona(initial, engine, "l3", original, resumed=False)
                self.assertIn(handoff_context, initial.prompt)
                self.assertNotIn(str(self.personas / "l1.md"), initial.prompt, "L3 does not delegate helpers")

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
