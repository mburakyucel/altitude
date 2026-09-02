"""Closed v2 boundary contracts remain strict and dormant until their adoption PRs."""
from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path

from altitude.contracts import (
    ContractError,
    validate_application_command_result,
    validate_operational_projection,
    validate_provider_quota_observation,
    validate_publication_scope,
    validate_task_projection,
    validate_worker_outcome,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = json.loads((ROOT / "schemas" / "fixtures" / "projections.v1.json").read_text())


class TestWorkerOutcome(unittest.TestCase):
    def test_four_closed_variants(self):
        outcomes = FIXTURES["valid"]["worker_outcomes"]
        self.assertEqual([value["kind"] for value in outcomes],
                         ["publish", "complete_no_code", "block", "continue"])
        for value in outcomes:
            with self.subTest(kind=value["kind"]):
                result = validate_worker_outcome(value)
                self.assertEqual(result["kind"], value["kind"])
                self.assertEqual(set(result["observations"]), {
                    "findings", "decisions", "fyis", "follow_up_proposals", "deviations",
                    "usage", "spend", "merge_hold",
                })

    def test_integral_json_version_is_normalized_in_a_copy(self):
        value = copy.deepcopy(FIXTURES["valid"]["worker_outcomes"][1])
        value["version"] = 1.0
        result = validate_worker_outcome(value)
        self.assertIs(type(result["version"]), int)
        self.assertIs(type(value["version"]), float)

    def test_rejects_unknown_version_variant_and_trusted_claims(self):
        valid = FIXTURES["valid"]["worker_outcomes"][0]
        for field, value in (("version", 2), ("kind", "deploy")):
            candidate = {**valid, field: value}
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_worker_outcome(candidate)
        for authority in ("merge_sha", "verification_receipt", "deployment_receipt", "capability_id"):
            with self.subTest(authority=authority), self.assertRaises(ContractError):
                validate_worker_outcome({**valid, authority: "untrusted"})

    def test_observations_are_closed_non_authoritative_and_copy_normalized(self):
        valid = copy.deepcopy(FIXTURES["valid"]["worker_outcomes"][0])
        for name in (
            "worker_outcome_missing_observations", "worker_outcome_extra_authority",
            "worker_outcome_observation_authority", "worker_outcome_human_reply",
        ):
            with self.subTest(name=name), self.assertRaises(ContractError):
                validate_worker_outcome(FIXTURES["invalid"][name])

        for value in FIXTURES["numeric_cases"]["observation_count"]["accepted"]:
            candidate = copy.deepcopy(valid); candidate["observations"]["spend"]["turns"] = value
            result = validate_worker_outcome(candidate)
            self.assertIs(type(result["observations"]["spend"]["turns"]), int)
            self.assertIs(type(candidate["observations"]["spend"]["turns"]), float)
        for value in FIXTURES["numeric_cases"]["observation_count"]["rejected"]:
            candidate = copy.deepcopy(valid); candidate["observations"]["spend"]["turns"] = value
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_worker_outcome(candidate)

        adversarial = (
            ("findings", [{"summary": "x", "severity": "critical", "disposition": "open", "reason": None}]),
            ("decisions", [{"question": "x", "answer": None, "options": [""]}]),
            ("merge_hold", False),
        )
        for field, value in adversarial:
            candidate = copy.deepcopy(valid); candidate["observations"][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_worker_outcome(candidate)
        for cost in (-0.1, float("nan"), float("inf"), True):
            candidate = copy.deepcopy(valid); candidate["observations"]["usage"]["cost_usd"] = cost
            with self.subTest(cost=cost), self.assertRaises(ContractError):
                validate_worker_outcome(candidate)


class TestPublicationScope(unittest.TestCase):
    def test_declared_and_policy_derived_variants(self):
        declared = validate_publication_scope({
            "version": 1, "kind": "paths", "paths": ["altitude/contracts.py", "web/src/data/contracts.ts"],
        })
        self.assertEqual(declared["paths"], ["altitude/contracts.py", "web/src/data/contracts.ts"])
        self.assertEqual(
            validate_publication_scope({"version": 1, "kind": "policy_derived"})["kind"], "policy_derived",
        )

    def test_rejects_non_normalized_duplicate_and_self_broadened_scope(self):
        invalid_paths = ("/absolute", "a/../b", "a//b", "a\\b", "folder/", "./file", "nul\0path")
        for path in invalid_paths:
            with self.subTest(path=path), self.assertRaises(ContractError):
                validate_publication_scope({"version": 1, "kind": "paths", "paths": [path]})
        with self.assertRaises(ContractError):
            validate_publication_scope({"version": 1, "kind": "paths", "paths": ["a", "a"]})
        with self.assertRaises(ContractError):
            validate_publication_scope({"version": 1, "kind": "policy_derived", "paths": ["everything"]})


class TestQuotaObservation(unittest.TestCase):
    def test_unknown_is_an_explicit_eligible_observation(self):
        observation = validate_provider_quota_observation({
            "version": 1.0, "provider": "codex", "freshness": "unknown", "observed_at": None,
            "weekly_remaining_percent": None, "short_remaining_percent": None,
            "availability": "unknown", "retry_at": None,
        })
        self.assertEqual(observation["availability"], "unknown")
        self.assertIs(type(observation["version"]), int)

    def test_failure_and_measurement_invariants_are_closed(self):
        quota_limited = {
            "version": 1, "provider": "claude", "freshness": "fresh",
            "observed_at": "2026-09-02T08:00:00+00:00", "weekly_remaining_percent": 4.5,
            "short_remaining_percent": 0, "availability": "quota_limited",
            "retry_at": "2026-09-02T09:00:00+00:00",
        }
        self.assertEqual(validate_provider_quota_observation(quota_limited)["availability"], "quota_limited")
        invalid = [
            {**quota_limited, "version": 2},
            {**quota_limited, "provider": "future-provider"},
            {**quota_limited, "weekly_remaining_percent": 101},
            {**quota_limited, "retry_at": None},
            {**quota_limited, "hold_id": "not-an-observation"},
            {
                **quota_limited, "freshness": "unknown", "availability": "unknown", "retry_at": None,
            },
        ]
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ContractError):
                validate_provider_quota_observation(candidate)


class TestApplicationCommandResult(unittest.TestCase):
    def test_applied_results_cover_exactly_six_authority_domains(self):
        for domain in ("project", "task", "worker", "condition", "recovery", "deployment"):
            result = validate_application_command_result({
                "version": 1.0, "kind": "applied", "domain": domain, "command_id": f"command-{domain}",
                "transition_id": f"transition-{domain}", "revision": 1.0,
            })
            self.assertEqual(result["domain"], domain)
            self.assertIs(type(result["version"]), int)
            self.assertIs(type(result["revision"]), int)

        for value in FIXTURES["numeric_cases"]["application_revision"]["accepted"]:
            candidate = {"version": 1, "kind": "applied", "domain": "task", "command_id": "c",
                         "transition_id": "t", "revision": value}
            result = validate_application_command_result(candidate)
            self.assertIs(type(result["revision"]), int)
            self.assertIs(type(candidate["revision"]), float)
        for value in FIXTURES["numeric_cases"]["application_revision"]["rejected"]:
            candidate = {"version": 1, "kind": "applied", "domain": "task", "command_id": "c",
                         "transition_id": "t", "revision": value}
            with self.assertRaises(ContractError):
                validate_application_command_result(candidate)

    def test_refusal_is_typed_and_unknown_fields_are_rejected(self):
        value = {
            "version": 1, "kind": "refused", "domain": "task", "command_id": "command-task",
            "code": "stale_authority", "message": "The owner generation changed.", "retryable": False,
        }
        self.assertEqual(validate_application_command_result(value)["kind"], "refused")
        for candidate in (
            {**value, "kind": "pending"},
            {**value, "domain": "audit"},
            {**value, "revision": 7},
            {**value, "capability_id": "private"},
        ):
            with self.assertRaises(ContractError):
                validate_application_command_result(candidate)


class TestProjectionWireFixtures(unittest.TestCase):
    def test_python_accepts_shared_v1_fixtures(self):
        task = validate_task_projection(FIXTURES["valid"]["task"])
        operational = validate_operational_projection(FIXTURES["valid"]["operational"])
        self.assertEqual(task["wire_version"], 1)
        self.assertEqual(operational["wire_version"], 1)

    def test_python_rejects_shared_unknown_versions_and_authority_fields(self):
        validators = {
            "task_unknown_version": validate_task_projection,
            "task_extra_authority": validate_task_projection,
            "operational_unknown_version": validate_operational_projection,
            "operational_extra_authority": validate_operational_projection,
        }
        for name, validator in validators.items():
            with self.subTest(name=name), self.assertRaises(ContractError):
                validator(FIXTURES["invalid"][name])

    def test_integral_json_numbers_match_wire_contract_and_normalize(self):
        for value in FIXTURES["numeric_cases"]["version"]["accepted"]:
            candidate = copy.deepcopy(FIXTURES["valid"]["task"])
            candidate["wire_version"] = value
            result = validate_task_projection(candidate)
            self.assertIs(type(result["wire_version"]), int)
            self.assertIs(type(candidate["wire_version"]), float)
        for value in FIXTURES["numeric_cases"]["version"]["rejected"]:
            candidate = {**FIXTURES["valid"]["task"], "wire_version": value}
            with self.subTest(field="wire_version", value=value), self.assertRaises(ContractError):
                validate_task_projection(candidate)

        publication = {"receipt_id": "receipt-1", "status": "merged", "pr_number": None}
        for value in FIXTURES["numeric_cases"]["publication_pr_number"]["accepted"]:
            candidate = copy.deepcopy(FIXTURES["valid"]["task"])
            candidate["publication"] = {**publication, "pr_number": value}
            result = validate_task_projection(candidate)
            self.assertIs(type(result["publication"]["pr_number"]), int)
            self.assertIs(type(candidate["publication"]["pr_number"]), float)
        for value in FIXTURES["numeric_cases"]["publication_pr_number"]["rejected"]:
            candidate = {**FIXTURES["valid"]["task"], "publication": {**publication, "pr_number": value}}
            with self.subTest(field="publication.pr_number", value=value), self.assertRaises(ContractError):
                validate_task_projection(candidate)

        for value in (float("nan"), float("inf"), float("-inf")):
            candidate = {**FIXTURES["valid"]["task"], "wire_version": value}
            with self.subTest(field="wire_version", value=value), self.assertRaises(ContractError):
                validate_task_projection(candidate)
            candidate = {**FIXTURES["valid"]["task"], "publication": {**publication, "pr_number": value}}
            with self.subTest(field="publication.pr_number", value=value), self.assertRaises(ContractError):
                validate_task_projection(candidate)

    def test_projection_variants_and_nested_fields_are_closed(self):
        for root_field in ("capability_id", "command_id", "transition_id"):
            task = copy.deepcopy(FIXTURES["valid"]["task"])
            task[root_field] = "private"
            with self.subTest(field=root_field), self.assertRaises(ContractError):
                validate_task_projection(task)
        task = copy.deepcopy(FIXTURES["valid"]["task"])
        task["state"] = "reported"
        with self.assertRaises(ContractError):
            validate_task_projection(task)
        operational = copy.deepcopy(FIXTURES["valid"]["operational"])
        operational["recovery"] = {"state": "clear", "episode_id": "private", "reason": None}
        with self.assertRaises(ContractError):
            validate_operational_projection(operational)

    def test_contracts_are_not_imported_by_runtime_paths(self):
        python_import = re.compile(r"(?:from\s+\.contracts\s+import|from\s+\.\s+import[^\n]*\bcontracts\b|import\s+altitude\.contracts)")
        for path in sorted((ROOT / "altitude").glob("*.py")):
            if path.name != "contracts.py":
                self.assertIsNone(python_import.search(path.read_text()), path)

        web_import = re.compile(r"from\s+['\"][^'\"]*contracts['\"]")
        for path in sorted((ROOT / "web" / "src").rglob("*.ts*")):
            if path.name == "contracts.ts" or ".test." in path.name:
                continue
            self.assertIsNone(web_import.search(path.read_text()), path)


if __name__ == "__main__":
    unittest.main()
