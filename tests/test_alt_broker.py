import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from altitude import alt_broker, dispatch


class TestL2BrokerPolicy(unittest.TestCase):
    def test_land_and_checkpoint_are_current_worktree_bounded(self):
        with tempfile.TemporaryDirectory(prefix="altitude-broker-policy-") as tmp:
            worktree = Path(tmp) / "worktree"
            outside = Path(tmp) / "outside"
            worktree.mkdir(); outside.mkdir()
            body = worktree / "body.md"; body.write_text("body")
            progress = worktree / "progress.md"; progress.write_text("progress")
            allowed = alt_broker.l2_policy(
                ["land", "--message", "safe", "--merge", "--pr-body-file", str(body)],
                project="p", slug="t", worktree=worktree,
            )
            checkpoint = alt_broker.l2_policy(
                ["task", "checkpoint", "t", "--name", "progress.md", "--source", str(progress)],
                project="p", slug="t", worktree=worktree,
            )
            self.assertEqual((allowed.kind, checkpoint.kind), ("cli", "checkpoint"))
            for command in (
                ["land", "--message", "unsafe", "--paths", "everything"],
                ["land", "--message", "unsafe", "--pr-body-file", str(outside / "body.md")],
                ["task", "checkpoint", "other", "--name", "progress.md", "--source", str(progress)],
                ["task", "done", "t"],
                ["task", "needs-user", "t", "--reason", "bypass"],
            ):
                with self.assertRaises(alt_broker.BrokerDenied):
                    alt_broker.l2_policy(command, project="p", slug="t", worktree=worktree)
    def test_codex_has_no_state_git_roots_or_network(self):
        values = dispatch._codex_extra_config(Path("/task-worktree"), "p", "t")
        joined = "\n".join(values)
        self.assertIn("network_access=false", joined)
        self.assertNotIn("writable_roots", joined)
        self.assertIn("codex_l2_cap", joined)
        self.assertIn("codex_guard", joined)

    def test_host_checkpoint_rejects_invalid_report_json(self):
        with tempfile.TemporaryDirectory(prefix="altitude-broker-json-") as tmp, \
             mock.patch.object(dispatch, "_codex_generation_current", return_value=True), \
             mock.patch.object(dispatch.S, "task_dir", return_value=Path(tmp)):
            with self.assertRaisesRegex(alt_broker.BrokerDenied, "invalid JSON"):
                dispatch._codex_checkpoint("p", "t", "t-1", "g", "report.json", b"not json")




class TestL3BrokerPolicy(unittest.TestCase):
    def test_current_project_orchestration_only(self):
        with tempfile.TemporaryDirectory(prefix="altitude-l3-policy-") as tmp:
            worktree = Path(tmp); worktree.mkdir(exist_ok=True)
            for command in (
                ["state"],
                ["task", "status", "any-task"],
                ["fyi", "any-task", "done"],
                ["incident", "list"],
                ["rule", "audit-input"],
                ["--project", "p", "state"],
            ):
                action = alt_broker.l3_policy(command, project="p", slug="__l3__", worktree=worktree)
                self.assertEqual(action.kind, "cli")
            for command in (
                ["land", "--message", "bypass"],
                ["l1", "run", "--brief", "x"],
                ["task", "approve", "t"],
                ["task", "done", "t", "--file", "/etc/passwd"],
                ["--project", "other", "state"],
            ):
                with self.assertRaises(alt_broker.BrokerDenied):
                    alt_broker.l3_policy(command, project="p", slug="__l3__", worktree=worktree)



class TestBrokerRoundTrip(unittest.TestCase):
    def request(self, broker, argv, cwd):
        return broker._process({
            "token": "secret", "project": "p", "slug": "t", "generation": "g",
            "argv": argv, "cwd": str(cwd), "stdin": "",
        })

    def test_forwards_only_current_generation_and_owned_cwd(self):
        with tempfile.TemporaryDirectory(prefix="altitude-broker-roundtrip-") as tmp:
            root = Path(tmp); worktree = root / "task"; l1tree = root / "l1"; other = root / "other"
            for path in (worktree, l1tree, other):
                path.mkdir()
            fake = root / "fake-alt.py"
            fake.write_text("import json, os, sys\nprint(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), "
                            "'identity': [os.getenv('ALTITUDE_PROJECT'), os.getenv('ALTITUDE_TASK'), "
                            "os.getenv('ALTITUDE_ACTOR'), os.getenv('ALTITUDE_TRIGGER'), os.getenv('GIT_DIR')]}))\n")
            broker = alt_broker.AltBroker(
                socket_path=root / "unused.sock", token="secret", project="p", slug="t", generation="g",
                worktree=worktree, trusted_alt=fake, policy=alt_broker.l2_policy,
                validate_generation=lambda: True,
                validate_cwd=lambda path: path in {worktree.resolve(), l1tree.resolve()},
                checkpoint=lambda name, data: {"name": name, "data": data.decode()},
                before_cli=lambda argv, cwd: {"GIT_DIR": "trusted"},
                host_env={"ALTITUDE_PROJECT": "wrong", "ALTITUDE_TASK": "other", "ALTITUDE_ACTOR": "l3",
                          "ALTITUDE_TRIGGER": "chat", "GIT_DIR": "attacker"},
            )
            status = self.request(broker, ["task", "status", "t"], worktree)
            landed = self.request(broker, ["land", "--message", "ok", "--merge"], l1tree)
            self.assertEqual((status["returncode"], landed["returncode"]), (0, 0))
            self.assertEqual(json.loads(status["stdout"])["argv"], ["--project", "p", "task", "status", "t"])
            self.assertEqual(json.loads(landed["stdout"])["cwd"], str(l1tree))
            self.assertEqual(json.loads(status["stdout"])["identity"], ["p", "t", "l2", "chat", "trusted"])
            with self.assertRaisesRegex(alt_broker.BrokerDenied, "persisted worktree"):
                self.request(broker, ["task", "status", "t"], other)
            with self.assertRaisesRegex(alt_broker.BrokerDenied, "current task"):
                self.request(broker, ["task", "status", "other"], worktree)

    def test_hook_round_trip_uses_host_generation_callback(self):
        with tempfile.TemporaryDirectory(prefix="altitude-broker-hook-") as tmp:
            root = Path(tmp); worktree = root / "task"; worktree.mkdir()
            seen = []
            broker = alt_broker.AltBroker(
                socket_path=root / "secure" / "broker.fifo", token="secret", project="p", slug="t",
                generation="g", worktree=worktree, trusted_alt=root / "unused", policy=alt_broker.l2_policy,
                validate_generation=lambda: True,
                hook=lambda payload: seen.append(payload) or {
                    "allowed": payload.get("tool_use_id") == "one", "message": "cap"},
            )
            request = {"kind": "hook", "token": "secret", "project": "p", "slug": "t",
                       "generation": "g", "cwd": str(worktree),
                       "payload": {"tool_use_id": "one", "tool_name": "Read", "tool_input": {}}}
            allowed = broker._process(request)
            request["payload"] = {
                "tool_use_id": "two", "tool_name": "Read", "tool_input": {}}
            denied = broker._process(request)
            self.assertEqual((allowed["returncode"], denied["returncode"]), (0, 2))
            self.assertEqual([item["tool_use_id"] for item in seen], ["one", "two"])
            self.assertEqual(denied["stderr"], "cap\n")

    def test_fifo_transport_round_trip_without_unix_sockets(self):
        with tempfile.TemporaryDirectory(prefix="altitude-broker-fifo-") as tmp:
            root = Path(tmp); worktree = root / "task"; worktree.mkdir()
            fake = root / "fake-alt.py"; fake.write_text("print('brokered')\n")
            broker = alt_broker.AltBroker(
                socket_path=root / "secure" / "broker.fifo", token="secret", project="p", slug="t",
                generation="g", worktree=worktree, trusted_alt=fake, policy=alt_broker.l2_policy,
                validate_generation=lambda: True, checkpoint=lambda name, data: {},
            )
            with broker:
                response = alt_broker._fifo_request(
                    broker.socket_path,
                    {"token": "secret", "project": "p", "slug": "t", "generation": "g",
                     "argv": ["task", "status", "t"], "cwd": str(worktree), "stdin": ""},
                    timeout=3,
                )
            self.assertEqual((response["returncode"], response["stdout"]), (0, "brokered\n"))

    def test_cli_file_is_pinned_before_the_trusted_process_reopens_it(self):
        with tempfile.TemporaryDirectory(prefix="altitude-broker-pin-") as tmp:
            root = Path(tmp); worktree = root / "task"; worktree.mkdir()
            source = worktree / "brief.md"; source.write_text("trusted brief")
            secret = root / "secret"; secret.write_text("host secret")
            fake = root / "fake-alt.py"
            fake.write_text("import pathlib, sys\noption = '--brief' if '--brief' in sys.argv else '--pr-body-file'\n"
                            "print(pathlib.Path(sys.argv[sys.argv.index(option) + 1]).read_text())\n")
            seen = []
            broker = alt_broker.AltBroker(
                socket_path=root / "broker" / "endpoint", token="secret", project="p", slug="t", generation="g",
                worktree=worktree, trusted_alt=fake, policy=alt_broker.l2_policy, validate_generation=lambda: True,
                before_cli=lambda argv, _cwd: seen.append(list(argv)) or {},
            )
            broker.socket_path.parent.mkdir(mode=0o700)
            real_read = alt_broker._read_regular_worktree_file
            def read_then_swap(value, exact_root, **kwargs):
                data = real_read(value, exact_root, **kwargs)
                source.unlink()
                source.symlink_to(secret)
                return data
            with mock.patch.object(alt_broker, "_read_regular_worktree_file", side_effect=read_then_swap):
                result = self.request(broker, ["l1", "run", "--brief", "brief.md"], worktree)
            self.assertEqual((result["returncode"], result["stdout"]), (0, "trusted brief\n"))
            pinned = seen[0][seen[0].index("--brief") + 1]
            self.assertTrue(pinned.startswith("/proc/self/fd/"), pinned)
            self.assertEqual(list(broker.socket_path.parent.glob("pinned-*")), [])

    def test_pr_body_file_is_pinned_before_the_trusted_process_reopens_it(self):
        with tempfile.TemporaryDirectory(prefix="altitude-broker-body-") as tmp:
            root = Path(tmp); worktree = root / "task"; worktree.mkdir()
            source = worktree / "body.md"; source.write_text("trusted body")
            secret = root / "secret"; secret.write_text("host secret")
            fake = root / "fake-alt.py"
            fake.write_text("import pathlib, sys\nprint(pathlib.Path(sys.argv[sys.argv.index('--pr-body-file') + 1]).read_text())\n")
            seen = []
            broker = alt_broker.AltBroker(
                socket_path=root / "broker" / "endpoint", token="secret", project="p", slug="t", generation="g",
                worktree=worktree, trusted_alt=fake, policy=alt_broker.l2_policy, validate_generation=lambda: True,
                before_cli=lambda argv, _cwd: seen.append(list(argv)) or {},
            )
            broker.socket_path.parent.mkdir(mode=0o700)
            real_read = alt_broker._read_regular_worktree_file
            def read_then_swap(value, exact_root, **kwargs):
                data = real_read(value, exact_root, **kwargs)
                source.unlink()
                source.symlink_to(secret)
                return data
            with mock.patch.object(alt_broker, "_read_regular_worktree_file", side_effect=read_then_swap):
                result = self.request(
                    broker, ["land", "--message", "safe", "--pr-body-file", "body.md"], worktree)
            self.assertEqual((result["returncode"], result["stdout"]), (0, "trusted body\n"))
            pinned = seen[0][seen[0].index("--pr-body-file") + 1]
            self.assertTrue(pinned.startswith("/proc/self/fd/"), pinned)
            self.assertEqual(list(broker.socket_path.parent.glob("pinned-*")), [])

    def test_replaced_git_pointer_cannot_redirect_brokered_git(self):
        with tempfile.TemporaryDirectory(prefix="altitude-broker-git-") as tmp:
            root = Path(tmp); repo = root / "repo"; worktree = root / "task"
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
            (repo / "tracked").write_text("ok")
            subprocess.run(["git", "-C", str(repo), "add", "tracked"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://example.invalid/repo.git"], check=True)
            subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "worktree-t", str(worktree), "HEAD"], check=True)
            dispatch._linked_worktree_gitdir(repo, worktree, "worktree-t")
            evil = worktree / "evil"
            subprocess.run(["git", "init", "-q", str(evil)], check=True)
            (worktree / ".git").write_text(f"gitdir: {evil / '.git'}\n")
            with self.assertRaisesRegex(Exception, "escaped the authoritative"):
                dispatch._linked_worktree_gitdir(repo, worktree, "worktree-t")

    def test_checkpoint_is_host_copied(self):
        with tempfile.TemporaryDirectory(prefix="altitude-broker-checkpoint-") as tmp:
            root = Path(tmp); worktree = root / "task"; worktree.mkdir()
            fake = root / "fake-alt.py"; fake.write_text("raise SystemExit(99)\n")
            report = worktree / "report.json"; report.write_text('{"ok": true}')
            copied = []
            broker = alt_broker.AltBroker(
                socket_path=root / "unused.sock", token="secret", project="p", slug="t",
                generation="g", worktree=worktree, trusted_alt=fake, policy=alt_broker.l2_policy,
                validate_generation=lambda: True,
                checkpoint=lambda name, data: copied.append((name, data)) or {"ok": True},
            )
            result = self.request(
                broker,
                ["task", "checkpoint", "t", "--name", "report.json", "--source", "report.json"],
                worktree,
            )
            self.assertEqual(result["returncode"], 0)
            self.assertEqual(copied, [("report.json", b'{"ok": true}')])


class TestProcessIdentity(unittest.TestCase):
    def test_kill_helper_identity_cases(self):
        self.assertTrue(dispatch._kill_process_group(None, None))
        with mock.patch.object(dispatch, "_proc_start_time", return_value="live"):
            self.assertFalse(dispatch._kill_process_group(123, None))
            self.assertTrue(dispatch._kill_process_group(123, "old"))
        with mock.patch.object(dispatch, "_proc_start_time", side_effect=["same", "same", None]), \
             mock.patch.object(dispatch.os, "getpgid", return_value=123), \
             mock.patch.object(dispatch.os, "killpg") as kill, \
             mock.patch.object(dispatch.time, "sleep"):
            self.assertTrue(dispatch._kill_process_group(123, "same"))
            kill.assert_called_once_with(123, dispatch.signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
