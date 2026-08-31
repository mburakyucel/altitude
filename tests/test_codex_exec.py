"""Codex exec exposes resumable lifecycle state without losing bounded raw evidence."""
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from altitude import alt_broker, config, engines

_REAL_CODEX = os.environ.get("ALTITUDE_TEST_REAL_CODEX") or shutil.which("codex")


class TestCodexExecLifecycle(unittest.TestCase):
    def setUp(self):
        self._runtime = tempfile.TemporaryDirectory(prefix="altitude-codex-test-runtime-")
        self.addCleanup(self._runtime.cleanup)
        self._runtime_patch = patch.object(engines, "CODEX_RUNTIME_ROOT", Path(self._runtime.name))
        self._runtime_patch.start()
        self.addCleanup(self._runtime_patch.stop)

    def test_fresh_and_resume_surface_pid_session_text_usage_and_resume_flags(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-exec-") as tmp:
            root = Path(tmp)
            args_path = root / "args.jsonl"
            fake = root / "codex"
            fake.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["ALTITUDE_TEST_ARGS_PATH"], "a") as handle:
    handle.write(json.dumps(args) + "\\n")
with open(os.environ["ALTITUDE_TEST_ENV_PATH"], "a") as handle:
    handle.write(json.dumps({key: os.environ.get(key) for key in ("CODEX_HOME", "GH_TOKEN", "OPENAI_API_KEY")}) + "\\n")
out = args[args.index("-o") + 1]
open(out, "w").write("final answer")
print(json.dumps({"type": "thread.started", "thread_id": "thread-1"}), flush=True)
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "streamed answer"}}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 3}}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 2}}), flush=True)
print("codex diagnostic", file=sys.stderr, flush=True)
""")
            fake.chmod(0o500)
            starts, sessions, texts = [], [], []
            env_path = root / "env.jsonl"
            env = {"ALTITUDE_TEST_ARGS_PATH": str(args_path), "ALTITUDE_TEST_ENV_PATH": str(env_path),
                   "GH_TOKEN": "do-not-publish", "OPENAI_API_KEY": "do-not-publish"}
            with patch.object(config, "CODEX_BIN", str(fake)):
                fresh = engines.codex_exec("build", cwd=root, sandbox="workspace-write", extra_env=env,
                                           on_start=starts.append, on_session=sessions.append, on_text=texts.append)
                resumed = engines.codex_exec("continue", cwd=root, sandbox="workspace-write", extra_env=env,
                                             resume="thread-1", bypass_hook_trust=True)
            fresh_args, resume_args = [json.loads(line) for line in args_path.read_text().splitlines()]
            fresh_env, resume_env = [json.loads(line) for line in env_path.read_text().splitlines()]

        self.assertEqual((fresh["session_id"], fresh["text"]), ("thread-1", "final answer"))
        self.assertEqual(fresh["usage"], {"input_tokens": 14, "output_tokens": 5})
        self.assertGreater(starts[0], 0)
        self.assertEqual((sessions, texts), (["thread-1"], ["streamed answer"]))
        self.assertIn("thread.started", fresh["raw_stdout"])
        self.assertEqual(fresh["raw_stderr"], "codex diagnostic\n")
        self.assertFalse(fresh["raw_stdout_truncated"])
        self.assertEqual(fresh_args[:2], ["exec", "--json"])
        self.assertEqual(resume_args[:2], ["exec", "resume"])
        self.assertNotIn("-s", fresh_args)
        self.assertNotIn("sandbox_mode", " ".join(fresh_args + resume_args))
        self.assertIn("--ignore-user-config", fresh_args)
        self.assertIn("--strict-config", fresh_args)
        self.assertIn("--ignore-user-config", resume_args)
        self.assertIn("--strict-config", resume_args)
        self.assertIn('default_permissions="altitude"', fresh_args)
        self.assertIn("thread-1", resume_args)
        self.assertIn("--dangerously-bypass-hook-trust", resume_args)
        self.assertEqual(fresh_env["CODEX_HOME"], resume_env["CODEX_HOME"])
        self.assertTrue(fresh_env["CODEX_HOME"])
        self.assertIsNone(fresh_env["GH_TOKEN"])
        self.assertIsNone(fresh_env["OPENAI_API_KEY"])
        self.assertIsNone(resumed["error"])

    def test_codex_exec_inherits_only_validated_broker_descriptors(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-exec-fds-") as tmp:
            root = Path(tmp)
            seen = root / "seen.json"
            fake = root / "codex"
            fake.write_text("""#!/usr/bin/env python3
import json, os, stat, sys
connection = int(os.environ["ALTITUDE_ALT_BROKER_FD"])
lock = int(os.environ["ALTITUDE_ALT_BROKER_LOCK_FD"])
info = [stat.S_ISSOCK(os.fstat(connection).st_mode), stat.S_ISREG(os.fstat(lock).st_mode)]
open(os.environ["ALTITUDE_TEST_SEEN"], "w").write(json.dumps(info))
args = sys.argv[1:]
open(args[args.index("-o") + 1], "w").write("fd inheritance ok")
print(json.dumps({"type": "turn.completed", "usage": {}}))
""")
            fake.chmod(0o500)
            connection, peer = socket.socketpair()
            lock_fd = os.memfd_create("codex-exec-test-lock", getattr(os, "MFD_CLOEXEC", 0))
            os.ftruncate(lock_fd, 1)
            inherited = (connection.fileno(), lock_fd)
            env = {alt_broker.FD_ENV: str(inherited[0]), alt_broker.LOCK_FD_ENV: str(inherited[1]),
                   "ALTITUDE_TEST_SEEN": str(seen)}
            try:
                with patch.object(config, "CODEX_BIN", str(fake)):
                    result = engines.codex_exec("probe", cwd=root, extra_env=env, broker_fds=inherited)
            finally:
                connection.close(); peer.close(); os.close(lock_fd)
            seen_value = json.loads(seen.read_text())
        self.assertEqual(result["error"], None)
        self.assertEqual(result["text"], "fd inheritance ok")
        self.assertEqual(seen_value, [True, True])

    def test_private_home_is_stable_private_and_imports_only_model_defaults(self):
        root = Path(tempfile.mkdtemp(prefix="altitude-codex-profile-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        cwd = root / "repo"; cwd.mkdir()
        trusted_home = root / "operator"; trusted = trusted_home / ".codex"; trusted.mkdir(parents=True)
        (trusted / "auth.json").write_text('{"tokens":{"access_token":"secret"}}\n')
        (trusted / "config.toml").write_text(
            'model="gpt-safe"\nmodel_reasoning_effort="xhigh"\n'
            '[mcp_servers.evil]\ncommand="/bin/false"\n[projects."/tmp"]\ntrust_level="trusted"\n'
        )
        for path in (trusted / "auth.json", trusted / "config.toml"):
            path.chmod(0o600)
        fake = root / "codex"; fake.write_bytes(b"#!/bin/sh\n" + b"#" * 64); fake.chmod(0o500)
        worker_root = root / "state" / "codex-workers"
        patches = (patch.object(config, "HOME", trusted_home), patch.object(config, "ROOT", root / "state"),
                   patch.object(config, "CODEX_BIN", str(fake)),
                   patch.object(engines, "CODEX_WORKER_HOMES", worker_root),
                   patch.object(engines, "CODEX_RUNTIME_ROOT", root / "runtime"))
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            first = engines.codex_worker_home(cwd=cwd, role="l2", permission_id="p/task", writable=True)
            second = engines.codex_worker_home(cwd=cwd, role="l2", permission_id="p/task", writable=True)
        home, runtime, model, effort, profile = first
        self.assertEqual((home, runtime), (second[0], second[1]))
        self.assertEqual((model, effort), ("gpt-safe", "xhigh"))
        self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((home / "auth.json").stat().st_mode), 0o600)
        self.assertNotIn("mcp_servers", profile)
        self.assertNotIn("projects.", profile)
        self.assertIn('default_permissions = "altitude"', profile)
        self.assertIn('"." = \'write\'', profile)
        self.assertIn(str(runtime), profile)

    def test_symlinked_trusted_auth_fails_closed_before_engine_start(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-profile-") as tmp:
            root = Path(tmp); cwd = root / "repo"; cwd.mkdir()
            trusted = root / "home" / ".codex"; trusted.mkdir(parents=True)
            target = root / "auth-target"; target.write_text("{}\n"); target.chmod(0o600)
            (trusted / "auth.json").symlink_to(target)
            (trusted / "config.toml").write_text('model="gpt-safe"\n'); (trusted / "config.toml").chmod(0o600)
            with patch.object(config, "HOME", root / "home"), patch.object(config, "ROOT", root / "state"), \
                 patch.object(engines, "CODEX_WORKER_HOMES", root / "state" / "codex-workers"):
                result = engines.codex_exec("never runs", cwd=cwd)
        self.assertFalse(result["engine_started"])
        self.assertIn("permission profile failed closed", result["error"])

    def test_inherited_broker_adds_no_profile_path_socket_or_network_exception(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-broker-profile-") as tmp:
            root = Path(tmp); worktree = root / "worktree"; worktree.mkdir()
            fake = root / "fake-alt.py"; fake.write_text("print('ok')\n")
            broker = alt_broker.AltBroker(
                socket_path=root / "host-only" / "broker.sock", token="secret", project="p", slug="t",
                generation="g", worktree=worktree, trusted_alt=fake, policy=alt_broker.l2_policy,
                validate_generation=lambda: True,
            )
            try:
                with broker:
                    env, inherited = broker.codex_capability()
                    reads, writes, sockets = engines._codex_broker_paths(env)
                    self.assertEqual(engines._validated_codex_broker_fds(env, inherited), inherited)
                    profile = engines._codex_profile_text(
                        cwd=worktree, writable=False, read_paths=tuple(reads),
                        write_paths=tuple(writes), unix_sockets=tuple(sockets),
                        model="gpt-test", effort="high",
                    )
            except PermissionError as exc:
                self.skipTest(f"outer sandbox prevents Unix-socket probe: {exc}")
        parsed = engines.tomllib.loads(profile)
        permission = parsed["permissions"]["altitude"]
        self.assertEqual(set(reads), {
            (config.REPO / "bin" / "alt").resolve(),
            (config.REPO / "altitude" / "__init__.py").resolve(),
            (config.REPO / "altitude" / "alt_broker.py").resolve(),
        })
        self.assertEqual((writes, sockets), ([], []))
        self.assertFalse(parsed["features"]["network_proxy"])
        self.assertFalse(permission["network"]["enabled"])
        self.assertNotIn("unix_sockets", permission["network"])
        self.assertNotIn(str(root.resolve()), permission["filesystem"])
        self.assertEqual(permission["filesystem"][str((config.REPO / "bin" / "alt").resolve())], "read")

    def test_broker_descriptor_environment_requires_exact_validated_pass_fds(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-broker-fds-") as tmp:
            root = Path(tmp); worktree = root / "worktree"; worktree.mkdir()
            fake = root / "fake-alt.py"; fake.write_text("print('ok')\n")
            broker = alt_broker.AltBroker(
                socket_path=root / "host-only" / "broker.sock", token="secret", project="p", slug="t",
                generation="g", worktree=worktree, trusted_alt=fake, policy=alt_broker.l2_policy,
                validate_generation=lambda: True,
            )
            try:
                with broker:
                    env, inherited = broker.codex_capability()
                    with self.assertRaisesRegex(engines.CodexPermissionProfileError, "not authorized"):
                        engines._validated_codex_broker_fds(env, None)
                    bad = dict(env); bad[alt_broker.FD_ENV] = str(inherited[0] + 1000)
                    with self.assertRaisesRegex(engines.CodexPermissionProfileError, "does not match"):
                        engines._validated_codex_broker_fds(bad, inherited)
                    with self.assertRaisesRegex(engines.CodexPermissionProfileError, "path transport"):
                        engines._codex_broker_paths({alt_broker.SOCKET_ENV: str(broker.socket_path)})
            except PermissionError as exc:
                self.skipTest(f"outer sandbox prevents Unix-socket probe: {exc}")

    @unittest.skipUnless(_REAL_CODEX, "Codex CLI is not installed")
    def test_real_profile_uses_only_inherited_broker_capability(self):
        """No model call: prove inherited broker FDs survive the real 0.151 sandbox."""
        private_root = os.environ.get("ALTITUDE_TEST_PRIVATE_ROOT")
        with tempfile.TemporaryDirectory(prefix="altitude-codex-broker-real-", dir=private_root) as tmp:
            root = Path(tmp); operator = root / "operator"; trusted = operator / ".codex"
            broker_root = root / "broker"; broker_root.mkdir()
            trusted.mkdir(parents=True)
            for name in ("auth.json", "config.toml"):
                source = config.HOME / ".codex" / name
                (trusted / name).write_bytes(source.read_bytes())
                (trusted / name).chmod(0o600)
            state = operator / ".altitude"; state.mkdir()
            worktree = operator / "workspace"; worktree.mkdir()
            (operator / "private-secret").write_text("host-private\n")
            fake_alt = root / "fake-alt.py"; fake_alt.write_text("print('brokered')\n")
            broker = alt_broker.AltBroker(
                socket_path=broker_root / "exact.sock", token="secret", project="p", slug="t",
                generation="g", worktree=worktree, trusted_alt=fake_alt,
                policy=alt_broker.l2_policy, validate_generation=lambda: True,
            )
            other_path = broker_root / "other.sock"
            other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                other.bind(str(other_path)); other.listen(1)
            except PermissionError as exc:
                other.close()
                self.skipTest(f"outer sandbox prevents Unix-socket probe: {exc}")
            try:
                with broker, patch.object(config, "HOME", operator), patch.object(config, "ROOT", state), \
                     patch.object(config, "CODEX_BIN", str(_REAL_CODEX)), \
                     patch.object(engines, "CODEX_WORKER_HOMES", state / "codex-workers"):
                    broker_env, broker_fds = broker.codex_capability()
                    reads, writes, sockets = engines._codex_broker_paths(broker_env)
                    home, runtime, _model, _effort, _profile = engines.codex_worker_home(
                        cwd=worktree, role="broker_probe", permission_id="exact", writable=False,
                        read_paths=tuple(reads), write_paths=tuple(writes), unix_sockets=tuple(sockets),
                    )
                    alt = config.REPO / "bin" / "alt"  # deliberately outside cwd: exact trusted-code read grant
                    command = (
                        'set -eu; test -z "${ALTITUDE_ALT_BROKER_SOCKET:-}"; '
                        f"python3 {alt} task status t & p1=$!; "
                        f"python3 {alt} task status t & p2=$!; "
                        'wait "$p1"; wait "$p2"; '
                        f"python3 {alt} task status t; "
                        'case "$(readlink /proc/self/fd/$ALTITUDE_ALT_BROKER_FD)" in socket:*) ;; *) exit 43;; esac; '
                        'if touch "$BROKER_PARENT/pwn" 2>/dev/null; then exit 44; fi; '
                        'if python3 -c "import os,socket; s=socket.socket(socket.AF_UNIX); '
                        's.connect(os.environ[\'OTHER_SOCKET\'])" 2>/dev/null; then exit 45; fi; '
                        'if python3 -c "import socket; socket.create_connection((\'1.1.1.1\',443),.4)" '
                        '2>/dev/null; then exit 46; fi; '
                        'test ! -r "$HOME/private-secret"; test ! -r "$HOME/.codex/auth.json"; '
                        'echo inherited-broker-ok'
                    )
                    env = engines.clean_env()
                    env.update({"HOME": str(operator), "CODEX_HOME": str(home),
                                "ALTITUDE_HOME": str(state), "BROKER_PARENT": str(broker.socket_path.parent),
                                "OTHER_SOCKET": str(other_path), **broker_env})
                    result = subprocess.run(
                        [str(runtime), "sandbox", "-C", str(worktree), "-P", "altitude", "--",
                         "/bin/sh", "-c", command],
                        capture_output=True, text=True, timeout=30, env=env, pass_fds=broker_fds,
                    )
            finally:
                other.close()
        # A broker-level EPERM is a policy failure, not an environmental skip.
        # Skip only when Codex's outer sandbox itself could not start.
        unavailable = ("bwrap:" in result.stderr or "linux-sandbox unavailable" in result.stderr)
        if result.returncode and unavailable:
            self.skipTest(f"outer test sandbox prevents nested Codex broker probe: {result.stderr[-300:]}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("inherited-broker-ok", result.stdout)
        self.assertEqual(result.stdout.count("brokered"), 3, result.stdout)
        self.assertFalse((broker_root / "pwn").exists())

    def test_legacy_sandbox_override_is_rejected_before_engine_start(self):
        result = engines.codex_exec(
            "never runs", cwd=Path.cwd(), sandbox="workspace-write",
            extra_config=["sandbox_workspace_write.network_access=false"],
        )
        self.assertFalse(result["engine_started"])
        self.assertIn("unsafe Codex config override", result["error"])

    @unittest.skipUnless(_REAL_CODEX, "Codex CLI is not installed")
    def test_real_permission_profile_hides_host_credentials_and_network(self):
        """No model call: exercise the generated 0.151 profile through ``codex sandbox``."""
        with tempfile.TemporaryDirectory(prefix="altitude-codex-real-profile-") as tmp:
            root = Path(tmp)
            operator = root / "operator"
            cwd = operator / "Projects" / "workspace"
            cwd.mkdir(parents=True)
            (cwd / "visible").write_text("yes\n")
            # An unrelated home secret proves the profile does not merely
            # enumerate today's well-known credential directories.
            (operator / "private-secret").write_text("host-private\n")
            for relative in (".ssh/config", ".config/gh/hosts.yml", ".claude/.credentials.json"):
                path = operator / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("secret\n")
            trusted = operator / ".codex"; trusted.mkdir()
            trusted_auth = config.HOME / ".codex" / "auth.json"
            trusted_config = config.HOME / ".codex" / "config.toml"
            (trusted / "auth.json").write_bytes(trusted_auth.read_bytes())
            (trusted / "config.toml").write_bytes(trusted_config.read_bytes())
            for private in (trusted / "auth.json", trusted / "config.toml"):
                private.chmod(0o600)
            state = operator / ".altitude"; state.mkdir(); (state / "projects.json").write_text("secret\n")
            runtime_root = Path(tempfile.mkdtemp(prefix="altitude-codex-runtime-probe-"))
            self.addCleanup(shutil.rmtree, runtime_root, ignore_errors=True)
            with patch.object(config, "HOME", operator), patch.object(config, "ROOT", state), \
                 patch.object(config, "CODEX_BIN", str(_REAL_CODEX)), \
                 patch.object(engines, "CODEX_WORKER_HOMES", state / "codex-workers"), \
                 patch.object(engines, "CODEX_RUNTIME_ROOT", runtime_root):
                home, runtime, _model, _effort, _profile = engines.codex_worker_home(
                    cwd=cwd, role="profile_probe", permission_id="exact", writable=True)
            command = (
                'test -r visible && echo changed > created && '
                'test ! -r "$CODEX_HOME/auth.json" && '
                'test ! -r "$HOME/private-secret" && '
                'test ! -r "$HOME/.ssh/config" && '
                'test ! -r "$HOME/.config/gh/hosts.yml" && '
                'test ! -r "$HOME/.claude/.credentials.json" && '
                'test ! -r "$HOME/.altitude/projects.json" && '
                'test ! -r "$ALTITUDE_HOME/projects.json" && '
                'if python3 -c "import socket; socket.create_connection((\'1.1.1.1\',443),.4)"; '
                'then exit 44; fi; echo permission-profile-ok'
            )
            env = engines.clean_env(); env.update({"HOME": str(operator), "ALTITUDE_HOME": str(state),
                                                    "CODEX_HOME": str(home)})
            result = subprocess.run(
                [str(runtime), "sandbox", "-C", str(cwd), "-P", "altitude", "--", "/bin/sh", "-c", command],
                capture_output=True, text=True, timeout=30, env=env,
            )
        unavailable = ("bwrap:" in result.stderr or "linux-sandbox" in result.stderr
                       or "Operation not permitted" in result.stderr or "Permission denied" in result.stderr)
        if result.returncode and unavailable:
            self.skipTest(f"outer test sandbox prevents nested Codex profile probe: {result.stderr[-300:]}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("permission-profile-ok", result.stdout)

    def test_missing_binary_is_a_structured_failure_with_empty_raw_contract(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-exec-") as tmp, \
             patch.object(config, "CODEX_BIN", str(Path(tmp) / "missing-codex")):
            result = engines.codex_exec("build", cwd=Path(tmp), resume="thread-old")

        self.assertEqual((result["returncode"], result["session_id"]), (1, "thread-old"))
        self.assertFalse(result["engine_started"])
        self.assertIn("permission profile failed closed", result["error"])
        self.assertEqual((result["raw_stdout"], result["raw_stderr"]), ("", ""))
        self.assertFalse(result["raw_stdout_truncated"])
        self.assertFalse(result["raw_stderr_truncated"])

    def test_raw_streams_are_bounded_without_losing_their_head_and_tail(self):
        raw_stdout = "HEAD" + ("x" * 300) + "TAIL"
        raw_stderr = "ERR-HEAD" + ("y" * 300) + "ERR-TAIL"

        class FakeProcess:
            pid = 456
            returncode = 0

            def __init__(self):
                self.stdout = io.StringIO(raw_stdout)
                self.stderr = io.StringIO(raw_stderr)

            def wait(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

        with patch.object(engines, "RAW_CAPTURE_CAP", 120), \
             patch.object(engines.subprocess, "Popen", side_effect=lambda *args, **kwargs: FakeProcess()):
            result = engines.codex_exec("build", cwd=Path("."))

        self.assertTrue(result["raw_stdout"].startswith("HEAD"))
        self.assertTrue(result["raw_stdout"].endswith("TAIL"))
        self.assertTrue(result["raw_stderr"].startswith("ERR-HEAD"))
        self.assertTrue(result["raw_stderr"].endswith("ERR-TAIL"))
        self.assertLessEqual(len(result["raw_stdout"].encode()), 120)
        self.assertLessEqual(len(result["raw_stderr"].encode()), 120)
        self.assertTrue(result["raw_stdout_truncated"])
        self.assertTrue(result["raw_stderr_truncated"])


    def test_nested_engine_callback_failure_kills_child_without_new_process_group(self):
        class FakeProcess:
            pid = 456
            returncode = None

            def __init__(self):
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO("")
                self.killed = False

            def kill(self):
                self.killed = True
                self.returncode = -9

            def wait(self):
                return self.returncode

        child = FakeProcess()
        seen = {}

        def popen(*args, **kwargs):
            seen.update(kwargs)
            return child

        with patch.object(engines.subprocess, "Popen", side_effect=popen):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                engines.codex_exec("build", cwd=Path("."), start_new_session=False,
                                   on_start=lambda _pid: (_ for _ in ()).throw(RuntimeError("cancelled")))
        self.assertFalse(seen["start_new_session"])
        self.assertTrue(child.killed)


if __name__ == "__main__":
    unittest.main()
