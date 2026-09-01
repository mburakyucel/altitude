"""Codex action schemas stay inside the provider's recursive strict-object subset."""
import json
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
