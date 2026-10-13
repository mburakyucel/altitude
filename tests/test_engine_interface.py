"""The shared engine module reaches native behavior only through one interface that every engine module provides."""
import ast
import unittest

from tests.support import REPO
from altitude import config
from altitude.engines import common

COMMON = REPO / "altitude" / "engines" / "common.py"


def _native_names(tree: ast.AST) -> set[str]:
    """Attributes read from `implementation(...)` or from a local bound to it."""
    bound = {target.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
             and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", None) == "implementation"
             for target in node.targets if isinstance(target, ast.Name)}
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        if isinstance(value, ast.IfExp):
            value = value.body
        if (isinstance(value, ast.Call) and getattr(value.func, "id", None) == "implementation"
                or isinstance(value, ast.Name) and value.id in bound):
            names.add(node.attr)
    return names


class TestEngineInterface(unittest.TestCase):
    def test_every_engine_module_provides_the_interface_the_shared_module_uses(self):
        names = _native_names(ast.parse(COMMON.read_text()))
        self.assertGreater(len(names), 30)
        for engine in config.ENGINES:
            native = common.implementation(engine)
            with self.subTest(engine=engine):
                self.assertEqual(sorted(name for name in names if not hasattr(native, name)), [])

    def test_shared_module_selects_no_engine_by_name(self):
        """An engine is dropped by removing its module and registry entry, never by editing shared branches."""
        tree = ast.parse(COMMON.read_text())
        named = [ast.unparse(node) for node in ast.walk(tree)
                 if isinstance(node, ast.Compare) and any(
                     isinstance(side, ast.Constant) and side.value in config.ENGINES
                     for side in (node.left, *node.comparators))]
        literal = [ast.unparse(node) for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "implementation"
                   and any(isinstance(arg, ast.Constant) or isinstance(arg, ast.IfExp) for arg in node.args)]
        self.assertEqual((named, literal), ([], []))

    def test_unknown_engine_has_no_implementation(self):
        with self.assertRaisesRegex(ValueError, "unknown engine"):
            common.implementation("fixture-engine")


if __name__ == "__main__":
    unittest.main()
