"""Decision 44: an exhausted subscription window is a hold with a reset time — detected, held, auto-resumed."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-limit-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, engines, dispatch, tasks as T, digest  # noqa: E402

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
    def tearDown(self):
        engines.note_usage_limit("2000-01-01T00:00:00+00:00")  # never leave a live hold behind for other tests

    def test_hold_until_reset_then_clear(self):
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds")
        self.assertTrue(engines.note_usage_limit(future, "x"))
        self.assertFalse(engines.note_usage_limit(future, "x"), "same reset time is not news twice")
        self.assertEqual(engines.usage_hold(), future)
        self.assertTrue(dispatch.wip_hold("altitude").startswith("usage limit"))
        self.assertTrue(engines.claude_print("hi", cwd=Path(_TMP)).get("limited"), "no call is made while held")
        engines.note_usage_limit("2000-01-01T00:00:00+00:00")
        self.assertIsNone(engines.usage_hold())


class TestPollAndResume(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP, "stacks": ["python"]}})

    def test_idle_worker_at_the_limit_is_limited_not_needs_input(self):
        orig = engines.claude_agents, engines.claude_stop, S.list_tasks, dispatch.job_detail
        live = {"id": "w1", "sessionId": "s1", "status": "idle", "state": "blocked"}
        rows = iter([[live], []])  # initial inventory, then stop+requery proof
        engines.claude_agents = lambda: next(rows)
        engines.claude_stop = lambda agent_id: "stopped"
        S.list_tasks = lambda project: [{"slug": "lim", "state": "running", "session_id": "s1", "agent_id": "w1"}]
        dispatch.job_detail = lambda aid: (LIMIT, datetime(2026, 8, 30, 2, 40, tzinfo=timezone.utc))
        try:
            # This fixture exercises quota classification, not the legacy-worker migration.
            # Current Claude workers always have a generation-fenced broker; unbrokered
            # pre-deploy workers are deliberately stopped and recovered.
            with mock.patch.object(dispatch, "_adopt_claude_broker_for_task"):
                out = dispatch.poll("altitude")
        finally:
            engines.claude_agents, engines.claude_stop, S.list_tasks, dispatch.job_detail = orig
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].get("limited"), "2026-08-30T03:00:00+00:00", "read relative to when the worker wrote it, not to now")

    def test_job_detail_reads_the_file_and_its_time(self):
        with tempfile.TemporaryDirectory(prefix="altitude-jobs-") as tmp:
            original = dispatch.JOBS_DIR
            dispatch.JOBS_DIR = Path(tmp)
            try:
                d = dispatch.JOBS_DIR / "t-detail"; d.mkdir(parents=True, exist_ok=True)
                (d / "state.json").write_text(json.dumps({"state": "idle", "detail": LIMIT}))
                text, at = dispatch.job_detail("t-detail")
            finally:
                dispatch.JOBS_DIR = original
        self.assertEqual(text, LIMIT)
        self.assertLess((datetime.now(timezone.utc) - at).total_seconds(), 60)
        self.assertEqual(dispatch.job_detail("no-such-job"), ("", None))

    def test_resume_due_is_oldest_first_and_wip_throttled(self):
        past = "2026-01-01T00:00:00+00:00"
        base = {"class": "S", "state": "blocked", "resume_after": past, "updated": S.now()}
        for i, slug in enumerate(("c-newest", "a-oldest", "b-middle")):
            S.task_dir("altitude", slug).mkdir(parents=True, exist_ok=True)
            S.save_task("altitude", {**base, "slug": slug, "title": slug, "created": f"2026-08-30T0{['3', '1', '2'][i]}:00:00+00:00"})
        resumed, holds = [], iter([None, None, "WIP limit: 3 running"])
        orig = dispatch.resume_blocked, dispatch.wip_hold
        dispatch.resume_blocked = lambda project, slug, answer, prefix="": resumed.append(slug)
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
