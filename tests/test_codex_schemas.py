"""Codex action schemas stay inside the provider's recursive strict-object subset."""
import json
import re
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parent.parent


class TestCodexSchemas(unittest.TestCase):
    def assert_strict_objects(self, node, path="$"):
        if isinstance(node, list):
            for index, item in enumerate(node):
                self.assert_strict_objects(item, f"{path}[{index}]")
            return
        if not isinstance(node, dict):
            return
        kinds = node.get("type")
        is_object = kinds == "object" or isinstance(kinds, list) and "object" in kinds
        if is_object:
            self.assertIs(node.get("additionalProperties"), False, f"{path} must be a closed object")
            properties = node.get("properties") or {}
            self.assertEqual(set(node.get("required") or []), set(properties),
                             f"{path} must require every property")
        for key, value in node.items():
            self.assert_strict_objects(value, f"{path}.{key}")

    def test_action_schemas_are_recursively_strict(self):
        for name in ("l2_action.json", "l3_action.json"):
            with self.subTest(name=name):
                self.assert_strict_objects(json.loads((ROOT / "schemas" / name).read_text()))

    def test_l2_schema_rejects_known_contract_mismatches(self):
        schema = json.loads((ROOT / "schemas" / "l2_action.json").read_text())
        definitions = schema["$defs"]
        path_pattern = definitions["scope"]["anyOf"][0]["properties"]["paths"]["items"]["pattern"]
        self.assertIsNone(re.search(path_pattern, "a/../b"))
        self.assertIsNone(re.search(path_pattern, "./a"))
        self.assertIsNotNone(re.search(path_pattern, "altitude/actions.py"))
        nonempty = definitions["observations"]["properties"]["fyis"]["items"]["pattern"]
        self.assertIsNone(re.search(nonempty, "   "))
        self.assertEqual(
            definitions["observations"]["properties"]["spend"]["properties"]["turns"]["maximum"],
            2**53 - 1,
        )

    def test_l2_provider_schema_matches_worker_outcome_fixtures_and_bounds(self):
        schema = json.loads((ROOT / "schemas" / "l2_action.json").read_text())
        validator = Draft202012Validator(schema)
        fixtures = json.loads((ROOT / "schemas" / "fixtures" / "projections.v1.json").read_text())
        for outcome in fixtures["valid"]["worker_outcomes"]:
            with self.subTest(kind=outcome["kind"]):
                self.assertEqual(list(validator.iter_errors({"message": "update", "outcome": outcome})), [])
        for name, outcome in fixtures["invalid"].items():
            if not name.startswith("worker_outcome_"):
                continue
            with self.subTest(name=name):
                self.assertTrue(list(validator.iter_errors({"message": "update", "outcome": outcome})))

        valid = fixtures["valid"]["worker_outcomes"][-1]
        whitespace_path = json.loads(json.dumps(valid))
        whitespace_path["helper_requests"][0]["scope"] = {
            "version": 1, "kind": "paths", "paths": ["   "]}
        self.assertTrue(list(validator.iter_errors({"message": "update", "outcome": whitespace_path})))
        too_many = json.loads(json.dumps(valid))
        too_many["helper_requests"] = too_many["helper_requests"] * 5
        self.assertTrue(list(validator.iter_errors({"message": "update", "outcome": too_many})))
        claude_helper = json.loads(json.dumps(valid))
        claude_helper["helper_requests"][0]["provider"] = "claude"
        self.assertTrue(list(validator.iter_errors({"message": "update", "outcome": claude_helper})))


if __name__ == "__main__":
    unittest.main()
