"""Optional `alt l1 run` workers are isolated, detached, and leave durable results."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="altitude-l1-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
FAKE = _TMP / "bin"; FAKE.mkdir()
(FAKE / "codex").write_text('''#!/usr/bin/env python3
import sys, json
args = sys.argv[1:]
out = args[args.index("-o") + 1]
open(out, "w").write("done.\\nRESULT: no PR — fake codex finished " + ("review" if "--output-schema" in args else "build"))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 50, "output_tokens": 7}}))
open(sys.argv[-1] + ".unused", "a") if False else None
''')
(FAKE / "claude").write_text('''#!/usr/bin/env python3
import sys, json
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "RESULT: #42 — fake claude opened PR"}], "usage": {"input_tokens": 5, "output_tokens": 3}}}))
print(json.dumps({"type": "result", "subtype": "success", "result": "RESULT: #42 — fake claude opened PR", "session_id": "fake-sid", "is_error": False}))
''')
(FAKE / "bwrap").write_text('''#!/bin/sh
while [ "$1" != "/bin/sh" ]; do shift; done
exec "$@"
''')
(FAKE / "systemd-run").write_text('''#!/bin/sh
while [ "$1" != "--" ]; do shift; done
shift
exec "$@"
''')
(FAKE / "systemctl").write_text('''#!/bin/sh
case " $* " in
  *" show "*) printf '%s\n' 'LoadState=not-found' 'ActiveState=inactive' 'SubState=dead' 'ControlGroup=' ;;
esac
exit 0
''')
for f in ("codex", "claude", "bwrap", "systemd-run", "systemctl"):
    (FAKE / f).chmod((FAKE / f).stat().st_mode | stat.S_IEXEC)
os.environ["PATH"] = str(FAKE) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")
os.environ["CODEX_BIN"] = str(FAKE / "codex")
os.environ["CLAUDE_BIN"] = str(FAKE / "claude")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T, l1, route, monitor  # noqa: E402

REPO = _TMP / "repo"


def _wait_done(project, slug, name, secs=30):
    for _ in range(secs * 4):
        r = l1.load(project, slug, name)
        if r and r.get("done"):
            return r
        time.sleep(0.25)
    raise AssertionError(f"run {name} did not finish: {l1.load(project, slug, name)}")


class TestL1Runs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        REPO.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=REPO, check=True)
        (REPO / ".gitignore").write_text(".claude/\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=REPO, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=REPO, check=True)
        remote = _TMP / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], cwd=REPO, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=REPO, check=True)
        subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=REPO, check=True)
        config.save_projects({"altitude": {"name": "altitude", "path": str(REPO)}})
        monitor.quota = lambda: {"known": False}
        route.quota_codex = lambda: {"known": False}

    def setUp(self):
        keys = ("ALTITUDE_ACTOR", "ALTITUDE_PROJECT", "ALTITUDE_TASK", "ALTITUDE_ATTEMPT")
        self._owner_env = {key: os.environ.get(key) for key in keys}

    def tearDown(self):
        for key, value in self._owner_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _task(self, slug):
        T.new("altitude", slug, "req", actor="l3")
        worktree = REPO / ".claude" / "worktrees" / slug
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", f"worktree-{slug}", str(worktree), "origin/main"],
            cwd=REPO, check=True,
        )
        with S.project_lock("altitude"):
            t = S.load_task("altitude", slug)
            t.update({"worktree": str(worktree), "branch": f"worktree-{slug}", "state": "running",
                      "paths": ["fixture.txt"],
                      "attempt": 1, "session_id": f"session-{slug}", "agent_id": f"agent-{slug}"})
            S.save_task("altitude", t)
        os.environ.update({"ALTITUDE_ACTOR": "l2", "ALTITUDE_PROJECT": "altitude",
                           "ALTITUDE_TASK": slug, "ALTITUDE_ATTEMPT": "1"})
        brief = S.task_dir("altitude", slug) / "sub-1.md"; brief.write_text("# sub-brief\nchange one thing\n")
        return slug, brief

    def test_codex_by_default_policy_in_its_own_worktree(self):
        slug, brief = self._task("l1-codex")
        rec = l1.start("altitude", slug, brief)
        self.assertEqual(rec["engine"], "codex"); self.assertIn("default policy", rec["why"])
        self.assertTrue(rec["branch"].startswith("l1/")); self.assertTrue(Path(rec["worktree"]).is_dir())
        self.assertNotEqual(Path(rec["worktree"]).resolve(), REPO.resolve(), "an implementer never works in the L2's checkout")
        prompt = (l1.runs_dir("altitude", slug) / f"{rec['name']}.prompt.md").read_text()
        self.assertIn("Your sublease is: ['fixture.txt']", prompt)
        self.assertIn("Do not commit", prompt)
        done = _wait_done("altitude", slug, rec["name"])
        self.assertIsNone(done["result"]["error"]); self.assertIsNone(done["result"]["pr"])
        self.assertIn("fake codex finished build", done["result"]["summary"])
        self.assertEqual(done["result"]["usage"]["input_tokens"], 50)
        w = l1.wait("altitude", slug, rec["name"], timeout=5)
        self.assertFalse(w["waiting"]); self.assertEqual(w["run"]["name"], rec["name"])
        kinds = [e["kind"] for e in S.read_events("altitude", slug)] if hasattr(S, "read_events") else ["l1-started", "l1-finished"]
        self.assertIn("l1-started", kinds); self.assertIn("l1-finished", kinds)

    def test_run_engine_override_selects_claude_and_pr_is_parsed(self):
        slug, brief = self._task("l1-claude")
        rec = l1.start("altitude", slug, brief, engine="claude")
        self.assertEqual(rec["engine"], "claude"); self.assertIn("forced", rec["why"])
        done = _wait_done("altitude", slug, rec["name"])
        self.assertEqual(done["result"]["pr"], 42)

    def test_no_available_engine_does_not_launch_or_fall_through_to_claude(self):
        slug, brief = self._task("l1-engine-hold")
        choice = {"engine": None, "why": "no engine available", "quota": {}}
        with mock.patch.object(route, "pick_engine", return_value=choice), \
             mock.patch.object(l1.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(T.TransitionError, "engine hold: no engine available"):
                l1.start("altitude", slug, brief)
        launch.assert_not_called()
        self.assertEqual(l1.list_runs("altitude", slug), [])

    def test_reviewer_can_run_on_the_other_engine(self):
        slug, brief = self._task("l1-review")
        # This is a routing/launch test, not a Codex sandbox integration test. Record the
        # completed author generation directly so a host-level Codex preflight denial cannot
        # file a system fault and make the independent Claude reviewer look broken.
        l1.save("altitude", slug, {
            "n": 1, "name": "implementer-1", "role": "implementer", "engine": "codex",
            "why": "default policy", "model": None,
            "worktree": str(REPO / ".claude" / "worktrees" / slug),
            "branch": f"worktree-{slug}", "brief": str(brief), "started": S.now(),
            "pid": None, "done": S.now(),
            "result": {"error": None, "pr": None, "summary": "synthetic completed author"},
        })
        rev = l1.start("altitude", slug, brief, role="reviewer")
        self.assertEqual(rev["engine"], "codex", "reviewer follows quota instead of forcing the scarcer seat")
        self.assertEqual(
            Path(rev["worktree"]).resolve(), (REPO / ".claude" / "worktrees" / slug).resolve(),
            "a reviewer reads in the L2 checkout, without another worktree",
        )
        done = _wait_done("altitude", slug, rev["name"])
        self.assertIsNone(done["result"]["error"])
        st = l1.status("altitude", slug)
        self.assertEqual([r["role"] for r in st], ["implementer", "reviewer"])

    def test_immutable_parent_sha_is_used_if_l2_head_moves_after_validation(self):
        slug, brief = self._task("l1-parent-race")
        parent = REPO / ".claude" / "worktrees" / slug
        captured = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=parent, check=True, capture_output=True, text=True,
        ).stdout.strip()
        real_check = l1.git_policy.commits_missing_task_trailer

        def advance_after_check(*args, **kwargs):
            missing = real_check(*args, **kwargs)
            (parent / "raced.txt").write_text("later\n")
            subprocess.run(["git", "add", "raced.txt"], cwd=parent, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "later",
                 "-m", f"Altitude-Task: altitude/{slug}"],
                cwd=parent, check=True,
            )
            return missing

        with mock.patch.object(l1.git_policy, "commits_missing_task_trailer", side_effect=advance_after_check):
            rec = l1.start("altitude", slug, brief)

        nested_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=rec["worktree"], check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(nested_head, captured)
        self.assertFalse((Path(rec["worktree"]) / "raced.txt").exists())
        _wait_done("altitude", slug, rec["name"])

    def test_z_implementer_refuses_parent_commit_from_another_provenance(self):
        slug, brief = self._task("l1-bad-parent")
        parent = REPO / ".claude" / "worktrees" / slug
        (parent / "direct.txt").write_text("direct\n")
        subprocess.run(["git", "add", "direct.txt"], cwd=parent, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "direct commit"],
            cwd=parent, check=True,
        )

        with self.assertRaisesRegex(T.TransitionError, "without exact.*provenance"):
            l1.start("altitude", slug, brief)
        self.assertEqual(l1.list_runs("altitude", slug), [])

    def test_z_explicit_main_cwd_is_refused(self):
        slug, brief = self._task("l1-explicit-main")
        with self.assertRaisesRegex(T.TransitionError, "protected branch 'main'"):
            l1.start("altitude", slug, brief, cwd=str(REPO))
        self.assertEqual(l1.list_runs("altitude", slug), [])

    def test_registered_task_worktree_is_allowed_for_review_fix_round(self):
        slug, brief = self._task("l1-review-fix")
        parent = REPO / ".claude" / "worktrees" / slug
        rec = l1.start("altitude", slug, brief, cwd=str(parent))
        self.assertEqual(Path(rec["worktree"]).resolve(), parent.resolve())
        self.assertEqual(rec["branch"], f"worktree-{slug}")
        _wait_done("altitude", slug, rec["name"])

    def test_stale_and_blocked_l2s_cannot_launch_l1(self):
        slug, brief = self._task("l1-owner-fence")
        with self.assertRaisesRegex(T.TransitionError, "no longer the current L2"):
            l1.start("altitude", slug, brief, expected_attempt=2)

        with S.project_lock("altitude"):
            task = S.load_task("altitude", slug)
            task["state"] = "blocked"
            S.save_task("altitude", task)
        with self.assertRaisesRegex(T.TransitionError, "current running L2"):
            l1.start("altitude", slug, brief)
        self.assertEqual(l1.list_runs("altitude", slug), [])


if __name__ == "__main__":
    unittest.main()
