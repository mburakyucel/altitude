"""Fixed forced-command door, real wire framing and filesystem config validation."""
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

from altitude import platform, validation_remote
from scripts import validation_remote as entry


class RemoteEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.template = self.root / "template"
        self.template.mkdir()
        self.file = self.root / "config.json"
        self.config = {"version": 1, "runner_uid": os.getuid(), "state": str(self.state),
                       "template": str(self.template), "python": str(Path(sys.executable).resolve())}
        self.file.write_text(json.dumps(self.config))
        self.broker = validation_remote.Broker(self.state, self.template, ["fixed-worker"])
        self.ident = "a" * 32

    def invoke(self, data, *, original="altitude-validation-v1", args=None, broker=None):
        source, output = io.BytesIO(data), io.BytesIO()
        env = {} if original is None else {"SSH_ORIGINAL_COMMAND": original}
        with (mock.patch.dict(os.environ, env, clear=True),
              mock.patch.object(entry, "load_config", return_value=self.config) as load,
              mock.patch.object(entry, "runtime", return_value=(platform, broker or self.broker)) as runtime):
            code = entry.main(args or ["serve"], source=source, output=output)
        value = platform.validation_frame_read(io.BytesIO(output.getvalue())) if output.getvalue() else None
        return code, value, load, runtime

    def test_real_status_frame_uses_one_run_identity(self):
        code, response, _, _ = self.invoke(platform.validation_frame_encode({"operation": "status", "run_id": self.ident}))
        self.assertEqual(code, 0)
        self.assertEqual(response, {"run_id": self.ident, "status": "absent"})

    def test_other_ssh_commands_refuse_before_config_or_runtime(self):
        for original in (None, "", "altitude-validation-v1 status", "work " + self.ident, "sh", "altitude-validation-v1\n"):
            with self.subTest(original=original):
                code, response, load, runtime = self.invoke(b"", original=original)
                self.assertEqual(code, 0)
                self.assertFalse(response["accepted"])
                self.assertEqual(response["status"], "unavailable")
                load.assert_not_called()
                runtime.assert_not_called()

    def test_malformed_oversized_and_trailing_frames_never_dispatch(self):
        broker = mock.Mock()
        good = platform.validation_frame_encode({"operation": "status", "run_id": self.ident})
        for frame in (b"short", struct.pack("!Q", platform.VALIDATION_FRAME_LIMIT + 1), good + b"trailing"):
            with self.subTest(frame=frame[:8]):
                code, response, _, _ = self.invoke(frame, broker=broker)
                self.assertEqual(code, 0)
                self.assertFalse(response["accepted"])
        broker.dispatch.assert_not_called()

    def test_dispatch_failure_does_not_claim_submission_was_unaccepted(self):
        broker = mock.Mock()
        broker.dispatch.side_effect = RuntimeError("PRIVATE_CONFIG_AND_ENDPOINT")
        code, response, _, _ = self.invoke(platform.validation_frame_encode({"operation": "status", "run_id": self.ident}), broker=broker)
        self.assertEqual(code, 0)
        self.assertNotIn("accepted", response)
        self.assertEqual(response["error"], entry.ERROR)
        self.assertNotIn("PRIVATE", json.dumps(response))

    def test_ssh_cannot_select_internal_worker(self):
        code, response, load, runtime = self.invoke(b"", args=["work", self.ident])
        self.assertEqual(code, 1)
        self.assertIsNone(response)
        load.assert_not_called()
        runtime.assert_not_called()

    def test_revoke_is_fixed_local_only_and_returns_cleanup_receipt(self):
        broker = mock.Mock()
        broker.revoke.return_value = {"status": "revoked", "cleanup": True}
        code, response, _, _ = self.invoke(b"", original=None, args=["revoke"], broker=broker)
        self.assertEqual(code, 0)
        self.assertEqual(response, broker.revoke.return_value)
        broker.revoke.assert_called_once_with()
        code, response, load, _ = self.invoke(b"", args=["revoke"], broker=broker)
        self.assertEqual(code, 1)
        self.assertIsNone(response)
        load.assert_not_called()

    def test_internal_worker_accepts_only_fixed_run_argument_and_no_ssh_environment(self):
        broker = mock.Mock()
        code, response, _, _ = self.invoke(b"", original=None, args=["work", self.ident], broker=broker)
        self.assertEqual(code, 0)
        self.assertIsNone(response)
        broker.work.assert_called_once_with(self.ident)
        for args in (["work", "../escape"], ["work", self.ident, "--config", "elsewhere"], ["shell"]):
            code, _, load, _ = self.invoke(b"", original=None, args=args)
            self.assertEqual(code, 1)
            load.assert_not_called()

    def test_config_accepts_private_precreated_state_with_fixed_schema(self):
        with mock.patch.object(entry, "protected_path") as protected:
            self.assertEqual(entry.load_config(self.file), self.config)
        self.assertIn(mock.call(self.state.parent, directory=True), protected.call_args_list)
        self.assertIn(mock.call(Path(self.config["python"])), protected.call_args_list)

    def test_config_rejects_extra_fields_wrong_uid_and_unsafe_state(self):
        for change in ({"shell": "/bin/sh"}, {"runner_uid": os.getuid() + 1}, {"state": "relative"}, {"version": True}):
            with self.subTest(change=change):
                self.file.write_text(json.dumps({**self.config, **change}))
                with mock.patch.object(entry, "protected_path"), self.assertRaises(ValueError):
                    entry.load_config(self.file)
        self.file.write_text(json.dumps(self.config))
        self.state.chmod(0o755)
        with mock.patch.object(entry, "protected_path"), self.assertRaisesRegex(ValueError, "private"):
            entry.load_config(self.file)

    def test_config_refuses_symlinked_state_or_config(self):
        linked = self.root / "linked-state"
        linked.symlink_to(self.state)
        self.file.write_text(json.dumps({**self.config, "state": str(linked)}))
        with mock.patch.object(entry, "protected_path"), self.assertRaisesRegex(ValueError, "Noncanonical"):
            entry.load_config(self.file)
        linked_file = self.root / "linked-config"
        linked_file.symlink_to(self.file)
        with self.assertRaisesRegex(ValueError, "Noncanonical"):
            entry.protected_path(linked_file)

    def test_unprotected_code_refuses_before_import(self):
        with (mock.patch.object(entry, "protected_path", side_effect=ValueError("unsafe source")),
              mock.patch.object(entry, "sys", mock.Mock(flags=mock.Mock(isolated=1), path=[])),
              mock.patch.object(entry.importlib, "import_module") as imported,
              self.assertRaisesRegex(ValueError, "unsafe source")):
            entry.runtime(self.config)
        imported.assert_not_called()

    def test_runtime_generates_immutable_worker_argv(self):
        fake_platform = mock.Mock()
        fake_platform.validation_host_identity.return_value = {"os": "macos"}
        with (mock.patch.object(entry, "protected_path"),
              mock.patch.object(entry, "sys", mock.Mock(flags=mock.Mock(isolated=1), path=[])),
              mock.patch.object(entry.importlib, "import_module", side_effect=[fake_platform, validation_remote])):
            _, broker = entry.runtime(self.config)
        self.assertEqual(broker.worker, [self.config["python"], "-I", "-B",
                                         str(entry.SOURCE / "scripts" / "validation_remote.py"), "work"])
        fake_platform._validation_standard_account.assert_called_once()

    def test_world_writable_file_and_directory_are_not_setup_owned(self):
        self.file.chmod(0o666)
        with self.assertRaisesRegex(ValueError, "Replaceable"):
            entry.protected_path(self.file)
        self.template.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "Replaceable"):
            entry.protected_path(self.template, directory=True)


if __name__ == "__main__":
    unittest.main()
