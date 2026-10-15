"""Altitude's own Claude sign-in, held by altd: the operator opens the link, pastes the code Claude's page shows, and
altd reads the token off a setup screen of its own and saves it where only Altitude reads it. The setup command is
`tests/fake_token_setup.py`, which draws as a full-screen program does; its token is fictional and no provider is
contacted."""
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.support import ALT, AltitudeCase, door_port
from tests import fake_token_setup as fake
from altitude import access, config, platform, route, server
from altitude.engines import token_setup

TOKEN, CODE = fake.TOKEN, fake.CODE


def gone(pid: int, seconds: float = 5) -> bool:
    """Whether the process has ended (a zombie waiting for its parent counts as ended)."""
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            with open(f"/proc/{pid}/stat") if Path("/proc").exists() else contextlib.nullcontext() as stat:
                if stat is not None and stat.read().rsplit(")", 1)[1].split()[0] == "Z":
                    return True
            os.kill(pid, 0)
        except (ProcessLookupError, FileNotFoundError):
            return True
        time.sleep(0.05)
    return False


class TestScreen(unittest.TestCase):
    """What the sign-in reads is where a terminal would show it."""

    def read(self, *parts: str) -> list[str]:
        wiped = []
        screen = token_setup._Screen(wiped.append)
        for part in parts:
            screen.feed(part)
        return [*wiped, screen.text()]

    def test_text_lands_where_a_terminal_draws_it(self):
        self.assertEqual(self.read("ab\x1b7\r\ncd\x1b8ef")[-1], "abef\ncd", "saving the cursor does not move it")
        self.assertEqual(self.read("ab\x1b[s\x1b[3;1Hcd\x1b[uef")[-1], "abef\n\ncd")
        self.assertEqual(self.read("x\x1b[>1uy\x1b[<uz\x1b[=5u")[-1], "xyz", "keyboard modes draw nothing")
        self.assertEqual(self.read("one\x1b[", "2;3Htwo")[-1], "one\n  two", "a sequence split across reads")
        self.assertEqual(self.read("old\x1b[?1049hnew\x1b[?1049l"), ["old", "new", ""], "each screen is read before it is wiped")
        self.assertEqual(self.read("ab\x1b]8;;https://example.invalid\x07cd\x1b]8;;\x07\x1b[1;31me\x1b[0m")[-1], "abcde")
        self.assertEqual(self.read("abcdef\x1b[1;3H\x1b[K")[-1], "ab    ")
        self.assertEqual(self.read("\x1b]" + "a" * 5000 + "z")[-1][-1:], "z", "an endless sequence is dropped")
        self.assertEqual(self.read("\x1b[" + "9" * 5000 + "Hx")[-1], "x", "an absurd position is read as the first")


class TokenSetupCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(access, "DIR", self.tmp / "access")
        binary = self.tmp / "claude"
        binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(fake.__file__).resolve()}" "$@"\n')
        binary.chmod(0o755)
        self.patch(config, "CLAUDE_BIN", str(binary))
        self.log = self.tmp / "setup.json"
        self.setenv("FAKE_SETUP_LOG", str(self.log))
        self.setenv("FAKE_SETUP", "")
        self.addCleanup(token_setup.cancel, "claude")
        self.addCleanup(route.signed_in, "claude")

    def mode(self, mode: str) -> None:
        self.setenv("FAKE_SETUP", mode)

    def setup_process(self) -> subprocess.Popen:
        return token_setup._open["claude"].process

    def assert_closed(self, process: subprocess.Popen) -> None:
        self.assertTrue(gone(process.pid), "the setup command ends")
        until = time.monotonic() + 5
        while list(access.DIR.glob("claude.setup*")) and time.monotonic() < until:
            time.sleep(0.05)
        self.assertEqual(list(access.DIR.glob("claude.setup*")), [], "its settings folder is removed")


class TestTokenSetup(TokenSetupCase):
    def test_a_sign_in_saves_the_token_where_only_altitude_reads_it_and_ends_the_lapse(self):
        self.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fixture-operator-token")
        self.setenv("ANTHROPIC_API_KEY", "fixture-api-key")
        route.note_rejection({"engine": "claude"}, {"scope": "engine", "sign_in": True, "why": "signed out"})
        self.assertTrue(route.signed_out("claude"))
        self.assertEqual(token_setup.view("claude")["state"], "idle")
        opened = token_setup.start("claude")
        self.assertEqual(opened["state"], "waiting")
        self.assertEqual(opened["link"], fake.link(1))
        process = self.setup_process()
        ran = json.loads(self.log.read_text())
        home = Path(ran["env"]["CLAUDE_CONFIG_DIR"])
        self.assertEqual(home.parent, access.DIR)
        self.assertEqual(home.stat().st_mode & 0o777, 0o700)
        self.assertEqual(ran["argv"], ["setup-token"])
        self.assertEqual([ran["tty"], ran["leader"], ran["columns"]], [True, True, token_setup.COLUMNS])
        self.assertEqual(ran["env"]["BROWSER"], "true", "no browser opens on the computer")
        self.assertIsNone(ran["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "no sign-in it could use instead")
        self.assertIsNone(ran["env"]["ANTHROPIC_API_KEY"])
        self.assertEqual(token_setup.view("claude")["state"], "waiting", "a reloaded page finds it open")
        done = token_setup.finish("claude", opened["attempt"], f"  {CODE}\n")
        self.assertEqual(done["state"], "done")
        self.assertIsNone(done["error"])
        self.assertEqual(access.engine_token("claude"), TOKEN)
        self.assertEqual((access.DIR / "claude.token").stat().st_mode & 0o777, 0o600)
        self.assertFalse(route.signed_out("claude"))
        self.assertEqual(token_setup.view("claude")["state"], "idle")
        self.assertAlmostEqual(token_setup.view("claude")["saved"], time.time(), delta=30)
        self.assert_closed(process)

    def test_a_wrong_code_changes_nothing_and_the_next_sign_in_replaces_the_earlier_one(self):
        access.save_engine_token("claude", "sk-ant-oat01-" + "earlier" * 8)
        opened = token_setup.start("claude")
        failed = token_setup.finish("claude", opened["attempt"], "fixture-wrong#code")
        self.assertEqual([failed["state"], failed["error"]], ["failed", "Claude didn't accept that code."])
        self.assert_closed(self.setup_process())
        self.assertEqual(access.engine_token("claude"), "sk-ant-oat01-" + "earlier" * 8, "the earlier sign-in still works")
        self.assertEqual(token_setup.view("claude")["error"], "Claude didn't accept that code.", "a reload shows it")
        with self.assertRaises(token_setup.SignInError):
            token_setup.finish("claude", opened["attempt"], CODE)
        again = token_setup.start("claude")
        self.assertEqual(again["link"], fake.link(1))
        self.assertNotEqual(again["attempt"], opened["attempt"])
        self.assertEqual(token_setup.finish("claude", again["attempt"], CODE)["state"], "done")
        self.assertEqual(access.engine_token("claude"), TOKEN)

    def test_a_code_that_cannot_be_typed_is_refused_without_reaching_the_screen(self):
        for code in ("", "two words", "line\nbreak", "x" * 2049, None):
            with self.subTest(code=code):
                opened = token_setup.start("claude")
                failed = token_setup.finish("claude", opened["attempt"], code)
                self.assertEqual(failed["error"], "Claude didn't accept that code.")
                self.assert_closed(self.setup_process())
        self.assertIsNone(access.engine_token("claude"))

    def test_every_other_end_fails_with_its_sentence_and_saves_nothing(self):
        self.patch(token_setup, "LINK_WAIT", 1)
        self.patch(token_setup, "FINISH_WAIT", 1)
        couldnt = "Altitude couldn't follow Claude's sign-in. If this repeats, Altitude needs an update for this Claude version."
        for mode, finishes, error in (("fail", True, "Claude didn't accept that code."),
                                      ("two", True, couldnt),        # two different tokens on screen
                                      ("wrapped", True, couldnt),    # no token alone on its line
                                      ("hang", True, couldnt),       # no answer to the code
                                      ("silent", False, couldnt),    # no link
                                      ("exit", False, couldnt)):     # ends before showing a link
            with self.subTest(mode=mode):
                self.mode(mode)
                opened = token_setup.start("claude")
                process = self.setup_process()
                if finishes:
                    self.assertEqual(opened["state"], "waiting")
                    opened = token_setup.finish("claude", opened["attempt"], CODE)
                self.assertEqual([opened["state"], opened["error"], opened["link"]], ["failed", error, None])
                self.assert_closed(process)
                self.assertIsNone(access.engine_token("claude"))

    def test_an_unanswered_sign_in_expires_and_a_failure_is_shown_for_a_while(self):
        self.patch(token_setup, "DEADLINE", 1)
        opened = token_setup.start("claude")
        process = self.setup_process()
        time.sleep(1.5)
        self.assertEqual(token_setup.view("claude")["error"], "The sign-in expired after ten minutes.")
        self.assert_closed(process)
        with self.assertRaises(token_setup.SignInError):
            token_setup.finish("claude", opened["attempt"], CODE)
        self.patch(token_setup, "SHOWN", 0)
        self.assertEqual(token_setup.view("claude")["state"], "idle")

    def test_cancelling_or_starting_again_ends_the_command_and_everything_it_started(self):
        self.mode("child")
        first = token_setup.start("claude")
        process, child = self.setup_process(), int(Path(f"{self.log}.child").read_text())
        second = token_setup.start("claude")
        self.assertEqual(second["state"], "waiting", "starting again replaces the open sign-in")
        self.assertTrue(gone(process.pid) and gone(child))
        with self.assertRaises(token_setup.SignInError):
            token_setup.finish("claude", first["attempt"], CODE)
        process, child = self.setup_process(), int(Path(f"{self.log}.child").read_text())
        self.assertEqual(token_setup.cancel("claude", first["attempt"])["state"], "waiting", "an older page cancels nothing")
        self.assertEqual(token_setup.cancel("claude", second["attempt"])["state"], "idle")
        self.assertTrue(gone(process.pid) and gone(child))
        self.assert_closed(process)

    def test_the_command_is_hung_up_when_altd_ends(self):
        self.mode("child")
        script = ("import os, sys\nfrom pathlib import Path\nfrom altitude import access, config\n"
                  "from altitude.engines import token_setup\n"
                  f"access.DIR = Path({str(self.tmp / 'ended')!r})\nconfig.CLAUDE_BIN = {config.CLAUDE_BIN!r}\n"
                  "token_setup.start('claude')\nprint(token_setup._open['claude'].process.pid, flush=True)\n"
                  "os._exit(9)\n")
        ended = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
                               cwd=config.SOURCE, env={**os.environ, "ALTITUDE_HOME": str(config.ROOT)})
        self.assertEqual(ended.returncode, 9, ended.stderr)
        self.assertTrue(gone(int(ended.stdout.split()[0])), "the setup command")
        self.assertTrue(gone(int(Path(f"{self.log}.child").read_text())), "what it started")

    def test_only_an_engine_whose_runs_take_a_token_signs_in_here(self):
        for engine in ("codex", "unknown"):
            with self.subTest(engine=engine), self.assertRaises(token_setup.SignInError):
                token_setup.start(engine)


class TestSignInRoutes(TokenSetupCase):
    def call(self, method: str, engine: str, body=None) -> tuple[int, dict]:
        request = urllib.request.Request(f"http://127.0.0.1:{door_port()}/api/sign-in/{engine}", method=method,
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_the_page_signs_in_through_the_service_and_hears_only_fixed_sentences(self):
        logged = []
        self.patch(server, "log", new=logged.append)
        replies = [self.call("GET", "claude")]
        self.assertEqual(replies[-1][1]["state"], "idle")
        replies.append(self.call("POST", "claude", {"action": "start"}))
        status, opened = replies[-1]
        self.assertEqual([status, opened["state"], opened["link"]], [200, "waiting", fake.link(1)])
        for body in ({"action": "finish", "attempt": opened["attempt"]}, {"action": "finish", "attempt": 1, "code": CODE},
                     {"action": "start", "code": CODE}, {"action": "open"}, ["start"]):
            with self.subTest(body=body):
                replies.append(self.call("POST", "claude", body))
                self.assertEqual(replies[-1], (400, {"error": "Unknown sign-in request."}))
        replies.append(self.call("POST", "claude", {"action": "finish", "attempt": "elsewhere", "code": CODE}))
        self.assertEqual(replies[-1], (409, {"error": "This sign-in is no longer open."}))
        replies.append(self.call("POST", "codex", {"action": "start"}))
        self.assertEqual(replies[-1], (409, {"error": "This engine does not sign in here."}))
        replies.append(self.call("POST", "claude", {"action": "finish", "attempt": opened["attempt"], "code": CODE}))
        self.assertEqual(replies[-1][:1] + (replies[-1][1]["state"],), (200, "done"))
        self.patch(token_setup, "start", side_effect=RuntimeError(f"screen showed {TOKEN}"))
        replies.append(self.call("POST", "claude", {"action": "start"}))
        self.assertEqual(replies[-1], (500, {"error": "The sign-in failed. Try again."}))
        self.assertEqual(logged, ["sign-in request failed: RuntimeError"])
        self.assertNotIn(TOKEN[13:], json.dumps(replies))
        self.assertEqual(access.engine_token("claude"), TOKEN)

    def test_the_cli_signs_in_through_the_service_without_showing_the_token(self):
        def alt(code: str, actor: str = config.OPERATOR_ACTOR) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, str(ALT), "sign-in", "claude"], input=code, capture_output=True,
                                  text=True, timeout=120, env={**os.environ, "ALTITUDE_HOME": str(config.ROOT),
                                                               "ALTITUDE_ACTOR": actor})
        refused = alt(CODE + "\n", actor="l2")
        self.assertEqual(refused.returncode, 1)
        self.assertTrue(refused.stderr.startswith("alt sign-in: "), "a worker cannot sign Altitude in")
        self.assertNotIn("claude", token_setup._open)
        wrong = alt("fixture-wrong#code\n")
        self.assertEqual([wrong.returncode, wrong.stderr.strip()], [1, "alt sign-in: Claude didn't accept that code."])
        left = alt("")
        self.assertIn("alt sign-in: cancelled; nothing changed", left.stderr)
        self.assertEqual(token_setup.view("claude")["state"], "idle")
        signed = alt(CODE + "\n")
        self.assertEqual(signed.returncode, 0, signed.stderr)
        self.assertIn(fake.link(1), signed.stdout)
        self.assertIn("Altitude's Claude runs now use this sign-in. Your own is unchanged.", signed.stdout)
        self.assertEqual(access.engine_token("claude"), TOKEN)
        for ran in (refused, wrong, left, signed):
            self.assertNotIn(TOKEN[13:], ran.stdout + ran.stderr)


class TestSignInAdmission(TokenSetupCase):
    gated = True

    def test_a_browser_that_is_not_paired_cannot_sign_in(self):
        request = urllib.request.Request(f"http://127.0.0.1:{door_port()}/api/sign-in/claude", method="POST",
                                         data=b'{"action": "start"}', headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=30)
        self.assertEqual(refused.exception.code, 401)
        # A task worker reaches altd only through the task door, which does not lead here.
        self.assertNotIn(["api", "sign-in", "claude"], server.TASK_DOOR)
        worker = urllib.request.Request(request.full_url, method="POST", headers={"Content-Type": "application/json"},
                                        data=json.dumps({"action": "start", "project": self.project, "slug": "task",
                                                         "attempt": 1}).encode())
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(worker, timeout=30)
        self.assertEqual(refused.exception.code, 401)
        self.assertNotIn("claude", token_setup._open)
        self.assertFalse(self.log.exists(), "no setup command ran")


class TestTheTokenStaysPrivate(TokenSetupCase):
    """The negative check: after sign-ins that succeed and fail, the token is in Altitude's private file and
    nowhere a terminal, a record, a log or a worker reads."""

    def test_the_token_appears_nowhere_but_its_private_file(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            replies = []
            for mode in ("two", "fail", ""):
                self.mode(mode)
                opened = token_setup.start("claude")
                replies += [opened, token_setup.finish("claude", opened["attempt"], CODE), token_setup.view("claude")]
            self.assert_closed(self.setup_process())
        secret = TOKEN[13:]
        self.assertEqual(replies[-2]["state"], "done")
        self.assertNotIn(secret, json.dumps(replies) + output.getvalue())
        self.assertEqual(access.engine_token("claude"), TOKEN)
        private = access.DIR / "claude.token"
        roots = [config.ROOT, self.tmp, Path(platform._jobs())]
        found = []
        for root in roots:
            for path in root.rglob("*") if root.exists() else ():
                if path == private or not path.is_file() or path.is_symlink():
                    continue
                try:
                    if secret.encode() in path.read_bytes():
                        found.append(str(path))
                except OSError:
                    continue
        self.assertEqual(found, [])
        self.assertFalse(any(token_setup._open[e].tokens for e in token_setup._open), "nothing held in memory")
