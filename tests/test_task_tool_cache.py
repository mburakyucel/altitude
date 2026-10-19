"""Issue #617: confined workers keep writable tool caches without moving installed managers, in the task's own scratch
folder beside Altitude's home, which is not among their writable roots (docs/ARCHITECTURE.md#task-door)."""
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, make_repo
from altitude import config, dispatch, engines, platform, state as S, tasks as T


class TaskToolCache(AltitudeCase):
    def test_corepack_reads_original_install_and_cache_environments_are_not_shared(self):
        original = {"HOME": "/fixture/home", "XDG_CACHE_HOME": "/fixture/original",
                    "NPM_CONFIG_CACHE": "/forbidden/npm", "NPM_CONFIG_STORE_DIR": "/forbidden/pnpm"}
        first = engines.task_tool_env(original, self.tmp / "one")
        second = engines.task_tool_env(original, self.tmp / "two")
        self.assertEqual(first["COREPACK_HOME"], "/fixture/original/node/corepack")
        self.assertNotEqual(first["XDG_CACHE_HOME"], second["XDG_CACHE_HOME"])
        self.assertNotIn("NPM_CONFIG_CACHE", first)
        self.assertNotIn("NPM_CONFIG_STORE_DIR", first)
        self.assertEqual(original["XDG_CACHE_HOME"], "/fixture/original")
        self.assertEqual(engines.task_tool_env({"HOME": "/fixture/home"}, self.tmp)["COREPACK_HOME"],
                         "/fixture/home/.cache/node/corepack")
        self.assertEqual(engines.task_tool_env({**original, "COREPACK_HOME": "/installed/managers"}, self.tmp)
                         ["COREPACK_HOME"], "/installed/managers")

    def test_both_platforms_and_engines_launch_resume_and_report_failure_with_writable_caches(self):
        make_repo(self.repo)
        self.patch(engines.common, "_worker_processes", {})
        self.patch(engines.claude, "claude_agents", return_value=[])
        self.patch(platform, "job_active", return_value=False)
        self.codex_signed_in()
        for host in ("linux", "darwin"):
            for engine in ("claude", "codex"):
                task = T.new(self.project, f"{host} {engine} cache", "Fictional worker")
                root = S.task_dir(self.project, task["slug"]) / "l2-engine"
                scratch = config.task_scratch(self.project, task["slug"])
                worktree = self.task_worktree(task["slug"])
                for resume in (False, True):
                    with self.subTest(host=host, engine=engine, resume=resume):
                        # The real driver runs an engine that writes every cache, starts its session and fails.
                        native = self.tmp / "engine"
                        native.write_text(f"#!{sys.executable}\n" + (
                            "import json, os, pathlib, sys\n"
                            "for key in ('XDG_CACHE_HOME','npm_config_cache','npm_config_store_dir','PIP_CACHE_DIR'):\n"
                            " p=pathlib.Path(os.environ[key]); p.mkdir(parents=True,exist_ok=True); (p/'fixture').write_text('ok')\n"
                            "def out(message):\n"
                            " print(json.dumps(message), flush=True)\n"
                            "if sys.argv[1:2] == ['app-server']:\n"
                            " for raw in sys.stdin:\n"
                            "  message = json.loads(raw); method = message.get('method')\n"
                            "  if method in ('initialize', 'account/login/start'): out({'id': message['id'], 'result': {}})\n"
                            "  elif method in ('thread/start', 'thread/resume'):\n"
                            "   out({'id': message['id'], 'result': {'thread': {'id': 'session'}}})\n"
                            "  elif method == 'turn/start':\n"
                            "   out({'id': message['id'], 'result': {'turn': {'id': 'turn-1'}}})\n"
                            "   out({'method': 'turn/completed', 'params': {'threadId': 'session', 'turn': {'id': 'turn-1', 'status': 'failed',\n"
                            "        'error': {'message': 'fixture failure'}}}})\n"
                            "else:\n"
                            " sys.stdin.readline()\n"
                            " out({'type': 'system', 'subtype': 'init', 'session_id': 'session'})\n"
                            " out({'type': 'result', 'is_error': True, 'result': 'fixture failure'})\n"))
                        native.chmod(0o755)
                        caches = {}
                        def command(unit, argv, child_env, **kw):
                            self.assertEqual(argv, engines.common._driver_command())
                            self.assertEqual(child_env["ALTITUDE_TASK"], task["slug"])
                            self.assertEqual(child_env["COREPACK_HOME"], "/fixture/managers")
                            for key in ("XDG_CACHE_HOME", "npm_config_cache", "npm_config_store_dir", "PIP_CACHE_DIR"):
                                caches[key] = Path(child_env[key])
                                self.assertTrue(caches[key].is_relative_to(scratch.resolve() / "tool-cache"))
                                self.assertTrue(any(caches[key].is_relative_to(path) for path in kw["writable"]))
                            # the task's own engine folder is its one root in Altitude's home
                            self.assertEqual([path for path in kw["writable"]
                                              if config.ROOT.resolve().is_relative_to(Path(path).resolve())
                                              or Path(path).resolve().is_relative_to(config.ROOT.resolve())],
                                             [Path(root) / engine], kw["writable"])
                            self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", child_env)
                            return ["/usr/bin/env", "-i", *(f"{k}={v}" for k,v in child_env.items()), *argv]
                        with mock.patch.object(platform.sys, "platform", host), \
                             mock.patch.object(config, "CLAUDE_BIN", str(native)), \
                             mock.patch.object(config, "CODEX_BIN", str(native)), \
                             mock.patch.object(platform, "job_command", side_effect=command):
                            result = engines.common._start_worker(engine, "fixture", "continue", cwd=worktree,
                                job_root=root, resume="session" if resume else None,
                                extra_env={"ALTITUDE_TASK":task["slug"], "ALTITUDE_SCRATCH": str(scratch),
                                           "COREPACK_HOME":"/fixture/managers",
                                           "XDG_CACHE_HOME":"/unwritable/service-cache", "PIP_CACHE_DIR":"/unwritable/pip"})
                            self.assertEqual(result["returncode"], 0, result)
                            worker_id = result["agent"]["id"]
                            process = engines.common._worker_processes.get(worker_id)
                            if process is not None:
                                process.wait(timeout=5)
                            row = engines.worker(engine, {"agent_id":worker_id}, job_root=root)
                            self.assertEqual((row["sessionId"], row["state"]), ("session", "failed"))
                        for key, cache in caches.items():
                            self.assertEqual((cache / "fixture").read_text(), "ok", key)

    def scratch(self, slug):
        return config.task_scratch(self.project, slug)

    def test_archival_removes_only_this_tasks_scratch_and_retains_evidence(self):
        self.assertTrue(T.shutil.rmtree.avoids_symlink_attacks)
        task = T.new(self.project, "Cache lifetime", "Fictional task")
        other = T.new(self.project, "Other lifetime", "Fictional task")
        cache = self.scratch(task["slug"]) / "tool-cache/pnpm"
        cache.mkdir(parents=True)
        (cache / "package").write_text("fixture package")
        evidence = S.task_dir(self.project, task["slug"]) / "l2-engine/worker.json"
        evidence.parent.mkdir()
        evidence.write_text('{}')
        other_scratch = self.scratch(other["slug"])
        other_scratch.mkdir(parents=True)
        T.reject(self.project, task["slug"], "Fixture rejection", actor="l3")
        self.assertFalse(self.scratch(task["slug"]).exists())
        self.assertTrue((S.archive_dir(self.project) / task["slug"] / "l2-engine/worker.json").exists())
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "rejected")
        self.assertTrue(other_scratch.exists())

    def test_scratch_cleanup_failure_does_not_claim_archival(self):
        task = T.new(self.project, "Cache cleanup error", "Fictional task")
        self.scratch(task["slug"]).mkdir(parents=True)
        with mock.patch.object(T.shutil, "rmtree", side_effect=PermissionError("cleanup refused")):
            with self.assertRaisesRegex(PermissionError, "cleanup refused"):
                T.reject(self.project, task["slug"], "Fixture rejection", actor="l3")
        self.assertTrue(S.task_dir(self.project, task["slug"]).exists())
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "queued")
        self.assertFalse((S.archive_dir(self.project) / task["slug"]).exists())

    def test_scratch_cleanup_refuses_a_linked_folder_and_never_follows_links_inside(self):
        task = T.new(self.project, "Cache linked folder", "Fictional task")
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        keep = elsewhere / "package"
        keep.write_text("unrelated data")
        scratch = self.scratch(task["slug"])
        scratch.parent.mkdir(parents=True)
        scratch.symlink_to(elsewhere)
        with self.assertRaises(OSError):
            T.reject(self.project, task["slug"], "Fixture rejection", actor="l3")
        self.assertEqual(keep.read_text(), "unrelated data")
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "queued")
        scratch.unlink()  # the entry repair
        (scratch / "tool-cache").mkdir(parents=True)
        (scratch / "tool-cache/linked-package").symlink_to(elsewhere)
        T.reject(self.project, task["slug"], "Fixture rejection", actor="l3")
        self.assertEqual(keep.read_text(), "unrelated data")
        self.assertFalse(scratch.exists())
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "rejected")

    def test_completion_cleanup_failure_retains_reported_state_and_no_digest(self):
        task = T.new(self.project, "Completion cleanup error", "Fictional task")
        T.dispatch(self.project, task["slug"], attempt=1, session_id="fixture", agent_id="fixture",
                   worktree="/fictional/worktree", branch="fixture", l2_engine=config.ENGINES[0])
        T.report(self.project, task["slug"], {"verdict":"ok", "prs":[], "spend":{}})
        self.scratch(task["slug"]).mkdir(parents=True)
        with mock.patch.object(T.shutil, "rmtree", side_effect=PermissionError("cleanup refused")):
            with self.assertRaisesRegex(PermissionError, "cleanup refused"):
                T.done(self.project, task["slug"], "Fixture completion")
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "reported")
        self.assertFalse((S.archive_dir(self.project) / task["slug"]).exists())
        self.assertFalse((S.task_dir(self.project, task["slug"]) / "digest.md").exists())

    def test_scratch_cleanup_stays_on_the_open_folder_when_its_parent_is_replaced(self):
        task = T.new(self.project, "Cache parent race", "Fictional task")
        project = self.scratch(task["slug"]).parent
        (project / task["slug"] / "tool-cache").mkdir(parents=True)
        elsewhere = self.tmp / "elsewhere"
        (elsewhere / task["slug"]).mkdir(parents=True)
        keep = elsewhere / task["slug"] / "package"
        keep.write_text("unrelated data")
        remove = T.shutil.rmtree
        def replace_parent(path, **kwargs):
            project.rename(project.with_name("old-project"))
            project.symlink_to(elsewhere)
            return remove(path, **kwargs)
        with mock.patch.object(T.shutil, "rmtree", side_effect=replace_parent):
            T._remove_scratch(self.project, task["slug"])
        self.assertFalse((project.with_name("old-project") / task["slug"]).exists())
        self.assertEqual(keep.read_text(), "unrelated data")

    def test_a_linked_home_parent_is_a_trusted_anchor(self):
        task = T.new(self.project, "Linked scratch parent", "Fictional task")
        cache = self.scratch(task["slug"]) / "tool-cache"
        cache.mkdir(parents=True)
        linked = self.tmp / "linked-parent"
        linked.symlink_to(config.SCRATCH.parent)
        with mock.patch.object(config, "SCRATCH", linked / config.SCRATCH.name):
            T._remove_scratch(self.project, task["slug"])
        self.assertFalse(cache.exists())

    def test_reject_stops_a_cache_writer_before_disposal_and_stop_failure_retains_cache(self):
        self.quiet_engines()
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = T.new(self.project, f"Reject writer {engine}", "Fictional task")
                task = T.dispatch(self.project, task["slug"], attempt=1, session_id="session", agent_id="fixture",
                                  worktree=str(self.repo), branch="fixture", l2_engine=engine)
                root = dispatch.l2_job_root(self.project, task["slug"])
                cache = self.scratch(task["slug"]) / "tool-cache"
                script = ("import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\n"
                          "end=time.monotonic()+10\nwhile time.monotonic()<end:\n"
                          " p.mkdir(parents=True,exist_ok=True); (p/'package').write_text('fixture'); time.sleep(.01)\n")
                child = subprocess.Popen([sys.executable, "-c", script, str(cache)])
                def cleanup():
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)
                self.addCleanup(cleanup)
                deadline = time.monotonic() + 5
                while not (cache / "package").exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue((cache / "package").exists())
                dispatch.request_task_operation(self.project, task["slug"], "reject", "Fixture rejection",
                                                actor=config.OPERATOR_ACTOR, generation="fixture")
                with mock.patch.object(engines, "remove_l2_worker", side_effect=RuntimeError("stop unconfirmed")):
                    refused = dispatch.run_task_operation(self.project, task["slug"])
                self.assertEqual(refused["request"]["status"], "refused", refused)
                self.assertIn("stop unconfirmed", refused["request"]["note"])
                self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")
                self.assertTrue(cache.exists())
                dispatch.request_task_operation(self.project, task["slug"], "reject", "Retry fixture rejection",
                                                actor=config.OPERATOR_ACTOR, generation="fixture")
                def stop(actual_engine, worker_id, *, job_root):
                    self.assertEqual((actual_engine, worker_id, job_root), (engine, "fixture", root))
                    self.assertTrue(cache.exists())
                    child.terminate()
                    child.wait(timeout=5)
                    return "fixture writer stopped"
                with mock.patch.object(engines, "remove_l2_worker", side_effect=stop):
                    result = dispatch.run_task_operation(self.project, task["slug"])
                self.assertEqual(result["request"]["status"], "done", result)
                self.assertFalse((S.tasks_dir(self.project) / task["slug"]).exists())
                self.assertFalse(cache.exists())
                self.assertEqual(S.load_task(self.project, task["slug"])["state"], "rejected")
