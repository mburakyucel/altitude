import ast
from collections import Counter
import fcntl
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("ALTITUDE_HOME", tempfile.mkdtemp(prefix="altitude-durable-state-"))

from altitude import state as S  # noqa: E402


class AtomicReplaceTests(unittest.TestCase):
    def test_file_replace_parent_fsync_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, calls = Path(tmp) / "state.json", []
            real_fsync, real_replace = os.fsync, os.replace

            def fsync(fd):
                calls.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
                return real_fsync(fd)

            def replace(source, target):
                calls.append("replace")
                return real_replace(source, target)

            with mock.patch.object(S.os, "fsync", side_effect=fsync), \
                    mock.patch.object(S.os, "replace", side_effect=replace):
                S.atomic_write(path, "new\n")
            self.assertEqual(calls, ["file", "replace", "dir"])

    def test_subprocess_killed_before_replace_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("old\n")
            code = (
                "import os,signal,sys; from pathlib import Path; from altitude import state as S; "
                "S.os.replace=lambda *a: os.kill(os.getpid(), signal.SIGKILL); "
                "S.atomic_write(Path(sys.argv[1]), 'new\\n')"
            )
            child = subprocess.run([sys.executable, "-c", code, str(path)], cwd=Path(__file__).parents[1])
            self.assertEqual(child.returncode, -signal.SIGKILL)
            self.assertEqual(path.read_text(), "old\n")
            S.atomic_write(path, "restarted\n")
            self.assertEqual(path.read_text(), "restarted\n")


class KeyedJsonlTests(unittest.TestCase):
    def test_repairs_only_incomplete_object_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_bytes(b'{"id":"one"}\n  {"id":   ')
            S.append_jsonl(path, {"id": "two"}, key_field="id")
            self.assertEqual(S.read_jsonl(path, key_field="id"), [{"id": "one"}, {"id": "two"}])

    def test_committed_non_object_rows_fail_unchanged(self):
        tails = [b"[1]", b'"open"', b"true", b"12", b"null"]
        for tail in tails:
            with self.subTest(tail=tail), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                original = b'{"id":"one"}\n' + tail + b"\n"
                path.write_bytes(original)
                with self.assertRaises(ValueError):
                    S.append_jsonl(path, {"id": "two"}, key_field="id")
                self.assertEqual(path.read_bytes(), original)

    def test_every_torn_byte_prefix_of_an_object_is_repairable(self):
        record = {
            "array": [1, {"nested": True}], "id": "two", "null": None,
            "number": -1.25e-10, "text": "snowman ☃ and slash \\u1234",
        }
        encoded = S._canonical_json(record)
        for length in range(1, len(encoded)):
            with self.subTest(length=length), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                path.write_bytes(b'{"id":"one"}\n' + encoded[:length])
                S.append_jsonl(path, {"id": "recovered"}, key_field="id")
                self.assertEqual(S.read_jsonl(path, key_field="id"), [
                    {"id": "one"}, {"id": "recovered"},
                ])

    def test_uncommitted_whitespace_is_repaired_but_committed_blank_rows_fail(self):
        for original in (b"  \t", b'{"id":"seed"}\n  \t'):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                path.write_bytes(original)
                S.append_jsonl(path, {"id": "one"}, key_field="id")
                S.append_jsonl(path, {"id": "two"}, key_field="id")
                expected = ([] if b"seed" not in original else [{"id": "seed"}])
                self.assertEqual(S.read_jsonl(path, key_field="id"), expected + [
                    {"id": "one"}, {"id": "two"},
                ])
                self.assertNotIn(b"\n\n", path.read_bytes())
        for original in (b"\n", b'{"id":"seed"}\n\n'):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                path.write_bytes(original)
                with self.assertRaises(ValueError):
                    S.read_jsonl(path, key_field="id")
                with self.assertRaises(ValueError):
                    S.append_jsonl(path, {"id": "one"}, key_field="id")
                self.assertEqual(path.read_bytes(), original)

    def test_complete_invalid_object_fails_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            original = b'{"id":"one"}\n{"id": nope}\n'
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                S.append_jsonl(path, {"id": "two"}, key_field="id")
            self.assertEqual(path.read_bytes(), original)

    def test_corrupt_non_final_row_fails_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            original = b'{"id":"one"}\nnot-json\n{"id":"two"}\n'
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                S.append_jsonl(path, {"id": "three"}, key_field="id")
            self.assertEqual(path.read_bytes(), original)

    def test_reader_never_exposes_an_unterminated_tail_before_repair(self):
        for tail in (b'{"id":"uncommitted"}', b'{"id":"partial'):
            with self.subTest(tail=tail), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                original = b'{"id":"one"}\n' + tail
                path.write_bytes(original)
                self.assertEqual(S.read_jsonl(path, key_field="id"), [{"id": "one"}])
                self.assertEqual(path.read_bytes(), original)

    def test_append_discards_valid_and_conflicting_unterminated_tails(self):
        tails = [b'{"id":"tail"}', b'{"id":"one","value":2}']
        for tail in tails:
            with self.subTest(tail=tail), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                path.write_bytes(b'{"id":"one","value":1}\n' + tail)
                S.append_jsonl(path, {"id": "two"}, key_field="id")
                self.assertEqual(path.read_bytes(),
                                 b'{"id":"one","value":1}\n{"id":"two"}\n')

    def test_carriage_return_is_content_not_a_record_delimiter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            original = b'{"id":"one"}\r{"id":"two"}\n'
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                S.read_jsonl(path, key_field="id")
            with self.assertRaises(ValueError):
                S.append_jsonl(path, {"id": "three"}, key_field="id")
            self.assertEqual(path.read_bytes(), original)

    def test_append_replaces_a_only_unterminated_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_bytes(b'{"id":"one"}')
            S.append_jsonl(path, {"id": "two"}, key_field="id")
            self.assertEqual(path.read_bytes(), b'{"id":"two"}\n')

    def test_stable_key_dedupes_and_conflict_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            row = {"id": "one", "value": 1}
            self.assertTrue(S.append_jsonl(path, row, key_field="id"))
            before = path.read_bytes()
            self.assertFalse(S.append_jsonl(path, row, key_field="id"))
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "conflicting JSONL"):
                S.append_jsonl(path, {"id": "one", "value": 2}, key_field="id")

    def test_stable_key_comparison_preserves_json_scalar_types(self):
        conflicts = [(True, 1), (1, 1.0), (0, -0.0)]
        for first, second in conflicts:
            with self.subTest(first=first, second=second), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                S.append_jsonl(path, {"id": "one", "value": first}, key_field="id")
                original = path.read_bytes()
                with self.assertRaisesRegex(ValueError, "conflicting JSONL"):
                    S.append_jsonl(path, {"id": "one", "value": second}, key_field="id")
                self.assertEqual(path.read_bytes(), original)

    def test_non_finite_numbers_are_not_json(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                with self.assertRaises(ValueError):
                    S.append_jsonl(path, {"id": "one", "value": value}, key_field="id")
                self.assertFalse(path.exists())

    def test_existing_strict_json_violations_fail_without_mutation(self):
        rows = [
            b'{"id":"one","id":"two"}\n',
            b'{"id":"one","value":1e400}\n',
            b'{"id":"one","value":NaN}\n',
            b'{"id":"one","value":"\\ud800"}\n',
        ]
        for original in rows:
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                path.write_bytes(original)
                with self.assertRaises(ValueError):
                    S.read_jsonl(path)
                with self.assertRaises(ValueError):
                    S.append_jsonl(path, {"id": "new"}, key_field="id")
                self.assertEqual(path.read_bytes(), original)

    def test_producer_rejects_non_json_nested_values_and_key_collisions(self):
        records = [
            {"id": "one", "nested": {1: "integer key"}},
            {"id": "one", "nested": {1: "integer", "1": "string"}},
            {"id": "one", "nested": (1, 2)},
            {"id": "one", "nested": "\ud800"},
        ]
        for record in records:
            with self.subTest(record=repr(record)), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                with self.assertRaises((TypeError, ValueError, UnicodeError)):
                    S.append_jsonl(path, record, key_field="id")
                self.assertFalse(path.exists())

    def test_every_existing_keyed_row_requires_a_nonempty_string_key(self):
        for row in ({"value": 1}, {"id": ""}, {"id": 1}):
            with self.subTest(row=row), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                original = (json.dumps(row) + "\n").encode()
                path.write_bytes(original)
                with self.assertRaisesRegex(ValueError, "non-empty string"):
                    S.append_jsonl(path, {"id": "two"}, key_field="id")
                self.assertEqual(path.read_bytes(), original)
                with self.assertRaisesRegex(ValueError, "non-empty string"):
                    S.read_jsonl(path, key_field="id")

    def test_reader_dedupes_exact_and_rejects_conflicting_physical_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text('{"id":"one","value":1}\n{"id":"one","value":1}\n')
            self.assertEqual(S.read_jsonl(path, key_field="id"), [{"id": "one", "value": 1}])
            path.write_text('{"id":"one","value":1}\n{"id":"one","value":2}\n')
            with self.assertRaisesRegex(ValueError, "conflicting JSONL"):
                S.read_jsonl(path, key_field="id")

    def test_subprocess_kill_tail_repair_and_same_inode_appends(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text('{"id":"seed"}\n')
            crash = (
                "import fcntl,os,signal,sys; p=sys.argv[1]; "
                "f=open(p,'ab'); fcntl.flock(f,fcntl.LOCK_EX); "
                "f.write(b'{\\\"id\\\":\\\"partial'); f.flush(); os.fsync(f.fileno()); "
                "os.kill(os.getpid(),signal.SIGKILL)"
            )
            child = subprocess.run([sys.executable, "-c", crash, str(path)])
            self.assertEqual(child.returncode, -signal.SIGKILL)
            S.append_jsonl(path, {"id": "recovered"}, key_field="id")
            writer = (
                "import sys; from pathlib import Path; from altitude import state as S; "
                "p=Path(sys.argv[1]); prefix=sys.argv[2]; "
                "[S.append_jsonl(p,{'id':prefix+str(i)},key_field='id') for i in range(20)]"
            )
            children = [subprocess.Popen([sys.executable, "-c", writer, str(path), prefix],
                                         cwd=Path(__file__).parents[1]) for prefix in ("a", "b")]
            self.assertEqual([child.wait() for child in children], [0, 0])
            rows = S.read_jsonl(path, key_field="id")
            self.assertEqual(len(rows), 42)
            self.assertEqual(len({row["id"] for row in rows}), 42)

    def test_cross_process_same_key_is_serialized_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, locked, start = root / "events.jsonl", root / "locked", root / "start"
            # Use a bounded holder without depending on scheduler timing for lock ownership.
            holder = (
                "import fcntl,sys,time; from pathlib import Path; "
                "p=Path(sys.argv[1]); ready=Path(sys.argv[2]); go=Path(sys.argv[3]); "
                "f=open(p,'a+'); fcntl.flock(f,fcntl.LOCK_EX); ready.write_text('yes'); "
                "\nwhile not go.exists(): time.sleep(.01)"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", holder, str(path), str(locked), str(start)]
            )
            try:
                for _ in range(100):
                    if locked.exists():
                        break
                    threading.Event().wait(0.01)
                self.assertTrue(locked.exists())
                result: list[bool] = []
                thread = threading.Thread(
                    target=lambda: result.append(
                        S.append_jsonl(path, {"id": "same", "value": 1}, key_field="id")
                    )
                )
                thread.start()
                threading.Event().wait(0.05)
                self.assertTrue(thread.is_alive(), "append did not wait for the destination inode lock")
                start.write_text("go")
                process.wait(timeout=5)
                thread.join(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            self.assertEqual(result, [True])
            self.assertFalse(
                S.append_jsonl(path, {"id": "same", "value": 1}, key_field="id")
            )

            race_start = root / "race-start"
            writer = (
                "import sys,time; from pathlib import Path; from altitude import state as S; "
                "p=Path(sys.argv[1]); ready=Path(sys.argv[2]); start=Path(sys.argv[3]); "
                "ready.write_text('yes'); "
                "\nwhile not start.exists(): time.sleep(.01)\n"
                "print(S.append_jsonl(p,{'id':'race','value':2},key_field='id'))"
            )
            racers = [subprocess.Popen(
                [sys.executable, "-c", writer, str(path), str(root / f"ready-{index}"),
                 str(race_start)], cwd=Path(__file__).parents[1], text=True,
                stdout=subprocess.PIPE
            ) for index in range(2)]
            for _ in range(100):
                if all((root / f"ready-{index}").exists() for index in range(2)):
                    break
                threading.Event().wait(0.01)
            self.assertTrue(all((root / f"ready-{index}").exists() for index in range(2)))
            race_start.write_text("go")
            outcomes = sorted(process.communicate(timeout=5)[0].strip() for process in racers)
            self.assertEqual(outcomes, ["False", "True"])
            self.assertEqual(S.read_jsonl(path, key_field="id"), [
                {"id": "same", "value": 1}, {"id": "race", "value": 2},
            ])


class LockOrderTests(unittest.TestCase):
    def test_all_levels_forward_reverse_same_level_and_exact_reentry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, (outer, inner) in enumerate(zip(S.LOCK_ORDER, S.LOCK_ORDER[1:])):
                with S.ordered_file_lock(root / f"forward-{index}", outer):
                    with S.ordered_file_lock(root / f"inner-{index}", inner):
                        pass
                with S.ordered_file_lock(root / f"reverse-outer-{index}", inner):
                    with self.assertRaises(S.LockOrderError):
                        with S.ordered_file_lock(root / f"reverse-inner-{index}", outer):
                            pass
            with S.ordered_file_lock(root / "project", S.LockLevel.PROJECT):
                with S.ordered_file_lock(root / "project", S.LockLevel.PROJECT):
                    with S.ordered_file_lock(root / "task", S.LockLevel.TASK):
                        pass
            with S.ordered_file_lock(root / "project", S.LockLevel.PROJECT):
                with S.ordered_file_lock(root / "task", S.LockLevel.TASK):
                    with self.assertRaises(S.LockOrderError):
                        with S.ordered_file_lock(root / "project", S.LockLevel.PROJECT):
                            pass
            with S.ordered_file_lock(root / "a", S.LockLevel.TASK):
                with S.ordered_file_lock(root / "b", S.LockLevel.TASK):
                    pass
            with S.ordered_file_lock(root / "b", S.LockLevel.TASK):
                with self.assertRaises(S.LockOrderError):
                    with S.ordered_file_lock(root / "a", S.LockLevel.TASK):
                        pass

    def test_every_adjacent_level_contends_across_processes(self):
        holder = (
            "import sys,time; from pathlib import Path; from altitude import state as S; "
            "p=Path(sys.argv[1]); ready=Path(sys.argv[2]); release=Path(sys.argv[3]); "
            "level=S.LockLevel(int(sys.argv[4])); "
            "c=S.ordered_file_lock(p,level); c.__enter__(); ready.write_text('yes'); "
            "\nwhile not release.exists(): time.sleep(.01)\n"
            "c.__exit__(None,None,None)"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, (outer, inner) in enumerate(zip(S.LOCK_ORDER, S.LOCK_ORDER[1:])):
                inner_path = root / f"inner-{index}"
                ready, release = root / f"ready-{index}", root / f"release-{index}"
                acquired = root / f"acquired-{index}"
                child = subprocess.Popen([
                    sys.executable, "-c", holder, str(inner_path), str(ready), str(release),
                    str(int(inner)),
                ], cwd=Path(__file__).parents[1])
                try:
                    for _ in range(100):
                        if ready.exists():
                            break
                        threading.Event().wait(0.01)
                    self.assertTrue(ready.exists())
                    observed = []

                    def release_after_observation():
                        threading.Event().wait(0.05)
                        observed.append(not acquired.exists())
                        release.write_text("go")

                    thread = threading.Thread(target=release_after_observation)
                    with S.ordered_file_lock(root / f"outer-{index}", outer):
                        thread.start()
                        started = time.monotonic()
                        with S.ordered_file_lock(inner_path, inner):
                            acquired.write_text("yes")
                        waited = time.monotonic() - started
                    thread.join(timeout=5)
                    child.wait(timeout=5)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
                self.assertEqual(observed, [True], f"pair {outer.name}->{inner.name} did not contend")
                self.assertGreaterEqual(waited, 0.04)
                self.assertEqual(acquired.read_text(), "yes")

    def test_canonical_alias_inode_and_cross_level_reentry_fail_before_flock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, hardlink, symlink = root / "lock", root / "hard", root / "symlink"
            path.touch()
            os.link(path, hardlink)
            symlink.symlink_to(path)
            with S.ordered_file_lock(path, S.LockLevel.PROJECT):
                with S.ordered_file_lock(symlink, S.LockLevel.PROJECT):
                    pass
                with self.assertRaises(S.LockOrderError):
                    with S.ordered_file_lock(path, S.LockLevel.TASK):
                        pass
                with self.assertRaises(S.LockOrderError):
                    with S.ordered_file_lock(hardlink, S.LockLevel.PROJECT):
                        pass

    def test_subprocess_kill_releases_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, ready, acquired = root / "lock", root / "ready", root / "acquired"
            holder = (
                "import os,sys,time; from pathlib import Path; from altitude import state as S; "
                "p=Path(sys.argv[1]); r=Path(sys.argv[2]); "
                "c=S.ordered_file_lock(p,S.LockLevel.PROJECT); c.__enter__(); r.write_text('ready'); time.sleep(60)"
            )
            child = subprocess.Popen([sys.executable, "-c", holder, str(lock), str(ready)],
                                     cwd=Path(__file__).parents[1])
            for _ in range(100):
                if ready.exists():
                    break
                threading.Event().wait(0.01)
            self.assertTrue(ready.exists())
            child.kill()
            child.wait()
            waiter = (
                "import sys; from pathlib import Path; from altitude import state as S; "
                "p=Path(sys.argv[1]); out=Path(sys.argv[2]); "
                "c=S.ordered_file_lock(p,S.LockLevel.PROJECT); c.__enter__(); out.write_text('yes'); c.__exit__(None,None,None)"
            )
            subprocess.run([sys.executable, "-c", waiter, str(lock), str(acquired)],
                           cwd=Path(__file__).parents[1], check=True, timeout=5)
            self.assertEqual(acquired.read_text(), "yes")

    def test_jsonl_inode_lock_participates_at_operation_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with S.ordered_file_lock(root / "publication", S.LockLevel.GIT_PUBLICATION):
                with self.assertRaises(S.LockOrderError):
                    S.append_jsonl(root / "events", {"id": "one"}, key_field="id")
            path = root / "events"
            path.touch()
            with S.ordered_file_lock(path, S.LockLevel.OPERATION):
                with self.assertRaises(S.LockOrderError):
                    S.read_jsonl(path, key_field="id")

    def test_jsonl_shared_reader_and_exclusive_writer_contend_across_processes(self):
        holder = (
            "import fcntl,sys,time; from pathlib import Path; "
            "p=Path(sys.argv[1]); ready=Path(sys.argv[2]); release=Path(sys.argv[3]); "
            "mode=fcntl.LOCK_SH if sys.argv[4]=='shared' else fcntl.LOCK_EX; "
            "f=open(p,'a+b'); fcntl.flock(f,mode); ready.write_text('yes'); "
            "\nwhile not release.exists(): time.sleep(.01)"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root, path = Path(tmp), Path(tmp) / "events.jsonl"
            S.append_jsonl(path, {"id": "seed"}, key_field="id")
            for mode in ("shared", "exclusive"):
                ready, release = root / f"{mode}-ready", root / f"{mode}-release"
                child = subprocess.Popen(
                    [sys.executable, "-c", holder, str(path), str(ready), str(release), mode]
                )
                try:
                    for _ in range(100):
                        if ready.exists():
                            break
                        threading.Event().wait(0.01)
                    self.assertTrue(ready.exists())
                    result = []
                    operation = (
                        (lambda: result.append(S.append_jsonl(
                            path, {"id": "after-reader"}, key_field="id"
                        ))) if mode == "shared" else
                        (lambda: result.append(S.read_jsonl(path, key_field="id")))
                    )
                    thread = threading.Thread(target=operation)
                    thread.start()
                    threading.Event().wait(0.05)
                    self.assertTrue(thread.is_alive(), f"{mode} lock did not block the peer")
                    release.write_text("go")
                    child.wait(timeout=5)
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
            self.assertEqual(result, [[{"id": "seed"}, {"id": "after-reader"}]])


class LegacyWriterInventoryTests(unittest.TestCase):
    EXPECTED_INVENTORY = {
        "flock": Counter({
            ("dispatch.py", "_resume_lock"): 2,
            ("dispatch.py", "publication_settlement"): 2,
            ("hooks/edit_count.py", "<module>"): 2,
            ("incidents.py", "_alloc_lock"): 2,
            ("incidents.py", "_fault_lock"): 2,
            ("l1.py", "start"): 1,
            ("recovery.py", "_launch_lock"): 2,
            ("recovery.py", "_lock"): 2,
            ("state.py", "append_jsonl"): 2,
            ("state.py", "ordered_file_lock"): 2,
            ("state.py", "read_jsonl"): 2,
        }),
        "write_json": Counter({
            ("actions.py", "_publish"): 1,
            ("deployment.py", "initialize_for_migration"): 1,
            ("dispatch.py", "poll"): 3,
            ("dispatch.py", "pull_after_done"): 1,
            ("dispatch.py", "session_settings"): 1,
            ("engines.py", "codex_bg"): 3,
            ("engines.py", "codex_stop"): 1,
            ("github_intake.py", "ensure_snapshot"): 1,
            ("incidents.py", "system_fault"): 2,
            ("l1.py", "save"): 1,
            ("l3.py", "save_info"): 1,
            ("l3_actions.py", "_claim"): 1,
            ("l3_actions.py", "_finish"): 1,
            ("l3_actions.py", "_github_issue_approve"): 1,
            ("l3_actions.py", "_github_issue_draft"): 1,
            ("quota_codex.py", "refresh"): 1,
            ("recovery.py", "attach_incident"): 1,
            ("recovery.py", "claim_l3_attention"): 1,
            ("recovery.py", "claim_repair"): 1,
            ("recovery.py", "complete_l3_attention"): 1,
            ("recovery.py", "fail_l3_attention"): 1,
            ("recovery.py", "hold"): 1,
            ("recovery.py", "l3_attention_is_current"): 1,
            ("recovery.py", "release_failed_claim"): 1,
            ("recovery.py", "request_l3_attention"): 1,
            ("server.py", "dispatch_waiting"): 2,
            ("server.py", "install_statusline"): 1,
            ("state.py", "save_task"): 1,
            ("transcript.py", "sync"): 1,
        }),
        "atomic_write": Counter({
            ("actions.py", "_helpers"): 1,
            ("config.py", "save_projects"): 1,
            ("digest.py", "text"): 1,
            ("incidents.py", "_index_correct"): 1,
            ("incidents.py", "amend_incident"): 1,
            ("incidents.py", "new_incident"): 1,
            ("state.py", "regen_state_md"): 1,
            ("state.py", "write_json"): 1,
            ("tasks.py", "brief"): 1,
            ("tasks.py", "done"): 1,
            ("tasks.py", "finalize_completion"): 1,
            ("tasks.py", "new"): 1,
            ("transcript.py", "sync"): 2,
        }),
        "append_jsonl": Counter(),
        "read_jsonl": Counter(),
        "writable_open": Counter({
            ("dispatch.py", "_resume_lock"): 1,
            ("dispatch.py", "publication_settlement"): 1,
            ("engines.py", "codex_bg"): 2,
            ("hooks/edit_count.py", "<module>"): 1,
            ("hooks/edit_count.py", "fault"): 1,
            ("incidents.py", "_alloc_lock"): 1,
            ("incidents.py", "_fault_lock"): 1,
            ("incidents.py", "_index_append"): 1,
            ("incidents.py", "new_incident"): 2,
            ("l1.py", "_spawn"): 1,
            ("l1.py", "start"): 1,
            ("l3.py", "chat_log"): 1,
            ("land.py", "land"): 1,
            ("recovery.py", "_launch_lock"): 1,
            ("recovery.py", "_lock"): 1,
            ("recovery.py", "clear"): 2,
            ("server.py", "log"): 1,
            ("state.py", "append_event"): 1,
            ("state.py", "append_jsonl"): 3,
            ("state.py", "atomic_write"): 1,
            ("state.py", "ordered_file_lock"): 1,
            ("state.py", "project_log"): 1,
            ("tasks.py", "append_task_message"): 1,
            ("tasks.py", "fyi"): 1,
            ("transcript.py", "export"): 1,
        }),
        "unknown_open_mode": Counter(),
        "path_write": Counter({
            ("config.py", "ensure_root"): 1,
            ("digest.py", "speak"): 1,
            ("engines.py", "claude_settings"): 1,
            ("engines.py", "note_usage_limit"): 1,
            ("hooks/edit_count.py", "<module>"): 1,
            ("l1.py", "_spawn"): 1,
            ("l1.py", "exec_run"): 2,
            ("land.py", "_ensure_pr"): 1,
            ("server.py", "tls_init"): 1,
        }),
        "json_dump": Counter(),
        "os_write": Counter(),
        "replace_move_copy": Counter({
            ("digest.py", "speak"): 1,
            ("engines.py", "note_usage_limit"): 1,
            ("hooks/edit_count.py", "<module>"): 1,
            ("scripts/restart_altitude.py", "build_bundle"): 1,
            ("state.py", "atomic_write"): 1,
            ("transcript.py", "sync"): 1,
        }),
        "path_move_remove": Counter({
            ("scripts/restart_altitude.py", "publish_and_restart"): 6,
            ("recovery.py", "clear"): 2,
            ("server.py", "tls_init"): 2,
            ("engines.py", "codex_sandbox_preflight"): 1,
            ("engines.py", "codex_exec"): 1,
            ("land.py", "_ensure_pr"): 1,
            ("land.py", "_candidate"): 1,
            ("land.py", "land"): 1,
            ("server.py", "drain_hook_faults"): 1,
            ("server.py", "dispatch_waiting"): 1,
            ("server.py", "main"): 1,
            ("state.py", "atomic_write"): 1,
            ("tasks.py", "_archive"): 1,
            ("scripts/restart_altitude.py", "build_bundle"): 1,
            ("scripts/restart_altitude.py", "main"): 1,
        }),
        "replace_call": Counter({
            ("hooks/guard.py", "_double_quoted_views"): 4,
            ("engines.py", "usage_limit_in"): 2,
            ("deployment.py", "_authorization"): 1,
            ("deployment.py", "_pending_values"): 1,
            ("hooks/guard.py", "<module>"): 2,
            ("hooks/guard.py", "_command_views"): 2,
            ("digest.py", "speak"): 1,
            ("dispatch.py", "_norm"): 1,
            ("dispatch.py", "cleanup_after_done"): 1,
            ("engines.py", "note_usage_limit"): 1,
            ("l1.py", "_codex_sandbox_stop"): 1,
            ("l3_actions.py", "_resume_paths"): 1,
            ("quota_codex.py", "_epoch_iso"): 1,
            ("recovery.py", "claim_l3_attention"): 1,
            ("recovery.py", "fail_l3_attention"): 1,
            ("route.py", "quota_codex"): 1,
            ("state.py", "atomic_write"): 1,
            ("state.py", "now"): 1,
            ("transcript.py", "view"): 1,
            ("hooks/edit_count.py", "<module>"): 1,
            ("hooks/guard.py", "_matched_fragment"): 1,
        }),
        "stream_mutation": Counter({
            ("state.py", "append_jsonl"): 2,
            ("engines.py", "claude_print"): 1,
            ("engines.py", "codex_bg"): 1,
            ("incidents.py", "_index_append"): 1,
            ("incidents.py", "new_incident"): 1,
            ("l3.py", "chat_log"): 1,
            ("land.py", "land"): 1,
            ("quota_codex.py", "_talk"): 1,
            ("recovery.py", "clear"): 1,
            ("server.py", "_json_bytes"): 1,
            ("server.py", "_file"): 1,
            ("server.py", "_static"): 1,
            ("server.py", "_stream_send"): 1,
            ("server.py", "_stream_close"): 1,
            ("server.py", "log"): 1,
            ("server.py", "write"): 1,
            ("state.py", "append_event"): 1,
            ("state.py", "project_log"): 1,
            ("state.py", "atomic_write"): 1,
            ("tasks.py", "fyi"): 1,
            ("tasks.py", "append_task_message"): 1,
            ("hooks/edit_count.py", "fault"): 1,
            ("bin/alt", "main"): 1,
        }),
        "temp_create": Counter({
            ("engines.py", "codex_exec"): 1,
            ("land.py", "_ensure_pr"): 1,
            ("land.py", "land"): 1,
            ("state.py", "atomic_write"): 1,
        }),
        "touch": Counter(),
    }
    SHELL_WRITERS = Counter({
        ("hooks/statusline-monitor.sh", 6, "mkdir"): 1,
        ("hooks/statusline-monitor.sh", 9, "redirect"): 1,
        ("hooks/statusline-monitor.sh", 9, "mv"): 1,
    })
    SHELL_SOURCES = ("hooks/statusline-monitor.sh",)

    @staticmethod
    def _python_sources(repository: Path) -> list[Path]:
        sources = sorted((repository / "altitude").rglob("*.py"))
        for directory in (repository / "hooks", repository / "scripts", repository / "bin"):
            for path in sorted(directory.rglob("*")):
                if not path.is_file():
                    continue
                first = path.read_text(errors="ignore").splitlines()[:1]
                if path.suffix == ".py" or (first and "python" in first[0]):
                    sources.append(path)
        return sources

    @staticmethod
    def _aliases(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
        modules, symbols = {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    bound = item.asname or item.name.split(".")[0]
                    modules[bound] = item.name if item.asname else item.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for item in node.names:
                    bound = item.asname or item.name
                    if module in ("", "altitude") and item.name in {
                        "state", "fcntl", "json", "os", "shutil",
                    }:
                        modules[bound] = f"{module}.{item.name}".lstrip(".")
                    else:
                        symbols[bound] = f"{module}.{item.name}".lstrip(".")
        return modules, symbols

    @classmethod
    def _scan_inventory(cls, repository: Path) -> dict[str, Counter]:
        inventory: dict[str, Counter] = {
            name: Counter() for name in (
                "flock", "write_json", "atomic_write", "append_jsonl", "read_jsonl",
                "writable_open", "unknown_open_mode", "path_write", "json_dump",
                "os_write", "replace_move_copy", "path_move_remove", "replace_call",
                "stream_mutation", "temp_create", "touch",
            )
        }
        state_functions = {"write_json", "atomic_write", "append_jsonl", "read_jsonl"}
        for path in cls._python_sources(repository):
            tree = ast.parse(path.read_text())
            parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            modules, symbols = cls._aliases(tree)
            assigned = {}

            def owner(node):
                while node in parents:
                    node = parents[node]
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        return node.name
                return "<module>"

            def qualified(node):
                if isinstance(node, ast.Name):
                    if node.id in assigned:
                        return assigned[node.id]
                    if node.id in symbols:
                        return symbols[node.id]
                    if node.id in modules:
                        return modules[node.id]
                    if path.name == "state.py" and node.id in state_functions:
                        return f"state.{node.id}"
                    return node.id
                if isinstance(node, ast.Attribute):
                    return f"{qualified(node.value)}.{node.attr}"
                return "?"

            for _ in range(2):
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Assign, ast.AnnAssign)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        value = node.value
                        if isinstance(value, (ast.Name, ast.Attribute)):
                            for target in targets:
                                if isinstance(target, ast.Name):
                                    assigned[target.id] = qualified(value)

            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                name = qualified(call.func)
                relative = str(path.relative_to(repository))
                source = path.name if path.parent.name == "altitude" else relative
                key = (source, owner(call))

                def argument(keyword: str, position: int):
                    if len(call.args) > position:
                        return call.args[position]
                    return next((item.value for item in call.keywords if item.arg == keyword), None)

                terminal = name.rsplit(".", 1)[-1]
                if terminal == "flock":
                    inventory["flock"][key] += 1
                if terminal in state_functions:
                    inventory[terminal][key] += 1
                if terminal in ("write_text", "write_bytes"):
                    inventory["path_write"][key] += 1
                if terminal == "dump" and name.endswith(("json.dump", ".json.dump")):
                    inventory["json_dump"][key] += 1
                if name.endswith("os.write"):
                    inventory["os_write"][key] += 1
                if terminal in ("replace", "rename", "move", "copy", "copy2", "copyfile") \
                        and any(part in name for part in ("os.", "shutil.")):
                    inventory["replace_move_copy"][key] += 1
                if (terminal in ("rename", "unlink", "rmdir")
                        or (terminal in ("remove", "rmtree", "move")
                            and any(part in name for part in ("os.", "shutil.")))):
                    inventory["path_move_remove"][key] += 1
                # Receiver types are not statically knowable in this closed source scan. Count
                # every replace call so Path.replace cannot hide behind a local or alias.
                if terminal == "replace":
                    inventory["replace_call"][key] += 1
                if terminal in ("write", "writelines", "truncate"):
                    inventory["stream_mutation"][key] += 1
                if terminal in ("mkstemp", "NamedTemporaryFile") and "tempfile." in name:
                    inventory["temp_create"][key] += 1
                if terminal == "touch":
                    inventory["touch"][key] += 1

                if name.endswith("os.open"):
                    flags = argument("flags", 1)
                    write_flags = {"O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"}
                    attrs = {
                        qualified(node).rsplit(".", 1)[-1] for node in ast.walk(flags)
                        if isinstance(node, (ast.Name, ast.Attribute))
                    } if flags is not None else set()
                    if attrs & write_flags:
                        inventory["writable_open"][key] += 1
                    elif flags is not None and not attrs:
                        inventory["unknown_open_mode"][key] += 1
                    continue

                is_open = terminal in ("open", "fdopen")
                if not is_open:
                    continue
                module_style = name in {
                    "open", "builtins.open", "os.fdopen", "tarfile.open", "gzip.open",
                    "bz2.open", "lzma.open", "codecs.open",
                }
                mode = argument("mode", 1 if module_style else 0)
                if mode is None:
                    continue
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                    if any(flag in mode.value for flag in "wax+"):
                        inventory["writable_open"][key] += 1
                else:
                    inventory["unknown_open_mode"][key] += 1
        return inventory

    @staticmethod
    def _shell_sources(repository: Path) -> list[Path]:
        sources = []
        for directory in (repository / "altitude", repository / "hooks",
                          repository / "scripts", repository / "bin"):
            for path in sorted(directory.rglob("*")):
                if not path.is_file():
                    continue
                first = path.read_text(errors="ignore").splitlines()[:1]
                if first and re.search(r"^#!.*\b(?:a|ba|da|fi|k|z)?sh\b", first[0]):
                    sources.append(path)
        return sources

    @classmethod
    def _scan_shell_inventory(cls, repository: Path) -> tuple[tuple[str, ...], Counter]:
        shell_writers = Counter()
        shell_sources = cls._shell_sources(repository)
        for path in shell_sources:
            for line_number, line in enumerate(path.read_text().splitlines(), start=1):
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                relative = str(path.relative_to(repository))
                for command in ("mv", "cp", "install", "rm", "mkdir"):
                    shell_writers[(relative, line_number, command)] += len(
                        re.findall(rf"\b{command}\s", line)
                    )
                shell_writers[(relative, line_number, "redirect")] += len(
                    re.findall(r"(?<![0-9])>{1,2}\s+(?!&)", line)
                )
        shell_writers += Counter()  # Drop zero-count keys from lines without writes.
        return tuple(str(path.relative_to(repository)) for path in shell_sources), shell_writers

    def test_all_legacy_locks_and_direct_writers_are_classified_with_multiplicity(self):
        repository = Path(__file__).parents[1]
        self.assertEqual(self._scan_inventory(repository), self.EXPECTED_INVENTORY)
        self.assertEqual(self._scan_shell_inventory(repository),
                         (self.SHELL_SOURCES, self.SHELL_WRITERS))

    @classmethod
    def _dormant_references(cls, repository: Path) -> list[tuple[str, int, str]]:
        references = []
        for path in cls._python_sources(repository):
            tree = ast.parse(path.read_text())
            modules, symbols = cls._aliases(tree)
            for node in ast.walk(tree):
                name = None
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    name = symbols.get(node.id)
                    if path.name == "state.py" and node.id in ("append_jsonl", "read_jsonl"):
                        name = f"state.{node.id}"
                elif isinstance(node, ast.Attribute):
                    root = node.value
                    if isinstance(root, ast.Name) and modules.get(root.id) in ("state", "altitude.state"):
                        name = f"state.{node.attr}"
                if name and name.rsplit(".", 1)[-1] in ("append_jsonl", "read_jsonl"):
                    references.append((str(path.relative_to(repository)), node.lineno, name))
        return references

    def test_inventory_catches_aliases_state_internals_and_multiplicity(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            package = repository / "altitude"
            package.mkdir()
            (package / "state.py").write_text(
                "import fcntl as locks\n"
                "from json import dump as emit\n"
                "from builtins import open as file_open\n"
                "from pathlib import Path as FilePath\n"
                "def writer(path, stream):\n"
                "    locks.flock(stream, 1); locks.flock(stream, 2)\n"
                "    with file_open(path, 'w') as target: emit({}, target)\n"
                "    FilePath(path).rename(path); FilePath(path).unlink()\n"
                "    move = FilePath(path).rename; erase = FilePath(path).unlink\n"
                "    move(path); erase()\n"
                "    FilePath(path).replace(path)\n"
                "    swap = FilePath(path).replace; swap(path)\n"
                "    stream.write('one'); output = stream.write\n"
                "    output('two'); output('three'); stream.truncate()\n"
            )
            (package / "consumer.py").write_text(
                "from altitude import state as durable\n"
                "from os import open as raw_open, O_WRONLY as WRITE\n"
                "def writer(path):\n"
                "    alias = durable.append_jsonl\n"
                "    persist = durable.write_json\n"
                "    persist(path, {})\n"
                "    raw_open(path, WRITE)\n"
                "    return alias\n"
            )
            found = self._scan_inventory(repository)
            self.assertEqual(found["flock"], Counter({("state.py", "writer"): 2}))
            self.assertEqual(found["writable_open"], Counter({
                ("state.py", "writer"): 1, ("consumer.py", "writer"): 1,
            }))
            self.assertEqual(found["json_dump"], Counter({("state.py", "writer"): 1}))
            self.assertEqual(found["write_json"], Counter({("consumer.py", "writer"): 1}))
            self.assertEqual(found["path_move_remove"], Counter({("state.py", "writer"): 4}))
            self.assertEqual(found["replace_call"], Counter({("state.py", "writer"): 2}))
            self.assertEqual(found["stream_mutation"], Counter({("state.py", "writer"): 4}))
            self.assertEqual(self._dormant_references(repository), [
                ("altitude/consumer.py", 4, "state.append_jsonl"),
            ])

    def test_shell_inventory_is_recursive_and_closes_the_runnable_roster(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            nested = repository / "hooks" / "nested"
            nested.mkdir(parents=True)
            script = nested / "writer"
            script.write_text(
                "#!/usr/bin/env -S bash -eu\n"
                "mkdir -p one; mkdir -p two\n"
                "printf value > output\n"
                "mv output final\n"
            )
            self.assertEqual(self._scan_shell_inventory(repository), (
                ("hooks/nested/writer",),
                Counter({
                    ("hooks/nested/writer", 2, "mkdir"): 2,
                    ("hooks/nested/writer", 3, "redirect"): 1,
                    ("hooks/nested/writer", 4, "mv"): 1,
                }),
            ))

    def test_dormant_jsonl_primitive_has_no_runtime_consumer(self):
        repository = Path(__file__).parents[1]
        self.assertEqual(self._dormant_references(repository), [])


if __name__ == "__main__":
    unittest.main()
