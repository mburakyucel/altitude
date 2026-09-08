"""Issue #259: recovery scope goes through L3 without widening the worker door."""
import json
from pathlib import Path

from tests.support import AltitudeCase, git, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, l3, land, server, state as S, tasks as T


class TestRecoveryLeases(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.project = "demo"
        make_repo(self.repo)
        self.register(self.project)
        self.private_ledgers()
        self.quiet_engines()
        self.engine = FakeL2()
        self.engine.install(self)

    def coordinator(self, *args):
        return server.l3_verb_request(self.project, {"kind": "alt", "args": list(args)})

    def recover(self, initial_paths, engine):
        task = T.new(self.project, "Recover demo", "Review preserved docs/archive/ changes.",
                     source="recovery", paths=initial_paths, engine=engine,
                     hold_merge="Review recovered content")
        slug = task["slug"]
        dispatch.run(self.project, slug)
        task = S.load_task(self.project, slug)
        worker_env = dispatch.l2_env(self.project, slug, task["attempt"])
        events = S.read_events(self.project, slug)
        other = T.new(self.project, "Other owner", "Unrelated work", paths=["src/"])
        for target in (slug, other["slug"]):
            denied = self.alt("task", "paths", target, "docs/archive/", env=worker_env)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("not available to an L2 worker", denied.stderr)
            self.assertIn("L3", denied.stderr)
            self.assertIn("alt task block", denied.stderr)
        self.assertEqual(S.load_task(self.project, slug), task)
        self.assertEqual(S.read_events(self.project, slug), events)
        self.assertEqual(S.load_task(self.project, other["slug"])["paths"], ["src/"])

        reason = "Please add docs/archive/ to my lease to review the preserved documentation."
        blocked = self.alt("task", "block", slug, "--reason", reason, env=worker_env)
        self.assertEqual(blocked.returncode, 0, blocked.stderr)
        blocked_task = S.load_task(self.project, slug)
        self.assertEqual((blocked_task["state"], blocked_task["waiting_on"]), ("blocked", "l3"))
        self.assertFalse(blocked_task.get("fault"))
        question = blocked_task["questions"][-1]
        self.assertEqual(question["audience"], "l3")
        self.assertIn(reason, T.task_messages(self.project, slug)[-1]["text"])
        notifications = l3.queued(self.project)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["trigger"], "block")
        self.assertIn(reason, notifications[0]["text"])

        invalid = self.coordinator("task", "paths", slug, ",")
        self.assertNotEqual(invalid["returncode"], 0)
        self.assertEqual(S.load_task(self.project, slug), blocked_task)
        self.assertEqual(len(self.engine.calls), 1)

        paths = [*initial_paths, "docs/archive/"]
        assigned = self.coordinator("task", "paths", slug, ",".join(paths))
        self.assertEqual(assigned["returncode"], 0, assigned["stderr"])
        observed = self.coordinator("task", "status", slug)
        self.assertEqual(observed["returncode"], 0, observed["stderr"])
        status = json.loads(observed["stdout"])
        self.assertEqual(status["lease"], paths)
        self.assertEqual(status["state"], "blocked")
        self.assertFalse(status.get("resume_after"))
        self.assertEqual(len(self.engine.calls), 1, "assignment alone does not resume")
        self.assertEqual(dispatch.resume_due(self.project), [])
        receipt = S.read_events(self.project, slug)[-1]
        self.assertEqual((receipt["kind"], receipt["by"], receipt["previous"], receipt["paths"]),
                         ("paths", "l3", initial_paths, paths))

        answer = "docs/archive/ is now recorded in your lease; continue reviewing in your worktree."
        message = self.coordinator("task", "message", slug, answer)
        self.assertEqual(message["returncode"], 0, message["stderr"])
        self.assertTrue(S.load_task(self.project, slug)["resume_after"])
        self.assertEqual(len(self.engine.calls), 1, "the daemon owns worker resume")
        self.assertEqual(dispatch.resume_due(self.project), [slug])
        dispatch.resume(self.project, slug)
        current = S.load_task(self.project, slug)
        self.assertEqual(current["state"], "running")
        self.assertEqual(current["paths"], paths)
        for key in ("attempt", "session_id", "l2_engine", "worktree", "branch", "hold_merge"):
            self.assertEqual(current[key], task[key], key)
        self.assertEqual(len(self.engine.calls), 2)
        self.assertEqual(self.engine.calls[-1]["session_id"], task["session_id"])
        self.assertIn(answer, self.engine.calls[-1]["prompt"])
        self.assertEqual(T.pending(self.project, slug), [])
        observed = self.alt("task", "status", slug, env=worker_env)
        self.assertEqual(observed.returncode, 0, observed.stderr)
        self.assertEqual(json.loads(observed.stdout)["lease"], paths)
        resolved = self.alt("task", "resolve", slug, "--question", question["id"],
                            "--revision", str(question["revision"]), "--message",
                            json.loads(message["stdout"])["id"], "--disposition", "answered",
                            "--reason", "L3 assigned the scope and status confirms it.", env=worker_env)
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self.assertEqual(S.load_task(self.project, slug)["questions"][-1]["status"], "resolved")

        # The recorded lease permits the intended recovery; other files still cannot be staged.
        self.fake_gh()
        for key, value in worker_env.items():
            self.setenv(key, value)
        worktree = Path(task["worktree"])
        archive = worktree / "docs/archive/recovered.md"
        archive.parent.mkdir(parents=True)
        archive.write_text("Reviewed fictional preserved documentation.\n")
        preview = land.land("docs: recover reviewed content", cwd=worktree, dry_run=True)
        self.assertEqual(preview["lease"], paths)
        (worktree / "outside.txt").write_text("Unassigned scope\n")
        with self.assertRaisesRegex(land.LandError, "outside.txt"):
            land.land("docs: recover reviewed content", cwd=worktree, dry_run=True)
        self.assertEqual(git("diff", "--cached", "--name-only", cwd=worktree), "")
        self.assertEqual(S.load_task(self.project, other["slug"])["paths"], ["src/"])

    def test_recovery_without_a_lease_resumes_the_same_session(self):
        self.recover([], config.ENGINES[0])

    def test_recovery_retains_existing_scope_on_the_other_engine(self):
        self.recover(["README.md"], config.ENGINES[-1])
