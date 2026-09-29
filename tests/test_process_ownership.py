"""Issue #543: a PID surviving numerically across recreation is not the same resume owner."""
import os
from unittest import mock

from tests.support import AltitudeCase
from altitude import dispatch, platform, state as S, tasks as T


class TestProcessOwnership(AltitudeCase):
    def test_process_evidence_distinguishes_start_boot_namespace_and_zombie(self):
        proc = self.tmp / "proc"
        (proc / "sys/kernel/random").mkdir(parents=True)
        boot = proc / "sys/kernel/random/boot_id"
        boot.write_text("fixture-boot")
        (proc / "42/ns").mkdir(parents=True)
        namespace = proc / "42/ns/pid"
        namespace.symlink_to("pid:[123]")
        stat = proc / "42/stat"
        def write_stat(start="777", state="R"):
            stat.write_text("42 (fixture) " + " ".join([state, *["0"] * 18, start]))
        write_stat()
        with mock.patch.object(platform, "PROC", proc):
            identity = platform.process_identity(42)
            self.assertTrue(platform.process_identity_live(identity))
            for field, value in (("start", "778"), ("boot", "other-boot"), ("namespace", "pid:[456]")):
                with self.subTest(field=field):
                    self.assertFalse(platform.process_identity_live({**identity, field: value}))
            write_stat(state="Z")
            self.assertFalse(platform.process_identity_live(identity))
            stat.unlink()
            self.assertFalse(platform.process_identity_live(identity))
            write_stat()
            with mock.patch.object(platform.os, "readlink", side_effect=PermissionError("other UID")) as readlink:
                self.assertFalse(platform.process_identity_live({**identity, "start": "old-start"}))
                self.assertFalse(platform.process_identity_live({**identity, "boot": "old-boot"}))
                readlink.assert_not_called()
                with self.assertRaises(PermissionError):
                    platform.process_identity_live(identity)
        self.assertFalse(platform.process_identity_live(None))
        self.assertFalse(dispatch._claim_owner_live({"owner_pid": os.getpid()}))

    def test_resume_claim_records_lifetime_and_numeric_pid_collision_is_stale(self):
        task = T.new(self.project, "Fictional resume identity", "Keep durable ownership")
        task.update(state="blocked", attempt=1, blocked_reason="Fixture decision", agent_id="fixture", session_id="fixture-session")
        S.save_task(self.project, task)
        T.message(self.project, task["slug"], "l3", "Continue once", by="l3")
        claim = T.claim_resume(self.project, task["slug"])
        self.assertNotIn("owner_pid", claim)
        self.assertTrue(dispatch._claim_owner_live(claim))
        saved = S.load_task(self.project, task["slug"])["resume_claim"]
        self.assertEqual(saved["owner_process"], platform.process_identity(os.getpid()))
        # The same numeric PID in a recreated PID namespace cannot retain the old claim.
        saved["owner_process"]["namespace"] = "pid:[fictional-previous-container]"
        self.assertFalse(dispatch._claim_owner_live(saved))
        self.assertEqual(saved["messages"][0]["text"], "Continue once")
