"""A task worker changes Altitude's records only through altd's task door: its `alt` verbs, its checkpoint hooks and
its engine driver on both engines keep working with Altitude's home read-only (docs/ARCHITECTURE.md#task-door).
`tests/test_land.py` lands the same way."""
import json
import os
import subprocess
import sys
import time

from tests.support import ALT, REPO, AltitudeCase, add_worktree, make_repo, read_only_home, require_read_only_home
from altitude import config, dispatch, engines, state as S, tasks as T

FAKE = REPO / "tests" / "fake_engine.py"
TEXT = "Use the staging copy."


class ReadOnlyHome(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        make_repo(self.repo)
        task = T.new(self.project, "Read-only home", "Change the records only through altd.")
        self.slug = task["slug"]
        self.worktree = add_worktree(self.repo, self.slug)
        task.update(state="running", attempt=1, agent_id="worker", session_id="session", worktree=str(self.worktree),
                    l2_engine=config.ENGINES[0])
        S.save_task(self.project, task)
        paths = engines.common._worker_paths(dispatch.l2_job_root(self.project, self.slug), "worker")
        paths["record"].parent.mkdir(parents=True, exist_ok=True)
        S.atomic_write(paths["record"], json.dumps({"id": "worker", "sends": str(paths["sends"])}))
        self.sends = paths["sends"]
        self.scratch = self.tmp / "scratch"
        self.scratch.mkdir()
        self.env = {**os.environ, **dispatch.l2_env(self.project, self.slug, 1), "TMPDIR": str(self.scratch)}
        require_read_only_home(self)

    def confined(self, *command, stdin=""):
        return subprocess.run(read_only_home(list(command), (self.tmp,)), input=stdin, capture_output=True, text=True,
                              env=self.env, cwd=self.worktree, timeout=120)

    def test_owner_verbs_change_records_through_altd(self):
        (self.worktree / "progress.md").write_text("Goal: the read-only home.\n")
        (self.worktree / "report.json").write_text(json.dumps({"summary": "Fixture report."}))
        (self.worktree / "README.md").write_text("# Draft\n\nThe new introduction.\n")
        done = self.confined(sys.executable, str(ALT), "task", "reply", "-", stdin="The draft is ready to read.")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(T.task_messages(self.project, self.slug)[-1]["text"], "The draft is ready to read.")
        for kind, name in (("progress", "progress.md"), ("report", "report.json")):
            done = self.confined(sys.executable, str(ALT), "task", "save", kind, name)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual((S.task_dir(self.project, self.slug) / name).read_text(), (self.worktree / name).read_text())
        done = self.confined(sys.executable, str(ALT), "task", "block", self.slug, "--reason", "Build the README as shown?",
                             "--for-operator", "--show", "README.md")
        self.assertEqual(done.returncode, 0, done.stderr)
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["state"], "blocked")
        self.assertEqual(task["questions"][-1]["shown"]["document"]["path"], "README.md")

    def alt(self, *arguments, stdin=""):
        done = self.confined(sys.executable, str(ALT), *arguments, stdin=stdin)
        self.assertEqual(done.returncode, 0, f"alt {' '.join(arguments)}: {done.stderr}")
        return done.stdout

    def test_every_owner_verb_works_with_a_read_only_home(self):
        for read in (("task", "status", self.slug), ("task", "show", self.slug), ("task", "events", self.slug),
                     ("task", "messages", self.slug), ("task", "list"), ("incident", "list")):
            self.alt(*read)
        self.alt("fyi", self.slug, "The fixture task notes a finding.")
        # The worker's end of every forwarded verb is the same; review request and assess, which need a reviewer
        # engine, reach altd the same way in tests/test_review_interfaces.py.
        self.alt("task", "review", "skip", "--subject", "changes", "--reason", "A fixture change needs no review.")
        (self.scratch / "handoff.json").write_text(json.dumps({"id": "fixture", "summary": "Hand-off."}))
        self.alt("task", "save", "handoff", str(self.scratch / "handoff.json"))
        (self.scratch / "questions.json").write_text(json.dumps({"questions": [
            {"question": "May I restart the fixture service?", "recommended_key": "yes",
             "options": [{"key": "yes", "label": "Allow", "text": "Restart it once."}]}]}))
        self.alt("task", "block", self.slug, "--reason", "One decision.", "--for-operator", "--questions-file",
                 str(self.scratch / "questions.json"))
        question = S.load_task(self.project, self.slug)["questions"][-1]
        answer = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Yes, restart it once.",
                           question_id=question["id"], revision=question["revision"])
        T.resume(self.project, self.slug)
        self.alt("task", "resolve", self.slug, "--question", question["id"], "--message", answer["id"],
                 "--disposition", "answered", "--reason", "The operator allowed it.")
        self.alt("task", "grant", self.slug, "--approval", answer["id"], "--question", question["id"],
                 "--revision", str(question["revision"]), "--reason", "The operator allowed one restart.")
        self.assertTrue(S.load_task(self.project, self.slug)["grant"])
        self.alt("task", "grant", self.slug, "--revoke", "--reason", "Purpose complete.")
        self.alt("task", "done", self.slug, "--digest", "The fixture work needed no repository change.")
        self.assertEqual(S.load_task(self.project, self.slug)["completion_requested"]["digest"],
                         "The fixture work needed no repository change.")

    def test_checkpoint_hooks_deliver_and_count_through_altd(self):
        T.message(self.project, self.slug, "l3", "Rebase before landing.")
        done = self.confined(sys.executable, str(config.HOOKS / "inbox.py"), stdin=json.dumps(
            {"hook_event_name": "PostToolUse", "session_id": "session", "tool_name": "Bash"}))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Rebase before landing.", json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(T.pending(self.project, self.slug), [])
        done = self.confined(sys.executable, str(config.HOOKS / "edit_count.py"), stdin=json.dumps(
            {"session_id": "session", "tool_name": "Edit", "tool_input": {"file_path": "README.md"}}))
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(S.read_json(S.counts_path(self.project, S.load_task(self.project, self.slug)), None),
                         {"edits": 1, "files": ["README.md"]})

    def test_landing_takes_its_locks_in_a_read_only_home(self):
        # altd makes the leaf of the task's merge lock with each launch; the project's lock exists since its first task.
        dispatch.session_settings(self.project, self.slug)
        done = self.confined(sys.executable, "-c", f"import sys\nsys.path.insert(0, {str(REPO)!r})\n"
                             "from altitude import reviews, state as S\n"
                             "with S.project_lock(sys.argv[1]), reviews.merge_lock(sys.argv[1], sys.argv[2]):\n"
                             "    print('locked')", self.project, self.slug)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "locked\n", ""))

    def drive(self, engine):
        """The worker job's driver and a fixture engine, both unable to write Altitude's home."""
        tool = self.tmp / f"{engine}.tool"
        spec = {"engine": engine, "command": [sys.executable, str(FAKE), *(["app-server"] if engine == "codex" else [])],
                "input": engines.common._engine_input(engine, "Start the work.", ()), "cwd": str(self.worktree),
                "sends": str(self.sends), "worker_id": "worker", "task": {"project": self.project, "slug": self.slug}}
        own = {}
        if engine == "codex":  # the job's own Codex folder and the sign-in altd hands it
            self.codex_signed_in()
            spec.update(operator=str(self.codex_home()), copies=[], sign_in=engines.codex.worker_sign_in())
            own = {"CODEX_HOME": str(self.tmp / "job-codex")}
        proc = subprocess.Popen(read_only_home(engines.common._driver_command(), (self.tmp,)), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.worktree,
                                env={**self.env, **own, "FAKE_ENGINE_LOG": str(self.tmp / f"{engine}.log"),
                                     "FAKE_ENGINE_TOOL": str(tool), "FAKE_ENGINE_END": "completed"})
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        proc.stdin.write(engines.common._driver_input(spec).encode())
        proc.stdin.close()
        proc.stdin = None
        deadline = time.monotonic() + 20
        while not tool.exists():
            self.assertIsNone(proc.poll(), "the turn ended before its command started")
            self.assertLess(time.monotonic(), deadline, "the fixture command never started")
            time.sleep(0.02)
        return proc

    def test_the_driver_takes_and_settles_messages_through_altd_on_both_engines(self):
        # Send now: altd claims the message into the worker's sends folder; the driver takes and settles it.
        message = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, TEXT)
        with S.project_lock(self.project):
            T.claim_send_now(self.project, S.load_task(self.project, self.slug), [message], self.sends)
        proc = self.drive("claude")
        stdout, stderr = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, stderr.decode())
        self.assertIn({"type": "altitude.send", "message_id": message["id"], "outcome": "delivered"},
                      [json.loads(line) for line in stdout.decode().splitlines()])
        self.assertEqual(S.load_task(self.project, self.slug)["message_deliveries"][message["id"]]["state"], "delivered")
        # An engine without hook checkpoints: its driver asks altd for an ordinary message waiting for the owner.
        proc = self.drive("codex")
        message = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, TEXT)
        stdout, stderr = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, stderr.decode())
        self.assertIn({"type": "altitude.send", "message_id": message["id"], "outcome": "delivered"},
                      [json.loads(line) for line in stdout.decode().splitlines()])
        self.assertEqual(T.pending(self.project, self.slug), [])
