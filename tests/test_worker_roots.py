"""A task worker on either engine writes its worktree, the Git parts it needs, its own scratch folder and its own engine
folder, never the rest of Altitude's home: a process confined to the roots each engine's launch names is refused every
record there, its own task's included, and every other task's engine folders, and on macOS
the launcher's job folders, where a job's input waits with its tokens (docs/ARCHITECTURE.md#task-door), and lists no
browser's profile."""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.support import (NATIVE_PLATFORM, AltitudeCase, add_worktree, make_repo, read_only_home,
                           require_read_only_home)
from altitude import config, dispatch, engines, l3, platform, state as S, tasks as T

CHILD = r'''
import json, os, sys
out = {}
for name, (kind, path) in json.loads(sys.argv[1]).items():
    try:
        if kind == "file":  # an existing record, opened for appending and left unchanged
            os.close(os.open(path, os.O_WRONLY | os.O_APPEND))
        elif kind == "read":  # an existing file, read
            with open(path) as f:
                f.read()
        elif kind == "list":  # an existing folder, listed
            os.listdir(path)
        else:  # a folder: a new file in it
            target = os.path.join(path, "probe")
            with open(target, "x") as f:
                f.write("fictional")
            os.unlink(target)
        out[name] = True
    except OSError:
        out[name] = False
print(json.dumps(out))
'''


class WorkerRoots(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        task = T.new(self.project, "Worker roots", "Write only the worktree and the scratch folder.")
        self.slug = task["slug"]
        self.worktree = add_worktree(self.repo, self.slug)
        other = T.new(self.project, "Another task", "A neighbour the worker must not write.")
        T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "A fictional message, so the conversation exists.")
        folder = S.task_dir(self.project, self.slug)
        record = engines.common._worker_paths(dispatch.l2_job_root(self.project, self.slug), "worker")["record"]
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("{}")
        (folder / "reviews").mkdir()
        images = config.project_dir(self.project) / "images"
        images.mkdir()
        self.scratch = engines.common.task_scratch(dispatch.l2_env(self.project, self.slug, 1))
        self.own = {engine: dispatch.l2_job_root(self.project, self.slug) / engine for engine in ("claude", "codex")}
        others = {engine: dispatch.l2_job_root(self.project, other["slug"]) / engine for engine in ("claude", "codex")}
        for folder_path in (*self.own.values(), *others.values()):
            folder_path.mkdir(parents=True)
        self.targets = {
            "task record": ("file", S.status_path(self.project, self.slug)),
            "worker record": ("file", record),
            "conversation": ("file", folder / "conversation.jsonl"),
            "project list": ("file", config.PROJECTS_FILE),
            "task folder": ("folder", folder), "other task": ("folder", S.task_dir(self.project, other["slug"])),
            "reviews folder": ("folder", folder / "reviews"), "image staging": ("folder", images),
            "home": ("folder", config.ROOT), "scratch": ("folder", self.scratch),
            "own Claude folder": ("folder", self.own["claude"]), "other task's Claude folder": ("folder", others["claude"]),
            "own Codex folder": ("folder", self.own["codex"]), "other task's Codex folder": ("folder", others["codex"])}
        if NATIVE_PLATFORM == "darwin":  # the launcher's job folders, where a job's input waits until it is read
            saved = platform._jobs() / "dev.altitude.job.fixture" / "stdin"
            saved.parent.mkdir(parents=True)
            self.addCleanup(shutil.rmtree, saved.parent)
            saved.write_text('{"FIXTURE": "input"}\n')
            self.targets.update({"job folders": ("folder", platform._jobs()), "saved input": ("file", saved),
                                 "saved input, read": ("read", saved)})
        self.assertTrue(all(Path(path).exists() for _, path in self.targets.values()))

    def roots(self, engine: str) -> tuple[list[Path], tuple]:
        """The writable roots and read-only paths a worker launch on `engine` names."""
        self.patch(engines.claude, "claude_agents", return_value=[])
        _, _, writable, read_only, *_ = engines.common.implementation(engine).prepare_worker(
            "fixture", "brief", cwd=self.worktree, scratch=self.scratch,
            job_root=dispatch.l2_job_root(self.project, self.slug), persona=None, model=None,
            settings=self.tmp / "settings.json", effort=None, resume=None, images=())
        return [Path(root) for root in writable], read_only

    def spec(self) -> str:
        return json.dumps({name: (kind, str(path)) for name, (kind, path) in self.targets.items()})

    def written(self, command: list[str]) -> dict:
        run = subprocess.run([*command, sys.executable, "-I", "-c", CHILD, self.spec()], capture_output=True, text=True,
                             cwd=self.worktree, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def assert_only_own(self, written: dict, engine: str):
        own = {"scratch", "own Claude folder" if engine == "claude" else "own Codex folder"}
        self.assertEqual({name for name, allowed in written.items() if allowed}, own, written)

    def test_no_engines_roots_reach_the_coordinator_folder_on_either_platform(self):
        """A worker that could write the coordinators' folder or any folder above it could plant, replace or redirect
        a socket or a turn's commands (#973 moved its name on macOS), or a coordinator's Claude settings in its
        persistent folder."""
        for darwin, persistent in ((False, False), (False, True), (True, False)):
            with mock.patch.object(platform, "_darwin", return_value=darwin):
                folder = platform.coordinator_directory(persistent=persistent).resolve()
                if not persistent:
                    self.assertEqual(l3.verb_socket_path(self.project).parent.resolve(), folder)
            for engine in ("claude", "codex"):
                with self.subTest(darwin=darwin, persistent=persistent, engine=engine):
                    writable, _ = self.roots(engine)
                    # A root ending in * admits every path that starts with it (Claude's account file family).
                    patterns = [os.path.realpath(str(root)[:-1]) for root in writable if str(root).endswith("*")]
                    self.assertFalse([prefix for prefix in patterns if str(folder).startswith(prefix)], writable)
                    self.assertFalse([root for root in writable if not str(root).endswith("*")
                                      and folder.is_relative_to(root.resolve())], writable)

    def test_each_engines_roots_admit_the_scratch_folder_and_no_record_in_the_home(self):
        require_read_only_home(self)
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                writable, _ = self.roots(engine)
                home = config.ROOT.resolve()
                inside = [root for root in writable if home.is_relative_to(root.resolve())
                          or root.resolve().is_relative_to(home)]
                self.assertEqual(inside, [self.own[engine]], writable)
                # Landlock names folders that exist; Claude's account file family is a name pattern, not a folder.
                folders = tuple(root for root in writable if not str(root).endswith("*") and root.is_dir())
                self.assert_only_own(self.written(read_only_home([], folders)), engine)

    @unittest.skipUnless(NATIVE_PLATFORM == "darwin", "Seatbelt's masks; Linux's are bubblewrap's (TestSandboxedJob)")
    def test_each_engines_worker_cannot_list_a_browser_profile_by_either_spelling(self):
        """A browser's profile holds every site's sign-in, a paired device's among them (#1053)."""
        require_read_only_home(self)
        home = Path(os.path.realpath(self.tmp)) / "home"
        profile = home / "Library/Application Support/Firefox"
        (profile / "Profiles").mkdir(parents=True)
        self.targets = {"browser profile": ("list", profile),
                        "browser profile, by the data volume": ("list", platform.DATA_VOLUME + str(profile))}
        self.assertTrue(os.listdir(platform.DATA_VOLUME + str(profile)), "the host lists it by both spellings")
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                writable, _ = self.roots(engine)
                folders = tuple(root for root in writable if not str(root).endswith("*") and root.is_dir())
                with mock.patch.object(platform.Path, "home", return_value=home):
                    command = read_only_home([], folders)
                self.assertEqual(self.written(command), {"browser profile": False,
                                                         "browser profile, by the data volume": False})
