"""Real fictional storage verifies the narrow project-lock and fixed-brief namespace boundary."""
import fcntl
import os
import select
import subprocess
import sys
import unittest
from collections import Counter
from contextlib import contextmanager
from unittest import mock

from tests.support import AltitudeCase
from altitude import (config, dispatch, engines, incidents, installation, l3, platform, project_setup, reviews,
                      server, state as S, storage, transcript)


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

    def test_existing_read_only_lock_keeps_its_bytes_and_can_serialize(self):
        # A task worker takes Altitude's locks in a home it cannot write.
        self.assertNotEqual(os.geteuid(), 0, "root cannot establish lock access parity")
        self.directory.mkdir()
        for name, locked in self.locks():
            leaf = self.directory / name
            leaf.write_bytes(b"existing lock bytes")
            leaf.chmod(0o400)
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

    def runtime_writers(self):
        """Every daemon append, log and lock in the runtime root, with the file each one opens."""
        project, slug = self.directory, self.directory / "tasks" / "demo"
        self.patch(config, "MONITOR_DIR", self.runtime / "monitor")
        self.patch(config, "INCIDENT_INDEX", self.runtime / "incidents.jsonl")
        self.patch(incidents, "FAULTS", self.runtime / "monitor" / "faults.json")
        self.patch(server, "LOG", self.runtime / "altd.log")

        @contextmanager
        def called(function, *args, **kwargs):
            function(*args, **kwargs)
            yield
        return (
            (slug / "events.log", lambda: called(S.append_event, self.project, "demo", "worker-exit",
                                                 reason="$(planted command)")),
            (project / "events.log", lambda: called(S.project_log, self.project, "note", text="$(planted)")),
            (project / "chat.jsonl", lambda: called(l3.chat_log, self.project, "l2", "$(planted)")),
            (project / "chat.lock", lambda: called(l3.chat_log, self.project, "l2", "text")),
            (project / ".publication-settlement.lock", lambda: dispatch.publication_settlement(self.project)),
            (project / ".setup.lock", lambda: project_setup.operation_lock(self.project)),
            (project / "incidents" / ".lock", lambda: incidents._incident_lock(self.project)),
            (project / "incidents.jsonl", lambda: called(incidents.new_incident, self.project, title="fixture",
                                                         task=None, what="$(planted)", evidence="-", cause="-",
                                                         tags=[])),
            (slug / ".review-merge.lock", lambda: reviews.merge_lock(self.project, "demo")),
            (self.runtime / "update.lock", lambda: installation._changing_update_record()),
            (self.runtime / ".launch.lock", lambda: dispatch.launch_lock()),
            (self.runtime / ".projects.lock", lambda: config.projects_lock()),
            (self.runtime / "monitor" / "restart.lock", lambda: config.restart_lock()),
            (self.runtime / "monitor" / "faults.lock", lambda: incidents._fault_lock()),
            (self.runtime / "incidents.jsonl", lambda: called(incidents._index_append, {"id": "x"})),
            (slug / "l2-engine" / "w.stdout.jsonl",
             lambda: open(slug / "l2-engine" / "w.stdout.jsonl", "ab", opener=storage.opener)),
            (slug / "l2-engine" / "w.stderr.log",  # an opener never truncates before the regular-file check
             lambda: open(slug / "l2-engine" / "w.stderr.log", "wb", opener=storage.opener)),
        )

    def test_runtime_writers_never_write_through_planted_links(self):
        # A worker can write the runtime root; a writer following its link would write outside the sandbox for it.
        victim = self.outside / "shell-startup"
        for leaf, written in self.runtime_writers():
            with self.subTest(leaf=leaf.relative_to(self.runtime)):
                victim.write_text("precious\n")
                with written():
                    pass  # creates the leaf and its folders
                leaf.unlink()
                leaf.symlink_to(victim)
                with self.assertRaises((OSError, ValueError)), written():
                    pass
                leaf.unlink()
                os.link(victim, leaf)
                with self.assertRaises((OSError, ValueError)), written():
                    pass
                leaf.unlink()
                parent = leaf.parent
                if parent != self.runtime:  # a worker cannot replace the runtime root itself
                    parent.rename(self.tmp / "moved")
                    parent.symlink_to(self.outside, target_is_directory=True)
                    with self.assertRaises((OSError, ValueError)), written():
                        pass
                    parent.unlink()
                    (self.tmp / "moved").rename(parent)
                self.assertEqual(victim.read_text(), "precious\n")
                self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ["shell-startup"])

    def test_worker_launch_refuses_a_linked_job_folder(self):
        jobs = S.task_dir(self.project, "demo") / "l2-engine"
        jobs.parent.mkdir(parents=True)
        jobs.symlink_to(self.outside, target_is_directory=True)
        self.patch(engines.common, "_worker_processes", {})
        self.patch(engines.claude, "claude_agents", return_value=[])
        worktree = self.task_worktree("demo")
        with mock.patch.object(engines.common.subprocess, "Popen", side_effect=AssertionError("launched")), \
                self.assertRaises((OSError, ValueError)):
            engines.start_l2("claude", "project/worker-1", "brief", cwd=worktree, persona=None, model=None,
                             settings=None, extra_env=self.worker_env("demo"), job_root=jobs)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_record_replacement_replaces_links_and_refuses_linked_folders(self):
        victim = self.outside / "shell-startup"
        folder = S.task_dir(self.project, "demo")
        folder.mkdir(parents=True)
        record = folder / "status.json"
        for plant in (record.symlink_to, lambda target: os.link(target, record)):
            victim.write_text("precious\n")
            plant(victim)
            S.write_json(record, {"text": "$(planted)"})
            self.assertFalse(record.is_symlink())
            self.assertEqual(os.stat(record).st_nlink, 1)
            self.assertEqual(S.read_json(record), {"text": "$(planted)"})
            self.assertEqual(victim.read_text(), "precious\n")
            record.unlink()
        folder.rmdir()
        folder.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            S.write_json(record, {"text": "$(planted)"})
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ["shell-startup"])

    def test_fixed_runtime_records_replace_a_planted_link(self):
        self.patch(config, "MONITOR_DIR", self.runtime / "monitor")
        self.patch(config, "PROJECTS_FILE", self.runtime / "projects.json")
        self.patch(config, "RELEASE", {"repository": "https://example.invalid/altitude"})
        self.patch(installation, "update_status", return_value={"available": {"version": "v9.9.9"},
                                                               "automatic_pending": False})
        victim = self.outside / "shell-startup"  # not there yet: a writer following the link would create it
        writers = ((engines.common.usage_limit_path(), lambda: engines.common.note_usage_limit("2099-01-01T00:00Z")),
                   (self.runtime / "claude-settings.json", engines.claude.claude_settings),
                   (self.runtime / "projects.json", config.ensure_root),
                   (self.runtime / "update-notice", installation.update_notice))
        for leaf, write in writers:
            with self.subTest(leaf=leaf.name):
                leaf.parent.mkdir(parents=True, exist_ok=True)
                leaf.unlink(missing_ok=True)
                leaf.symlink_to(victim)
                write()
                self.assertFalse(leaf.is_symlink())
                self.assertEqual(list(self.outside.iterdir()), [])

    def test_server_log_drops_a_line_rather_than_follow_a_link(self):
        self.patch(server, "LOG", self.runtime / "altd.log")
        victim = self.outside / "shell-startup"
        victim.write_text("precious\n")
        self.runtime.joinpath("altd.log").symlink_to(victim)
        server.log("$(planted)")
        self.assertEqual(victim.read_text(), "precious\n")

    def test_review_context_and_worker_failure_never_read_through_links(self):
        secret = self.outside / "secret"
        secret.write_text("OUTSIDE SECRET\n")
        folder = S.task_dir(self.project, "demo")
        folder.mkdir(parents=True)
        (folder / "request.md").write_text("request\n")
        (folder / "brief.md").symlink_to(secret)
        self.patch(reviews.T, "task_messages", return_value=[])
        with self.assertRaises(reviews.T.TransitionError):
            reviews._context(self.project, {"slug": "demo"})
        (folder / "brief.md").unlink()
        (folder / "brief.md").write_bytes("Acceptance: café\r\nDone\r".encode())
        self.assertEqual(reviews._context(self.project, {"slug": "demo"})["brief"], "Acceptance: café\nDone\n")
        (folder / "brief.md").write_bytes(b"\xff")
        with self.assertRaisesRegex(reviews.T.TransitionError, "not UTF-8"):
            reviews._context(self.project, {"slug": "demo"})
        (folder / "brief.md").unlink()
        self.assertEqual(reviews._context(self.project, {"slug": "demo"})["brief"], "")
        jobs = folder / "l2-engine"
        jobs.mkdir()
        S.write_json(jobs / "w1.json", {"id": "w1", "engine": "codex", "unit": "", "started_at": S.now()})
        (jobs / "w1.stdout.jsonl").write_text("")
        (jobs / "w1.stdout.jsonl").unlink()
        os.link(secret, jobs / "w1.stdout.jsonl")
        with self.assertRaises(ValueError):
            storage.tail(jobs / "w1.stdout.jsonl", 500)
        (jobs / "w1.stdout.jsonl").unlink()
        (jobs / "w1.stdout.jsonl").write_text("")
        (jobs / "w1.stderr.log").symlink_to(secret)
        self.patch(platform, "job_active", return_value=False)
        row = engines.owned_worker("w1", job_root=jobs)
        self.assertEqual(row["state"], "failed")
        self.assertNotIn("SECRET", row["detail"])

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
