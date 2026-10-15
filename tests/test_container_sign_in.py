"""A container signs in with this computer's Altitude sign-in, handed over on input; the operator's own logins stay out."""
import io
import json
import os
import sys
from unittest import mock

from tests.support import AltitudeCase
from altitude import access, config, engines, platform, route
from scripts import container

TOKEN = "sk-fictional-altitude-sign-in-0123456789"
OWN_LOGIN = "fictional-operator-login-sentinel"


class TestContainerSignIn(AltitudeCase):
    host = "linux"  # the container launcher is Linux's

    def setUp(self):
        super().setUp()
        self.commands = []
        self.patch(container, "owned", return_value={"Id": "c" * 64})
        self.patch(platform, "container_command",
                   side_effect=lambda arguments, **options: self.commands.append((arguments, options)) or "")
        self.addCleanup(lambda: (access.DIR / "claude.token").unlink(missing_ok=True))
        # The operator's own engine and GitHub logins, which nothing here may read, mount or send.
        for path in (config.HOME / ".claude/.credentials.json", config.HOME / ".claude.json",
                     config.HOME / ".codex/auth.json", config.HOME / ".config/gh/hosts.yml"):
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(OWN_LOGIN)
                self.addCleanup(path.unlink)

    def assert_nothing_private(self):
        sent = json.dumps([[arguments, {key: value for key, value in options.items() if key != "input"}]
                           for arguments, options in self.commands])
        for private in (TOKEN, OWN_LOGIN, str(config.HOME)):
            self.assertNotIn(private, sent)
        self.assertNotIn(OWN_LOGIN, json.dumps([options.get("input") for _, options in self.commands]))

    def test_the_sign_in_travels_on_input_with_its_saved_time_and_lands_where_altitudes_runs_read_it(self):
        access.save_engine_token("claude", TOKEN)
        os.utime(access.DIR / "claude.token", (1_700_000_000.5,) * 2)
        shown = container.sign_in("fictional", "claude")
        self.assertNotIn(TOKEN, shown)
        [(arguments, options)] = self.commands
        self.assertEqual(arguments[:2], ["exec", "-i"])
        self.assertEqual(arguments[arguments.index("--user") + 1], "1000:1000")
        self.assertEqual(options["input"], TOKEN)
        self.assert_nothing_private()

        # Inside the container the same code runs as the application user, whose home is the persistent volume.
        (access.DIR / "claude.token").unlink()
        route.note_rejection({"engine": "claude"}, {"scope": "engine", "sign_in": True, "why": "signed out"})
        self.addCleanup(route.signed_in, "claude")
        program = arguments[arguments.index("-c") + 1]
        with mock.patch.object(sys, "argv", ["-c", *arguments[arguments.index("-c") + 2:]]), \
                mock.patch.object(sys, "stdin", io.StringIO(options["input"])), \
                mock.patch.object(sys, "path", list(sys.path)):
            exec(program, {})
        self.assertEqual(access.engine_token("claude"), TOKEN)
        self.assertEqual(access.engine_token_saved("claude"), 1_700_000_000.5, "its year runs from the first save")
        self.assertEqual((access.DIR / "claude.token").stat().st_mode & 0o777, 0o600)
        self.assertTrue(engines.sign_in("claude")["signed_in"])
        self.assertFalse(route.signed_out("claude"))

    def test_without_altitudes_sign_in_nothing_reaches_the_container(self):
        with self.assertRaisesRegex(ValueError, "alt sign-in claude"):
            container.sign_in("fictional", "claude")
        self.assertEqual(self.commands, [])

    def test_engines_without_a_sign_in_of_altitudes_own_are_refused(self):
        others = [engine for engine in config.ENGINES if engine not in engines.TOKEN_SETUP]
        for engine in others:
            with self.subTest(engine=engine), mock.patch.object(sys, "argv", ["container.py", "sign-in", engine]), \
                    self.assertRaises(SystemExit) as refused:
                container.main()
            self.assertNotEqual(refused.exception.code, 0)
        self.assertEqual(self.commands, [])

    def test_a_started_container_mounts_its_two_volumes_and_nothing_of_the_host(self):
        self.patch(platform, "container_runtime")
        self.patch(container, "volume_pair", return_value={"lineage": "a" * 32, "pair": "b" * 32, "archive": ""})
        self.patch(platform, "container_command", return_value=json.dumps(
            [{"Id": "i" * 64, "Labels": {container.LABEL: "1"}}]))
        launched = self.patch(platform, "container_launch", return_value="c" * 64)
        container.start("localhost/altitude:fixture", "fictional", "fictional-home", "fictional-projects",
                        "127.0.0.1", "localhost", 19443, new_volumes=True)
        arguments = launched.call_args.args[1]
        mounts = [arguments[index + 1] for index, item in enumerate(arguments) if item in ("--volume", "-v")]
        self.assertEqual(mounts, ["fictional-home:/home/altitude:nocopy",
                                  "fictional-projects:/home/altitude/Projects:nocopy"])
        self.assertFalse({"--mount", "--secret", "--env-file", "--env-host"} & set(arguments))
        self.assertNotIn(str(config.HOME), json.dumps(arguments))
