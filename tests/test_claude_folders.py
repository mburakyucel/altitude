"""Every Claude job Altitude starts keeps its Claude state in a folder of its own and signs in with Altitude's own
token, so a task worker cannot change the settings, hooks, sign-in or GitHub CLI configuration that other runs,
the operator's own Claude and Altitude's `gh` calls load. Fixture engines only; no provider is contacted."""
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, add_worktree, make_repo
from altitude import access, config, dispatch, engines, l3, platform, repair, route, state as S

TOKEN = "fixture-claude-token"


class _Stop(Exception):
    pass


class _Process:
    def __init__(self):
        self.pid, self.returncode = 4242, None
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return 0


class TestClaudeFolders(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.operator = engines.claude.OPERATOR_HOME
        self.operator.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: (access.DIR / "claude.token").unlink(missing_ok=True))

    def save_token(self):
        access.save_engine_token("claude", TOKEN)

    def shared(self) -> list[Path]:
        """What other runs, the operator's own engines and Altitude's `gh` calls load from the home folder."""
        home = config.HOME
        return [self.operator, home / ".claude.json", home / ".claude.json.backup", home / ".claude.lock",
                home / ".codex", home / ".config/gh", home / ".local/share/claude", home / ".local/bin"]

    def assert_none_shared(self, roots):
        for root in map(Path, roots):
            for place in self.shared():
                with self.subTest(root=str(root), place=str(place)):
                    self.assertFalse(place == root or root in place.parents or str(root).endswith("*"),
                                     f"{root} lets the job write {place}")

    def test_no_job_on_either_engine_can_write_what_other_runs_load(self):
        worktree = add_worktree(make_repo(self.repo), "folders")
        job_root = self.runtime_tmp / "l2-engine"
        self.patch(engines.claude, "claude_agents", return_value=[])
        _, _, writable, _, env, _ = engines.claude.prepare_worker(
            "p/folders", "brief", cwd=worktree, scratch=config.task_scratch(self.project, "folders"), job_root=job_root,
            persona=None, model=None, settings=None,
            effort=None, resume=None, images=())
        self.assertIn(job_root / "claude", writable)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(job_root / "claude"))
        self.assert_none_shared(writable)

        captured = {}

        def job_command(unit, command, env, **kw):
            captured.update(kw)
            raise _Stop

        home = self.tmp / "coordinator-claude"
        with mock.patch.object(platform, "job_command", side_effect=job_command), self.assertRaises(_Stop):
            engines.claude_print("prompt", cwd=self.tmp, unit="altitude-coordinator-fixture.service", home=home)
        self.assertIn(home, captured["writable"])
        self.assert_none_shared(captured["writable"])
        self.assert_none_shared(engines.claude.review_writable(self.tmp / "runtime"))

        _, _, writable, _, env, _ = engines.codex.prepare_worker(
            "p/folders", "brief", cwd=worktree, scratch=config.task_scratch(self.project, "folders"), job_root=job_root,
            persona=None, model=None, settings=None, effort=None, resume=None, images=())
        self.assertIn(job_root / "codex", writable)
        self.assertEqual(env["CODEX_HOME"], str(job_root / "codex"))
        self.assert_none_shared(writable)
        self.assertIsNone(engines.codex.review_writable(self.tmp / "runtime"))

    def test_a_worker_starts_with_its_own_folder_and_sign_in_off_its_command_line(self):
        self.save_token()
        worktree = add_worktree(make_repo(self.repo), "worker-1")
        self.patch(engines.common, "_worker_processes", {})
        self.patch(engines.claude, "claude_agents", return_value=[])
        self.patch(platform, "job_active", return_value=True)
        self.patch(platform.sys, "platform", "linux")
        self.patch(platform, "bubblewrap_profile", return_value=[])  # confinement itself is test_worker_roots'
        self.fake_engines()
        (self.operator / "settings.json").write_text("{}")
        (self.operator / "skills").mkdir(exist_ok=True)
        (self.operator / "commands").mkdir(exist_ok=True)
        log, job_root = self.tmp / "engine.log", self.runtime_tmp / "l2-engine"
        launches = []

        def popen(cmd, **kw):
            kw["stdout"].write((json.dumps({"type": "system", "subtype": "init", "session_id": "s"}) + "\n").encode())
            launches.append((cmd, _Process()))
            return launches[-1][1]

        with mock.patch.object(engines.common.subprocess, "Popen", side_effect=popen):
            result = engines.start_l2("claude", "project/worker-1", "brief", cwd=worktree, persona=None, model=None,
                                      settings=None, job_root=job_root,
                                      extra_env={"FAKE_ENGINE_LOG": str(log),
                                                 "ALTITUDE_SCRATCH": str(config.task_scratch(self.project, "worker-1"))})
        self.assertEqual(result["returncode"], 0, result)
        command, sent = launches[0][0], launches[0][1].stdin.getvalue()
        self.assertNotIn(TOKEN, " ".join(command))
        spec = json.loads(sent)
        self.assertNotIn(TOKEN, json.dumps(spec["command"]))
        # A skill or command typed in task chat runs because the worker loads them and keeps the Skill tool.
        off = ("--restricted", "--safe-mode", "--bare", "--disable-slash-commands", "--tools", "--disallowedTools",
               "--setting-sources")
        self.assertEqual([arg for arg in spec["command"] if arg.startswith(off)], [])
        self.assertEqual(spec["env"], {"CLAUDE_CONFIG_DIR": str(job_root / "claude"), "CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
        # The driver, inside the job, prepares the folder before Claude starts.
        job = subprocess.run(command[command.index("--") + 1:], input=sent, capture_output=True, timeout=60)
        self.assertEqual(job.returncode, 0, job.stderr)
        first = json.loads(log.read_text().splitlines()[0])
        self.assertEqual(first["claude"], {"CLAUDE_CONFIG_DIR": str(job_root / "claude"), "CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
        home = job_root / "claude"
        self.assertEqual(os.readlink(home / "settings.json"), str(self.operator / "settings.json"))
        self.assertEqual(os.readlink(home / "skills"), str(self.operator / "skills"))
        self.assertEqual(os.readlink(home / "commands"), str(self.operator / "commands"))
        self.assertFalse(os.path.lexists(home / "plugins"), "only what the operator has is linked")
        self.assertFalse((home / ".claude.json").exists(), "the folder's state file starts empty")

    def test_the_driver_links_once_and_copies_a_resumed_session_into_the_folder_once(self):
        (self.operator / "CLAUDE.md").write_text("operator rules")
        project = self.operator / "projects" / "-fixture-repo"
        (project / "sid-1" / "subagents").mkdir(parents=True)
        (project / "sid-1.jsonl").write_text('{"turn": 1}\n')
        (project / "sid-1" / "subagents" / "agent-a.jsonl").write_text('{"helper": 1}\n')
        (project / "sid-2.jsonl").write_text('{"other": 1}\n')
        home = self.runtime_tmp / "claude"
        home.mkdir()
        (home / "settings.json").write_text('{"own": true}')
        copies = engines.claude._first_resume(home, "sid-1")
        self.assertEqual(copies, [project / "sid-1", project / "sid-1.jsonl"], "only that session's records")
        engines.claude._own_folder(home, list(map(str, copies)))
        self.assertEqual((home / "settings.json").read_text(), '{"own": true}', "an existing entry is kept")
        self.assertEqual(os.readlink(home / "CLAUDE.md"), str(self.operator / "CLAUDE.md"))
        copied = home / "projects" / "-fixture-repo"
        self.assertEqual((copied / "sid-1.jsonl").read_text(), '{"turn": 1}\n')
        self.assertEqual((copied / "sid-1" / "subagents" / "agent-a.jsonl").read_text(), '{"helper": 1}\n')
        self.assertFalse((copied / "sid-2.jsonl").exists())
        (copied / "sid-1.jsonl").write_text('{"turn": 1}\n{"turn": 2}\n')
        self.assertEqual(engines.claude._first_resume(home, "sid-1"), [], "a session in the folder is its own")
        engines.claude._own_folder(home, list(map(str, copies)))
        self.assertEqual((copied / "sid-1.jsonl").read_text(), '{"turn": 1}\n{"turn": 2}\n',
                         "the folder's own session is never replaced")
        for resume in ("unknown", "../sid-1", None):
            self.assertEqual(engines.claude._first_resume(home, resume), [])

    def test_the_operators_claude_folder_is_hidden_from_every_confined_job_but_what_its_launch_reopens(self):
        """Through the seam: the folder and the state file beside it, with a copy Claude left, are among the places
        every confined job is denied; each Claude launch names the entries its folder links to, a worker also the
        project's notes, and a launch resuming a session for the first time that session's records."""
        state = [config.HOME / ".claude.json", config.HOME / ".claude.json.tmp.1.fixture"]
        for path in state:
            path.write_text("fictional state")
            self.addCleanup(path.unlink)
        kept = self.tmp / "dotfiles" / "claude.json"  # a state copy a dotfiles manager links in
        kept.parent.mkdir()
        kept.write_text("fictional state")
        (config.HOME / ".claude.json.backup.fixture").symlink_to(kept)
        self.addCleanup((config.HOME / ".claude.json.backup.fixture").unlink)
        (self.operator / ".credentials.json").write_text("fictional sign-in")
        self.addCleanup((self.operator / ".credentials.json").unlink)
        (self.operator / "settings.json").write_text("{}")
        (self.operator / "skills").mkdir(exist_ok=True)
        hidden = platform._operator_places()
        self.assertLessEqual({os.path.realpath(self.operator), os.path.realpath(config.HOME) + "/.claude.json*",
                              os.path.realpath(kept)}, set(hidden),
                             "the state file's copies too, whenever Claude makes them, and where a linked one leads")
        self.assertLessEqual({os.path.realpath(self.operator), *map(os.path.realpath, state), os.path.realpath(kept)},
                             set(platform.operator_paths(engines.claude.operator_places())))
        linked = engines.claude._linked()
        self.assertLessEqual({self.operator / "settings.json", self.operator / "skills"}, set(linked))
        self.assertEqual(linked, [self.operator / name for name in engines.claude.LINKED
                                  if (self.operator / name).exists()], "only what the operator has")

        worktree = add_worktree(make_repo(self.repo), "hidden")
        notes = engines.notes_folder(self.repo)
        notes.mkdir(parents=True)
        session = self.operator / "projects" / "-repo" / "sid-old.jsonl"
        session.parent.mkdir(parents=True, exist_ok=True)
        session.write_text("{}\n")
        self.patch(engines.claude, "claude_agents", return_value=[])
        _, _, writable, read_only, _, launch = engines.claude.prepare_worker(
            "p/hidden", "brief", cwd=worktree, scratch=config.task_scratch(self.project, "hidden"),
            job_root=self.runtime_tmp / "l2-engine", persona=None, model=None,
            settings=dispatch.session_settings(self.project, "hidden"), effort=None, resume="sid-old", images=())
        self.assertEqual(list(read_only[-len(linked) - 2:]), [*linked, notes, session])
        self.assertEqual(launch, {"copies": [str(session)], "linked": True})
        self.assert_none_shared(writable)

        captured = {}

        def job_command(unit, command, env, **kw):
            captured.update(kw)
            raise _Stop

        with mock.patch.object(platform, "job_command", side_effect=job_command), self.assertRaises(_Stop):
            engines.claude_print("prompt", cwd=self.tmp, unit="altitude-coordinator-fixture.service",
                                 home=self.tmp / "coordinator-claude")
        self.assertEqual(captured["read_only"], tuple(linked), "a coordinator reads its links, not the notes")

        temporary = str(self.tmp / "job-tmp")
        args = platform.bubblewrap_profile([str(worktree)], [str(path) for path in read_only], temporary=temporary,
                                           hidden=hidden)
        pairs = [args[n:n + 3] for n in range(len(args))]
        for path in (*state, kept):
            self.assertIn(["--ro-bind", "/dev/null", os.path.realpath(path)], pairs)
        emptied = next(n for n, pair in enumerate(pairs) if pair[:2] == ["--tmpfs", os.path.realpath(self.operator)])
        for path in (*linked, notes, session):
            self.assertGreater(pairs.index(["--ro-bind-try", str(path), str(path)]), emptied)
        profile = platform.seatbelt_profile([str(worktree)], [str(path) for path in read_only], temporary=temporary,
                                            hidden=hidden)
        self.assertIn(f"(deny file-read* file-write* {platform._prefix_rule(os.path.realpath(config.HOME) + '/.claude.json')})",
                      profile)
        self.assertEqual(platform._prefix_rule('/Users/a-b c/.claude.json'), r'(regex #"^/Users/a-b c/\.claude\.json")',
                         "only the regular expressions' own special characters are escaped")
        deny = profile.index(f'(deny file-read* file-write* (subpath "{os.path.realpath(self.operator)}"))')
        for path in (*linked, notes, session):
            self.assertGreater(profile.index(f'(allow file-read* (subpath "{path}"))'), deny)
        # A Codex coordinator runs in Codex's own sandbox, whose profile names each path present, apart from one
        # inside a place it refuses whole, such as this case's home in the shared temporary folder.
        present = ["/home/fictional/.claude", "/home/fictional/.claude.json", os.path.realpath(state[0])]
        with mock.patch.object(platform, "operator_paths", return_value=present) as named:
            filesystem = next(setting for setting in engines.codex_l3_permissions(self.tmp, project=self.project)
                              if ".filesystem=" in setting)
        self.assertEqual(set(named.call_args.args[0]), {*platform.credential_places(), *engines.claude.operator_places(),
                                                        str(repair.home()), str(config.CHATS)},
                                                       "a Codex coordinator keeps its own folder")
        for path in present[:2]:
            self.assertIn(f'{json.dumps(path)}="deny"', filesystem)
        self.assertNotIn(json.dumps(present[2]), filesystem)

    def test_an_operators_claude_folder_behind_a_link_is_hidden_where_it_lies_with_its_entries_reopened(self):
        target = self.tmp / "dotfiles" / "claude"
        (target / "skills").mkdir(parents=True)
        (target / "settings.json").write_text("{}")
        link = self.tmp / "home-with-link" / ".claude"
        link.parent.mkdir()
        link.symlink_to(target)
        self.patch(engines.claude, "OPERATOR_HOME", link)
        hidden = platform._operator_places()
        resolved = os.path.realpath(target)
        self.assertIn(resolved, hidden)
        read_only = [str(path) for path in engines.claude._linked()]
        self.assertEqual(read_only, [str(link / "settings.json"), str(link / "skills")])
        work = self.tmp / "work"
        args = platform.bubblewrap_profile([str(work)], read_only, temporary=str(self.tmp / "job-tmp"), hidden=hidden)
        pairs = [args[n:n + 3] for n in range(len(args))]
        emptied = next(n for n, pair in enumerate(pairs) if pair[:2] == ["--tmpfs", resolved])
        for name in ("settings.json", "skills"):
            again = pairs.index(["--ro-bind-try", os.path.join(resolved, name), os.path.join(resolved, name)])
            self.assertGreater(again, emptied, f"{name} comes back where the link leads")
        profile = platform.seatbelt_profile([str(work)], read_only, temporary=str(self.tmp / "job-tmp"), hidden=hidden)
        deny = profile.index(f'(deny file-read* file-write* (subpath "{resolved}"))')
        for name in ("settings.json", "skills"):
            self.assertGreater(profile.index(f'(allow file-read* (subpath "{os.path.join(resolved, name)}"))'), deny,
                               "Seatbelt matches the resolved path")

    def test_readers_use_the_jobs_folder_without_following_a_planted_link(self):
        slug = "reader"
        job_root = S.task_dir(self.project, slug) / "l2-engine"
        own = job_root / "claude" / "projects" / "-repo"
        own.mkdir(parents=True)
        (own / "sid-own.jsonl").write_text('{"type": "user"}\n')
        before = self.operator / "projects" / "-repo"
        before.mkdir(parents=True)
        (before / "sid-before.jsonl").write_text('{"type": "user"}\n')
        (before / "sid-own.jsonl").write_text("stale\n")
        self.assertEqual(engines.claude._claude_path("sid-own", job_root), own / "sid-own.jsonl")
        self.assertEqual(engines.claude._claude_path("sid-before", job_root), before / "sid-before.jsonl")
        task = {"session_id": "sid-own", "l2_engine": "claude"}
        with engines.transcript_sources(self.project, slug, task) as sources:
            self.assertEqual([str(source) for source, _ in sources], [str(own / "sid-own.jsonl")])
            self.assertEqual(sources[0][0].read()[0], b'{"type": "user"}\n')
        self.assertEqual(engines.session_context_source(self.project, slug, task), own / "sid-own.jsonl")
        found, _ = engines.claude.token_sources(None, ["sid-own"], lambda *a, **k: None, job_root)
        self.assertEqual([path for path, _, _ in found], [own / "sid-own.jsonl", before / "sid-own.jsonl"])
        # A job that replaces its transcript with a link to a private file gets nothing read through it.
        secret = access.DIR / "claude.token"
        self.save_token()
        (own / "sid-own.jsonl").unlink()
        (own / "sid-own.jsonl").symlink_to(secret)
        with engines.transcript_sources(self.project, slug, task) as sources:
            with self.assertRaises(OSError):
                sources[0][0].read()
            self.assertEqual(sources[0][0].stat(), ())
        usage = engines.observe_token_usage("claude", "sid-own", None, job_root=job_root)
        self.assertNotIn(TOKEN, json.dumps(usage))
        # Nor does Altitude list a folder outside the job's own through a planted folder link.
        outside = self.tmp / "outside"
        (outside / "-x" / "sid-out" / "subagents").mkdir(parents=True)
        (outside / "-x" / "sid-out.jsonl").write_text('{"type": "user"}\n')
        (outside / "-x" / "sid-out" / "subagents" / "agent-a.jsonl").write_text('{"type": "user"}\n')
        (own.parent / "-x").symlink_to(outside / "-x")
        self.assertIsNone(engines.claude._claude_path("sid-out", job_root))
        self.assertEqual(engines.claude.token_sources(None, ["sid-out"], lambda *a, **k: None, job_root)[0], [])
        os.rename(own.parent, self.tmp / "projects-moved")
        (job_root / "claude" / "projects").symlink_to(outside)
        self.assertIsNone(engines.claude._claude_path("sid-out", job_root))
        self.assertEqual(engines.claude.token_sources(None, ["sid-out"], lambda *a, **k: None, job_root)[0], [])

    def test_claude_runs_sign_in_with_altitudes_token_and_the_usage_check_leaves_its_lapse(self):
        self.assertEqual(engines.sign_in("claude"), {"signed_in": False, "command": "alt sign-in claude"})
        home = self.tmp / "home"
        self.assertEqual(engines.claude.job_env(home), {"CLAUDE_CONFIG_DIR": str(home)},
                         "without the token Claude reports itself signed out, never using the operator's sign-in")
        self.save_token()
        self.assertTrue(engines.sign_in("claude")["signed_in"])
        self.assertEqual(engines.claude.job_env(home)["CLAUDE_CODE_OAUTH_TOKEN"], TOKEN)
        for engine in config.ENGINES:
            route.note_rejection({"engine": engine}, {"scope": "engine", "sign_in": True, "why": "signed out"})
        self.addCleanup(lambda: [route.signed_in(engine) for engine in config.ENGINES])
        self.patch(engines.common, "_last_quota_refresh", None)
        for engine in config.ENGINES:
            self.patch(engines.common.implementation(engine), "read_quota", return_value={"known": True, "at": 1})
        engines.refresh_quotas()
        self.assertTrue(route.signed_out("claude"), "the operator's own sign-in says nothing about Altitude's")
        if "codex" in config.ENGINES:
            self.assertFalse(route.signed_out("codex"), "Codex runs share the sign-in its usage check reads")

    def test_each_project_hears_once_when_the_sign_ins_year_is_nearly_over(self):
        self.assertIsNone(route.tell_renewal("claude", "one"))
        self.save_token()
        token = access.DIR / "claude.token"
        age = lambda days: os.utime(token, (time.time() - days * 86400,) * 2)
        for days in (300, 365.5):
            age(days)
            self.assertIsNone(route.tell_renewal("claude", "one"), "too early, or ended and so a sign-in lapse")
        age(354.5)
        self.assertEqual([route.tell_renewal("claude", project) for project in ("one", "one", "two")], [10, None, 10])
        self.save_token()
        age(360.5)
        self.assertEqual(route.tell_renewal("claude", "one"), 4, "a new sign-in is told of again")

    def test_a_coordinators_folder_outlasts_a_restart_out_of_a_workers_reach(self):
        runtime = self.tmp / "run"
        with mock.patch.object(platform, "_darwin", return_value=False), \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
            home, socket = l3._engine_home(self.project, "claude"), l3.verb_socket_path(self.project)
        self.assertIn(runtime, socket.parents, "sockets stay out of backups")
        self.assertNotIn(runtime, home.parents, "the per-user runtime folder is emptied on each restart")
        self.assertEqual(home.parent.parent, config.HOME / ".config/altitude")
        self.assertEqual([path.stat().st_mode & 0o777 for path in (home, home.parent)], [0o700, 0o700])
        self.assert_none_shared([home])

    def test_no_sandbox_on_a_mac_reads_the_launch_input_a_job_folder_holds(self):
        jobs = os.path.realpath(platform._jobs())
        with mock.patch.object(platform, "_darwin", return_value=True):
            profile = platform.seatbelt_profile([str(self.tmp)], temporary=str(self.tmp))
            coordinator = engines.codex.denied()
        self.assertIn('(deny file-read* file-write* ', profile)
        self.assertIn(f'(subpath "{jobs}")', profile.rsplit("(deny file-read* file-write* ", 1)[1])
        self.assertIn(jobs, coordinator)

    def test_a_review_reads_its_sign_in_from_its_input_not_its_command(self):
        self.save_token()
        snapshot, runtime = self.tmp / "snapshot", self.tmp / "runtime"
        snapshot.mkdir()
        runtime.mkdir()
        engines.common._review_served(runtime).touch()
        self.patch(engines.common, "review_capability", return_value={"available": True})
        self.patch(platform, "job_active", return_value=False)
        seen = self.tmp / "seen.json"
        reviewer = ("import json, os, sys; sys.stdin.read(); "
                    f"open({str(seen)!r}, 'w').write(json.dumps({{k: os.environ.get(k) for k in "
                    "('CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_OAUTH_TOKEN')})); "
                    "print(json.dumps({'type': 'result', 'result': json.dumps({'text': 'Fine', 'findings': [], "
                    "'limitations': []})}))")
        self.patch(engines.common, "_review_command", return_value=[sys.executable, "-I", "-c", reviewer])
        commands = []

        def job_command(unit, command, env, **kw):
            commands.append(command)
            return command

        self.patch(platform, "job_command", side_effect=job_command)
        result = engines.review("Review", engine="claude", snapshot=snapshot, runtime=runtime)
        self.assertNotIn(TOKEN, json.dumps(commands))
        self.assertTrue(seen.exists(), result)
        self.assertEqual(json.loads(seen.read_text()),
                         {"CLAUDE_CONFIG_DIR": str(runtime.resolve() / "claude"), "CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
        self.assertIsNone(result["error"], result)

    def test_workers_read_the_projects_notes_where_they_cannot_change_them(self):
        settings = json.loads(dispatch.session_settings(self.project, "notes").read_text())
        notes = engines.notes_folder(self.repo)
        self.assertEqual(settings["autoMemoryDirectory"], str(notes))
        self.assertEqual(notes.parent.parent, self.operator / "projects")
        self.assertEqual(engines.notes_folder(Path("/srv/my_project.git")),
                         self.operator / "projects" / "-srv-my-project-git" / "memory")
