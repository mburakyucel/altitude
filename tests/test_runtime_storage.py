"""Real fictional storage verifies the narrow project-lock and fixed-brief namespace boundary."""
import fcntl
import json
import os
import select
import subprocess
import sys
import unittest
from collections import Counter
from contextlib import contextmanager
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, installation, platform, server, state as S, storage, transcript


class RuntimeStorage(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.runtime = self.tmp / "runtime"
        self.runtime.mkdir()
        self.patch(config, "ROOT", self.runtime)
        self.patch(config, "RUNTIME_ROOT", self.runtime.resolve())
        self.project = "fictional-project"
        self.directory = self.runtime / self.project
        self.outside = self.tmp / "outside"
        self.outside.mkdir()

    @contextmanager
    def serialized(self):
        with S.project_lock(self.project):
            yield True

    def locks(self):
        return ((".lock", self.serialized), (".activity.lock", lambda: config.project_activity(self.project)))

    def test_registry_and_settings_writes_fsync_the_pinned_search_parent(self):
        config.ensure_root()
        with config.projects_lock() as parent:
            config.save_projects({self.project: {"path": "fictional"}}, parent)
            S.write_json_at(parent, "settings.json", {"fictional": "é\n"})
            self.assertEqual(config._load_projects(parent)[self.project]["path"], "fictional")
            self.assertEqual(config.machine_settings(parent)["fictional"], "é\n")

    def test_failed_atomic_write_keeps_primary_error_when_temp_cleanup_fails(self):
        original_unlink = os.unlink
        with storage.runtime_directory([], search=True) as parent:
            primary = OSError("fictional replace failure")
            def cleanup(path, *args, **kwargs):
                if str(path).startswith(".settings.json."):
                    raise PermissionError("fictional cleanup failure")
                return original_unlink(path, *args, **kwargs)
            with mock.patch.object(os, "replace", side_effect=primary), mock.patch.object(os, "unlink", side_effect=cleanup):
                with self.assertRaises(OSError) as found:
                    storage.atomic_write(parent, "settings.json", "fictional")
            self.assertIs(found.exception, primary)
        for temporary in self.runtime.glob(".settings.json.*"):
            temporary.unlink()

    def test_create_absent_does_not_follow_substituted_temp_link(self):
        marker = self.outside / "marker"
        marker.write_text("outside sentinel")
        real_link = os.link
        with storage.runtime_directory([], search=True) as parent:
            def substitute(src, dst, *args, **kwargs):
                os.unlink(src, dir_fd=parent)
                os.symlink(marker, src, dir_fd=parent)
                self.assertFalse(kwargs["follow_symlinks"])
                return real_link(src, dst, *args, **kwargs)
            with mock.patch.object(os, "link", side_effect=substitute):
                storage.create_absent(parent, "restart-pending.json", "complete record")
        self.assertTrue((self.runtime / "restart-pending.json").is_symlink())
        self.assertEqual(marker.read_text(), "outside sentinel")
        (self.runtime / "restart-pending.json").unlink()

    def test_archive_rename_checks_source_before_creating_destination_parent(self):
        source = self.directory / "tasks" / "fictional-task"
        destination = self.directory / "archive" / "fictional-task"
        storage.runtime_rename_dir(source, destination)
        self.assertFalse(destination.parent.exists())
        source.parent.mkdir(parents=True)
        source.write_text("ordinary file")
        storage.runtime_rename_dir(source, destination)
        self.assertFalse(destination.parent.exists())
        source.unlink()
        source.mkdir()
        storage.runtime_rename_dir(source, destination)
        self.assertTrue(destination.is_dir())
        self.assertFalse(source.exists())

    def test_optional_unlink_accepts_missing_parent_but_refuses_linked_parent(self):
        missing = self.directory / "missing" / "record.json"
        storage.runtime_unlink(missing, missing_ok=True)
        with self.assertRaises(FileNotFoundError):
            storage.runtime_unlink(missing)
        self.directory.mkdir()
        (self.directory / "missing").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(OSError):
            storage.runtime_unlink(missing, missing_ok=True)

    def test_list_tasks_skips_child_archived_or_removed_after_enumeration(self):
        live = S.tasks_dir(self.project) / "fictional-task"
        archived = S.archive_dir(self.project) / "fictional-task"
        archived.parent.mkdir(parents=True)
        real_directory = storage.directory
        for action in ("archive", "remove"):
            with self.subTest(action=action):
                live.mkdir(parents=True)
                S.write_runtime_json(live / "status.json", {"slug": "fictional-task"})
                @contextmanager
                def move_before_open(root, parts, **kwargs):
                    if isinstance(root, int) and parts == ["fictional-task"]:
                        if action == "archive":
                            live.rename(archived)
                        else:
                            (live / "status.json").unlink()
                            live.rmdir()
                    with real_directory(root, parts, **kwargs) as opened:
                        yield opened
                opened, closed = [], []
                real_open, real_dup, real_close = os.open, os.dup, os.close
                def opening(*args, **kwargs):
                    fd = real_open(*args, **kwargs); opened.append(fd); return fd
                def duplicating(fd):
                    result = real_dup(fd); opened.append(result); return result
                def closing(fd):
                    closed.append(fd); return real_close(fd)
                with mock.patch.object(storage, "directory", move_before_open), \
                        mock.patch.object(os, "open", side_effect=opening), \
                        mock.patch.object(os, "dup", side_effect=duplicating), \
                        mock.patch.object(os, "close", side_effect=closing):
                    self.assertEqual(S.list_tasks(self.project), [])
                self.assertEqual(Counter(opened), Counter(closed))
                if action == "archive":
                    self.assertEqual(S.list_tasks(self.project, include_archive=True), [{"slug": "fictional-task"}])
                    (archived / "status.json").unlink()
                    archived.rmdir()
                self.assertFalse(live.exists())

    def test_update_status_uses_held_record_and_settings_after_root_replacement(self):
        self.runtime.mkdir(exist_ok=True)
        S.write_runtime_json(self.runtime / "update.json", {"latest": {"version": "v0.2.0"}})
        S.write_runtime_json(self.runtime / "settings.json", {"update_check": True})
        release = {"version": "v0.1.0", "repository": "https://github.com/example/altitude"}
        with mock.patch.object(config, "RELEASE", release), mock.patch.object(platform, "containerized", return_value=False):
            with installation._changing_update_record() as (record, parent):
                moved = self.tmp / "original-runtime"
                self.runtime.rename(moved)
                self.runtime.mkdir()
                (self.runtime / "update.json").write_text('{"latest":{"version":"v0.3.0"}}')
                (self.runtime / "settings.json").write_text('{"update_check":false}')
                status = installation.update_status(record=record, parent=parent)
                self.assertEqual(status["available"]["version"], "v0.2.0")
                self.assertTrue(status["check"])

    def test_ensure_root_needs_search_only_on_existing_monitor_and_hooks(self):
        config.ensure_root()
        paths = (self.runtime / "monitor", self.runtime / "hooks")
        for path in paths:
            path.chmod(0o111)
        try:
            self.assertEqual(config.load_projects(), {})
        finally:
            for path in paths:
                path.chmod(0o755)

    def test_task_selector_preserves_live_directory_and_falsey_status_fallback(self):
        live = S.tasks_dir(self.project) / "fictional-task"
        archived = S.archive_dir(self.project) / "fictional-task"
        live.mkdir(parents=True)
        archived.mkdir(parents=True)
        S.write_runtime_json(archived / "status.json", {"slug": "fictional-task", "state": "done"})
        self.assertEqual(S.task_dir(self.project, "fictional-task"), live)
        self.assertEqual(S.load_task(self.project, "fictional-task")["state"], "done")
        for raw in ("", "  \n", "null", "false", "0", "[]", "{}"):
            with self.subTest(raw=raw):
                (live / "status.json").write_text(raw)
                self.assertEqual(S.load_task(self.project, "fictional-task")["state"], "done")
                self.assertEqual(S.task_dir(self.project, "fictional-task"), live)
        (live / "status.json").unlink()
        live.rmdir()
        self.assertEqual(S.task_dir(self.project, "fictional-task"), archived)

    def test_task_selector_surfaces_corrupt_decode_link_and_special_status(self):
        live = S.tasks_dir(self.project) / "fictional-task"
        archived = S.archive_dir(self.project) / "fictional-task"
        live.mkdir(parents=True)
        archived.mkdir(parents=True)
        S.write_runtime_json(archived / "status.json", {"slug": "fictional-task", "state": "done"})
        status = live / "status.json"
        status.write_text("{")
        with self.assertRaisesRegex(ValueError, "corrupt JSON"):
            S.load_task(self.project, "fictional-task")
        status.write_bytes(b"\xff")
        with self.assertRaises(UnicodeDecodeError):
            S.load_task(self.project, "fictional-task")
        status.unlink()
        marker = self.outside / "status.json"
        marker.write_text('{"outside":true}')
        status.symlink_to(marker)
        with self.assertRaises(OSError):
            S.load_task(self.project, "fictional-task")
        status.unlink()
        os.mkfifo(status)
        with self.assertRaises(storage.NotRegularFile):
            S.load_task(self.project, "fictional-task")
        status.unlink()
        status.write_text('{"slug":"fictional-task"}')
        if os.geteuid() != 0:
            status.chmod(0)
            try:
                with self.assertRaises(PermissionError):
                    S.load_task(self.project, "fictional-task")
            finally:
                status.chmod(0o600)
        self.assertEqual(marker.read_text(), '{"outside":true}')

    def test_append_and_atomic_replace_refuse_specials_and_preserve_outside_leaf(self):
        leaf = self.runtime / "events.log"
        marker = self.outside / "events.log"
        marker.write_text("outside sentinel")
        leaf.symlink_to(marker)
        with self.assertRaises(OSError):
            storage.runtime_append(leaf, "never written")
        self.assertEqual(marker.read_text(), "outside sentinel")
        storage.runtime_atomic_write(leaf, "é\nlast\n")
        self.assertFalse(leaf.is_symlink())
        self.assertEqual(storage.runtime_read_text(leaf), "é\nlast\n")
        self.assertEqual(marker.read_text(), "outside sentinel")
        self.assertEqual(leaf.stat().st_mode & 0o777, 0o600)
        leaf.chmod(0o200)
        storage.runtime_atomic_write(leaf, "replacement")
        self.assertEqual(storage.runtime_read_text(leaf), "replacement")
        storage.runtime_append(leaf, " é\n", sync=True)
        self.assertEqual(storage.runtime_read_text(leaf), "replacement é\n")
        leaf.unlink()
        os.mkfifo(leaf)
        with self.assertRaises(OSError):
            storage.runtime_append(leaf, "never written")
        with self.assertRaises(storage.NotRegularFile):
            storage.runtime_read_text(leaf)

    def test_generic_json_helpers_remain_available_for_nonruntime_output(self):
        output = self.outside / "evaluation.json"
        S.write_json(output, {"text": "é\n"})
        self.assertEqual(S.read_json(output), {"text": "é\n"})
        with self.assertRaises(storage.StorageRefused):
            S.read_runtime_json(output)
        self.assertEqual(json.loads(output.read_text())["text"], "é\n")

    def test_api_change_marks_follow_atomic_task_status_replacement(self):
        config.ensure_root()
        with config.edit_projects() as projects:
            projects[self.project] = {"path": str(self.outside)}
        status = S.tasks_dir(self.project) / "fictional-task" / "status.json"
        S.write_runtime_json(status, {"slug": "fictional-task", "state": "queued"})
        before = server.change_marks()[self.project]
        S.write_runtime_json(status, {"slug": "fictional-task", "state": "running"})
        after = server.change_marks()[self.project]
        self.assertNotEqual(before, after)
        self.assertEqual(server.change_marks()[self.project], after)

    def test_creation_regular_contents_and_shared_exclusive_contention(self):
        for name, locked in self.locks():
            with locked() as acquired:
                self.assertTrue(acquired)
            leaf = self.directory / name
            leaf.write_bytes(b"existing lock bytes")
            with locked():
                self.assertEqual(leaf.read_bytes(), b"existing lock bytes")
        with config.project_activity(self.project) as shared:
            self.assertTrue(shared)
            with config.project_activity(self.project) as other:
                self.assertTrue(other)
            with config.project_activity(self.project, exclusive=True) as exclusive:
                self.assertFalse(exclusive)
        with config.project_activity(self.project, exclusive=True) as exclusive:
            self.assertTrue(exclusive)
            with config.project_activity(self.project) as shared:
                self.assertFalse(shared)
        # Activity then serialization is the existing order; releasing either also releases contention.
        with config.project_activity(self.project, exclusive=True), S.project_lock(self.project):
            pass
        with config.project_activity(self.project, exclusive=True) as acquired:
            self.assertTrue(acquired)

    def test_simulated_service_backend_keeps_native_filesystem_search_flags(self):
        """Cross-platform service fixtures still operate real storage on the runner's kernel."""
        expected = os.O_SEARCH if hasattr(os, "O_SEARCH") else os.O_PATH
        live = self.directory / "tasks/fictional-task"
        live.mkdir(parents=True)
        (live / "brief.md").write_text("native full brief")
        for backend in ("linux", "darwin"):
            with self.subTest(backend=backend), mock.patch.object(platform.sys, "platform", backend):
                self.assertEqual(platform.directory_search_access(), expected)
                with S.project_lock(self.project), config.project_activity(self.project) as acquired:
                    self.assertTrue(acquired)
                    self.assertEqual(transcript._turn_prompt(self.project, "fictional-task", {}, {}), "native full brief")

    def test_existing_write_only_lock_keeps_its_bytes_and_can_serialize(self):
        self.assertNotEqual(os.geteuid(), 0, "root cannot establish lock access parity")
        self.directory.mkdir()
        for name, locked in self.locks():
            leaf = self.directory / name
            leaf.write_bytes(b"existing lock bytes")
            leaf.chmod(0o200)
            try:
                with locked() as acquired:
                    self.assertTrue(acquired)
            finally:
                leaf.chmod(0o600)
            self.assertEqual(leaf.read_bytes(), b"existing lock bytes")

    def test_serialization_waits_for_existing_writer_without_truncating(self):
        script = ("from altitude import state; import sys; print('ready',flush=True); "
                  "\nwith state.project_lock(sys.argv[1]): print('acquired',flush=True)")
        env = {**os.environ, "ALTITUDE_HOME": str(self.runtime)}
        with S.project_lock(self.project):
            child = subprocess.Popen([sys.executable, "-c", script, self.project], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                self.assertEqual(select.select([child.stdout], [], [], 0.1)[0], [])
                self.assertIsNone(child.poll())
            except BaseException:
                child.kill()
                child.communicate(timeout=5)
                raise
        try:
            out, err = child.communicate(timeout=5)
            self.assertEqual((child.returncode, out, err), (0, "acquired\n", ""))
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)

    def test_project_and_fixed_lock_links_never_touch_outside_storage(self):
        for name, locked in self.locks():
            target = self.outside / name
            target.write_bytes(b"outside sentinel")
            self.directory.symlink_to(self.outside, target_is_directory=True)
            with self.assertRaises((OSError, ValueError)), locked():
                self.fail("linked project admitted")
            self.directory.unlink()
            self.directory.mkdir()
            leaf = self.directory / name
            leaf.symlink_to(target)
            with self.assertRaises((OSError, ValueError)), locked():
                self.fail("linked lock admitted")
            self.assertEqual(target.read_bytes(), b"outside sentinel")
            leaf.unlink()
            self.directory.rmdir()

    def test_fifo_directory_and_lock_failure_close_descriptors(self):
        self.directory.mkdir()
        for name, locked in self.locks():
            leaf = self.directory / name
            for special in ("fifo", "directory"):
                if special == "fifo":
                    os.mkfifo(leaf)
                else:
                    leaf.mkdir()
                with self.assertRaises((OSError, ValueError)), locked():
                    self.fail("nonregular lock admitted")
                leaf.rmdir() if special == "directory" else leaf.unlink()
            with mock.patch.object(fcntl, "flock", side_effect=OSError("fictional lock failure")):
                with self.assertRaises(OSError), locked():
                    pass
            with locked() as acquired:
                self.assertTrue(acquired)

    def test_leaf_replacement_and_opened_parent_do_not_follow_new_links(self):
        original = os.open
        for name, locked in self.locks():
            self.directory.mkdir(exist_ok=True)
            leaf = self.directory / name
            target = self.outside / name
            target.write_bytes(b"outside sentinel")
            def replaced(path, flags, *args, **kwargs):
                if path == name:
                    leaf.symlink_to(target)
                return original(path, flags, *args, **kwargs)
            with mock.patch.object(os, "open", side_effect=replaced):
                with self.assertRaises(OSError), locked():
                    pass
            leaf.unlink()
            moved = self.runtime / "pinned-project"
            def moved_after_open(path, flags, *args, **kwargs):
                fd = original(path, flags, *args, **kwargs)
                if path == self.project:
                    self.directory.rename(moved)
                    self.directory.symlink_to(self.outside, target_is_directory=True)
                return fd
            with mock.patch.object(os, "open", side_effect=moved_after_open), locked():
                self.assertTrue((moved / name).is_file())
                self.assertEqual(target.read_bytes(), b"outside sentinel")
            self.directory.unlink()
            (moved / name).unlink()
            moved.rmdir()

    def test_configured_alias_preserves_display_and_uses_initial_anchor(self):
        alias = self.tmp / "root alias"
        alias.symlink_to(self.runtime, target_is_directory=True)
        self.patch(config, "ROOT", alias)
        self.project = "unusual valid project"
        with S.project_lock(self.project):
            self.assertEqual(config.project_dir(self.project), alias / self.project)
            self.assertTrue((self.runtime / self.project / ".lock").is_file())
        alias.unlink()
        alias.symlink_to(self.outside, target_is_directory=True)
        with S.project_lock(self.project):
            self.assertFalse((self.outside / self.project).exists())
        moved = self.tmp / "original-runtime"
        self.runtime.rename(moved)
        self.runtime.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(OSError), S.project_lock(self.project):
            pass
        self.assertFalse((self.outside / self.project).exists())

    def test_full_brief_newlines_missing_live_archive_and_decode_error(self):
        live = self.directory / "tasks/fictional-task"
        archived = self.directory / "archive/fictional-task"
        live.mkdir(parents=True)
        archived.mkdir(parents=True)
        (archived / "brief.md").write_text("archive")
        read = lambda: transcript._turn_prompt(self.project, "fictional-task", {}, {})
        self.assertEqual(read(), "")  # existing live directory never borrows an archived brief
        text = "é" * (1 << 20) + "\nlast\nline\n"
        (live / "brief.md").write_bytes(("é" * (1 << 20) + "\r\nlast\rline\n").encode())
        self.assertEqual(read(), text)
        (live / "brief.md").write_bytes(b"\xff")
        with self.assertRaises(UnicodeDecodeError):
            read()
        (live / "brief.md").unlink()
        live.rmdir()
        self.assertEqual(read(), "archive")
        (archived / "brief.md").unlink()
        os.mkfifo(archived / "brief.md")
        self.assertEqual(read(), "")

    def test_brief_ancestor_and_leaf_links_are_unavailable_without_disclosing_marker(self):
        live = self.directory / "tasks/fictional-task"
        live.mkdir(parents=True)
        target = self.outside / "brief.md"
        target.write_text("outside marker")
        (live / "brief.md").symlink_to(target)
        read = lambda: transcript._turn_prompt(self.project, "fictional-task", {}, {})
        self.assertEqual(read(), "")
        (live / "brief.md").unlink()
        live.rmdir()
        live.symlink_to(self.outside, target_is_directory=True)
        self.assertEqual(read(), "")
        self.assertEqual(target.read_text(), "outside marker")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux execute-only native filesystem evidence")
    def test_search_only_ancestors_do_not_require_directory_listing(self):
        self.assertNotEqual(os.geteuid(), 0)
        live = self.directory / "tasks/fictional-task"
        live.mkdir(parents=True)
        (live / "brief.md").write_text("complete readable brief")
        self.directory.chmod(0o311)
        (self.directory / "tasks").chmod(0o111)
        live.chmod(0o111)
        try:
            with self.assertRaises(PermissionError):
                os.listdir(live)
            self.assertEqual(transcript._turn_prompt(self.project, "fictional-task", {}, {}), "complete readable brief")
            with S.project_lock(self.project), config.project_activity(self.project) as acquired:
                self.assertTrue(acquired)
        finally:
            live.chmod(0o755)
            (self.directory / "tasks").chmod(0o755)
            self.directory.chmod(0o755)

    def test_descriptor_cleanup_on_fstat_decode_directory_and_caller_failures(self):
        self.directory.mkdir()
        (self.directory / "brief.md").write_bytes(b"\xff")
        for failure in ("fstat", "decode", "open", "caller"):
            opened, closed = [], []
            real_open, real_dup, real_close = os.open, os.dup, os.close
            def opened_fd(*args, **kwargs):
                if failure == "open" and args[0] == "missing":
                    raise OSError("fictional intermediate open")
                fd = real_open(*args, **kwargs); opened.append(fd); return fd
            def dup(fd):
                result = real_dup(fd); opened.append(result); return result
            def close(fd):
                closed.append(fd); return real_close(fd)
            with mock.patch.object(os, "open", side_effect=opened_fd), \
                    mock.patch.object(os, "dup", side_effect=dup), mock.patch.object(os, "close", side_effect=close):
                with self.assertRaises((OSError, UnicodeDecodeError, RuntimeError)):
                    with storage.runtime_directory([self.project, *(["missing"] if failure == "open" else [])], search=True) as directory:
                        if failure == "fstat":
                            with mock.patch.object(os, "fstat", side_effect=OSError("fictional fstat")), \
                                    storage.regular_reader(directory, "brief.md"):
                                pass
                        elif failure == "decode":
                            with storage.regular_reader(directory, "brief.md") as fd, os.fdopen(fd, encoding="utf-8", closefd=False) as stream:
                                stream.read()
                        else:
                            raise RuntimeError("fictional caller")
            self.assertEqual(Counter(opened), Counter(closed), failure)
