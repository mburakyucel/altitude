"""Task effort selection, native launch arguments, and conversation-preserving resume."""
import json
import subprocess
from types import SimpleNamespace
from unittest import mock

from tests.support import ALT, AltitudeCase
from tests.test_codex_door import FakeProcess
from tests.test_engine_raw_capture import FakeProcess as PrintProcess
from tests.test_engines import _Process as ClaudeWorker
from altitude import config, dispatch, engines, incidents, route, state as S, status, tasks as T


def set_effort(project, value):
    """The L2 Codex default: the setUp routing sends fresh attempts to Codex."""
    dispatch.request_setting(project, "l2_codex_effort", value, "Set test effort", actor="burak")
    dispatch._run_setting(project, "l2_codex_effort")


class TestTaskEffort(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        self.register(self.project, routing=config.parse_routing("codex > claude:opus"))
        self.patch(dispatch.git_policy, "fetch_origin", return_value="a" * 40)
        self.patch(dispatch, "_task_worktree", return_value=self.repo)
        self.patch(dispatch, "_validate_task_worktree")
        self.patch(engines, "worker_live", return_value=False)
        self.patch(engines, "stop_l2_worker")
        self.patch(incidents, "system_fault")

    @staticmethod
    def launched(*args, **kwargs):
        return {"returncode": 0, "agent": {"id": "worker", "sessionId": "conversation"}}

    def launch(self, task):
        with mock.patch.object(engines, "start_l2", side_effect=self.launched) as launch:
            dispatch.run(self.project, task["slug"])
        return launch.call_args

    def test_default_and_override_persist_separately_from_observations(self):
        for effort, expected in ((None, "high"), ("high", "high"), ("xhigh", "xhigh")):
            with self.subTest(effort=effort):
                task = T.new(self.project, str(effort), "request", effort=effort)
                self.assertEqual(task["effort"], effort)
                self.assertEqual(self.launch(task).kwargs["effort"], expected)
                saved = S.load_task(self.project, task["slug"])
                self.assertEqual(saved["launch_effort"], expected)
                self.assertIsNone(saved["engine_reasoning_effort"])
                self.assertIsNone(saved["engine"], "effort must not manufacture an engine pin")
                view = status.status(self.project, task["slug"])
                self.assertEqual((view["effort"], view["launch_effort"], view["engine_reasoning_effort"]),
                                 (effort, expected, None))

    def test_message_resume_preserves_effort_engine_and_conversation_after_policy_change(self):
        task = T.new(self.project, "Difficult work", "request", effort="xhigh")
        self.launch(task)
        saved = S.load_task(self.project, task["slug"])
        saved["engine_reasoning_effort"] = "medium"  # observation must never become the launch choice
        S.save_task(self.project, saved)
        T.block(self.project, task["slug"], "Paused")
        T.message(self.project, task["slug"], "burak", "Continue with this correction", by="burak")
        self.register(self.project, routing=config.parse_routing("claude:opus"))
        with mock.patch.object(engines, "resume_l2", return_value=self.launched()) as resume:
            dispatch.resume(self.project, task["slug"])
        self.assertEqual((resume.call_args.args[0], resume.call_args.args[2]), ("codex", "conversation"))
        self.assertIn("Continue with this correction", resume.call_args.args[3])
        self.assertEqual(resume.call_args.kwargs["effort"], "xhigh")
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual((saved["attempt"], saved["launch_effort"], saved["effort"]), (1, "xhigh", "xhigh"))
        self.assertIsNone(saved["engine_reasoning_effort"], "a new turn has no observation yet")

    def test_project_default_is_read_at_dispatch_and_override_wins(self):
        set_effort(self.project, "medium")
        queued = T.new(self.project, "Queued before change", "request")
        explicit = T.new(self.project, "Explicit choice", "request", effort="low")
        native = T.new(self.project, "Native choice", "request", effort="native")
        set_effort(self.project, "max")
        self.assertEqual(self.launch(queued).kwargs["effort"], "max")
        self.assertEqual(self.launch(explicit).kwargs["effort"], "low")
        self.assertIsNone(self.launch(native).kwargs["effort"])
        saved = S.load_task(self.project, queued["slug"])
        self.assertIsNone(saved["effort"])
        self.assertEqual(saved["launch_effort"], "max")
        set_effort(self.project, "low")
        T.block(self.project, queued["slug"], "Paused")
        T.message(self.project, queued["slug"], "burak", "Continue", by="burak")
        with mock.patch.object(engines, "resume_l2", return_value=self.launched()) as resume:
            dispatch.resume(self.project, queued["slug"])
        self.assertEqual(resume.call_args.kwargs["effort"], "max")
        self.assertEqual(resume.call_args.args[2], "conversation")
        self.assertEqual(self.launch(T.new(self.project, "After change", "request")).kwargs["effort"], "low")

    def test_native_started_task_stays_native_after_project_default_change(self):
        set_effort(self.project, "native")
        task = T.new(self.project, "Native project default", "request")
        self.assertIsNone(self.launch(task).kwargs["effort"])
        set_effort(self.project, "high")
        T.block(self.project, task["slug"], "Paused")
        with mock.patch.object(engines, "resume_l2", return_value=self.launched()) as resume:
            dispatch.resume(self.project, task["slug"])
        self.assertIsNone(resume.call_args.kwargs["effort"])

    def test_invalid_and_pinned_unsupported_effort_refuse_before_task_creation(self):
        for kwargs in ({"effort": "invalid"}, {"effort": ""}, {"effort": "ultra", "engine": "claude"},
                       {"effort": "ultra", "model": "opus"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(T.TransitionError):
                T.new(self.project, "Invalid", "request", **kwargs)
        self.register(self.project, l2_engine="claude")
        with self.assertRaisesRegex(T.TransitionError, "does not support reasoning effort"):
            T.new(self.project, "Invalid project pin", "request", effort="ultra")
        self.assertEqual(S.list_tasks(self.project), [])

    def test_auto_filters_unsupported_engines_and_single_engine_without_override_works(self):
        self.register(self.project, routing=config.parse_routing("claude:opus > codex"))
        task = T.new(self.project, "Explicit", "request", effort="ultra")
        self.assertEqual(self.launch(task).args[0], "codex")
        self.register(self.project, routing=config.parse_routing("claude:opus"))
        choice = route.pick_task(config.project(self.project), task)
        self.assertIsNone(choice["engine"])
        self.assertIn("does not support reasoning effort", choice["why"])
        ordinary = T.new(self.project, "Ordinary", "request")
        launched = self.launch(ordinary)
        self.assertEqual(launched.args[0], "claude")
        self.assertIsNone(launched.kwargs["effort"])

    def test_provider_model_effort_rejection_does_not_downgrade_or_switch_engine(self):
        task = T.new(self.project, "Unsupported model", "request", effort="xhigh")
        error = "reasoning effort xhigh is not supported with this model"
        self.assertIsNone(engines.rejection("codex", {"stderr": error}))
        failed = {"returncode": 1, "stderr": error}
        with mock.patch.object(engines, "start_l2", return_value=failed) as launch:
            with self.assertRaisesRegex(dispatch.DispatchFailure, "reasoning effort xhigh"):
                dispatch.run(self.project, task["slug"])
        self.assertEqual(launch.call_count, 1)
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual(saved["launch_effort"], "xhigh")
        self.assertFalse(saved.get("dispatching"))
        self.assertIsNone(saved["session_id"])
        self.assertIn(error, S.read_events(self.project, task["slug"])[-1]["reason"])

    def test_resume_failure_retains_selection_identity_and_message(self):
        task = T.new(self.project, "Retry same conversation", "request", effort="xhigh")
        self.launch(task)
        T.block(self.project, task["slug"], "Paused")
        T.message(self.project, task["slug"], "burak", "Keep this message", by="burak")
        with mock.patch.object(engines, "resume_l2", side_effect=RuntimeError("unsupported effort for model")):
            with self.assertRaises(dispatch.ResumeFailure):
                dispatch.resume(self.project, task["slug"])
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual((saved["state"], saved["session_id"], saved["launch_effort"]),
                         ("blocked", "conversation", "xhigh"))
        self.assertFalse(saved.get("resume_claim"))
        self.assertIn("Keep this message", (S.task_dir(self.project, task["slug"]) / "inbox.jsonl").read_text())

    def test_model_rejection_after_thread_start_is_a_failure_without_rerouting(self):
        task = T.new(self.project, "Late unsupported effort", "request", effort="xhigh")
        error = "reasoning effort xhigh is not supported with this model"
        native_launches = []
        real_popen = subprocess.Popen
        def popen(cmd, **kwargs):
            if config.CODEX_BIN not in cmd:
                return real_popen(cmd, **kwargs)
            native_launches.append(cmd)
            proc = FakeProcess(cmd, stdout=kwargs["stdout"], thread="conversation")
            kwargs["stdout"].write((json.dumps({"type": "turn.failed", "error": {"message": error}}) + "\n").encode())
            proc.alive = False
            return proc
        with mock.patch.object(engines.subprocess, "Popen", side_effect=popen) as launch, \
             mock.patch.object(engines, "_git_dirs", return_value=[]), \
             mock.patch.object(engines, "_unit_active", return_value=False):
            dispatch.run(self.project, task["slug"])
            finished = dispatch.poll(self.project)
        self.assertEqual(len(native_launches), 1)
        self.assertEqual(len(finished), 1)
        self.assertTrue(finished[0]["died"])
        self.assertIn(error, finished[0]["detail"])
        self.assertFalse(any(finished[0].get(key) for key in ("rejection", "limited", "capacity")))
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual((saved["l2_engine"], saved["launch_effort"], saved["session_id"]),
                         ("codex", "xhigh", "conversation"))

    def test_cli_accepts_supported_choices_and_rejects_invalid_values(self):
        for effort in (*config.TASK_EFFORTS, "invalid"):
            result = subprocess.run([str(ALT), "-p", self.project, "task", "new", "--title", effort,
                                     "--effort", effort, "request"], capture_output=True, text=True)
            if effort == "invalid":
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid choice", result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["effort"], effort)


class TestEffortCommand(AltitudeCase):
    def test_claude_worker_fresh_and_resume_honor_effort_without_inherited_override(self):
        self.quiet_engines()
        self.patch(engines, "_unit_active", return_value=False)
        self.patch(engines, "_codex_processes", {})
        self.setenv("CLAUDE_CODE_EFFORT_LEVEL", "low")
        for resume in (False, True):
            for effort in (None, "medium", "xhigh", "max"):
                with self.subTest(resume=resume, effort=effort):
                    def popen(cmd, **kwargs):
                        return ClaudeWorker(kwargs["stdout"], session="conversation")
                    with mock.patch.object(engines.subprocess, "Popen", side_effect=popen) as launch:
                        kwargs = dict(cwd=self.repo, persona=config.PERSONAS / "l2.md", model="opus",
                                      effort=effort, settings=self.tmp / "settings.json", extra_env={},
                                      job_root=self.tmp / "jobs")
                        result = (engines.resume_l2("claude", "task", "conversation", "message", **kwargs) if resume
                                  else engines.start_l2("claude", "task", "request", **kwargs))
                    self.assertEqual(result["returncode"], 0)
                    command = launch.call_args.args[0]
                    self.assertEqual("--resume" in command, resume)
                    self.assertEqual("--effort" in command, effort is not None)
                    if effort is not None:
                        self.assertEqual(command[command.index("--effort") + 1], effort)
                        self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", launch.call_args.kwargs["env"])
                    record = S.read_json(self.tmp / "jobs" / (result["agent"]["id"] + ".json"))
                    self.assertEqual(record["launch_effort"], effort)

    def test_claude_l3_fresh_and_resume_native_arguments(self):
        self.quiet_engines()
        self.setenv("CLAUDE_CODE_EFFORT_LEVEL", "low")
        for resume in (None, "conversation"):
            for effort in (None, "high", "max"):
                with self.subTest(resume=resume, effort=effort):
                    event = {"type": "result", "session_id": "conversation", "result": "done"}
                    with mock.patch.object(engines.subprocess, "Popen", return_value=PrintProcess(json.dumps(event) + "\n")) as launch:
                        result = engines.claude_print("request", cwd=self.repo, effort=effort, resume=resume,
                                                      settings=self.tmp / "settings.json")
                    self.assertIsNone(result["error"])
                    command = launch.call_args.args[0]
                    self.assertEqual("--resume" in command, resume is not None)
                    self.assertEqual("--effort" in command, effort is not None)
                    if effort is not None:
                        self.assertEqual(command[command.index("--effort") + 1], effort)
                        self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", launch.call_args.kwargs["env"])

    def test_fresh_resume_and_legacy_commands_and_worker_evidence(self):
        for resume in (False, True):
            for effort in (None, "low", "medium", "high", "xhigh", "max", "ultra"):
                with self.subTest(resume=resume, effort=effort):
                    commands = []
                    def popen(cmd, **kwargs):
                        commands.append(cmd)
                        return FakeProcess(cmd, stdout=kwargs["stdout"], thread="conversation")
                    with mock.patch.object(engines.subprocess, "Popen", side_effect=popen), \
                         mock.patch.object(engines, "_git_dirs", return_value=[]), \
                         mock.patch.object(engines, "_unit_active", return_value=False):
                        kwargs = dict(cwd=self.repo, persona=config.PERSONAS / "l2.md", model=None,
                                      effort=effort, settings=self.tmp / "settings.json", extra_env={},
                                      job_root=self.tmp / "jobs")
                        result = (engines.resume_l2("codex", "task", "conversation", "message", **kwargs) if resume
                                  else engines.start_l2("codex", "task", "request", **kwargs))
                    self.assertEqual(result["returncode"], 0)
                    selected = [arg for arg in commands[0] if arg.startswith("model_reasoning_effort=")]
                    self.assertEqual(selected, [] if effort is None else [f'model_reasoning_effort="{effort}"'])
                    self.assertEqual("resume" in commands[0], resume)
                    record = S.read_json(self.tmp / "jobs" / (result["agent"]["id"] + ".json"))
                    self.assertEqual(record["launch_effort"], effort)
                    self.assertNotIn("engine_reasoning_effort", record)

    def test_codex_l3_fresh_and_resume_native_arguments(self):
        output = json.dumps({"type": "thread.started", "thread_id": "conversation"}) + "\n"
        for resume in (None, "conversation"):
            for effort in (None, "high", "ultra"):
                with self.subTest(resume=resume, effort=effort):
                    process = SimpleNamespace(pid=123, returncode=0, communicate=lambda *args, **kwargs: (output, ""))
                    with mock.patch.object(engines.subprocess, "Popen", return_value=process) as launch:
                        result = engines.codex_exec("request", cwd=self.repo, effort=effort, resume=resume)
                    self.assertIsNone(result["error"])
                    command = launch.call_args.args[0]
                    self.assertEqual("resume" in command, resume is not None)
                    selected = [arg for arg in command if arg.startswith("model_reasoning_effort=")]
                    self.assertEqual(selected, [] if effort is None else [f'model_reasoning_effort="{effort}"'])

    def test_unsupported_engine_is_refused_before_external_effects(self):
        with mock.patch.object(engines.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(ValueError, "does not support reasoning effort"):
                engines.start_l2("claude", "task", "request", cwd=self.repo, persona=config.PERSONAS / "l2.md",
                                 model="opus", effort="ultra", settings=self.tmp / "settings.json",
                                 extra_env={}, job_root=self.tmp / "jobs")
        launch.assert_not_called()
        self.assertFalse((self.tmp / "jobs").exists())
