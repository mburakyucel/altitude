"""First run's machine settings (issue #482): the operator's name, incident publication consent and the
prerequisites the agents need. Fixture engine and GitHub CLIs only."""
import json
import os
import subprocess
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, incidents, installation, land, platform, server, tasks as T


class OnboardingCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        files = [config.ROOT / "settings.json", *(config.ROOT / f"{setting}-request.json"
                                                  for setting in ("operator_name", "incident_repository"))]
        saved = {path: path.read_text() if path.exists() else None for path in files}
        self.addCleanup(lambda: [path.write_text(text) if text is not None else path.unlink(missing_ok=True)
                                 for path, text in saved.items()])
        for path in files[1:]:
            path.unlink(missing_ok=True)
        self.patch(config, "OPERATOR", None)
        self.patch(config, "UPSTREAM_ISSUE_REPOSITORY", None)
        self.git_name = mock.patch.object(config, "_git_name", return_value=None)
        self.git_name.start()
        self.addCleanup(self.git_name.stop)

    def gh(self, returncode: int):
        """Answer `gh repo view` like the signed-in GitHub CLI would; any other command fails the test."""
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            self.assertEqual(args[:3], ["gh", "repo", "view"])
            return subprocess.CompletedProcess(args, returncode, "{}", "not found")
        self.patch(server.subprocess, "run", run)
        return calls


class TestOperatorName(OnboardingCase):
    def test_the_chosen_name_outranks_the_environment_and_git(self):
        self.assertIsNone(config.operator_name())
        self.assertEqual(config.operator_label(), "Operator")
        with mock.patch.object(config, "_git_name", return_value="Git Fixture"):
            self.assertEqual(config.operator_name(), "Git Fixture")
            self.patch(config, "OPERATOR", "Env Fixture")
            self.assertEqual(config.operator_name(), "Env Fixture")
            self.assertEqual(server.save_operator_name({"name": "  Ada   Fixture "})["operator"], "Ada Fixture")
            self.assertEqual(config.operator_name(), "Ada Fixture")
            self.assertEqual(server.overview()["operator"], "Ada Fixture")
            self.assertEqual(server.save_operator_name({"name": ""})["operator"], "Env Fixture")

    def test_a_name_is_one_line_of_bounded_text_and_a_refusal_changes_nothing(self):
        server.save_operator_name({"name": "Ada Fixture"})
        for body in ({"name": "x" * 81}, {"name": 7}, {"name": "Ada", "email": "a@example.test"}):
            with self.subTest(body=body), self.assertRaises((ValueError, T.TransitionError)):
                server.save_operator_name(body)
        self.assertEqual(config.operator_name(), "Ada Fixture")

    def test_the_saved_name_is_scrubbed_from_public_issues(self):
        server.save_operator_name({"name": "Ada Fixture"})
        self.assertNotIn("Ada Fixture", incidents.sanitize("Ada Fixture approved the retry"))

    def test_a_name_ending_in_punctuation_is_scrubbed_and_refused_in_public_text(self):
        server.save_operator_name({"name": "Ada F."})
        self.assertEqual(incidents.sanitize("Ada F. approved the retry"), "the operator approved the retry")
        with self.assertRaisesRegex(ValueError, "operator's name stays on this machine"):
            incidents.check_public("Ada F. approved the retry")
        self.assertEqual(incidents.sanitize("Adam F.x"), "Adam F.x")

    def test_git_name_reads_the_global_config_once(self):
        self.git_name.stop()
        config._git_name.cache_clear()
        self.addCleanup(config._git_name.cache_clear)
        with mock.patch.object(config.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, "Git Fixture\n", "")) as run:
            self.assertEqual(config.operator_name(), "Git Fixture")
            self.assertEqual(config.operator_name(), "Git Fixture")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["git", "config", "--global", "user.name"])
        self.git_name.start()


class TestIncidentReports(OnboardingCase):
    def test_publication_is_off_until_turned_on_and_the_form_offers_altitude(self):
        view = server.machine_view()
        self.assertEqual((view["incident_repository"], view["altitude_repository"]), (None, "mburakyucel/altitude"))
        with self.assertRaisesRegex(ValueError, "stay on this machine until incident reports are turned on"):
            server.issue_repository()

    def test_turning_on_checks_the_repository_with_the_signed_in_cli(self):
        calls = self.gh(0)
        view = server.save_incident_reports({"repository": "https://github.com/fork-fixture/altitude.git"})
        self.assertEqual(view["incident_repository"], "fork-fixture/altitude")
        self.assertEqual(calls[0][:4], ["gh", "repo", "view", "fork-fixture/altitude"])
        self.assertEqual(server.issue_repository(), "https://github.com/fork-fixture/altitude")

    def test_an_unseen_or_malformed_repository_is_refused_and_publication_stays_as_it_was(self):
        self.gh(1)
        with self.assertRaisesRegex(ValueError, "cannot see fork-fixture/missing"):
            server.save_incident_reports({"repository": "fork-fixture/missing"})
        with self.assertRaisesRegex(ValueError, "owner/name"):
            server.save_incident_reports({"repository": "not a repository"})
        self.assertIsNone(config.incident_repository())

    def test_turning_off_overrides_the_environment_seed(self):
        self.patch(config, "UPSTREAM_ISSUE_REPOSITORY", "env-fixture/altitude")
        self.assertEqual(server.machine_view()["incident_repository"], "env-fixture/altitude")
        self.assertIsNone(server.save_incident_reports({"repository": None})["incident_repository"])
        self.assertFalse(json.loads((config.ROOT / "settings.json").read_text())["incident_repository"])
        with self.assertRaisesRegex(ValueError, "stay on this machine"):
            server.issue_repository()


class TestPrerequisites(OnboardingCase):
    def items(self, *, gh: bool, signed: dict, installed: dict, git: bool = True, lacks: tuple | None = ()) -> dict:
        which = {"gh": "/fixture/gh", "git": "/fixture/git" if git else None}
        with mock.patch.object(installation.shutil, "which", side_effect=lambda name: which.get(name)), \
             mock.patch.object(platform, "package_manager", return_value="apt"), \
             mock.patch.object(installation, "_gh_signed_in", return_value=gh), \
             mock.patch.object(installation, "_gh_lacks", return_value=None if lacks is None else list(lacks)), \
             mock.patch.object(engines, "installation",
                               side_effect=lambda e: {"available": None if installed[e] else False, "why": ""}), \
             mock.patch.object(engines, "sign_in", side_effect=lambda e: {"signed_in": signed[e],
                                                                         "command": engines.SIGN_IN[e][1]}):
            return {item["key"]: item for item in installation.prerequisites()}

    def test_github_sign_in_comes_first_with_the_command_to_run(self):
        items = self.items(gh=False, signed={"claude": True, "codex": False}, installed={"claude": True, "codex": True})
        self.assertEqual(list(items)[0], "github")
        self.assertEqual((items["github"]["state"], items["github"]["command"]), ("unmet", "gh auth login"))

    def test_a_missing_tool_shows_the_command_that_installs_it(self):
        with mock.patch.object(installation.shutil, "which", return_value=None), \
             mock.patch.object(platform, "package_manager", return_value="apt"), \
             mock.patch.object(engines, "installation", return_value={"available": False, "why": ""}):
            items = {item["key"]: item for item in installation.prerequisites()}
            commands = {"github": ("unmet", platform.install_command("gh")), "git": ("unmet", platform.install_command("git"))}
        self.assertEqual({key: (item["state"], item["command"]) for key, item in items.items()},
                         {**commands, **{engine: ("unmet", engines.INSTALL[engine]) for engine in config.ENGINES}})

    def test_a_github_cli_that_cannot_land_shows_the_command_that_replaces_it(self):
        items = self.items(gh=True, lacks=("baseRefOid",), signed={"claude": True, "codex": True},
                           installed={"claude": True, "codex": True})
        github = items["github"]
        self.assertEqual((github["label"], github["state"], github["command"]),
                         ("GitHub CLI too old", "unmet", platform.install_command("gh")))
        self.assertIn("2.72 or newer; this one lacks baseRefOid", github["detail"])
        with mock.patch.object(platform, "containerized", return_value=True):
            github = self.items(gh=True, lacks=("baseRefOid",), signed={"claude": True, "codex": True},
                                installed={"claude": True, "codex": True})["github"]
        self.assertEqual((github["state"], github["command"]), ("unmet", None))
        self.assertIn("Replace this incomplete image", github["detail"])

    def test_the_install_command_names_a_github_cli_that_can_land_on_each_platform(self):
        with mock.patch.object(platform.sys, "platform", "darwin"):
            self.assertEqual((platform.install_command("gh"), platform.install_command("git")),
                             ("brew install gh", "xcode-select --install"))
        with mock.patch.object(platform.sys, "platform", "linux"):
            for manager, git in (("apt", "sudo apt install git"), ("dnf", "sudo dnf install git"),
                                 ("pacman", "sudo pacman -S git"), ("zypper", "sudo zypper install git"), (None, None)):
                with self.subTest(manager=manager), mock.patch.object(platform, "package_manager", return_value=manager):
                    self.assertEqual(platform.install_command("git"), git)
                    if manager in ("pacman", None):
                        self.assertEqual(platform.install_command("gh"), manager and "sudo pacman -S github-cli")
                    else:
                        self.assertNotIn("apt" if manager != "apt" else "dnf", platform.install_command("gh"))
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        log = self.tmp / "commands"
        for name, body in (("sudo", f'echo "sudo $*" >> {log}; [ "$1" = tee ] && cat >> {log}; exit 0'),
                           ("curl", f'echo "curl $*" >> {log}; [ -n "$FAIL" ] && exit 22; echo key > "$4"')):
            (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
            (bin_dir / name).chmod(0o755)
        keyring = "/etc/apt/keyrings/githubcli-archive-keyring.gpg"
        expected = {
            "apt": ("githubcli-archive-keyring.gpg", lambda download: [
                f"sudo install -D -m 644 {download} {keyring}", "sudo tee /etc/apt/sources.list.d/github-cli.list",
                f"deb [signed-by={keyring}] https://cli.github.com/packages stable main",
                "sudo apt update", "sudo apt install gh"]),
            "dnf": ("rpm/gh-cli.repo", lambda download: [
                f"sudo install -D -m 644 {download} /etc/yum.repos.d/gh-cli.repo", "sudo dnf install gh",
                "sudo dnf upgrade gh"]),
            "zypper": ("rpm/gh-cli.repo", lambda download: [
                f"sudo install -D -m 644 {download} /etc/zypp/repos.d/gh-cli.repo", "sudo zypper install gh"])}
        for manager, (source, steps) in expected.items():
            with mock.patch.object(platform.sys, "platform", "linux"), \
                    mock.patch.object(platform, "package_manager", return_value=manager):
                command = platform.install_command("gh")
            for fail in ("1", ""):
                with self.subTest(manager=manager, fail=bool(fail)):
                    log.unlink(missing_ok=True)
                    run = subprocess.run(["sh", "-c", command], capture_output=True, text=True,
                                         env={"PATH": f"{bin_dir}:/usr/bin:/bin", "FAIL": fail, "TMPDIR": str(self.tmp)})
                    lines = log.read_text().splitlines()
                    download = lines[0].split()[-1]
                    self.assertEqual(lines[0], f"curl -fsSL https://cli.github.com/packages/{source} -o {download}")
                    if fail:
                        self.assertEqual((run.returncode, len(lines)), (22, 1), "a failed download changes nothing")
                        continue
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertFalse(Path(download).exists(), "the download is removed once installed")
                    self.assertEqual(lines[1:], steps(download))

    def test_the_package_manager_is_the_first_one_this_system_has(self):
        for present, manager in ((("apt-get", "brew"), "apt"), (("dnf",), "dnf"), (("pacman",), "pacman"),
                                 (("zypper",), "zypper"), (("brew",), None), ((), None)):
            with self.subTest(present=present), \
                    mock.patch.object(platform.shutil, "which", side_effect=lambda name: name in present or None):
                self.assertEqual(platform.package_manager(), manager)

    def test_without_a_known_package_manager_first_run_names_each_tools_own_page(self):
        with mock.patch.object(platform.sys, "platform", "linux"), \
                mock.patch.object(platform, "package_manager", return_value=None), \
                mock.patch.object(installation.shutil, "which", return_value=None), \
                mock.patch.object(engines, "installation", return_value={"available": False, "why": ""}):
            items = {item["key"]: item for item in installation.prerequisites()}
        self.assertEqual((items["github"]["command"], items["git"]["command"]), (None, None))
        self.assertIn("Install it (https://github.com/cli/cli#installation), then sign in", items["github"]["detail"])
        self.assertEqual(items["git"]["detail"], "Agents work in Git checkouts. Install it (https://git-scm.com/downloads/linux).")

    def test_the_landing_fields_come_from_the_github_cli_field_list_without_credentials(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        script = bin_dir / "gh"
        fields = land.PR_FIELDS.split(",")
        for offered, expected in ((fields, []), ([f for f in fields if f != "baseRefOid"], ["baseRefOid"])):
            listing = "\\n  ".join(["Specify one or more comma-separated fields for `--json`:", *offered])
            script.write_text("#!/bin/sh\n[ \"$*\" = 'pr view --json' ] || exit 9\n"
                              f"printf '{listing}\\n' >&2\nexit 1\n")
            script.chmod(0o755)
            with self.subTest(offered=len(offered)), mock.patch.dict(os.environ, {"PATH": str(bin_dir)}):
                self.assertEqual(installation._gh_lacks(), expected)
        script.write_text("#!/bin/sh\necho 'failed to read configuration: invalid config file' >&2\nexit 1\n")
        for path in (bin_dir, self.tmp / "empty"):
            with self.subTest(path=path.name), mock.patch.dict(os.environ, {"PATH": str(path)}):
                self.assertIsNone(installation._gh_lacks(), "a GitHub CLI that lists no fields proves nothing")

    def test_doctor_reports_whether_the_github_cli_can_land(self):
        for lacks, state in (([], "tested"), (["baseRefOid", "closingIssuesReferences"], "unavailable"), (None, "unknown")):
            with self.subTest(state=state), \
                    mock.patch.object(installation.shutil, "which", side_effect=lambda name, **_: f"/fixture/{name}"), \
                    mock.patch.object(installation, "_gh_lacks", return_value=lacks), \
                    mock.patch.object(installation, "_gh_signed_in", return_value=False), \
                    mock.patch.object(platform, "status", side_effect=RuntimeError("no service in tests")), \
                    mock.patch("altitude.tls.info", side_effect=OSError("fixture: no certificate")):
                checks = {check["name"]: check for check in installation.doctor()["checks"]}
            self.assertEqual(checks["gh"]["state"], "configured")
            self.assertEqual(checks["GitHub CLI pull request fields"]["state"], state)
            if lacks:
                self.assertIn("lacks baseRefOid, closingIssuesReferences", checks["GitHub CLI pull request fields"]["detail"])
                self.assertIn(platform.install_command("gh"), checks["GitHub CLI pull request fields"]["detail"])

    def test_a_github_cli_that_lists_no_fields_falls_back_to_its_sign_in_check(self):
        items = self.items(gh=False, lacks=None, signed={"claude": True, "codex": True},
                           installed={"claude": True, "codex": True})
        self.assertEqual((items["github"]["label"], items["github"]["command"]), ("GitHub CLI signed in", "gh auth login"))

    def test_one_signed_in_agent_is_enough_and_others_become_optional(self):
        items = self.items(gh=True, signed={"claude": True, "codex": False}, installed={"claude": True, "codex": False})
        self.assertEqual([items[k]["state"] for k in ("github", "claude", "codex", "git")], ["met", "met", "optional", "met"])
        self.assertTrue(items["codex"]["detail"].startswith("Optional"))

    def test_with_no_signed_in_agent_each_shows_its_sign_in_command(self):
        items = self.items(gh=True, signed={"claude": False, "codex": False}, installed={"claude": True, "codex": True},
                           git=False)
        self.assertEqual({k: (items[k]["state"], items[k]["command"]) for k in ("claude", "codex")},
                         {k: ("unmet", engines.SIGN_IN[k][1]) for k in ("claude", "codex")})
        self.assertEqual(items["git"]["state"], "unmet")

    def test_sign_in_reads_the_engine_cli_status_exit(self):
        script = self.tmp / "engine"
        for code, expected in ((0, True), (1, False)):
            script.write_text(f"#!/bin/sh\n[ \"$1 $2\" = 'auth status' ] || exit 9\nexit {code}\n")
            script.chmod(0o755)
            with self.subTest(code=code), mock.patch.object(config, "CLAUDE_BIN", str(script)):
                self.assertEqual(engines.sign_in("claude"), {"signed_in": expected, "command": "claude auth login"})
