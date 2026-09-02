"""An exhausted subscription window is detected, held until its reset time, and then resumed."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="altitude-limit-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, engines, dispatch, monitor, recovery, tasks as T, digest  # noqa: E402

LIMIT = "You've hit your session limit · resets 8pm (America/Los_Angeles)"


class TestDetect(unittest.TestCase):
    def test_text_with_zone(self):
        now = datetime(2026, 8, 30, 2, 55, tzinfo=timezone.utc)  # 7:55pm in LA → 8pm is 5 minutes away
        self.assertEqual(engines.usage_limit_in(LIMIT, now=now), "2026-08-30T03:00:00+00:00")

    def test_past_time_rolls_to_tomorrow(self):
        now = datetime(2026, 8, 30, 3, 30, tzinfo=timezone.utc)  # 8:30pm LA: the 8pm reset already happened → next one
        self.assertEqual(engines.usage_limit_in(LIMIT, now=now), "2026-08-31T03:00:00+00:00")

    def test_quota_epoch_wins(self):
        self.assertEqual(engines.usage_limit_in("", {"status": "rejected", "resetsAt": 1788058800}), "2026-08-30T03:00:00+00:00")

    def test_ordinary_text_is_not_a_limit(self):
        self.assertIsNone(engines.usage_limit_in("PR #32 ready; awaiting reviewer pass to merge + restart"))
        self.assertIsNone(engines.usage_limit_in("", {"status": "allowed", "resetsAt": 1}))


class TestHold(unittest.TestCase):
    def setUp(self):
        recovery.hold_path().unlink(missing_ok=True)
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP}})
        self._quota = monitor.quota
        monitor.quota = lambda: {"known": True}

    def tearDown(self):
        engines.note_usage_limit("2000-01-01T00:00:00+00:00")  # never leave a live hold behind for other tests
        monitor.quota = self._quota
        recovery.hold_path().unlink(missing_ok=True)

    def test_hold_until_reset_then_clear(self):
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds")
        self.assertTrue(engines.note_usage_limit(future, "x"))
        self.assertFalse(engines.note_usage_limit(future, "x"), "same reset time is not news twice")
        self.assertEqual(engines.usage_hold(), future)
        self.assertFalse((dispatch.wip_hold("altitude") or "").startswith("usage limit"),
                         "a Claude hold does not globally freeze Codex dispatch")
        with self.assertRaisesRegex(engines.EngineCapabilityError, "autonomous/mutating launch disabled"):
            engines.claude_print("hi", cwd=Path(_TMP))
        engines.note_usage_limit("2000-01-01T00:00:00+00:00")
        self.assertIsNone(engines.usage_hold())


class TestPollAndResume(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP}})
        cls._jobs_dir = dispatch.JOBS_DIR
        dispatch.JOBS_DIR = Path(_TMP) / ".claude" / "jobs"

    @classmethod
    def tearDownClass(cls):
        dispatch.JOBS_DIR = cls._jobs_dir

    def test_idle_worker_at_the_limit_is_limited_not_needs_input(self):
        orig = engines.claude_agents, S.list_tasks, dispatch.job_detail
        engines.claude_agents = lambda: [{"id": "w1", "sessionId": "s1", "status": "idle", "state": "blocked"}]
        S.list_tasks = lambda project: [{"slug": "lim", "state": "running", "session_id": "s1", "agent_id": "w1"}]
        dispatch.job_detail = lambda aid: (LIMIT, datetime(2026, 8, 30, 2, 40, tzinfo=timezone.utc))
        try:
            out = dispatch.poll("altitude")
        finally:
            engines.claude_agents, S.list_tasks, dispatch.job_detail = orig
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].get("limited"), "2026-08-30T03:00:00+00:00", "read relative to when the worker wrote it, not to now")

    def test_job_detail_reads_the_file_and_its_time(self):
        d = dispatch.JOBS_DIR / "t-detail"; d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps({"state": "idle", "detail": LIMIT}))
        text, at = dispatch.job_detail("t-detail")
        self.assertEqual(text, LIMIT)
        self.assertLess((datetime.now(timezone.utc) - at).total_seconds(), 60)
        self.assertEqual(dispatch.job_detail("no-such-job"), ("", None))

    def test_resume_due_is_oldest_first_and_wip_throttled(self):
        past = "2026-01-01T00:00:00+00:00"
        base = {"state": "blocked", "l2_engine": "codex", "resume_after": past, "updated": S.now()}
        for i, slug in enumerate(("c-newest", "a-oldest", "b-middle")):
            S.task_dir("altitude", slug).mkdir(parents=True, exist_ok=True)
            S.save_task("altitude", {**base, "slug": slug, "title": slug, "created": f"2026-08-30T0{['3', '1', '2'][i]}:00:00+00:00"})
        resumed, holds = [], iter([None, None, "WIP limit: 3 running"])
        orig = dispatch.resume_blocked, dispatch.wip_hold
        dispatch.resume_blocked = lambda project, slug, answer, prefix="", **_kwargs: resumed.append(slug)
        dispatch.wip_hold = lambda project, task=None: next(holds)
        try:
            back = dispatch.resume_due("altitude")
        finally:
            dispatch.resume_blocked, dispatch.wip_hold = orig
        self.assertEqual(back, ["a-oldest", "b-middle"])
        # the one still queued is Altitude's to resume: not a "Needs you" card, but listed as waiting for a slot
        self.assertNotIn("c-newest", [d["slug"] for d in T.decisions("altitude")])
        self.assertIn(("c-newest", "resume"), [(w["slug"], w["why"]) for w in digest.wip()["waiting"]])
        self.assertEqual(resumed, ["a-oldest", "b-middle"])
        self.assertIsNone(S.load_task("altitude", "a-oldest").get("resume_after"))
        self.assertEqual(S.load_task("altitude", "c-newest").get("resume_after"), past)


if __name__ == "__main__":
    unittest.main()
