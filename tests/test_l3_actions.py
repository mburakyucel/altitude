"""Codex L3 actions are one-at-a-time, owner-fenced, and durably idempotent."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(tempfile.mkdtemp(prefix="altitude-l3-actions-"))
os.environ["ALTITUDE_HOME"] = str(_ROOT / "state")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, engines, l3_actions, recovery, state as S, tasks as T  # noqa: E402


def envelope(action):
    defaults = {"slug": None, "title": None, "text": None, "reason": None, "request": None,
                "digest": None, "answer": None, "source": None, "engine": None, "model": None,
                "paths": [], "hold_merge": None, "merge_hold": None, "incident": None, "labels": []}
    return {"message": "handled", "actions": [{**defaults, **action}]}


class TestL3Actions(unittest.TestCase):
    def setUp(self):
        self.repo = _ROOT / "repo"
        self.repo.mkdir(exist_ok=True)
        config.ensure_root()
        config.save_projects({"p": {"name": "p", "path": str(self.repo)}})
        recovery.hold_path().unlink(missing_ok=True)
        recovery.clearance_history_path().unlink(missing_ok=True)
        for directory in (S.tasks_dir("p"), S.archive_dir("p"), config.project_dir("p") / "l3-actions"):
            if directory.exists():
                for path in sorted(directory.rglob("*"), reverse=True):
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()
        real_apply = l3_actions.apply
        def apply_bound(project, value, **kwargs):
            if "recovery_observation" not in kwargs:
                kwargs["recovery_observation"] = recovery.observe_l3_state(project)
            return real_apply(project, value, **kwargs)
        self.apply_patch = mock.patch.object(l3_actions, "apply", side_effect=apply_bound)
        self.apply_patch.start()

    def tearDown(self):
        self.apply_patch.stop()

    def task(self, title="Task", state="running", **updates):
        task = T.new("p", title, "request")
        task.update({"state": state, "dispatch_id": f"{task['slug']}-1", "session_id": "session-1",
                     "agent_id": "agent-1", "l2_engine": "codex", **updates})
        S.save_task("p", task)
        return task

    def test_new_task_accepts_the_live_codex_brief_in_text(self):
        brief = "Implement issue #127 as one focused documentation change."
        action = {
            "type": "new_task", "slug": "implement-github-issue-127",
            "title": "Implement GitHub issue #127", "text": brief, "request": None,
            "source": "chat", "engine": "codex", "model": None, "paths": [],
            "reason": None, "digest": None, "answer": None, "hold_merge": None,
            "merge_hold": None, "incident": None, "labels": [],
        }

        source = "Please implement GitHub issue #127 now."
        result = l3_actions.apply("p", envelope(action), action_id="0" * 64,
                                  github_issue_source=source)
        task = S.load_task("p", result[0]["slug"])

        self.assertEqual(task["state"], "queued")
        self.assertEqual(task["engine"], "codex")
        self.assertEqual((S.task_dir("p", task["slug"]) / "request.md").read_text(), brief + "\n")

    def test_new_task_prefers_request_and_rejects_two_empty_brief_fields(self):
        explicit = {"type": "new_task", "title": "Explicit", "request": "canonical brief",
                    "text": "fallback brief", "source": "chat"}
        result = l3_actions.apply("p", envelope(explicit), action_id="a" * 64)
        slug = result[0]["slug"]
        self.assertEqual((S.task_dir("p", slug) / "request.md").read_text(), "canonical brief\n")

        empty = {"type": "new_task", "title": "Empty", "request": None, "text": "  "}
        with self.assertRaisesRegex(l3_actions.L3ActionError, "requires request"):
            l3_actions.apply("p", envelope(empty), action_id="b" * 64)
        self.assertFalse(S.task_dir("p", "empty").exists())
        self.assertFalse((config.project_dir("p") / "l3-actions" / ("b" * 64 + ".json")).exists())

    def test_new_issue_task_must_be_authorized_by_the_current_user_message(self):
        action = {"type": "new_task", "title": "Implement GitHub issue #127",
                  "request": "Implement issue #127 as one focused documentation change."}
        with self.assertRaisesRegex(l3_actions.L3ActionError, "not authorized"):
            l3_actions.apply("p", envelope(action), action_id="4" * 64,
                             github_issue_source="Please improve the documentation")
        self.assertFalse(S.task_dir("p", "implement-github-issue-127").exists())

    def test_one_turn_cannot_apply_multiple_actions(self):
        value = {"message": "too many", "actions": [
            {"type": "task_fyi", "slug": "a", "text": "one"},
            {"type": "task_fyi", "slug": "b", "text": "two"},
        ]}
        with self.assertRaisesRegex(l3_actions.L3ActionError, "at most one"):
            l3_actions.apply("p", value, action_id="a" * 64)
        self.assertEqual(list((config.project_dir("p") / "l3-actions").glob("*.json")), [])

    def test_python_ingestion_matches_closed_schema_types_before_claim(self):
        invalid = [
            {"type": "task_hold_merge", "slug": "x", "merge_hold": "false"},
            {"type": "new_task", "title": "x", "request": "y", "model": {}},
            {"type": "incident_new", "incident": {"id": None, "title": "x", "task": None,
             "what": "w", "evidence": "e", "cause": "c", "tags": [1], "status": None}},
            {"type": "task_fyi", "slug": "x", "text": ["not", "text"]},
            {"type": "new_task", "title": "x", "request": "y", "engine": "claude"},
        ]
        for index, action in enumerate(invalid):
            action_id = f"{index + 1:x}" * 64
            with self.assertRaisesRegex(l3_actions.L3ActionError, "unknown L3 action"):
                l3_actions.apply("p", envelope(action), action_id=action_id)
            self.assertFalse(l3_actions._journal_path("p", action_id).exists())  # noqa: SLF001

    def test_corrupt_existing_action_journal_is_never_overwritten(self):
        action = envelope({"type": "task_fyi", "slug": "x", "text": "note"})
        for index, raw in enumerate(("", "[]", "x" * 70000)):
            action_id = f"{index + 10:x}" * 64
            path = l3_actions._journal_path("p", action_id)  # noqa: SLF001
            S.atomic_write(path, raw); before = path.read_bytes()
            with self.assertRaises(l3_actions.L3ActionError):
                l3_actions.apply("p", action, action_id=action_id)
            self.assertEqual(path.read_bytes(), before)

    def test_schema_valid_oversized_action_is_rejected_before_journal_or_effect(self):
        action_id = "f" * 64
        value = envelope({"type": "task_fyi", "slug": "missing", "text": "x" * 70000})
        with mock.patch.object(l3_actions, "_execute") as execute, \
             self.assertRaisesRegex(l3_actions.L3ActionError, "reserved terminal headroom"):
            l3_actions.apply("p", value, action_id=action_id)
        execute.assert_not_called()
        self.assertFalse(l3_actions._journal_path("p", action_id).exists())  # noqa: SLF001

    def test_near_boundary_action_always_has_room_for_terminal_receipt(self):
        action_id = "e" * 64
        action = envelope({"type": "task_fyi", "slug": "bounded", "text": ""})["actions"][0]
        candidate = {"id": action_id, "at": S.now(), "status": "applying", "action": action}
        room = l3_actions._JOURNAL_APPLYING_CAP - len(  # noqa: SLF001
            l3_actions._journal_text(candidate).encode("utf-8")) - 16  # noqa: SLF001
        action["text"] = "x" * room
        self.assertIsNone(l3_actions._claim("p", action_id, action))  # noqa: SLF001
        l3_actions._finish("p", action_id, action, result=[{  # noqa: SLF001
            "type": "task_fyi", "slug": "bounded"}])
        path = l3_actions._journal_path("p", action_id)  # noqa: SLF001
        self.assertLessEqual(path.stat().st_size, l3_actions._JOURNAL_CAP)  # noqa: SLF001
        self.assertEqual(S.read_json(path)["status"], "complete")

    def test_completed_action_replays_its_result_without_repeating_side_effect(self):
        task = self.task(state="blocked")
        value = envelope({"type": "task_fyi", "slug": task["slug"], "text": "one durable note"})
        first = l3_actions.apply("p", value, action_id="b" * 64)
        second = l3_actions.apply("p", value, action_id="b" * 64)
        self.assertEqual(first, second)
        lines = (config.project_dir("p") / "inbox.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)

    def test_interrupted_actions_reconcile_only_with_domain_native_markers(self):
        safe = {"type": "github_issue", "title": "Stable draft", "text": "Stable draft request", "labels": []}
        safe_id = "7" * 64
        S.write_json(l3_actions._journal_path("p", safe_id), {  # noqa: SLF001
            "id": safe_id, "at": S.now(), "status": "applying", "action": envelope(safe)["actions"][0],
        })
        result = l3_actions.apply("p", envelope(safe), action_id=safe_id,
                                  github_issue_source="Stable draft request")
        self.assertTrue(result[0]["pending_review"])
        self.assertEqual(S.read_json(l3_actions._journal_path("p", safe_id))["status"], "complete")  # noqa: SLF001

        unsafe = {"type": "task_fyi", "slug": "missing", "text": "ambiguous note"}
        unsafe_id = "8" * 64
        S.write_json(l3_actions._journal_path("p", unsafe_id), {  # noqa: SLF001
            "id": unsafe_id, "at": S.now(), "status": "applying", "action": envelope(unsafe)["actions"][0],
        })
        with mock.patch.object(l3_actions, "_execute") as execute, \
             self.assertRaisesRegex(l3_actions.L3ActionError, "reconciliation_required"):
            l3_actions.apply("p", envelope(unsafe), action_id=unsafe_id)
        execute.assert_not_called()

    def test_ambiguous_action_has_no_b2_disposition_escape(self):
        action = {"type": "task_fyi", "slug": "missing", "text": "ambiguous note"}
        action_id = "6" * 64
        S.write_json(l3_actions._journal_path("p", action_id), {  # noqa: SLF001
            "id": action_id, "at": S.now(), "status": "applying", "action": envelope(action)["actions"][0],
        })
        self.assertFalse(hasattr(l3_actions, "resolve_reconciliation"))
        with mock.patch.object(l3_actions, "_execute") as execute, \
             self.assertRaisesRegex(l3_actions.L3ActionError, "reconciliation_required"):
            l3_actions.apply("p", envelope(action), action_id=action_id)
        execute.assert_not_called()

    def test_partial_local_mutation_remains_applying_and_fences_a_fresh_action_id(self):
        task = self.task(state="blocked")
        action = {"type": "task_fyi", "slug": task["slug"], "text": "one durable note"}
        real_fyi = T.fyi

        def mutate_then_fail(*args, **kwargs):
            real_fyi(*args, **kwargs)
            raise l3_actions.L3ActionError("event projection failed after inbox append")

        with mock.patch.object(T, "fyi", side_effect=mutate_then_fail), \
             self.assertRaisesRegex(l3_actions.L3ActionError, "reconciliation_required"):
            l3_actions.apply("p", envelope(action), action_id="1" * 64)
        record = S.read_json(l3_actions._journal_path("p", "1" * 64))  # noqa: SLF001
        self.assertEqual(record["status"], "applying"); self.assertIn("projection", record["last_error"])
        lines = (config.project_dir("p") / "inbox.jsonl").read_text().splitlines()

        with mock.patch.object(T, "fyi") as repeat, \
             self.assertRaisesRegex(l3_actions.L3ActionError, "reconciliation_required"):
            l3_actions.apply("p", envelope(action), action_id="1" * 64)
        repeat.assert_not_called()
        self.assertEqual((config.project_dir("p") / "inbox.jsonl").read_text().splitlines(), lines)
        self.assertFalse(l3_actions._journal_path("p", "2" * 64).exists())  # noqa: SLF001

    def test_stale_recovery_precheck_blocks_before_action_claim(self):
        task = self.task(state="blocked")
        action_id = "f" * 64
        inbox = config.project_dir("p") / "inbox.jsonl"
        before = inbox.read_bytes() if inbox.exists() else b""
        with self.assertRaisesRegex(l3_actions.L3ActionError, "stale permit"):
            l3_actions.apply(
                "p", envelope({"type": "task_fyi", "slug": task["slug"], "text": "do not append"}),
                action_id=action_id, effect_precheck=lambda: (_ for _ in ()).throw(RuntimeError("stale permit")),
            )
        self.assertFalse((config.project_dir("p") / "l3-actions" / f"{action_id}.json").exists())
        self.assertEqual(inbox.read_bytes() if inbox.exists() else b"", before)

    def test_local_mutation_permission_drains_before_hold_returns(self):
        task = self.task(state="blocked")
        observation = recovery.observe_l3_state("p")
        entered, release, applied = threading.Event(), threading.Event(), []
        real_fyi = T.fyi
        def mutate_then_wait(*args, **kwargs):
            result = real_fyi(*args, **kwargs); entered.set(); release.wait(2); return result
        action = envelope({"type": "task_fyi", "slug": task["slug"], "text": "before hold"})
        worker = threading.Thread(target=lambda: applied.append(l3_actions.apply(
            "p", action, action_id="c" * 64, recovery_observation=observation)))
        with mock.patch.object(T, "fyi", side_effect=mutate_then_wait):
            worker.start(); self.assertTrue(entered.wait(1))
            holder = threading.Thread(target=lambda: recovery.hold("race", kind="race"))
            holder.start()
            for _ in range(100):
                if recovery.status(): break
                threading.Event().wait(.005)
            self.assertIsNotNone(recovery.status()); self.assertTrue(holder.is_alive())
            release.set(); worker.join(2); holder.join(2)
        self.assertEqual(len(applied), 1)
        inbox = (config.project_dir("p") / "inbox.jsonl").read_text().splitlines()
        with mock.patch.object(T, "fyi") as stale, \
             self.assertRaisesRegex(l3_actions.L3ActionError, "stale"):
            l3_actions.apply("p", envelope({"type": "task_fyi", "slug": task["slug"], "text": "stale"}),
                action_id="d" * 64, recovery_observation=observation,
                effect_precheck=lambda: (_ for _ in ()).throw(RuntimeError("stale recovery observation")))
        stale.assert_not_called()
        self.assertEqual((config.project_dir("p") / "inbox.jsonl").read_text().splitlines(), inbox)

    def test_l3_never_blocks_or_completes_a_live_worker(self):
        task = self.task()
        live = {"id": "agent-1", "state": "working", "status": "busy"}
        with mock.patch.object(engines, "codex_worker", return_value=live):
            with self.assertRaisesRegex(l3_actions.L3ActionError, "cannot block a live"):
                l3_actions.apply("p", envelope({"type": "task_block", "slug": task["slug"],
                                                "reason": "stop"}), action_id="c" * 64)
        self.assertEqual(S.load_task("p", task["slug"])["state"], "running")

        task["state"] = "reported"
        S.save_task("p", task)
        with mock.patch.object(engines, "codex_worker", return_value=live):
            with self.assertRaisesRegex(l3_actions.L3ActionError, "no live L2"):
                l3_actions.apply("p", envelope({"type": "task_done", "slug": task["slug"],
                                                "digest": "done"}), action_id="d" * 64)
        self.assertEqual(S.load_task("p", task["slug"])["state"], "reported")

    def test_missing_codex_worker_record_fails_closed(self):
        task = self.task(state="reported", l2_engine="codex")
        with mock.patch.object(engines, "codex_worker", return_value=None):
            with self.assertRaisesRegex(l3_actions.L3ActionError, "worker record is missing"):
                l3_actions.apply("p", envelope({"type": "task_done", "slug": task["slug"],
                                                "digest": "done"}), action_id="9" * 64)
        self.assertEqual(S.load_task("p", task["slug"])["state"], "reported")

    def test_remote_publication_and_recovery_commands_are_dormant_before_claim(self):
        for index, kind in enumerate(("github_issue_approve", "recovery_hold", "recovery_clear", "task_resume")):
            action_id = f"{index + 1:x}" * 64
            before = recovery.hold_path().read_bytes() if recovery.hold_path().exists() else None
            with self.assertRaisesRegex(l3_actions.L3ActionError, "unknown L3 action"):
                l3_actions.apply("p", envelope({"type": kind, "digest": "a" * 24,
                                                "reason": "not active"}), action_id=action_id)
            self.assertFalse(l3_actions._journal_path("p", action_id).exists())  # noqa: SLF001
            self.assertEqual(recovery.hold_path().read_bytes() if recovery.hold_path().exists() else None, before)

    def test_active_recovery_cannot_create_an_ordinary_or_repair_task(self):
        held = recovery.hold("fault", kind="fault")
        recovery.request_l3_attention("p", kind="fault")
        claim = recovery.claim_l3_attention("p")
        observation = recovery.observe_l3_state("p", {
            "episode_id": held["episode"], "permit_revision": claim["revision"], "claim": claim["claim"]})
        for index, source in enumerate((None, "chat", "recovery")):
            action_id = f"{index + 5:x}" * 64
            with self.assertRaisesRegex(l3_actions.L3ActionError, "recovery repair delegation"):
                l3_actions.apply("p", envelope({"type": "new_task", "title": "repair", "request": "repair",
                                                "source": source}), action_id=action_id,
                                 recovery_observation=observation)
            self.assertFalse(l3_actions._journal_path("p", action_id).exists())  # noqa: SLF001
        self.assertEqual(S.list_tasks("p"), [])

    def test_github_issue_draft_cannot_include_hidden_context(self):
        safe_source = "Please preserve the sidecar transcript audit idea"
        hidden = {"type": "github_issue", "title": "sidecar transcript audit idea",
                  "text": safe_source + "\nprivate model-added detail", "labels": []}
        with self.assertRaisesRegex(l3_actions.L3ActionError, "exact current user message"):
            l3_actions.apply("p", envelope(hidden), action_id="7" * 64,
                             github_issue_source=safe_source)

    def test_security_mechanism_issue_is_private_until_exact_human_approval(self):
        source = "the sandbox lets a worker stop Altitude through the user bus"
        action = {"type": "github_issue", "title": "sandbox lets a worker stop Altitude",
                  "text": source, "labels": []}
        result = l3_actions.apply("p", envelope(action), action_id="5" * 64,
                                  github_issue_source=source)
        self.assertTrue(result[0]["pending_review"])


if __name__ == "__main__":
    unittest.main()
