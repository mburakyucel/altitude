"""Issue #617: confined workers keep writable tool caches without moving installed managers."""
import json
import subprocess
import sys
import time
import tomllib
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
        self.patch(engines, "_codex_processes", {})
        self.patch(engines, "claude_agents", return_value=[])
        self.patch(platform, "job_active", return_value=False)
        for host in ("linux", "darwin"):
            for engine in ("claude", "codex"):
                task = T.new(self.project, f"{host} {engine} cache", "Fictional worker")
                root = S.task_dir(self.project, task["slug"]) / "l2-engine"
                for resume in (False, True):
                    with self.subTest(host=host, engine=engine, resume=resume):
                        init = ({"type": "system", "subtype": "init", "session_id": "session"}
                                if engine == "claude" else {"type": "thread.started", "thread_id": "session"})
                        end = ({"type": "result", "is_error": True, "result": "fixture failure"}
                               if engine == "claude" else {"type": "turn.failed", "error": {"message": "fixture failure"}})
                        script = ("import os, pathlib, sys\n"
                                  "sys.stdin.read()\n"
                                  "for key in ('XDG_CACHE_HOME','npm_config_cache','npm_config_store_dir','PIP_CACHE_DIR'):\n"
                                  " p=pathlib.Path(os.environ[key]); p.mkdir(parents=True,exist_ok=True); (p/'fixture').write_text('ok')\n"
                                  f"print({json.dumps(init)!r}, flush=True)\n"
                                  f"print({json.dumps(end)!r}, flush=True)\n")
                        def command(unit, argv, child_env, **kw):
                            self.assertEqual(child_env["ALTITUDE_TASK"], task["slug"])
                            self.assertEqual(child_env["COREPACK_HOME"], "/fixture/managers")
                            for key in ("XDG_CACHE_HOME", "npm_config_cache", "npm_config_store_dir", "PIP_CACHE_DIR"):
                                self.assertTrue(Path(child_env[key]).is_relative_to(root / "tool-cache"))
                                if engine == "claude":
                                    self.assertTrue(any(Path(child_env[key]).is_relative_to(path) for path in kw["writable"]))
                                else:
                                    settings = [argv[n + 1] for n,part in enumerate(argv[:-1]) if part == "-c"]
                                    policy = tomllib.loads("\n".join(setting for setting in settings if setting.startswith("permissions.")))
                                    roots = policy["permissions"]["altitude-task"]["workspace_roots"]
                                    self.assertTrue(any(Path(child_env[key]).is_relative_to(path) for path in roots))
                            self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", child_env)
                            return ["/usr/bin/env", "-i", *(f"{k}={v}" for k,v in child_env.items()),
                                    sys.executable, "-c", script]
                        with mock.patch.object(platform.sys, "platform", host), \
                             mock.patch.object(platform, "job_command", side_effect=command):
                            result = engines._start_worker(engine, "fixture", "continue", cwd=self.repo,
                                job_root=root, resume="session" if resume else None,
                                extra_env={"ALTITUDE_TASK":task["slug"], "COREPACK_HOME":"/fixture/managers",
                                           "XDG_CACHE_HOME":"/unwritable/service-cache", "PIP_CACHE_DIR":"/unwritable/pip"})
                            self.assertEqual(result["returncode"], 0, result)
                            worker_id = result["agent"]["id"]
                            process = engines._codex_processes.get(worker_id)
                            if process is not None:
                                process.wait(timeout=5)
                            row = engines.worker(engine, {"agent_id":worker_id}, job_root=root)
                            self.assertEqual((row["sessionId"], row["state"]), ("session", "failed"))

    def test_archival_removes_only_this_tasks_tool_cache_and_retains_evidence(self):
        self.assertTrue(T.shutil.rmtree.avoids_symlink_attacks)
        task = T.new(self.project, "Cache lifetime", "Fictional task")
        other = T.new(self.project, "Other lifetime", "Fictional task")
        directory = S.task_dir(self.project, task["slug"])
        cache = directory / "l2-engine/tool-cache/pnpm"
        cache.mkdir(parents=True)
        (cache / "package").write_text("fixture package")
        evidence = directory / "l2-engine/worker.json"
        evidence.write_text('{}')
        other_cache = S.task_dir(self.project, other["slug"]) / "l2-engine/tool-cache"
        other_cache.mkdir(parents=True)
        T.reject(self.project, task["slug"], "Fixture rejection", actor="l3")
        archived = S.archive_dir(self.project) / task["slug"]
        self.assertFalse((archived / "l2-engine/tool-cache").exists())
        self.assertTrue((archived / "l2-engine/worker.json").exists())
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "rejected")
        self.assertTrue(other_cache.exists())

    def test_cache_cleanup_failure_does_not_claim_archival(self):
        task = T.new(self.project, "Cache cleanup error", "Fictional task")
        (S.task_dir(self.project, task["slug"]) / "l2-engine/tool-cache").mkdir(parents=True)
        with mock.patch.object(T.shutil, "rmtree", side_effect=PermissionError("cleanup refused")):
            with self.assertRaisesRegex(PermissionError, "cleanup refused"):
                T.reject(self.project, task["slug"], "Fixture rejection", actor="l3")
        self.assertTrue(S.task_dir(self.project, task["slug"]).exists())
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "queued")
        self.assertFalse((S.archive_dir(self.project) / task["slug"]).exists())

    def test_cache_cleanup_refuses_linked_parent_and_never_follows_package_links(self):
        task = T.new(self.project, "Cache linked parent", "Fictional task")
        directory = S.task_dir(self.project, task["slug"])
        elsewhere = self.tmp / "elsewhere"
        (elsewhere / "tool-cache").mkdir(parents=True)
        keep = elsewhere / "tool-cache/package"
        keep.write_text("unrelated data")
        (directory / "l2-engine").symlink_to(elsewhere)
        with self.assertRaises(OSError):
            T.reject(self.project, task["slug"], "Fixture rejection", actor="l3")
        self.assertEqual(keep.read_text(), "unrelated data")
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "queued")
        (directory / "l2-engine").unlink()
        cache = directory / "l2-engine/tool-cache"
        cache.mkdir(parents=True)
        (cache / "linked-package").symlink_to(elsewhere)
        T.reject(self.project, task["slug"], "Fixture rejection", actor="l3")
        self.assertEqual(keep.read_text(), "unrelated data")
        self.assertFalse((S.archive_dir(self.project) / task["slug"] / "l2-engine/tool-cache").exists())

    def test_completion_cleanup_failure_retains_reported_state_and_no_digest(self):
        task = T.new(self.project, "Completion cleanup error", "Fictional task")
        T.dispatch(self.project, task["slug"], attempt=1, session_id="fixture", agent_id="fixture",
                   worktree="/fictional/worktree", branch="fixture")
        T.report(self.project, task["slug"], {"verdict":"ok", "prs":[], "spend":{}})
        (S.task_dir(self.project, task["slug"]) / "l2-engine/tool-cache").mkdir(parents=True)
        with mock.patch.object(T.shutil, "rmtree", side_effect=PermissionError("cleanup refused")):
            with self.assertRaisesRegex(PermissionError, "cleanup refused"):
                T.done(self.project, task["slug"], "Fixture completion")
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "reported")
        self.assertFalse((S.archive_dir(self.project) / task["slug"]).exists())
        self.assertFalse((S.task_dir(self.project, task["slug"]) / "digest.md").exists())

    def test_cache_cleanup_stays_on_open_job_when_its_parent_is_replaced(self):
        task = T.new(self.project, "Cache parent race", "Fictional task")
        directory = S.task_dir(self.project, task["slug"])
        job = directory / "l2-engine"
        (job / "tool-cache").mkdir(parents=True)
        elsewhere = self.tmp / "elsewhere"
        (elsewhere / "tool-cache").mkdir(parents=True)
        keep = elsewhere / "tool-cache/package"
        keep.write_text("unrelated data")
        remove = T.shutil.rmtree
        def replace_parent(path, **kwargs):
            job.rename(directory / "old-job")
            job.symlink_to(elsewhere)
            return remove(path, **kwargs)
        with mock.patch.object(T.shutil, "rmtree", side_effect=replace_parent):
            T._remove_tool_cache(self.project, task["slug"])
        self.assertFalse((directory / "old-job/tool-cache").exists())
        self.assertEqual(keep.read_text(), "unrelated data")

    def test_linked_cache_is_refused_and_entry_repair_allows_retry(self):
        task = T.new(self.project, "Cache entry repair", "Fictional task")
        job = S.task_dir(self.project, task["slug"]) / "l2-engine"
        job.mkdir()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        keep = elsewhere / "package"
        keep.write_text("unrelated data")
        (job / "tool-cache").symlink_to(elsewhere)
        with self.assertRaises(OSError):
            T.reject(self.project, task["slug"], "Fixture rejection", actor="l3")
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "queued")
        (job / "tool-cache").unlink()
        T.reject(self.project, task["slug"], "Fixture rejection", actor="l3")
        self.assertEqual(keep.read_text(), "unrelated data")
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "rejected")

    def test_configured_home_link_is_a_trusted_anchor(self):
        task = T.new(self.project, "Linked runtime home", "Fictional task")
        cache = S.task_dir(self.project, task["slug"]) / "l2-engine/tool-cache"
        cache.mkdir(parents=True)
        home = self.tmp / "runtime-home"
        home.symlink_to(config.ROOT)
        with mock.patch.object(config, "ROOT", home):
            T._remove_tool_cache(self.project, task["slug"])
        self.assertFalse(cache.exists())

    def test_reject_stops_a_cache_writer_before_disposal_and_stop_failure_retains_cache(self):
        self.quiet_engines()
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = T.new(self.project, f"Reject writer {engine}", "Fictional task")
                task = T.dispatch(self.project, task["slug"], attempt=1, session_id="session", agent_id="fixture",
                                  worktree=str(self.repo), branch="fixture", l2_engine=engine)
                root = dispatch.l2_job_root(self.project, task["slug"])
                cache = root / "tool-cache"
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
                self.assertFalse((S.archive_dir(self.project) / task["slug"] / "l2-engine/tool-cache").exists())
                self.assertEqual(S.load_task(self.project, task["slug"])["state"], "rejected")
