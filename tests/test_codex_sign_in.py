"""A Codex worker runs in Altitude's sandbox with a Codex folder of its own: altd hands it the operator's current access
token, never the refresh token, and renews that token itself; the operator's Codex folder stays hidden from the job
apart from what the worker's folder links to (docs/ARCHITECTURE.md#isolation)."""
import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest import mock

from tests.support import REPO, AltitudeCase, add_worktree, codex_token, door_port, make_repo
from altitude import config, door, engines, platform, server, state as S, tasks as T

FAKE = REPO / "tests" / "fake_engine.py"
codex = engines.codex


def rollout(home: Path, sid: str, parent: str | None = None) -> Path:
    path = home / "sessions/2026/10/18" / f"rollout-2026-10-18T00-00-00-{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"id": sid, **({"parent_thread_id": parent} if parent else {})}
    path.write_text(json.dumps({"type": "session_meta", "payload": meta}) + "\n")
    return path


class OwnFolder(AltitudeCase):
    def setUp(self):
        super().setUp()
        codex._TOKEN_ROLLOUT_INDEX.clear()
        self.addCleanup(codex._TOKEN_ROLLOUT_INDEX.clear)
        self.operator = self.codex_home()
        (self.operator / "rules").mkdir(parents=True)
        for name in ("config.toml", "AGENTS.md"):
            (self.operator / name).write_text(f"fictional {name}\n")
        for name in ("review", ".system"):
            (self.operator / "skills" / name).mkdir(parents=True)
        program = self.operator / "packages/standalone/releases/9.9.9/bin/codex"
        program.parent.mkdir(parents=True)
        program.write_text("#!/bin/sh\n")
        program.chmod(0o755)
        self.patch(config, "CODEX_BIN", str(program))
        self.worktree = add_worktree(make_repo(self.repo), "folder")
        self.job_root = self.runtime_tmp / "l2-engine"

    def launch(self, resume=None):
        return codex.prepare_worker("p/folder", "brief", cwd=self.worktree, scratch=self.tmp / "scratch",
                                    job_root=self.job_root, persona=None, model=None, settings=None, effort=None,
                                    resume=resume, images=())

    def test_a_job_links_the_operators_settings_and_reads_only_what_it_links_to(self):
        _, _, writable, read_only, env, launch = self.launch()
        home = self.job_root / "codex"
        self.assertEqual((writable[-1], env), (home, {"CODEX_HOME": str(home)}))
        linked = [self.operator / name for name in ("config.toml", "AGENTS.md", "rules", "skills")]
        self.assertEqual(sorted(read_only[-5:]), sorted([self.operator / "packages", *linked]))
        self.assertNotIn(self.operator / "auth.json", read_only)
        codex._own_folder(home, Path(launch["operator"]), launch["copies"])
        codex._own_folder(home, Path(launch["operator"]), launch["copies"])  # a later turn finds it ready
        self.assertEqual(home.stat().st_mode & 0o777, 0o700)
        self.assertEqual(sorted(os.listdir(home)), ["AGENTS.md", "config.toml", "rules", "skills"])
        self.assertEqual(os.readlink(home / "config.toml"), str(self.operator / "config.toml"))
        self.assertEqual(os.listdir(home / "skills"), ["review"], "Codex writes its own system skills")
        self.assertFalse((home / "auth.json").exists())

    def test_a_conversation_resumed_for_the_first_time_is_copied_with_its_helpers_and_counted_once(self):
        owner, helper = rollout(self.operator, "thr-1"), rollout(self.operator, "thr-2", parent="thr-1")
        rollout(self.operator, "thr-9")
        _, _, _, read_only, _, launch = self.launch(resume="thr-1")
        self.assertEqual(launch["copies"], [str(owner), str(helper)])
        self.assertEqual(read_only[-2:], (owner, helper))
        home = self.job_root / "codex"
        codex._own_folder(home, self.operator, launch["copies"])
        self.assertEqual((home / owner.relative_to(self.operator)).read_text(), owner.read_text())
        self.assertEqual(self.launch(resume="thr-1")[5]["copies"], [], "the job's folder holds it now")
        seen = []
        sources, _ = codex.token_sources(None, {"thr-1"}, lambda sid, parent, **kw: seen.append(sid),
                                         self.job_root)
        self.assertEqual(sorted(seen), ["thr-1", "thr-2"])
        self.assertTrue(all(path.is_relative_to(home) for path, _, _ in sources))

    def test_altd_reads_a_jobs_codex_records_without_following_a_link_it_planted(self):
        home = self.job_root / "codex"
        context = {"type": "turn_context", "timestamp": "2026-10-18T00:00:01+00:00",
                   "payload": {"model": "fixture-model", "effort": "high"}}
        elsewhere = rollout(self.tmp / "elsewhere", "thr-5")
        elsewhere.write_text(elsewhere.read_text() + json.dumps(context) + "\n")
        planted = home / elsewhere.relative_to(self.tmp / "elsewhere")
        planted.parent.mkdir(parents=True)
        planted.symlink_to(elsewhere)
        (home / "sessions/2026/11").symlink_to(elsewhere.parent.parent)
        started = "2026-10-18T00:00:00+00:00"
        self.assertEqual(codex._codex_session_model("thr-5", started, home), {})
        self.assertEqual(codex._token_rollouts(home)[0], {})
        planted.unlink()
        planted.write_text(elsewhere.read_text())
        self.assertEqual(codex._codex_session_model("thr-5", started, home),
                         {"engine_model": "fixture-model", "engine_reasoning_effort": "high"})

    def test_the_operators_codex_folder_is_hidden_from_every_confined_job(self):
        hidden = os.path.realpath(self.operator)
        self.assertIn(hidden, platform._operator_places())
        _, _, writable, read_only, _, _ = self.launch()
        temporary = str(self.tmp / "job-tmp")
        args = platform.bubblewrap_profile([str(p) for p in writable], [str(p) for p in read_only],
                                           temporary=temporary, hidden=[hidden])
        pairs = [args[n:n + 3] for n in range(len(args))]
        empty = args.index(hidden, args.index("--tmpfs", args.index(hidden) - 1))
        for path in read_only[-5:]:
            again = pairs.index(["--ro-bind-try", str(path), str(path)])
            self.assertGreater(again, empty, f"{path} comes back after the folder is emptied")
        self.assertFalse([pair for pair in pairs if pair[0] in ("--bind", "--ro-bind-try")
                          and pair[1] == str(self.operator / "auth.json")])
        profile = platform.seatbelt_profile([str(p) for p in writable], [str(p) for p in read_only],
                                            temporary=temporary, hidden=[hidden])
        deny = profile.index(f'(deny file-read* file-write* (subpath "{hidden}"))')
        for path in read_only[-5:]:
            self.assertGreater(profile.index(f'(allow file-read* (subpath "{path}"))'), deny)
        self.assertLess(deny, profile.index(platform.private_rule()), "the private places' refusal stays last")
        with mock.patch.object(platform, "_darwin", return_value=True):  # Seatbelt may match either spelling
            spelled = platform._operator_places()
        self.assertIn(platform.DATA_VOLUME + hidden, spelled)
        profile = platform.seatbelt_profile([str(p) for p in writable], [str(p) for p in read_only],
                                            temporary=temporary, hidden=spelled)
        deny = profile.index(f'(deny file-read* file-write* (subpath "{platform.DATA_VOLUME + hidden}"))')
        for path in read_only[-5:]:
            self.assertGreater(profile.index(f'(allow file-read* (subpath "{platform.DATA_VOLUME}{path}"))'), deny)
        captured = {}
        with mock.patch.object(platform, "_darwin", return_value=False), \
             mock.patch.object(platform, "bubblewrap_profile", side_effect=lambda *a, **kw: captured.update(kw) or []):
            platform.job_command("u.service", ["true"], {}, writable=(self.tmp,))
            self.assertEqual(captured["hidden"], platform._operator_places())
            captured.clear()
            platform.job_command("u.service", ["true"], {})
            self.assertEqual(captured, {}, "a job without Altitude's sandbox is the engine's to confine")


class SignIn(AltitudeCase):
    def stored(self, **auth):
        (codex.operator_home() / "auth.json").write_text(json.dumps(auth))

    def test_each_sign_in_that_cannot_be_handed_over_says_why(self):
        home = self.codex_home()
        self.assertIn("signed out", codex.worker_sign_in()["error"])
        (home / "config.toml").write_text('cli_auth_credentials_store = "keyring"\n')
        self.assertEqual(codex.worker_sign_in(), {"error": codex.KEYCHAIN})
        self.stored(OPENAI_API_KEY="fixture-key")
        self.assertEqual(codex.worker_sign_in(), {"error": codex.API_KEY})
        self.stored(tokens={"access_token": "opaque"})
        self.assertIn("no ChatGPT account", codex.worker_sign_in()["error"])
        token = codex_token(issued=time.time(), expires=time.time() + 86400, account="claimed-account")
        self.stored(tokens={"access_token": token, "refresh_token": "fixture-refresh"})
        self.assertEqual(codex.worker_sign_in(), {"access_token": token, "account_id": "claimed-account"})
        with self.assertRaisesRegex(ValueError, "API key"):
            self.stored(OPENAI_API_KEY="fixture-key")
            engines.worker_sign_in("codex")

    def test_a_token_past_half_its_lifetime_is_renewed_once_for_workers_asking_together(self):
        now = time.time()
        stale = codex_token(issued=now - 3000, expires=now + 1000)
        fresh = codex_token(issued=now, expires=now + 4000)
        self.codex_signed_in(stale)
        calls = []

        def renew(timeout, requests):
            calls.append(requests)
            time.sleep(0.2)
            self.codex_signed_in(fresh)

        self.patch(codex, "_talk", side_effect=renew)
        answers = []
        workers = [threading.Thread(target=lambda: answers.append(codex.worker_sign_in())) for _ in range(5)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertEqual(calls, [codex._REFRESH])
        self.assertEqual({answer["access_token"] for answer in answers}, {fresh})
        self.assertEqual(codex.worker_sign_in()["access_token"], fresh)
        self.assertEqual(len(calls), 1, "a token with more than half its lifetime left is handed over as it is")

    def test_a_failed_renewal_still_hands_over_the_current_token(self):
        now = time.time()
        stale = self.codex_signed_in(codex_token(issued=now - 3000, expires=now + 1000))
        self.patch(codex, "_talk", side_effect=RuntimeError("fixture: no network"))
        self.assertEqual(codex.worker_sign_in()["access_token"], stale)

    def test_a_signed_out_worker_is_a_sign_in_rejection_and_a_refused_hand_over_is_not(self):
        self.assertTrue(codex.rejection_flags("", [f"{codex.SIGNED_OUT}Codex is signed out."], None, False, False)[0])
        self.assertTrue(codex.rejection_flags("", [f"{codex.HAND_OVER_REFUSED}no"], None, False, False)[0] is False)


class Driver(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.log = self.tmp / "engine.log"
        self.home = self.tmp / "codex"

    def spec(self, sign_in):
        return {"engine": "codex", "command": [sys.executable, str(FAKE), "app-server"],
                "input": engines.common._engine_input("codex", "Start.", ()), "cwd": str(self.repo),
                "operator": str(self.tmp / "operator"), "copies": [], "sign_in": sign_in,
                "env": {"CODEX_HOME": str(self.home)}}

    def run_driver(self, sign_in, **env):
        spec = self.spec(sign_in)
        driver = codex._CodexDriver(spec, {**os.environ, **spec["env"], "FAKE_ENGINE_LOG": str(self.log), **env})
        self.addCleanup(lambda: driver.child.poll() is None and driver.child.kill())
        self.addCleanup(driver.child.stdout.close)
        events = []
        driver.emit = events.append
        timeout = threading.Timer(20, lambda: driver.child.poll() is None and driver.child.kill())
        timeout.start()
        try:
            driver.run()
        finally:
            timeout.cancel()
            timeout.join()
        received = [row for row in map(json.loads, self.log.read_text().splitlines()) if "environment" not in row]
        return events, received

    def test_the_driver_signs_codex_in_over_its_protocol_before_the_thread_opens(self):
        events, received = self.run_driver({"access_token": "fixture-access", "account_id": "fixture-account"})
        methods = [row.get("method") for row in received if row.get("method")]
        self.assertEqual(methods[:4], ["initialize", "initialized", "account/login/start", "thread/start"])
        self.assertEqual(received[0]["params"]["capabilities"], {"experimentalApi": True})
        login = next(row for row in received if row.get("method") == "account/login/start")
        self.assertEqual(login["params"], {"type": "chatgptAuthTokens", "accessToken": "fixture-access",
                                           "chatgptAccountId": "fixture-account", "chatgptPlanType": None})
        self.assertIn({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}, events)
        self.assertEqual(self.home.stat().st_mode & 0o777, 0o700)
        environment = json.loads(self.log.read_text().splitlines()[0])
        self.assertEqual(environment["codex"], str(self.home))
        self.assertNotIn("fixture-access", json.dumps(events))

    def test_a_missing_or_refused_sign_in_ends_the_job_before_any_thread(self):
        for sign_in, env, expected in (({"error": "Codex is signed out."}, {}, f"{codex.SIGNED_OUT}Codex is signed out."),
                                       ({"access_token": "fixture-access", "account_id": "a"},
                                        {"FAKE_ENGINE_REFUSE_SIGN_IN": "1"}, codex.HAND_OVER_REFUSED)):
            with self.subTest(expected=expected):
                self.log.unlink(missing_ok=True)
                events, received = self.run_driver(sign_in, **env)
                self.assertEqual(len(events), 1)
                self.assertTrue(events[0]["message"].startswith(expected), events)
                self.assertFalse([row for row in received if row.get("method") in ("thread/start", "turn/start")])

    def test_codex_asking_for_a_fresh_token_gets_altds_current_one(self):
        with mock.patch.object(door, "call", return_value={"access_token": "renewed", "account_id": "fixture-account"}) as ask:
            events, received = self.run_driver({"access_token": "expired", "account_id": "fixture-account"},
                                               FAKE_ENGINE_EXPIRE="1")
        ask.assert_called_once_with("sign-in", {}, wait=60)
        answer = next(row for row in received if row.get("id") == "refresh-1")
        self.assertEqual(answer["result"], {"accessToken": "renewed", "chatgptAccountId": "fixture-account",
                                            "chatgptPlanType": None})
        self.assertIn({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}, events)
        with mock.patch.object(door, "call", side_effect=door.DoorError("Codex is signed out.")):
            _, received = self.run_driver({"access_token": "expired", "account_id": "fixture-account"},
                                          FAKE_ENGINE_EXPIRE="1")
        answer = [row for row in received if row.get("id") == "refresh-1"][-1]
        self.assertEqual(answer["error"]["message"], f"{codex.SIGNED_OUT}Codex is signed out.")


class DoorSignIn(AltitudeCase):
    door = False

    def setUp(self):
        super().setUp()
        self.serving(door_port())
        self.owner = self.patch(server, "task_owner_connection", return_value=True)
        task = T.new(self.project, "Codex owner", "Work through the door.")
        self.slug = task["slug"]
        task.update(state="running", attempt=2, agent_id="worker", l2_engine="codex")
        S.save_task(self.project, task)

    def post(self, **fields):
        import http.client
        import uuid
        body = {"project": self.project, "slug": self.slug, "attempt": "2", "kind": "sign-in",
                "request": uuid.uuid4().hex, **fields}
        connection = http.client.HTTPConnection("127.0.0.1", door_port(), timeout=60)
        try:
            connection.request("POST", "/api/task/door", body=json.dumps(body), headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_the_worker_job_alone_gets_the_current_token_and_no_answer_is_kept(self):
        token = self.codex_signed_in()
        self.assertEqual(self.post(), (200, {"access_token": token, "account_id": "fixture-account"}))
        ledger = S.task_dir(self.project, self.slug) / door.LEDGER
        kept = [path.read_text() for path in ledger.iterdir()] if ledger.is_dir() else []
        self.assertFalse([text for text in kept if token in text])
        self.assertEqual(self.post(extra=1)[0], 409)
        self.owner.return_value = False
        self.assertEqual(self.post(), (403, {"error": server.TASK_DOOR_REFUSED}))
        self.owner.return_value = True
        (codex.operator_home() / "auth.json").unlink()
        status, answer = self.post()
        self.assertEqual(status, 409)
        self.assertIn("signed out", answer["error"])
        task = S.load_task(self.project, self.slug)
        S.save_task(self.project, {**task, "l2_engine": "claude"})
        self.assertIn("launch input", self.post()[1]["error"])
