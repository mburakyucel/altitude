"""An exhausted subscription window is detected, held until its reset time, and then resumed."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.support import AltitudeCase
from altitude import state as S, engines, dispatch, monitor, tasks as T, digest

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


class TestHold(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()  # the hold file this case writes is its own
        self.patch(monitor, "quota", return_value={"known": True})

    def test_hold_until_reset_then_clear(self):
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds")
        self.assertTrue(engines.note_usage_limit(future, "x"))
        self.assertFalse(engines.note_usage_limit(future, "x"), "same reset time is not news twice")
        self.assertEqual(engines.usage_hold(), future)
        self.assertFalse((dispatch.wip_hold(self.project) or "").startswith("usage limit"),
                         "a Claude hold does not globally freeze Codex dispatch")
        self.assertTrue(engines.claude_print("hi", cwd=self.repo).get("limited"), "no call is made while held")
        engines.note_usage_limit("2000-01-01T00:00:00+00:00")
        self.assertIsNone(engines.usage_hold())


class TestPollAndResume(AltitudeCase):
    def test_idle_worker_at_the_limit_is_limited_not_needs_input(self):
        self.patch(engines, "worker", return_value={
            "id": "w1", "sessionId": "s1", "status": "exited", "state": "failed", "detail": LIMIT,
            "detail_at": datetime(2026, 8, 30, 2, 40, tzinfo=timezone.utc).timestamp()})
        self.patch(S, "list_tasks",
                   return_value=[{"slug": "lim", "state": "running", "session_id": "s1", "agent_id": "w1"}])
        out = dispatch.poll(self.project)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].get("limited"), "2026-08-30T03:00:00+00:00", "read relative to when the worker wrote it, not to now")

    def test_job_detail_reads_the_file_and_its_time(self):
        d = engines.JOBS_DIR / "t-detail"; d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps({"state": "idle", "detail": LIMIT}))
        text, at = engines.claude_job_detail("t-detail")
        self.assertEqual(text, LIMIT)
        self.assertLess((datetime.now(timezone.utc) - at).total_seconds(), 60)
        self.assertEqual(engines.claude_job_detail("no-such-job"), ("", None))

    def test_resume_due_is_oldest_first_and_wip_throttled(self):
        past = "2026-01-01T00:00:00+00:00"
        base = {"state": "blocked", "resume_after": past, "updated": S.now(), "attempt": 1,
                "session_id": "s", "agent_id": "w"}
        for i, slug in enumerate(("c-newest", "a-oldest", "b-middle")):
            S.task_dir(self.project, slug).mkdir(parents=True, exist_ok=True)
            S.save_task(self.project, {**base, "slug": slug, "title": slug, "created": f"2026-08-30T0{['3', '1', '2'][i]}:00:00+00:00"})
        holds = iter([None, None, "WIP limit: 3 running"])
        with mock.patch.object(dispatch, "wip_hold", side_effect=lambda project, task=None: next(holds)), \
             mock.patch.object(engines, "usage_hold", return_value=None):
            back = dispatch.resume_due(self.project)
        self.assertEqual(back, ["a-oldest", "b-middle"])
        # the one still held is Altitude's to resume: not a "Needs you" card, but listed as waiting for a slot
        self.assertNotIn("c-newest", [d["slug"] for d in T.decisions(self.project)])
        self.assertIn(("c-newest", "resume"), [(w["slug"], w["why"]) for w in digest.wip()["waiting"]])


if __name__ == "__main__":
    unittest.main()
