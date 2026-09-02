import json
import fcntl
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


# A focused invocation of this module must be as isolated as full-suite discovery.
os.environ.setdefault("ALTITUDE_HOME", tempfile.mkdtemp(prefix="altitude-durable-state-"))

from altitude import state as S  # noqa: E402


class AtomicReplaceTests(unittest.TestCase):
    def test_atomic_write_fsyncs_file_then_renames_then_fsyncs_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            calls = []
            real_fsync = os.fsync
            real_replace = os.replace

            def recording_fsync(fd):
                kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
                calls.append(f"fsync-{kind}")
                return real_fsync(fd)

            def recording_replace(source, target):
                calls.append("replace")
                return real_replace(source, target)

            with mock.patch.object(S.os, "fsync", side_effect=recording_fsync), mock.patch.object(
                S.os, "replace", side_effect=recording_replace
            ):
                S.atomic_write(path, "new\n")

            self.assertEqual(calls, ["fsync-file", "replace", "fsync-directory"])
            self.assertEqual(path.read_text(), "new\n")

    def test_failed_replace_preserves_old_target_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("old\n")
            with mock.patch.object(S.os, "replace", side_effect=OSError("injected crash")):
                with self.assertRaisesRegex(OSError, "injected crash"):
                    S.atomic_write(path, "new\n")
            self.assertEqual(path.read_text(), "old\n")
            self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])


class KeyedJsonlTests(unittest.TestCase):
    def test_new_file_is_fsynced_and_its_directory_entry_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            with mock.patch.object(S, "_fsync_directory", wraps=S._fsync_directory) as sync_dir:
                self.assertTrue(S.append_jsonl(path, {"id": "one"}, key_field="id"))
            sync_dir.assert_called_once_with(path.parent)
            self.assertEqual(S.read_jsonl(path, key_field="id"), [{"id": "one"}])

    def test_invalid_partial_final_row_is_repaired_before_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            path.write_bytes(b'{"id":"one"}\n{"id":')
            S.append_jsonl(path, {"id": "two"}, key_field="id")
            self.assertEqual(
                S.read_jsonl(path, key_field="id"), [{"id": "one"}, {"id": "two"}]
            )
            self.assertTrue(path.read_bytes().endswith(b"\n"))

    def test_valid_unterminated_final_row_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            path.write_bytes(b'{"id":"one"}')
            S.append_jsonl(path, {"id": "two"}, key_field="id")
            self.assertEqual(path.read_bytes().count(b"\n"), 2)
            self.assertEqual(
                S.read_jsonl(path, key_field="id"), [{"id": "one"}, {"id": "two"}]
            )

    def test_corrupt_complete_row_fails_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            original = b'{"id":"one"}\nnot-json\n'
            path.write_bytes(original)
            with self.assertRaisesRegex(ValueError, "line 2"):
                S.append_jsonl(path, {"id": "two"}, key_field="id")
            self.assertEqual(path.read_bytes(), original)

    def test_identical_stable_id_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            record = {"id": "one", "value": 1}
            self.assertTrue(S.append_jsonl(path, record, key_field="id"))
            before = path.read_bytes()
            self.assertFalse(S.append_jsonl(path, record, key_field="id"))
            self.assertEqual(path.read_bytes(), before)

    def test_retry_after_ambiguous_fsync_failure_reconciles_and_fsyncs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            record = {"id": "one", "value": 1}
            real_fsync = os.fsync
            failed = False

            def fail_first_file_fsync(fd):
                nonlocal failed
                if not failed and not stat.S_ISDIR(os.fstat(fd).st_mode):
                    failed = True
                    raise OSError("injected fsync failure")
                return real_fsync(fd)

            with mock.patch.object(S.os, "fsync", side_effect=fail_first_file_fsync):
                with self.assertRaisesRegex(OSError, "injected fsync failure"):
                    S.append_jsonl(path, record, key_field="id")
                self.assertFalse(S.append_jsonl(path, record, key_field="id"))
            self.assertEqual(S.read_jsonl(path, key_field="id"), [record])

    def test_stable_id_conflict_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            S.append_jsonl(path, {"id": "one", "value": 1}, key_field="id")
            with self.assertRaisesRegex(ValueError, "conflicting JSONL record"):
                S.append_jsonl(path, {"id": "one", "value": 2}, key_field="id")

    def test_legacy_unkeyed_rows_remain_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            path.write_text('{"kind":"legacy"}\n')
            S.append_jsonl(
                path,
                {"event_id": "one", "kind": "current"},
                key_field="event_id",
            )
            self.assertEqual(
                S.read_jsonl(path, key_field="event_id"),
                [{"kind": "legacy"}, {"event_id": "one", "kind": "current"}],
            )

    def test_read_reconciles_exact_physical_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            row = json.dumps({"id": "one", "value": 1}, sort_keys=True)
            path.write_text(f"{row}\n{row}\n")
            self.assertEqual(
                S.read_jsonl(path, key_field="id"), [{"id": "one", "value": 1}]
            )

    def test_read_rejects_conflicting_physical_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            path.write_text('{"id":"one","value":1}\n{"id":"one","value":2}\n')
            with self.assertRaisesRegex(ValueError, "conflicting JSONL record"):
                S.read_jsonl(path, key_field="id")

    def test_append_waits_for_the_jsonl_file_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            path.touch()
            started = threading.Event()
            finished = threading.Event()

            def append_in_thread():
                started.set()
                S.append_jsonl(path, {"id": "one"}, key_field="id")
                finished.set()

            with open(path, "r+b") as locked:
                fcntl.flock(locked, fcntl.LOCK_EX)
                worker = threading.Thread(target=append_in_thread)
                worker.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(0.1))
                fcntl.flock(locked, fcntl.LOCK_UN)
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(finished.is_set())

    def test_reader_waits_for_the_jsonl_writer_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            path.write_text('{"id":"one"}\n')
            started = threading.Event()
            finished = threading.Event()

            def read_in_thread():
                started.set()
                S.read_jsonl(path, key_field="id")
                finished.set()

            with open(path, "r+b") as locked:
                fcntl.flock(locked, fcntl.LOCK_EX)
                worker = threading.Thread(target=read_in_thread)
                worker.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(0.1))
                fcntl.flock(locked, fcntl.LOCK_UN)
            worker.join(2)
            self.assertTrue(finished.is_set())

    def test_complete_non_object_tail_fails_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            original = b'{"id":"one"}\n[]'
            path.write_bytes(original)
            with self.assertRaisesRegex(ValueError, "expected object"):
                S.append_jsonl(path, {"id": "two"}, key_field="id")
            self.assertEqual(path.read_bytes(), original)

    def test_complete_corrupt_unterminated_tail_fails_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            original = b'{"id":"one"}\nnot-json'
            path.write_bytes(original)
            with self.assertRaisesRegex(ValueError, "line 2"):
                S.append_jsonl(path, {"id": "two"}, key_field="id")
            self.assertEqual(path.read_bytes(), original)

    def test_partial_final_utf8_codepoint_is_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            path.write_bytes(b'{"id":"one"}\n{"id":"two","text":"\xe2\x82')
            S.append_jsonl(path, {"id": "three"}, key_field="id")
            self.assertEqual(S.read_jsonl(path, key_field="id"),
                             [{"id": "one"}, {"id": "three"}])

    def test_partial_utf8_after_corrupt_json_fails_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.log"
            original = b'{"id":"one"}\nnot-json-\xe2\x82'
            path.write_bytes(original)
            with self.assertRaisesRegex(ValueError, "line 2"):
                S.append_jsonl(path, {"id": "three"}, key_field="id")
            self.assertEqual(path.read_bytes(), original)

    def test_nested_append_of_same_or_reverse_order_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with S.ordered_file_lock(root / "z.jsonl", S.LockLevel.OPERATION):
                with self.assertRaises(S.LockOrderError):
                    S.append_jsonl(root / "z.jsonl", {"id": "one"}, key_field="id")
                with self.assertRaises(S.LockOrderError):
                    S.append_jsonl(root / "a.jsonl", {"id": "one"}, key_field="id")

    def test_task_event_retry_can_supply_a_stable_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(S.config, "project_dir", return_value=root):
                (root / "tasks" / "task").mkdir(parents=True)
                fixed = {"at": "2026-09-02T00:00:00+00:00", "event_id": "stable"}
                S.append_event("demo", "task", "state", **fixed)
                S.append_event("demo", "task", "state", **fixed)
                events = S.read_events("demo", "task")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_id"], "stable")


class LockOrderTests(unittest.TestCase):
    def test_every_adjacent_level_contends_without_reversing_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, (outer, inner) in enumerate(zip(S.LOCK_ORDER, S.LOCK_ORDER[1:])):
                with self.subTest(outer=outer.name, inner=inner.name):
                    outer_path = root / f"{index}-outer-contention.lock"
                    inner_path = root / f"{index}-inner-contention.lock"
                    entered_outer = threading.Event()
                    finished = threading.Event()
                    errors = []

                    def acquire_in_order():
                        try:
                            with S.ordered_file_lock(outer_path, outer):
                                entered_outer.set()
                                with S.ordered_file_lock(inner_path, inner):
                                    pass
                        except BaseException as exc:
                            errors.append(exc)
                        finally:
                            finished.set()

                    with S.ordered_file_lock(inner_path, inner):
                        worker = threading.Thread(target=acquire_in_order)
                        worker.start()
                        self.assertTrue(entered_outer.wait(1))
                        self.assertFalse(finished.wait(0.05))
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(errors, [])

    def test_every_adjacent_level_allows_forward_and_rejects_reverse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, (outer, inner) in enumerate(zip(S.LOCK_ORDER, S.LOCK_ORDER[1:])):
                with self.subTest(outer=outer.name, inner=inner.name):
                    with S.ordered_file_lock(root / f"{index}-outer.lock", outer):
                        with S.ordered_file_lock(root / f"{index}-inner.lock", inner):
                            pass
                    with S.ordered_file_lock(root / f"{index}-reverse-outer.lock", inner):
                        with self.assertRaises(S.LockOrderError):
                            with S.ordered_file_lock(root / f"{index}-reverse-inner.lock", outer):
                                pass

    def test_same_level_locks_require_increasing_canonical_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with S.ordered_file_lock(root / "a.lock", S.LockLevel.TASK):
                with S.ordered_file_lock(root / "b.lock", S.LockLevel.TASK):
                    pass
            with S.ordered_file_lock(root / "b.lock", S.LockLevel.TASK):
                with self.assertRaises(S.LockOrderError):
                    with S.ordered_file_lock(root / "a.lock", S.LockLevel.TASK):
                        pass

    def test_project_lock_participates_in_the_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(S.config, "project_dir", return_value=root):
                with S.ordered_file_lock(root / "operation.lock", S.LockLevel.OPERATION):
                    with self.assertRaises(S.LockOrderError):
                        with S.project_lock("demo"):
                            pass

    def test_real_canonical_lock_wrappers_follow_the_declared_order(self):
        from altitude import dispatch, incidents, recovery

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = root / "project"
            task_root = project_root / "tasks" / "task"
            task_root.mkdir(parents=True)
            monitor = root / "monitor"
            with mock.patch.object(S.config, "project_dir", return_value=project_root), \
                    mock.patch.object(S.config, "MONITOR_DIR", monitor), \
                    mock.patch.object(incidents, "FAULTS", monitor / "faults.json"), \
                    mock.patch.object(recovery, "lock_path", return_value=monitor / "recovery-hold.lock"), \
                    mock.patch.object(recovery, "launch_lock_path", return_value=monitor / "recovery-launch.lock"):
                with incidents._fault_lock():  # noqa: SLF001 - exercise the production lock
                    with recovery._lock():  # noqa: SLF001
                        with recovery._launch_lock():  # noqa: SLF001
                            with S.project_lock("demo"):
                                with S.task_lock(task_root / ".resume.lock"):
                                    with S.task_lock(task_root / "l1" / ".lock"):
                                        with S.ordered_file_lock(
                                            project_root / "incidents" / ".alloc.lock",
                                            S.LockLevel.OPERATION,
                                        ):
                                            with dispatch.publication_settlement("demo"):
                                                pass

    def test_real_publication_to_recovery_reverse_order_is_refused(self):
        from altitude import dispatch, recovery

        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            with mock.patch.object(S.config, "project_dir", return_value=project_root):
                with dispatch.publication_settlement("demo"):
                    with self.assertRaises(S.LockOrderError):
                        with recovery._lock():  # noqa: SLF001
                            pass


class TaskTransitionReconciliationTests(unittest.TestCase):
    def _seed(self, root: Path, *, state: str = "running") -> dict:
        task = {"slug": "task", "title": "Task", "state": state, "created": S.now(),
                "updated": S.now(), "blocked_reason": None, "agent_id": None}
        directory = root / "tasks" / "task"
        directory.mkdir(parents=True)
        S.write_json(directory / "status.json", task)
        return task

    def test_crash_between_state_and_event_reconciles_on_idempotent_retry(self):
        from altitude import tasks as T

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            real_append = S.append_jsonl
            failed = False

            def fail_first_event(path, record, *, key_field):
                nonlocal failed
                if not failed and Path(path).name == "events.log":
                    failed = True
                    raise OSError("injected state/event crash")
                return real_append(path, record, key_field=key_field)

            with mock.patch.object(S.config, "project_dir", return_value=root):
                with mock.patch.object(S, "append_jsonl", side_effect=fail_first_event):
                    with self.assertRaisesRegex(OSError, "state/event crash"):
                        T.block("demo", "task", "wait", actor="altd")
                persisted = S.load_task("demo", "task")
                self.assertEqual(persisted["state"], "blocked")
                self.assertEqual(persisted["transition"]["event_kind"], "state")
                transition_id = persisted["transition"]["transition_id"]
                self.assertEqual(S.read_events("demo", "task"), [])

                retried = T.block("demo", "task", "wait", actor="altd")
                self.assertEqual(retried["transition"]["transition_id"], transition_id)
                events = S.read_events("demo", "task")
                self.assertEqual([event["event_id"] for event in events], [transition_id])

    def test_next_mutation_reconciles_prior_transition_before_new_state(self):
        from altitude import tasks as T

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            real_append = S.append_jsonl
            with mock.patch.object(S.config, "project_dir", return_value=root):
                with mock.patch.object(S, "append_jsonl", side_effect=OSError("between")):
                    with self.assertRaisesRegex(OSError, "between"):
                        T.block("demo", "task", "wait")
                blocked_id = S.load_task("demo", "task")["transition"]["transition_id"]
                with mock.patch.object(S, "append_jsonl", wraps=real_append):
                    T.resume("demo", "task")
                events = S.read_events("demo", "task")
                self.assertEqual(events[0]["event_id"], blocked_id)
                self.assertEqual([event["to"] for event in events], ["blocked", "running"])

    def test_merge_hold_is_state_first_and_retry_idempotent(self):
        from altitude import tasks as T

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            with mock.patch.object(S.config, "project_dir", return_value=root):
                with mock.patch.object(S, "append_jsonl", side_effect=OSError("between")):
                    with self.assertRaisesRegex(OSError, "between"):
                        T.set_hold_merge("demo", "task", "review", actor="l3")
                persisted = S.load_task("demo", "task")
                transition_id = persisted["transition"]["transition_id"]
                self.assertEqual(persisted["hold_merge"], "review")
                retried = T.set_hold_merge("demo", "task", "review", actor="l3")
                self.assertEqual(retried["transition"]["transition_id"], transition_id)
                self.assertEqual(len(S.read_events("demo", "task")), 1)


if __name__ == "__main__":
    unittest.main()
