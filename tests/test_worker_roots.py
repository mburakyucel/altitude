"""A task worker on either engine writes its worktree, the Git parts it needs and its own scratch folder, and a Claude
worker its own Claude folder, never the rest of Altitude's home: a process confined to the roots each engine's launch
names is refused every record there, its own task's included, and every other task's Claude folder
(docs/ARCHITECTURE.md#task-door)."""
import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, add_worktree, make_repo, read_only_home, require_read_only_home
from altitude import config, dispatch, engines, l3, platform, state as S, tasks as T

CHILD = r'''
import json, os, sys
out = {}
for name, (kind, path) in json.loads(sys.argv[1]).items():
    try:
        if kind == "file":  # an existing record, opened for appending and left unchanged
            os.close(os.open(path, os.O_WRONLY | os.O_APPEND))
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


def settings(cmd: list[str]) -> list[str]:
    """The `-c` settings of a Codex launch."""
    return [cmd[n + 1] for n, part in enumerate(cmd[:-1]) if part == "-c"]


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
        self.own_claude = dispatch.l2_job_root(self.project, self.slug) / "claude"
        other_claude = dispatch.l2_job_root(self.project, other["slug"]) / "claude"
        for folder_path in (self.own_claude, other_claude):
            folder_path.mkdir(parents=True)
        self.targets = {
            "task record": ("file", S.status_path(self.project, self.slug)),
            "worker record": ("file", record),
            "conversation": ("file", folder / "conversation.jsonl"),
            "project list": ("file", config.PROJECTS_FILE),
            "task folder": ("folder", folder), "other task": ("folder", S.task_dir(self.project, other["slug"])),
            "reviews folder": ("folder", folder / "reviews"), "image staging": ("folder", images),
            "home": ("folder", config.ROOT), "scratch": ("folder", self.scratch),
            "own Claude folder": ("folder", self.own_claude), "other task's Claude folder": ("folder", other_claude)}
        self.assertTrue(all(Path(path).exists() for _, path in self.targets.values()))

    def roots(self, engine: str) -> tuple[list[Path], tuple]:
        """The writable roots and read-only paths a worker launch on `engine` names."""
        self.patch(engines.claude, "claude_agents", return_value=[])
        cmd, _, writable, read_only, _ = engines.common.implementation(engine).prepare_worker(
            "fixture", "brief", cwd=self.worktree, scratch=self.scratch,
            job_root=dispatch.l2_job_root(self.project, self.slug), persona=None, model=None,
            settings=self.tmp / "settings.json", effort=None, resume=None, images=())
        if writable is None:  # Codex names its roots in its own sandbox settings
            policy = tomllib.loads("\n".join(s for s in settings(cmd) if s.startswith("permissions.")))
            writable = list(policy["permissions"]["altitude-task"]["workspace_roots"])
        return [Path(root) for root in writable], read_only

    def spec(self) -> str:
        return json.dumps({name: (kind, str(path)) for name, (kind, path) in self.targets.items()})

    def written(self, command: list[str]) -> dict:
        run = subprocess.run([*command, sys.executable, "-I", "-c", CHILD, self.spec()], capture_output=True, text=True,
                             cwd=self.worktree, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def assert_only_own(self, written: dict, engine: str):
        own = {"scratch", "own Claude folder"} if engine == "claude" else {"scratch"}
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
                self.assertEqual(inside, [self.own_claude] if engine == "claude" else [], writable)
                # Landlock names folders that exist; Claude's account file family is a name pattern, not a folder.
                folders = tuple(root for root in writable if not str(root).endswith("*") and root.is_dir())
                self.assert_only_own(self.written(read_only_home([], folders)), engine)

    @unittest.skipUnless(os.environ.get("ALTITUDE_TEST_CODEX_SANDBOX") == "1",
                         "opt in on a host with the real Codex sandbox and Altitude's home outside temporary folders")
    def test_codexs_own_sandbox_admits_the_scratch_folder_and_no_record_in_the_home(self):
        """A Codex worker runs under Codex's own sandbox rather than Altitude's profile: given the task's permissions,
        it too admits the scratch folder and no record in the home. No model is asked; Codex needs no sign-in."""
        from tests.support import run_native_sandbox_probe
        cmd, *_ = engines.common.implementation("codex").prepare_worker(
            "fixture", "brief", cwd=self.worktree, scratch=self.scratch,
            job_root=dispatch.l2_job_root(self.project, self.slug), persona=None, model=None,
            settings=self.tmp / "settings.json", effort=None, resume=None, images=())
        task = settings(cmd)
        profile = next(s for s in task if s.startswith("default_permissions=")).split("=", 1)[1].strip('"')
        # The environment a worker's job gets, with its temporary folder.
        result = run_native_sandbox_probe(self.worktree, task, CHILD, [self.spec()], profile=profile,
                                          env=platform.job_env(engines.launch_env()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_only_own(json.loads(result.stdout), "codex")
