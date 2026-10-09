"""I-20260916-021856: service PATH finds installed tools without interactive setup."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, make_repo
from altitude import config, engines, platform
from tests.test_restart_command import load_script


def nvm_fixture(case):
    """A private native-manager fixture and a PATH with no ambient Node installation."""
    bindir = case.tmp / "bin"
    bindir.mkdir(exist_ok=True)
    for command in ("bash", "git", "python3", "env"):
        target = bindir / command
        if not target.exists():
            target.symlink_to(shutil.which(command))
    nvm = case.tmp / "installed nvm"
    node_bin = nvm / "versions/node/v24.21.0/bin"
    node_bin.mkdir(parents=True)
    (nvm / "nvm.sh").write_text(
        '[ "$1" = "--no-use" ] || return 91\n'
        'nvm() { [ "$1 $2" = "which default" ] || return 92; '
        'printf "%s\\n" "${NVM_DIR:-$HOME/.nvm}/versions/node/v24.21.0/bin/node"; }\n')
    for name, version in (("node", "v24.21.0"), ("pnpm", "10.34.5")):
        executable = node_bin / name
        executable.write_text(f"#!/bin/sh\nprintf '{version}\\n'\n")
        executable.chmod(0o755)
    case.setenv("PATH", str(bindir))
    case.setenv("NVM_DIR", str(nvm))
    return node_bin


class TestToolchain(AltitudeCase):
    def test_minimal_path_recovers_paired_tools_without_profiles_or_environment_mutation(self):
        node_bin = nvm_fixture(self)
        profile = self.tmp / "must-not-source"
        profile.write_text("exit 99\n")
        self.setenv("BASH_ENV", str(profile))
        original = dict(os.environ)
        env = config.subprocess_env()
        self.assertEqual(env["PATH"], f"{node_bin}:{original['PATH']}")
        for command, version in (("node", "v24.21.0"), ("pnpm", "10.34.5")):
            self.assertEqual(subprocess.check_output([command, "--version"], env=env, text=True).strip(), version)
        self.assertEqual(dict(os.environ), original)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(config.subprocess_env(), env)

    def test_explicit_node_takes_precedence_over_nvm_default(self):
        nvm_fixture(self)
        node = self.tmp / "bin/node"
        node.write_text("#!/bin/sh\necho v22.22.2\n")
        node.chmod(0o755)
        with mock.patch.object(config.subprocess, "run") as probe:
            self.assertEqual(config.subprocess_env(), dict(os.environ))
        probe.assert_not_called()

    def test_default_home_location_is_supported(self):
        node_bin = nvm_fixture(self)
        home = self.tmp / "home"
        home.mkdir()
        node_bin.parents[3].rename(home / ".nvm")
        self.setenv("NVM_DIR", None)
        self.setenv("HOME", str(home))
        self.assertEqual(Path(shutil.which("node", path=config.subprocess_env()["PATH"])),
                         home / ".nvm/versions/node/v24.21.0/bin/node")

    def test_unavailable_default_preserves_other_tools_and_recovers_on_next_call(self):
        node_bin = nvm_fixture(self)
        script = node_bin.parents[3] / "nvm.sh"
        original = script.read_text()
        for body in ('return 3\n', 'nvm() { printf "N/A\\n"; }\n'):
            script.write_text(body)
            with self.assertLogs("altitude.config", level="WARNING"):
                self.assertEqual(config.subprocess_env(), dict(os.environ))
        script.write_text(original)
        self.assertEqual(config.subprocess_env()["PATH"].split(":")[0], str(node_bin))
        script.unlink()
        self.assertEqual(config.subprocess_env(), dict(os.environ))

    def test_probe_timeout_preserves_environment(self):
        nvm_fixture(self)
        with mock.patch.object(config.subprocess, "run", side_effect=subprocess.TimeoutExpired("nvm", 10)), \
                self.assertLogs("altitude.config", level="WARNING"):
            self.assertEqual(config.subprocess_env(), dict(os.environ))

    def test_discovery_and_launch_environment_agree(self):
        node_bin = nvm_fixture(self)
        self.patch(config, "CODEX_BIN", "fixture-engine")
        binary = node_bin / "fixture-engine"
        binary.write_text("#!/bin/sh\necho --strict-config\n")
        binary.chmod(0o755)
        self.assertIsNone(engines.installation("codex")["available"])
        self.assertTrue(engines.image_capability("codex")["available"])
        self.assertEqual(shutil.which("fixture-engine", path=engines.clean_env()["PATH"]), str(binary))

    def test_restart_build_uses_same_toolchain_and_frozen_install(self):
        node_bin = nvm_fixture(self)
        restart = load_script()
        self.patch(restart, "WEB", self.tmp)
        commands = []

        def run(args, *, cwd, env):
            commands.append(args)
            self.assertEqual(cwd, self.tmp)
            self.assertEqual(shutil.which("node", path=env["PATH"]), str(node_bin / "node"))
            if "--outDir" in args:
                staging = Path(args[-1])
                (staging / "assets").mkdir()
                (staging / "assets/app.js").write_text("fixture")
                (staging / "index.html").write_text('<script src="/assets/app.js"></script>')

        with mock.patch.object(restart, "run", side_effect=run):
            staging = restart.build_bundle()
        self.assertTrue((staging / "index.html").exists())
        self.assertEqual(commands[0], ["pnpm", "install", "--frozen-lockfile"])

    def test_fresh_and_resumed_workers_execute_tools_with_identity_and_failure_intact(self):
        make_repo(self.repo)
        nvm_fixture(self)
        self.patch(engines, "_codex_processes", {})
        self.patch(engines, "claude_agents", return_value=[])
        self.patch(platform, "job_active", return_value=False)
        check_tools = (
            "import json, os, subprocess, sys\n"
            "assert os.environ['ALTITUDE_TASK'] == 'toolchain-task'\n"
            "assert os.environ['ALTITUDE_ATTEMPT'] == '1'\n"
            "assert subprocess.check_output(['node', '--version'], text=True).strip() == 'v24.21.0'\n"
            "assert subprocess.check_output(['pnpm', '--version'], text=True).strip() == '10.34.5'\n"
            "def out(message): print(json.dumps(message), flush=True)\n")
        speak = {
            # Claude's stream-json: the first input line is the prompt; input closes after the result.
            "claude": (
                "first = json.loads(sys.stdin.readline())\n"
                "assert 'continue' in first['message']['content'][0]['text']\n"
                "assert ('--resume' in sys.argv) == resume\n"
                "out({'type': 'system', 'subtype': 'init', 'session_id': 'session'})\n"
                "out({'type': 'result', 'is_error': True, 'result': 'fixture failure'})\n"
                "sys.stdin.read()\n"),
            # Codex app-server: a resume is a thread/resume request, and the turn fails.
            "codex": (
                "for raw in sys.stdin:\n"
                "    message = json.loads(raw)\n"
                "    method, params = message.get('method'), message.get('params') or {}\n"
                "    if method == 'initialize':\n"
                "        out({'id': message['id'], 'result': {}})\n"
                "    elif method in ('thread/start', 'thread/resume'):\n"
                "        assert (method == 'thread/resume') == resume and params.get('threadId') == (resume and 'session' or None)\n"
                "        out({'id': message['id'], 'result': {'thread': {'id': 'session'}}})\n"
                "    elif method == 'turn/start':\n"
                "        assert 'continue' in params['input'][0]['text']\n"
                "        out({'id': message['id'], 'result': {'turn': {'id': 'turn-1'}}})\n"
                "        out({'method': 'turn/started', 'params': {'turn': {'id': 'turn-1'}}})\n"
                "        out({'method': 'turn/completed', 'params': {'turn': {'id': 'turn-1', 'status': 'failed',\n"
                "            'error': {'message': 'fixture failure'}}}})\n"),
        }
        for engine in ("claude", "codex"):
            for resume in (False, True):
                with self.subTest(engine=engine, resume=resume):
                    fake = self.tmp / f"fake-{engine}-{resume}"
                    fake.write_text(f"#!{sys.executable}\nresume = {resume}\n" + check_tools + speak[engine])
                    fake.chmod(0o755)
                    self.patch(config, "CLAUDE_BIN" if engine == "claude" else "CODEX_BIN", str(fake))

                    def service_command(unit, command, child_env, **_):
                        self.assertEqual(command, engines._driver_command())
                        # Preserve the real transient unit's clean child environment without contacting systemd.
                        return ["/usr/bin/env", "-i", *(f"{k}={v}" for k, v in child_env.items()), *command]

                    with mock.patch.object(platform, "job_command", side_effect=service_command):
                        result = engines._start_worker(
                            engine, "fixture", "continue", cwd=self.repo, job_root=self.tmp / "jobs",
                            resume="session" if resume else None,
                            extra_env={"ALTITUDE_TASK": "toolchain-task", "ALTITUDE_ATTEMPT": "1"})
                    self.assertEqual(result["returncode"], 0, result)
                    worker_id = result["agent"]["id"]
                    process = engines._codex_processes.get(worker_id)
                    if process:
                        process.wait(timeout=5)
                    self.assertEqual(engines._codex_paths(self.tmp / "jobs", worker_id)["stderr"].read_text(), "")
                    row = engines.worker(engine, {"agent_id": worker_id}, job_root=self.tmp / "jobs")
                    self.assertEqual(row["sessionId"], "session")
                    self.assertEqual(row["state"], "failed")
