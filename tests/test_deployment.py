"""Phase 2B's dormant DeploymentRecord is strict, durable, and replay-safe."""
import hashlib
import json
import os
import subprocess
import sys
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

    def _contribute(self, sha=None, receipt="publication-1", observed="2026-09-02T01:00:00+00:00") -> dict:
        return deployment.append_contribution(
            "altitude", publication_receipt_id=receipt,
            publication_receipt_sha256=hashlib.sha256(receipt.encode()).hexdigest(),
            merge_sha=sha or "e" * 40, observed_at=observed, root=self.root,
        )

    def _qualify(self, contribution_id, *, status="passed", count=0, held=False,
                 supersedes=None, observed="2026-09-02T02:00:00+00:00") -> dict:
        contribution = next(item for item in deployment.load("altitude", root=self.root)["contributions"]
                            if item["id"] == contribution_id)
        return deployment.append_qualification(
            "altitude", contribution_id=contribution_id, observed_at=observed,
            main_check_status=status, main_check_head_sha=contribution["merge_sha"],
            main_check_evidence_sha256="1" * 64,
            open_findings_count=count, open_findings_evidence_sha256="2" * 64,
            merge_hold=held, merge_hold_reason="operator review" if held else None,
            supersedes=supersedes or [], root=self.root,
        )

    def _write_v1(self, record: dict) -> dict:
        v1 = {key: deepcopy(value) for key, value in record.items()
              if key in deployment._V1_RECORD_FIELDS and key != "record_sha256"}  # noqa: SLF001
        v1["schema_version"] = deployment.SCHEMA_V1
        v1["record_sha256"] = self._hash(v1, "record_sha256")
        S.write_json(deployment.record_path("altitude", root=self.root), v1)
        return v1

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

    def test_deployment_module_has_only_its_four_closed_writers_and_no_effect_adapter(self):
        repo = Path(__file__).resolve().parent.parent
        source = (repo / "altitude" / "deployment.py").read_text()
        self.assertEqual(source.count("S.write_json("), 4)
        self.assertNotIn("systemctl", source)
        self.assertNotIn("subprocess", source)

    def test_merge_is_not_qualification_and_each_blocker_is_evidence_derived(self):
        self._initialize()
        record = self._contribute()
        contribution = record["contributions"][0]
        pending = deployment.evaluate_eligibility(
            "altitude", candidate_sha="e" * 40,
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )
        self.assertFalse(pending["eligible"])
        self.assertEqual(pending["blockers"][0]["kind"], "unqualified_contribution")

        for index, values in enumerate((("failed", 0, False), ("passed", 1, False),
                                        ("passed", 0, True))):
            record = self._qualify(
                contribution["id"], status=values[0], count=values[1], held=values[2],
                observed=f"2026-09-02T02:0{index}:00+00:00",
            )
            self.assertEqual(record["qualifications"][-1]["decision"], "blocked")
        result = deployment.evaluate_eligibility(
            "altitude", candidate_sha="e" * 40,
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )
        self.assertFalse(result["eligible"])

    def test_exact_successful_qualification_makes_one_contribution_eligible(self):
        self._initialize(); record = self._contribute()
        record = self._qualify(record["contributions"][0]["id"])
        result = deployment.evaluate_eligibility(
            "altitude", candidate_sha="e" * 40,
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["covered"], [{
            "sha": "e" * 40, "kind": "qualified_contribution",
            "reference": record["qualifications"][0]["id"],
        }])

    def test_contribution_replay_key_ignores_retry_time_but_rejects_changed_receipt(self):
        self._initialize(); first = self._contribute()
        replay = self._contribute(observed="2026-09-02T09:00:00+00:00")
        self.assertEqual(replay, first)
        self.assertEqual(len(replay["contributions"]), 1)
        before = deployment.record_path("altitude", root=self.root).read_bytes()
        with self.assertRaises(deployment.DeploymentConflict):
            deployment.append_contribution(
                "altitude", publication_receipt_id="publication-1",
                publication_receipt_sha256="9" * 64, merge_sha="e" * 40,
                observed_at="2026-09-02T10:00:00+00:00", root=self.root,
            )
        self.assertEqual(deployment.record_path("altitude", root=self.root).read_bytes(), before)

    def test_reused_receipt_id_and_rewritten_merge_ancestry_fail_closed(self):
        self._initialize(); record = self._contribute()
        contribution = record["contributions"][0]
        self._qualify(contribution["id"])
        before = deployment.record_path("altitude", root=self.root).read_bytes()
        with self.assertRaisesRegex(deployment.DeploymentError, "receipt id"):
            deployment.append_contribution(
                "altitude", publication_receipt_id="publication-1",
                publication_receipt_sha256="9" * 64, merge_sha="f" * 40,
                observed_at="2026-09-02T03:00:00+00:00", root=self.root,
            )
        self.assertEqual(deployment.record_path("altitude", root=self.root).read_bytes(), before)
        rewritten = deployment.evaluate_eligibility(
            "altitude", candidate_sha="f" * 40,
            first_parent_chain=[self.sha, "f" * 40], root=self.root,
        )
        self.assertFalse(rewritten["eligible"])
        self.assertEqual(rewritten["blockers"][0]["kind"],
                         "contribution_not_in_candidate_ancestry")

    def test_corrective_contribution_explicitly_supersedes_a_failed_receipt(self):
        self._initialize(); record = self._contribute()
        first = record["contributions"][0]
        record = self._qualify(first["id"], status="failed")
        failed = record["qualifications"][0]
        record = self._contribute(
            sha="f" * 40, receipt="publication-correction",
            observed="2026-09-02T03:00:00+00:00",
        )
        correction = record["contributions"][1]
        self._qualify(correction["id"], supersedes=[failed["id"]],
                      observed="2026-09-02T04:00:00+00:00")
        result = deployment.evaluate_eligibility(
            "altitude", candidate_sha="f" * 40,
            first_parent_chain=[self.sha, "e" * 40, "f" * 40], root=self.root,
        )
        self.assertTrue(result["eligible"])
        self.assertEqual([row["kind"] for row in result["covered"]],
                         ["corrected_contribution", "qualified_contribution"])

    def test_an_earlier_commit_cannot_supersede_a_later_failed_contribution(self):
        self._initialize()
        later = self._contribute(sha="f" * 40, receipt="publication-later")
        later_contribution = later["contributions"][0]
        failed = self._qualify(later_contribution["id"], status="failed")["qualifications"][0]
        earlier = self._contribute(
            sha="e" * 40, receipt="publication-earlier",
            observed="2026-09-02T03:00:00+00:00",
        )
        earlier_contribution = earlier["contributions"][1]
        self._qualify(earlier_contribution["id"], supersedes=[failed["id"]],
                      observed="2026-09-02T04:00:00+00:00")
        result = deployment.evaluate_eligibility(
            "altitude", candidate_sha="f" * 40,
            first_parent_chain=[self.sha, "e" * 40, "f" * 40], root=self.root,
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["blockers"][-1]["kind"], "unqualified_contribution")
        repaired = self._contribute(
            sha="d" * 40, receipt="publication-genuine-correction",
            observed="2026-09-02T05:00:00+00:00",
        )
        genuine = repaired["contributions"][2]
        self._qualify(genuine["id"], supersedes=[failed["id"]],
                      observed="2026-09-02T06:00:00+00:00")
        repaired_result = deployment.evaluate_eligibility(
            "altitude", candidate_sha="d" * 40,
            first_parent_chain=[self.sha, "e" * 40, "f" * 40, "d" * 40], root=self.root,
        )
        self.assertTrue(repaired_result["eligible"])

    def test_phase2b_rejects_nonempty_future_activation_high_water(self):
        self._initialize(); record = self._contribute()
        record["satisfied_contributions"] = [record["contributions"][0]["id"]]
        record["record_sha256"] = self._hash(record, "record_sha256")
        with self.assertRaisesRegex(deployment.DeploymentError, "must be empty"):
            deployment.validate_record(record)

    def test_unknown_or_reused_supersession_refuses_without_changing_record(self):
        self._initialize(); record = self._contribute()
        contribution = record["contributions"][0]
        before = deployment.record_path("altitude", root=self.root).read_bytes()
        with self.assertRaises(deployment.DeploymentError):
            self._qualify(contribution["id"], supersedes=["9" * 64])
        self.assertEqual(deployment.record_path("altitude", root=self.root).read_bytes(), before)

    def test_qualification_must_check_its_exact_merge_and_supersede_only_a_blocker(self):
        self._initialize(); record = self._contribute()
        contribution = record["contributions"][0]
        before = deployment.record_path("altitude", root=self.root).read_bytes()
        with self.assertRaisesRegex(deployment.DeploymentError, "merge sha"):
            deployment.append_qualification(
                "altitude", contribution_id=contribution["id"],
                observed_at="2026-09-02T02:00:00+00:00", main_check_status="passed",
                main_check_head_sha="f" * 40, main_check_evidence_sha256="1" * 64,
                open_findings_count=0, open_findings_evidence_sha256="2" * 64,
                merge_hold=False, root=self.root,
            )
        self.assertEqual(deployment.record_path("altitude", root=self.root).read_bytes(), before)
        record = self._qualify(contribution["id"])
        eligible = record["qualifications"][0]
        with self.assertRaisesRegex(deployment.DeploymentError, "supersedes"):
            self._qualify(contribution["id"], supersedes=[eligible["id"]],
                          observed="2026-09-02T03:00:00+00:00")

    def test_same_contribution_and_blocked_qualification_cannot_supersede(self):
        self._initialize(); record = self._contribute()
        contribution = record["contributions"][0]
        failed = self._qualify(contribution["id"], status="failed")["qualifications"][0]
        with self.assertRaisesRegex(deployment.DeploymentError, "supersedes"):
            self._qualify(contribution["id"], supersedes=[failed["id"]],
                          observed="2026-09-02T03:00:00+00:00")
        record = self._contribute(
            sha="f" * 40, receipt="blocked-correction",
            observed="2026-09-02T04:00:00+00:00",
        )
        correction = record["contributions"][1]
        with self.assertRaisesRegex(deployment.DeploymentError, "eligible corrective"):
            self._qualify(correction["id"], status="failed", supersedes=[failed["id"]],
                          observed="2026-09-02T05:00:00+00:00")

    def test_operator_provenance_covers_only_the_exact_recorded_chain(self):
        self._initialize()
        auth = {**self.auth, "id": "coverage-authorization", "reason": "cover external commits"}
        deployment.append_operator_coverage(
            "altitude", start_exclusive=self.sha, commits=["e" * 40, "f" * 40],
            observed_at="2026-09-02T03:00:00+00:00", reason="externally authored reviewed range",
            authorization=auth, root=self.root,
        )
        exact = deployment.evaluate_eligibility(
            "altitude", candidate_sha="f" * 40,
            first_parent_chain=[self.sha, "e" * 40, "f" * 40], root=self.root,
        )
        self.assertTrue(exact["eligible"])
        wrong = deployment.evaluate_eligibility(
            "altitude", candidate_sha="d" * 40,
            first_parent_chain=[self.sha, "e" * 40, "d" * 40], root=self.root,
        )
        self.assertFalse(wrong["eligible"])
        self.assertEqual(wrong["blockers"][-1]["kind"], "uncovered_commit")

    def test_known_failed_contribution_cannot_be_hidden_by_operator_coverage(self):
        self._initialize(); record = self._contribute()
        contribution = record["contributions"][0]
        self._qualify(contribution["id"], status="failed")
        deployment.append_operator_coverage(
            "altitude", start_exclusive=self.sha, commits=["e" * 40],
            observed_at="2026-09-02T03:00:00+00:00", reason="external range",
            authorization={**self.auth, "id": "coverage-auth"}, root=self.root,
        )
        result = deployment.evaluate_eligibility(
            "altitude", candidate_sha="e" * 40,
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["blockers"][0]["kind"], "unqualified_contribution")

    def test_legacy_evidence_blocks_until_the_exact_evidence_is_reconciled(self):
        raw = json.dumps({
            "since": "2026-09-02T00:00:00+00:00", "head": "e" * 40,
            "files": ["altitude/server.py"],
        }, sort_keys=True).encode()
        self._initialize(legacy_pending_bytes=raw)
        deployment.append_operator_coverage(
            "altitude", start_exclusive=self.sha, commits=["e" * 40],
            observed_at="2026-09-02T03:00:00+00:00", reason="external range",
            authorization={**self.auth, "id": "coverage-auth"}, root=self.root,
        )
        held = deployment.evaluate_eligibility(
            "altitude", candidate_sha="e" * 40,
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )
        self.assertEqual(held["blockers"][-1]["kind"], deployment.LEGACY_PENDING_BLOCKER)
        deployment.reconcile_legacy_pending(
            "altitude", observed_at="2026-09-02T04:00:00+00:00",
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )
        self.assertTrue(deployment.evaluate_eligibility(
            "altitude", candidate_sha="e" * 40,
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )["eligible"])

    def test_tampered_legacy_coverage_digest_blocks_even_with_new_record_hash(self):
        raw = json.dumps({
            "since": "2026-09-02T00:00:00+00:00", "head": "e" * 40,
            "files": ["altitude/server.py"],
        }, sort_keys=True).encode()
        self._initialize(legacy_pending_bytes=raw)
        deployment.append_operator_coverage(
            "altitude", start_exclusive=self.sha, commits=["e" * 40],
            observed_at="2026-09-02T03:00:00+00:00", reason="external range",
            authorization={**self.auth, "id": "coverage-auth"}, root=self.root,
        )
        deployment.reconcile_legacy_pending(
            "altitude", observed_at="2026-09-02T04:00:00+00:00",
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )
        record = deployment.load("altitude", root=self.root)
        record["legacy_reconciliation"]["coverage_sha256"] = "9" * 64
        record["record_sha256"] = self._hash(record, "record_sha256")
        S.write_json(deployment.record_path("altitude", root=self.root), record)
        with self.assertRaisesRegex(deployment.DeploymentError, "immutable facts"):
            deployment.evaluate_eligibility(
                "altitude", candidate_sha="e" * 40,
                first_parent_chain=[self.sha, "e" * 40], root=self.root,
            )

    def test_later_stronger_prefix_evidence_preserves_legacy_reconciliation(self):
        raw = json.dumps({
            "since": "2026-09-02T00:00:00+00:00", "head": "e" * 40,
            "files": ["altitude/server.py"],
        }, sort_keys=True).encode()
        self._initialize(legacy_pending_bytes=raw)
        deployment.append_operator_coverage(
            "altitude", start_exclusive=self.sha, commits=["e" * 40],
            observed_at="2026-09-02T03:00:00+00:00", reason="external range",
            authorization={**self.auth, "id": "coverage-auth"}, root=self.root,
        )
        original = deployment.reconcile_legacy_pending(
            "altitude", observed_at="2026-09-02T04:00:00+00:00",
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )["legacy_reconciliation"]
        contribution = self._contribute()["contributions"][0]
        self._qualify(contribution["id"])
        self.assertTrue(deployment.evaluate_eligibility(
            "altitude", candidate_sha="e" * 40,
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )["eligible"])
        replay = deployment.reconcile_legacy_pending(
            "altitude", observed_at="2026-09-02T05:00:00+00:00",
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )
        self.assertEqual(replay["legacy_reconciliation"], original)

    def test_later_correction_can_repair_a_blocked_contribution_in_legacy_prefix(self):
        raw = json.dumps({
            "since": "2026-09-02T00:00:00+00:00", "head": "e" * 40,
            "files": ["altitude/server.py"],
        }, sort_keys=True).encode()
        self._initialize(legacy_pending_bytes=raw)
        deployment.append_operator_coverage(
            "altitude", start_exclusive=self.sha, commits=["e" * 40],
            observed_at="2026-09-02T03:00:00+00:00", reason="external range",
            authorization={**self.auth, "id": "coverage-auth"}, root=self.root,
        )
        original = deployment.reconcile_legacy_pending(
            "altitude", observed_at="2026-09-02T04:00:00+00:00",
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )["legacy_reconciliation"]
        blocked = self._contribute()["contributions"][0]
        failed = self._qualify(blocked["id"], status="failed")["qualifications"][0]
        correction = self._contribute(
            sha="f" * 40, receipt="publication-prefix-correction",
            observed="2026-09-02T05:00:00+00:00",
        )["contributions"][1]
        self._qualify(correction["id"], supersedes=[failed["id"]],
                      observed="2026-09-02T06:00:00+00:00")
        result = deployment.evaluate_eligibility(
            "altitude", candidate_sha="f" * 40,
            first_parent_chain=[self.sha, "e" * 40, "f" * 40], root=self.root,
        )
        self.assertTrue(result["eligible"])
        replay = deployment.reconcile_legacy_pending(
            "altitude", observed_at="2026-09-02T07:00:00+00:00",
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )
        self.assertEqual(replay["legacy_reconciliation"], original)

    def test_legacy_reconciliation_rejects_unrecognized_tampered_or_uncovered_evidence(self):
        self._initialize(legacy_pending_bytes=b'{"future":true}\n')
        with self.assertRaisesRegex(deployment.DeploymentError, "unrecognized"):
            deployment.reconcile_legacy_pending(
                "altitude", observed_at="2026-09-02T04:00:00+00:00",
                first_parent_chain=[self.sha], root=self.root,
            )
        self.temp.cleanup(); self.temp = tempfile.TemporaryDirectory(prefix="altitude-deployment-")
        self.root = Path(self.temp.name); self.checkout = str(self.root / "checkout"); Path(self.checkout).mkdir()
        raw = json.dumps({"since": "2026-09-02T00:00:00+00:00", "head": "e" * 40,
                          "files": ["altitude/server.py"]}, sort_keys=True).encode()
        self._initialize(legacy_pending_bytes=raw)
        with self.assertRaisesRegex(deployment.DeploymentError, "not fully covered"):
            deployment.reconcile_legacy_pending(
                "altitude", observed_at="2026-09-02T04:00:00+00:00",
                first_parent_chain=[self.sha, "e" * 40], root=self.root,
            )
        with self.assertRaises(deployment.DeploymentError):
            deployment.reconcile_legacy_pending(
                "altitude", observed_at="2026-09-02T04:00:00+00:00",
                first_parent_chain=[self.sha, "f" * 40], root=self.root,
            )

    def test_phase2a_v1_upgrade_is_locked_idempotent_and_preserves_baseline(self):
        raw = json.dumps({
            "since": "2026-09-02T00:00:00+00:00", "head": "e" * 40,
            "files": ["altitude/server.py"],
        }, sort_keys=True).encode()
        record = self._initialize(legacy_pending_bytes=raw)
        v1 = self._write_v1(record)
        path = deployment.record_path("altitude", root=self.root)
        with self.assertRaises(deployment.DeploymentError):
            deployment.load("altitude", root=self.root)
        first = deployment.upgrade_v1_for_migration("altitude", root=self.root)
        self.assertEqual(first["bootstrap_anchor"], v1["bootstrap_anchor"])
        self.assertEqual(first["legacy_pending"], v1["legacy_pending"])
        self.assertEqual(first["contributions"], [])
        before = path.read_bytes()
        self.assertEqual(deployment.upgrade_v1_for_migration("altitude", root=self.root), first)
        self.assertEqual(path.read_bytes(), before)

    def test_v1_upgrade_is_cross_process_single_writer(self):
        v1 = self._write_v1(self._initialize())
        code = (
            "from pathlib import Path; from altitude import deployment; "
            f"deployment.upgrade_v1_for_migration('altitude',root=Path({str(self.root)!r}))"
        )
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
        children = [subprocess.Popen([sys.executable, "-c", code], env=env) for _ in range(2)]
        self.assertEqual([child.wait(timeout=10) for child in children], [0, 0])
        upgraded = deployment.load("altitude", root=self.root)
        self.assertEqual(upgraded["bootstrap_anchor"], v1["bootstrap_anchor"])
        self.assertEqual(upgraded["legacy_pending"], v1["legacy_pending"])

    def test_v1_upgrade_failure_before_atomic_replace_preserves_v1(self):
        v1 = self._write_v1(self._initialize())
        path = deployment.record_path("altitude", root=self.root)
        before = path.read_bytes()
        with mock.patch.object(S, "write_json", side_effect=RuntimeError("simulated crash")):
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                deployment.upgrade_v1_for_migration("altitude", root=self.root)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(deployment.validate_v1_record(json.loads(before)), v1)

    def test_two_process_replay_appends_one_contribution(self):
        self._initialize()
        code = (
            "from pathlib import Path; from altitude import deployment; "
            f"deployment.append_contribution('altitude',publication_receipt_id='p',"
            f"publication_receipt_sha256='{'1' * 64}',merge_sha='{'e' * 40}',"
            f"observed_at='2026-09-02T01:00:00+00:00',root=Path({str(self.root)!r}))"
        )
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
        children = [subprocess.Popen([sys.executable, "-c", code], env=env) for _ in range(2)]
        self.assertEqual([child.wait(timeout=10) for child in children], [0, 0])
        self.assertEqual(len(deployment.load("altitude", root=self.root)["contributions"]), 1)

    def test_two_process_legacy_replay_ignores_retry_time(self):
        raw = json.dumps({
            "since": "2026-09-02T00:00:00+00:00", "head": "e" * 40,
            "files": ["altitude/server.py"],
        }, sort_keys=True).encode()
        self._initialize(legacy_pending_bytes=raw)
        deployment.append_operator_coverage(
            "altitude", start_exclusive=self.sha, commits=["e" * 40],
            observed_at="2026-09-02T03:00:00+00:00", reason="external range",
            authorization={**self.auth, "id": "coverage-auth"}, root=self.root,
        )
        prefix = (
            "from pathlib import Path; from altitude import deployment; "
            "deployment.reconcile_legacy_pending('altitude',"
        )
        suffix = (
            f",first_parent_chain=['{self.sha}','{'e' * 40}'],"
            f"root=Path({str(self.root)!r}))"
        )
        codes = [
            prefix + f"observed_at='2026-09-02T04:0{minute}:00+00:00'" + suffix
            for minute in range(2)
        ]
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
        children = [subprocess.Popen([sys.executable, "-c", code], env=env) for code in codes]
        self.assertEqual([child.wait(timeout=10) for child in children], [0, 0])
        record = deployment.load("altitude", root=self.root)
        self.assertIsNotNone(record["legacy_reconciliation"])
        self.assertTrue(deployment.evaluate_eligibility(
            "altitude", candidate_sha="e" * 40,
            first_parent_chain=[self.sha, "e" * 40], root=self.root,
        )["eligible"])


if __name__ == "__main__":
    unittest.main()
