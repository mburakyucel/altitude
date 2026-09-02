"""Phase 2A's DeploymentRecord is strict, durable, truthful, and dormant."""
import hashlib
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-deployment-bootstrap-")

from altitude import deployment, state as S


class TestDeploymentRecord(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="altitude-deployment-")
        self.root = Path(self.temp.name)
        self.checkout = str(self.root / "checkout")
        Path(self.checkout).mkdir()
        self.sha = "a" * 40
        self.auth = {
            "id": "freeze-authorization-1", "actor": "operator",
            "at": "2026-09-02T00:00:00+00:00", "reason": "initialize stopped baseline",
        }

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _hash(value: dict, field: str) -> str:
        unsigned = {key: item for key, item in value.items() if key != field}
        return hashlib.sha256(json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode()).hexdigest()

    def _manifest(self, *, running=True) -> dict:
        manager = ({
            "observed": True, "active_state": "active", "sub_state": "running",
            "main_pid": 4242, "cgroup": {"empty": False, "pids": [4242], "error": None},
            "process": {"pid": 4242, "start_ticks": 99123, "error": None},
        } if running else {
            "observed": True, "active_state": "inactive", "sub_state": "dead",
            "main_pid": 0, "cgroup": {"empty": True, "pids": [], "error": None},
            "process": {"pid": 0, "start_ticks": None, "error": None},
        })
        state_sha, raw_sha = "b" * 64, "c" * 64
        value = {
            "schema_version": deployment.RUNTIME_MANIFEST_SCHEMA,
            "role": "running_install" if running else "stopped_install",
            "prior_kind": "ordinary_running_install" if running else "bootstrap_freeze",
            "build_version": "test", "source_commit": self.sha, "expected_commit": self.sha,
            "identity_errors": [], "valid": True,
            "loaded_source": {
                "checkout": self.checkout, "git_head": self.sha, "dirty": False,
                "commit_comparison": {"exact": True},
            },
            "executed_source": {"captured": running, "valid": True if running else None},
            "installed_checkout": {"path": self.checkout, "git_head": self.sha, "error": None},
            "installation_identity": {
                "checkout_path": self.checkout, "checkout_head": self.sha,
                "source_commit": self.sha,
            },
            "remote_main": {"sha": self.sha},
            "state": {"sha256": state_sha},
            "service_unit": {"name": "altitude.service", "manager": manager},
            "web_bundle": {"sha256": "d" * 64},
            "prior_install_manifest_sha256": None,
            "bootstrap_evidence": None if running else {
                "source_commit": self.sha, "state_sha256": state_sha,
                "raw_evidence_sha256": raw_sha, "process_empty": True,
                "operator_authorization": deepcopy(self.auth),
            },
        }
        value["manifest_sha256"] = self._hash(value, "manifest_sha256")
        return value

    def _initialize(self, manifest=None, **kwargs) -> dict:
        return deployment.initialize_for_migration(
            service_id="altitude", unit="altitude.service", repository_id="owner/altitude",
            checkout=self.checkout, runtime_manifest=manifest or self._manifest(),
            root=self.root, **kwargs,
        )

    def test_running_exact_manifest_is_the_only_source_of_activated_sha(self):
        with mock.patch.object(S, "_fsync_directory", wraps=S._fsync_directory) as fsync_parent:
            record = self._initialize()
        self.assertEqual(record["activated_sha"], self.sha)
        self.assertEqual(record["bootstrap_anchor"], {
            "kind": "loaded_manifest", "sha": self.sha,
            "manifest_sha256": self._manifest()["manifest_sha256"],
        })
        self.assertIsNone(record["legacy_pending"])
        self.assertNotIn("activation_receipt", record)
        self.assertNotIn("deployment_operation", record)
        self.assertEqual(deployment.load("altitude", root=self.root), record)
        fsync_parent.assert_called_with(self.root / "deployments")

    def test_stopped_bootstrap_is_operator_provenance_not_activation(self):
        manifest = self._manifest(running=False)
        record = self._initialize(
            manifest, operator_provenance={
                "evidence_limitation": "loaded process identity was unavailable after the authorized stop",
                "authorization": deepcopy(self.auth),
            })
        self.assertIsNone(record["activated_sha"])
        self.assertEqual(record["bootstrap_anchor"]["kind"], "operator_provenance")
        self.assertEqual(record["bootstrap_anchor"]["sha"], self.sha)
        self.assertEqual(record["bootstrap_anchor"]["frozen_state_sha256"], "b" * 64)
        self.assertEqual(record["bootstrap_anchor"]["raw_freeze_evidence_sha256"], "c" * 64)
        self.assertEqual(record["bootstrap_anchor"]["authorization"], self.auth)
        self.assertFalse(any("receipt" in key for key in record))

    def test_stopped_or_unverifiable_manifest_never_backdates_activation(self):
        stopped = self._manifest(running=False)
        with self.assertRaisesRegex(deployment.DeploymentError, "requires explicit operator"):
            self._initialize(stopped)
        bad = self._manifest()
        bad["service_unit"]["manager"]["sub_state"] = "exited"
        bad["manifest_sha256"] = self._hash(bad, "manifest_sha256")
        with self.assertRaisesRegex(deployment.DeploymentError, "stable active service"):
            self._initialize(bad)
        bad = self._manifest(); bad["valid"] = False; bad["identity_errors"] = ["unknown"]
        bad["manifest_sha256"] = self._hash(bad, "manifest_sha256")
        with self.assertRaisesRegex(deployment.DeploymentError, "does not prove"):
            self._initialize(bad)
        self.assertIsNone(deployment.load("altitude", root=self.root))

    def test_manifest_hash_role_service_repository_and_execution_must_match(self):
        changes = (
            ("hash", lambda row: row.update(manifest_sha256="0" * 64)),
            ("service", lambda row: row["service_unit"].update(name="other.service")),
            ("checkout", lambda row: row["loaded_source"].update(checkout="/other")),
            ("executed", lambda row: row["executed_source"].update(valid=False)),
        )
        for label, change in changes:
            manifest = self._manifest(); change(manifest)
            if label != "hash":
                manifest["manifest_sha256"] = self._hash(manifest, "manifest_sha256")
            with self.subTest(label=label), self.assertRaises(deployment.DeploymentError):
                self._initialize(manifest)
        self.assertFalse((self.root / "deployments" / "altitude.json").exists())

    def test_same_import_is_byte_idempotent_and_conflict_refuses_unchanged(self):
        first = self._initialize()
        path = deployment.record_path("altitude", root=self.root)
        before = path.read_bytes()
        self.assertEqual(self._initialize(), first)
        self.assertEqual(path.read_bytes(), before)
        other = self._manifest(); other["source_commit"] = other["expected_commit"] = "e" * 40
        other["loaded_source"]["git_head"] = "e" * 40
        other["installation_identity"]["source_commit"] = "e" * 40
        other["installation_identity"]["checkout_head"] = "e" * 40
        other["installed_checkout"]["git_head"] = "e" * 40
        other["manifest_sha256"] = self._hash(other, "manifest_sha256")
        with self.assertRaisesRegex(deployment.DeploymentConflict, "conflicting"):
            self._initialize(other)
        self.assertEqual(path.read_bytes(), before)

    def test_strict_record_rejects_unknown_missing_or_contradictory_fields(self):
        record = self._initialize()
        cases = []
        unknown = deepcopy(record); unknown["activation_receipts"] = []
        cases.append(unknown)
        missing = deepcopy(record); missing.pop("bootstrap_anchor")
        cases.append(missing)
        contradiction = deepcopy(record); contradiction["activated_sha"] = "f" * 40
        contradiction["record_sha256"] = self._hash(contradiction, "record_sha256")
        cases.append(contradiction)
        pending = deepcopy(record); pending["legacy_pending"] = {
            "status": "recognized", "evidence_sha256": "1" * 64,
            "head": "e" * 40, "since": "not-a-time", "files": ["../unsafe"],
            "blocker": deployment.LEGACY_PENDING_BLOCKER,
        }
        pending["record_sha256"] = self._hash(pending, "record_sha256")
        cases.append(pending)
        for value in cases:
            with self.assertRaises(deployment.DeploymentError):
                deployment.validate_record(value)

    def test_legacy_pending_is_bounded_blocker_not_truth_or_fence(self):
        raw = json.dumps({
            "since": "2026-09-02T00:00:00+00:00", "head": "e" * 40,
            "files": ["altitude/server.py", "hooks/guard.py"],
        }, sort_keys=True).encode()
        record = self._initialize(legacy_pending_bytes=raw)
        pending = record["legacy_pending"]
        self.assertEqual(pending["status"], "recognized")
        self.assertEqual(pending["blocker"], deployment.LEGACY_PENDING_BLOCKER)
        self.assertEqual(pending["head"], "e" * 40)
        self.assertEqual(record["activated_sha"], self.sha)
        self.assertNotEqual(pending["head"], record["activated_sha"])
        self.assertNotIn("qualified", pending)
        self.assertNotIn("launch", pending)

    def test_unknown_legacy_marker_is_hash_only_and_still_blocks(self):
        raw = b'{"future": {"unbounded": "detail"}}\n'
        record = self._initialize(legacy_pending_bytes=raw)
        self.assertEqual(record["legacy_pending"], {
            "status": "unrecognized", "evidence_sha256": hashlib.sha256(raw).hexdigest(),
            "error": "unrecognized_shape", "blocker": deployment.LEGACY_PENDING_BLOCKER,
        })
        self.assertNotIn("future", json.dumps(record))
        with self.assertRaisesRegex(deployment.DeploymentError, "at most"):
            self._initialize(legacy_pending_bytes=b"x" * 65_537)

    def test_operator_anchor_is_bound_to_exact_freeze_authorization(self):
        manifest = self._manifest(running=False)
        declaration = {
            "evidence_limitation": "running identity unavailable after stop",
            "authorization": deepcopy(self.auth),
        }
        for label, mutate in (
            ("state", lambda row: row["state"].update(sha256="d" * 64)),
            ("source", lambda row: row["bootstrap_evidence"].update(source_commit="e" * 40)),
            ("authorization", lambda row: row["bootstrap_evidence"]["operator_authorization"].update(id="other")),
            ("not empty", lambda row: row["service_unit"]["manager"]["cgroup"].update(empty=False)),
        ):
            value = deepcopy(manifest); mutate(value)
            value["manifest_sha256"] = self._hash(value, "manifest_sha256")
            with self.subTest(label=label), self.assertRaises(deployment.DeploymentError):
                self._initialize(value, operator_provenance=declaration)

    def test_inputs_are_not_mutated_and_corrupt_existing_record_fails_closed(self):
        manifest, declaration = self._manifest(running=False), {
            "evidence_limitation": "running identity unavailable after stop",
            "authorization": deepcopy(self.auth),
        }
        before_manifest, before_declaration = deepcopy(manifest), deepcopy(declaration)
        self._initialize(manifest, operator_provenance=declaration)
        self.assertEqual(manifest, before_manifest)
        self.assertEqual(declaration, before_declaration)
        path = deployment.record_path("altitude", root=self.root)
        path.write_text("{}\n")
        with self.assertRaises(deployment.DeploymentError):
            self._initialize(manifest, operator_provenance=declaration)

    def test_path_and_embedded_service_identity_are_exact(self):
        with self.assertRaisesRegex(deployment.DeploymentError, "normalized absolute"):
            deployment.initialize_for_migration(
                service_id="altitude", unit="altitude.service",
                repository_id="owner/altitude", checkout=f"{self.checkout}/../checkout",
                runtime_manifest=self._manifest(), root=self.root,
            )
        record = self._initialize()
        record["service"]["id"] = "other"
        record["record_sha256"] = self._hash(record, "record_sha256")
        S.write_json(deployment.record_path("altitude", root=self.root), record)
        with self.assertRaisesRegex(deployment.DeploymentError, "differs from its path"):
            deployment.load("altitude", root=self.root)

    def test_no_runtime_consumer_or_activated_sha_writer_exists(self):
        repo = Path(__file__).resolve().parent.parent
        consumers = []
        for path in [*(repo / "altitude").glob("*.py"), repo / "bin" / "alt",
                     *(repo / "scripts").glob("*.py")]:
            if path.name == "deployment.py":
                continue
            text = path.read_text()
            if "altitude.deployment" in text or "import deployment" in text or "activated_sha" in text:
                consumers.append(path.relative_to(repo).as_posix())
        self.assertEqual(consumers, [])
        source = (repo / "altitude" / "deployment.py").read_text()
        self.assertEqual(source.count("S.write_json("), 1)
        self.assertNotIn("systemctl", source)
        self.assertNotIn("subprocess", source)


if __name__ == "__main__":
    unittest.main()
