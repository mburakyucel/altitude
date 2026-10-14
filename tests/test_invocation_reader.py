"""A request keeps runtime invocation selection and consumption in one namespace."""
import json
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tests.test_live_transcript import _CodexTranscriptCase
from altitude import config, state as S, storage, transcript
from altitude.engines import codex, invocation


class TestInvocationReader(_CodexTranscriptCase):
    def test_invalid_names_refuse_before_any_directory_access(self):
        invalid = ("", ".", "..", "../outside", "alias/marker", "/outside", "task\\name")
        for slug in invalid:
            with self.subTest(slug=slug), \
                 mock.patch.object(storage, "runtime_directory", side_effect=AssertionError("opened before refusal")):
                with self.assertRaises(invocation.InvocationRefused):
                    with invocation.job_directory(self.project, slug):
                        self.fail("malformed task name selected a job")
        with invocation.job_directory(self.project, self.slug) as (job, root):
            for worker in ("", ".", "../w2"):
                with self.subTest(worker=worker), \
                     mock.patch.object(storage, "regular_reader", side_effect=AssertionError("opened before refusal")):
                    with self.assertRaises(invocation.InvocationRefused):
                        invocation.invocation(job, root, worker)

    def test_activity_rejects_malformed_identity_before_reader_entry(self):
        with mock.patch.object(invocation, "job_directory", side_effect=AssertionError("reader entered")):
            with self.assertRaises(transcript.TranscriptAccessError):
                transcript.activity(self.project, "../outside")

    def test_runtime_consumers_never_reopen_display_paths(self):
        record = self.root / "w2.json"
        current = S.read_json(record)
        current.update(engine="codex", session_id="thread-1")
        S.write_json(record, current)
        self.new.write_text(json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\n"
                            + json.dumps({"type": "item.completed", "item": {
                                "id": "r", "type": "agent_message", "text": "inside"}}) + "\n")
        operations = ("open", "stat", "glob", "iterdir")
        originals = {name: getattr(Path, name) for name in operations}
        def guarded(name):
            def call(path, *args, **kwargs):
                if path == self.root or self.root in path.parents:
                    raise AssertionError(f"runtime display path {name}: {path}")
                return originals[name](path, *args, **kwargs)
            return call
        with mock.patch.object(Path, "open", guarded("open")), \
             mock.patch.object(Path, "stat", guarded("stat")), \
             mock.patch.object(Path, "glob", guarded("glob")), \
             mock.patch.object(Path, "iterdir", guarded("iterdir")):
            self.assertIn("inside", json.dumps(self._view(raw=True)))
            self.assertEqual(transcript.activity(self.project, self.slug)["state"], "available")

    def test_regular_leaf_replacement_after_projection_refuses_before_index_commit(self):
        record = self.root / "w2.json"
        source_record = S.read_json(record)
        source_record.update(engine="codex", session_id="thread-1")
        S.write_json(record, source_record)
        self.new.write_text(json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\n"
                            + json.dumps({"type": "item.completed", "item": {
                                "id": "r", "type": "agent_message", "text": "first"}}) + "\n")
        original = transcript._projection
        for raw, mode in ((False, "initial"), (True, "initial"), (True, "record")):
            with self.subTest(raw=raw, mode=mode):
                baseline = self._view(raw=raw)
                key = (str(config.project_dir(self.project)), self.slug, "codex", "thread-1", 1, raw)
                index = transcript._indexes[key]
                before = (index.epoch, index.version, index.fingerprint, dict(index.rows), dict(index.sources))
                row = next(row for row in baseline["events"] if row["source"] == "codex" and row["kind"] == "message")
                def swap(*args, **kwargs):
                    projected = original(*args, **kwargs)
                    temp = self.new.with_name("replacement.tmp")
                    temp.write_bytes(self.new.read_bytes())
                    temp.replace(self.new)
                    return projected
                with mock.patch.object(transcript, "_projection", swap):
                    with self.assertRaises(transcript.TranscriptAccessError):
                        self._view(raw=raw, mode=mode, record=row["id"] if mode == "record" else "")
                self.assertEqual((index.epoch, index.version, index.fingerprint,
                                  dict(index.rows), dict(index.sources)), before)
        self.assertIn("first", json.dumps(self._view()))

    def test_altitude_rewriting_the_worker_record_during_projection_still_publishes(self):
        # Altitude rewrites a worker's record at launch, model observation and stop; the stdout identity alone
        # decides what may be published.
        record = self.root / "w2.json"
        source_record = S.read_json(record)
        source_record.update(engine="codex", session_id="thread-1")
        S.write_json(record, source_record)
        self.new.write_text(json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\n"
                            + json.dumps({"type": "item.completed", "item": {
                                "id": "r", "type": "agent_message", "text": "first"}}) + "\n")
        original = transcript._projection
        def rewrite(*args, **kwargs):
            projected = original(*args, **kwargs)
            S.write_json(record, dict(source_record, input_delivered=True))
            return projected
        with mock.patch.object(transcript, "_projection", rewrite):
            self.assertIn("first", json.dumps(self._view(raw=True)))

    def test_initialization_header_stops_at_its_first_thread_or_work_and_precedes_the_record(self):
        def started(thread):
            return {"type": "thread.started", "thread_id": thread}
        work = {"type": "item.completed", "item": {"id": "r", "type": "agent_message", "text": "selected turn"}}
        record = self.root / "w2.json"
        cases = (("first thread wins", [started("thread-1"), started("thread-0"), work], None, True),
                 ("work ends the header", [work, started("thread-1")], None, False),
                 ("record fills a headerless turn", [work, started("thread-0")], "thread-1", True),
                 ("header outranks the record", [started("thread-0"), work], "thread-1", False))
        for name, events, session, selected in cases:
            with self.subTest(name):
                S.write_json(record, dict(S.read_json(record), session_id=session))
                self.new.write_text("".join(json.dumps(event) + "\n" for event in events))
                with transcript._indexes_lock:
                    transcript._indexes.clear()
                self.assertEqual("selected turn" in json.dumps(self._view(raw=True)), selected)

    def test_unavailable_at_discovery_cannot_consume_late_stdout(self):
        record = self.root / "w2.json"
        current = S.read_json(record)
        current.update(engine="codex", session_id="thread-1")
        S.write_json(record, current)
        self.new.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "r", "type": "agent_message", "text": "late bytes"}}) + "\n")
        original = invocation.RuntimeInvocation.discover_stdout
        def vanish(source, *, header):
            if source.worker == "w2":
                held = self.new.with_name("held-stdout")
                self.new.rename(held)
                result = original(source, header=header)
                held.rename(self.new)
                return result
            return original(source, header=header)
        with mock.patch.object(invocation.RuntimeInvocation, "discover_stdout", vanish):
            self.assertNotIn("late bytes", json.dumps(self._view(raw=True)))
            self.assertEqual(transcript.activity(self.project, self.slug)["state"], "unavailable")
        self.assertIn("late bytes", json.dumps(self._view(raw=True)))

    def test_job_directory_replacement_after_projection_refuses(self):
        self.old.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "r", "type": "agent_message", "text": "first"}}) + "\n")
        baseline = self._view()
        held = self.root.with_name("held-engine")
        original = transcript._projection
        def swap(*args, **kwargs):
            projected = original(*args, **kwargs)
            self.root.rename(held)
            self.root.mkdir()
            for name in ("w1.json", "w1.stdout.jsonl"):
                (self.root / name).write_bytes((held / name).read_bytes())
            return projected
        with mock.patch.object(transcript, "_projection", swap):
            with self.assertRaises(transcript.TranscriptAccessError):
                self._view()
        self.root.rename(self.root.with_name("new-engine"))
        held.rename(self.root)
        self.assertEqual(self._view(mode="delta", cursor=baseline["cursor"])["events"], [])

    def test_quiet_poll_and_second_fingerprint_do_not_read_full_stdout(self):
        self.old.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "r", "type": "agent_message", "text": "first"}}) + "\n")
        original = invocation.RuntimeInvocation.read
        reads = []
        def counted(source):
            reads.append(source.worker)
            return original(source)
        with mock.patch.object(invocation.RuntimeInvocation, "read", counted):
            first = self._view()
            self.assertEqual(reads, ["w1"])
            reads.clear()
            quiet = self._view(mode="delta", cursor=first["cursor"])
            self.assertEqual(quiet["events"], [])
            self.assertEqual(reads, [])
            with self.old.open("a") as stream:
                stream.write(json.dumps({"type": "item.completed", "item": {
                    "id": "r2", "type": "agent_message", "text": "later"}}) + "\n")
            self._view(mode="delta", cursor=first["cursor"])
            self.assertEqual(reads, ["w1"])

    def test_complete_utf8_crlf_and_job_descriptor_cleanup(self):
        text = "Résumé 🙂 " * 1000
        self.new.write_bytes((json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\r\n"
                              + json.dumps({"type": "item.completed", "item": {
                                  "id": "r", "type": "agent_message", "text": text}}, ensure_ascii=False) + "\r\n").encode())
        raw = next(row for row in self._view(raw=True)["events"] if row["source"] == "codex"
                   and row["kind"] == "message")
        detail = self._view(raw=True, mode="record", record=raw["id"])
        chunks = detail["text"]
        while detail["next_offset"] is not None:
            detail = self._view(raw=True, mode="record", record=raw["id"], offset=detail["next_offset"])
            chunks += detail["text"]
        self.assertEqual(json.loads(chunks)["item"]["text"], text)
        with invocation.job_directory(self.project, self.slug) as (job, _):
            os.fstat(job)
        with self.assertRaises(OSError):
            os.fstat(job)

    def test_json_replacement_refuses_view_before_index_commit_and_rediscovers(self):
        record = self.root / "w2.json"
        current = S.read_json(record)
        current.update(engine="codex", session_id="thread-1")
        S.write_json(record, current)
        self.new.write_text(json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\n"
                            + json.dumps({"type": "item.completed", "item": {"id": "r", "type": "agent_message", "text": "inside"}}) + "\n")
        before = self._view()
        outside = self.tmp / "outside.json"
        outside.write_text(json.dumps({"id": "w2", "session_id": "thread-1", "marker": "outside marker"}))
        original = invocation.RuntimeInvocation.read
        def replace(source):
            if source.worker == "w2" and not record.is_symlink():
                record.unlink()
                record.symlink_to(outside)
            return original(source)
        for raw in (False, True):
            with mock.patch.object(invocation.RuntimeInvocation, "read", replace):
                with self.assertRaises(transcript.TranscriptAccessError):
                    self._view(raw=raw)
            record.unlink()
            S.write_json(record, current)
        expected = transcript.activity(self.project, self.slug)
        self.assertEqual(expected["state"], "available")
        with mock.patch.object(invocation.RuntimeInvocation, "read", replace):
            # A replaced record no longer withholds activity: it was parsed once, before its replacement.
            self.assertEqual(transcript.activity(self.project, self.slug), expected)
        record.unlink()
        record.symlink_to(outside)
        # A record already replaced by a link when activity opens it refuses activity, as stdout does.
        self.assertEqual(transcript.activity(self.project, self.slug)["state"], "unavailable")
        record.unlink()
        S.write_json(record, current)
        self.assertEqual(self._view(mode="delta", cursor=before["cursor"])["events"], [])
        self.assertIn("inside", json.dumps(self._view()))
        self.assertNotIn("outside marker", json.dumps(self._view(raw=True)))

    def test_stdout_link_replacement_refuses_view_and_activity(self):
        record = self.root / "w2.json"
        current = S.read_json(record)
        current["engine"] = "codex"
        S.write_json(record, current)
        self.new.write_text(json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\n")
        outside = self.tmp / "outside.jsonl"
        outside.write_text('{"type":"item.completed","item":{"type":"agent_message","text":"outside marker"}}\n')
        original = invocation.RuntimeInvocation.read
        def replace(source):
            if source.worker == "w2" and not self.new.is_symlink():
                self.new.unlink()
                self.new.symlink_to(outside)
            return original(source)
        with mock.patch.object(invocation.RuntimeInvocation, "read", replace):
            with self.assertRaises(transcript.TranscriptAccessError):
                self._view()
        self.new.unlink()
        self.new.write_text(json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\n"
                            + json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "safe"}}) + "\n")
        with mock.patch.object(invocation.RuntimeInvocation, "read", replace):
            result = transcript.activity(self.project, self.slug)
        self.assertEqual(result["state"], "unavailable")
        self.assertNotIn("outside marker", json.dumps(result))
        self.new.unlink()
        self.new.write_text(json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\n"
                            + json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "safe"}}) + "\n")
        self.assertIn("safe", json.dumps(self._view()))

    def test_live_regular_file_selects_archive_but_link_refuses(self):
        task_dir = self.root.parent
        archive = task_dir.parent.parent / "archive" / self.slug
        archive.parent.mkdir(exist_ok=True)
        os.rename(task_dir, archive)
        task_dir.write_text("ordinary entry")
        with invocation.job_directory(self.project, self.slug) as (job, _):
            self.assertIsNotNone(job)
        task_dir.unlink()
        task_dir.symlink_to(archive, target_is_directory=True)
        with self.assertRaises(invocation.InvocationRefused):
            with invocation.job_directory(self.project, self.slug):
                pass

    def _archive_without_live_tasks_directory(self, replacement):
        self.old.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "archive", "type": "agent_message", "text": "archived invocation"}}) + "\n")
        task_dir = self.root.parent
        tasks_dir = task_dir.parent
        archive = tasks_dir.parent / "archive" / self.slug
        archive.parent.mkdir(exist_ok=True)
        task_dir.rename(archive)
        tasks_dir.rmdir()
        if replacement == "file":
            tasks_dir.write_text("ordinary entry")
        elif replacement == "link":
            outside = self.tmp / "outside-tasks"
            (outside / self.slug / "l2-engine").mkdir(parents=True)
            (outside / self.slug / "l2-engine" / "w1.json").write_text('{"marker":"outside marker"}')
            tasks_dir.symlink_to(outside, target_is_directory=True)
        return archive

    def test_missing_live_tasks_parent_selects_archive_and_closes_job(self):
        archive = self._archive_without_live_tasks_directory("missing")
        with invocation.job_directory(self.project, self.slug) as (job, display):
            self.assertIsNotNone(job)
            self.assertEqual(display, archive / "l2-engine")
            source = invocation.invocation(job, display, "w1")
            source.discover_stdout(header=True)
            self.assertIn(b"archived invocation", source.read()[0])
            self.assertEqual([turn.worker for turn in codex.codex_turns(job, display, "thread-1")], ["w1"])
        with self.assertRaises(OSError):
            os.fstat(job)
        self.assertIn("archived invocation", json.dumps(self._view()))
        self.assertIn("archived invocation", json.dumps(self._view(raw=True)))

    def test_ordinary_file_live_tasks_parent_selects_archive_and_closes_job(self):
        archive = self._archive_without_live_tasks_directory("file")
        with invocation.job_directory(self.project, self.slug) as (job, display):
            self.assertIsNotNone(job)
            self.assertEqual(display, archive / "l2-engine")
            source = invocation.invocation(job, display, "w1")
            source.discover_stdout(header=True)
            self.assertIn(b"archived invocation", source.read()[0])
            self.assertEqual([turn.worker for turn in codex.codex_turns(job, display, "thread-1")], ["w1"])
        with self.assertRaises(OSError):
            os.fstat(job)

    def test_archive_parent_and_task_entries_preserve_refusal_and_close_directories(self):
        archive = self._archive_without_live_tasks_directory("missing")
        outside = self.tmp / "outside-archive"
        outside.mkdir()
        directory = storage.directory
        for entry in (archive, archive.parent):
            for kind in ("missing", "file", "link"):
                with self.subTest(entry=entry.name, kind=kind):
                    held = entry.with_name("held-archive-entry")
                    entry.rename(held)
                    if kind == "file":
                        entry.write_text("ordinary entry")
                    elif kind == "link":
                        entry.symlink_to(outside, target_is_directory=True)
                    descriptors = []
                    @contextmanager
                    def tracked(*args, **kwargs):
                        with directory(*args, **kwargs) as fd:
                            descriptors.append(fd)
                            yield fd
                    try:
                        with mock.patch.object(storage, "directory", tracked), \
                             mock.patch.object(storage, "regular_reader", side_effect=AssertionError("outside read")):
                            if kind == "link":
                                with self.assertRaises(invocation.InvocationRefused):
                                    with invocation.job_directory(self.project, self.slug):
                                        self.fail("linked archive selected")
                            else:
                                with invocation.job_directory(self.project, self.slug) as (job, _):
                                    self.assertIsNone(job)
                        self.assertTrue(descriptors)
                        for fd in descriptors:
                            with self.assertRaises(OSError):
                                os.fstat(fd)
                    finally:
                        if kind != "missing":
                            entry.unlink()
                        held.rename(entry)

    def test_linked_live_tasks_parent_refuses_archive_without_outside_read(self):
        self._archive_without_live_tasks_directory("link")
        directory = storage.runtime_directory
        held = []
        @contextmanager
        def tracked(*args, **kwargs):
            with directory(*args, **kwargs) as fd:
                held.append(fd)
                yield fd
        with mock.patch.object(storage, "runtime_directory", tracked), \
             mock.patch.object(storage, "regular_reader", side_effect=AssertionError("outside read")):
            with self.assertRaises(invocation.InvocationRefused):
                with invocation.job_directory(self.project, self.slug):
                    self.fail("linked parent selected an invocation")
        self.assertEqual(len(held), 1)
        with self.assertRaises(OSError):
            os.fstat(held[0])

    def test_linked_job_directory_refuses_without_reading_outside(self):
        held = self.root.with_name("held-engine")
        self.root.rename(held)
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "w2.json").write_text('{"marker":"outside marker"}')
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(transcript.TranscriptAccessError):
            self._view(raw=True)
        self.assertEqual(transcript.activity(self.project, self.slug)["state"], "unavailable")
        self.root.unlink()
        held.rename(self.root)
        self.assertNotIn("outside marker", json.dumps(self._view(raw=True)))

    def test_live_task_replacement_between_selection_and_open_refuses(self):
        task_dir = self.root.parent
        held = task_dir.with_name("held-task")
        substitute = self.tmp / "substitute"
        (substitute / "l2-engine").mkdir(parents=True)
        (substitute / "l2-engine" / "w2.json").write_text('{"marker":"outside marker"}')
        directory = storage.directory
        replaced = False
        @contextmanager
        def swap(root, parts, **kwargs):
            nonlocal replaced
            if parts == [self.slug] and not replaced:
                replaced = True
                task_dir.rename(held)
                substitute.rename(task_dir)
            with directory(root, parts, **kwargs) as fd:
                yield fd
        with mock.patch.object(storage, "directory", swap):
            with self.assertRaises(transcript.TranscriptAccessError):
                self._view(raw=True)
        self.assertTrue(replaced)
        task_dir.rename(substitute)
        held.rename(task_dir)
        self.assertNotIn("outside marker", json.dumps(self._view(raw=True)))

    def test_many_turns_keep_one_regular_reader_at_a_time(self):
        for number in range(120):
            worker = f"many-{number}"
            S.write_json(self.root / f"{worker}.json", {"id": worker, "session_id": "thread-1"})
            (self.root / f"{worker}.stdout.jsonl").write_text("")
        regular_reader = storage.regular_reader
        active = peak = 0
        @contextmanager
        def counted(directory, name):
            nonlocal active, peak
            with regular_reader(directory, name) as fd:
                active += 1
                peak = max(peak, active)
                try:
                    yield fd
                finally:
                    active -= 1
        with mock.patch.object(storage, "regular_reader", counted):
            self._view()
        self.assertEqual(active, 0)
        self.assertEqual(peak, 1)


if __name__ == "__main__":
    unittest.main()
