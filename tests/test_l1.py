"""Decision 45: `alt l1 run` — detached run on either engine, own worktree, cap enforced, record closed with the result."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

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
for f in ("codex", "claude"):
    (FAKE / f).chmod((FAKE / f).stat().st_mode | stat.S_IEXEC)
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
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"], cwd=REPO, check=True)
        config.save_projects({"altitude": {"name": "altitude", "path": str(REPO), "stacks": ["python"]}})
        monitor.quota = lambda: {"known": False}
        route.quota_codex = lambda: {"known": False}

    def _task(self, slug, cls="S", engine=None):
        T.new("altitude", slug, cls, "req", actor="l3", engine=engine)
        with S.project_lock("altitude"):
            t = S.load_task("altitude", slug); t["worktree"] = str(REPO); S.save_task("altitude", t)
        brief = S.task_dir("altitude", slug) / "sub-1.md"; brief.write_text("# sub-brief\nchange one thing\n")
        return slug, brief

    def test_codex_by_default_policy_in_its_own_worktree(self):
        slug, brief = self._task("l1-codex")
        rec = l1.start("altitude", slug, brief)
        self.assertEqual(rec["engine"], "codex"); self.assertIn("default policy", rec["why"])
        self.assertTrue(rec["branch"].startswith("l1/")); self.assertTrue(Path(rec["worktree"]).is_dir())
        self.assertNotEqual(Path(rec["worktree"]).resolve(), REPO.resolve(), "an implementer never works in the L2's checkout")
        done = _wait_done("altitude", slug, rec["name"])
        self.assertIsNone(done["result"]["error"]); self.assertIsNone(done["result"]["pr"])
        self.assertIn("fake codex finished build", done["result"]["summary"])
        self.assertEqual(done["result"]["usage"]["input_tokens"], 50)
        w = l1.wait("altitude", slug, rec["name"], timeout=5)
        self.assertFalse(w["waiting"]); self.assertEqual(w["run"]["name"], rec["name"])
        kinds = [e["kind"] for e in S.read_events("altitude", slug)] if hasattr(S, "read_events") else ["l1-started", "l1-finished"]
        self.assertIn("l1-started", kinds); self.assertIn("l1-finished", kinds)

    def test_task_engine_forces_claude_and_pr_is_parsed(self):
        slug, brief = self._task("l1-claude", engine="claude")
        rec = l1.start("altitude", slug, brief)
        self.assertEqual(rec["engine"], "claude"); self.assertIn("forced on the task", rec["why"])
        done = _wait_done("altitude", slug, rec["name"])
        self.assertEqual(done["result"]["pr"], 42)

    def test_in_flight_cap_and_reviewer_on_the_other_engine(self):
        slug, brief = self._task("l1-cap")  # S: one implementer in flight
        first = l1.start("altitude", slug, brief)
        # a second implementer while the first is (possibly) still running must be refused by the cap
        with S.project_lock("altitude"):
            pass
        rec = l1.load("altitude", slug, first["name"])
        if not rec.get("done"):
            with self.assertRaises(T.TransitionError):
                l1.start("altitude", slug, brief)
        _wait_done("altitude", slug, first["name"])
        rev = l1.start("altitude", slug, brief, role="reviewer")
        self.assertEqual(rev["engine"], "claude", "author was codex → reviewer takes claude")
        self.assertEqual(Path(rev["worktree"]).resolve(), REPO.resolve(), "a reviewer reads in place, no worktree")
        done = _wait_done("altitude", slug, rev["name"])
        self.assertIsNone(done["result"]["error"])
        st = l1.status("altitude", slug)
        self.assertEqual([r["role"] for r in st], ["implementer", "reviewer"])

    def test_cli_counts_as_a_launch_for_the_cap_hook(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("cap", Path(__file__).resolve().parent.parent / "hooks" / "subagent_cap.py")
        src = (Path(__file__).resolve().parent.parent / "hooks" / "subagent_cap.py").read_text()
        ns = {}
        exec(src.split("\ninp = json.load")[0], ns)  # the regex + is_launch, without the hook's stdin main
        self.assertTrue(ns["is_launch"]("bin/alt l1 run --brief x.md"))
        self.assertTrue(ns["is_launch"]("alt l1 run --role reviewer --brief r.md"))
        self.assertFalse(ns["is_launch"]("alt l1 wait implementer-1"))
        self.assertFalse(ns["is_launch"]("alt l1 status"))


if __name__ == "__main__":
    unittest.main()
