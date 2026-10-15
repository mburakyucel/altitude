"""The shared engine module reaches native behavior only through one interface that every engine module provides."""
import ast
import unittest

from tests.support import REPO
from altitude import config
from altitude.engines import common

COMMON = REPO / "altitude" / "engines" / "common.py"
SELECTORS = ("implementation", "_configured")
#: Records written before an engine was stored name none; these readers supply the engine that wrote them.
LEGACY_DEFAULTS = {"transcript_engine": "claude", "_record_engine": "codex"}


def _native_names(tree: ast.AST) -> set[str]:
    """Attributes read from `implementation(...)` or from a local bound to it."""
    def selects(value) -> bool:
        return isinstance(value, ast.Call) and getattr(value.func, "id", None) in SELECTORS

    bound = {target.id for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.NamedExpr)) and selects(node.value)
             for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
             if isinstance(target, ast.Name)}
    return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            and (selects(node.value) or isinstance(node.value, ast.Name) and node.value.id in bound)}


class TestEngineInterface(unittest.TestCase):
    def test_every_engine_module_provides_the_interface_the_shared_module_uses(self):
        names = _native_names(ast.parse(COMMON.read_text()))
        self.assertGreater(len(names), 30)
        for engine in config.ENGINES:
            native = common.implementation(engine)
            with self.subTest(engine=engine):
                self.assertEqual(sorted(name for name in names if not hasattr(native, name)), [])

    def test_shared_module_names_no_engine_beyond_its_legacy_record_defaults(self):
        """An engine is dropped by removing its module and registry entry, never by editing shared branches."""
        tree = ast.parse(COMMON.read_text())
        named = {}
        for function in ast.walk(tree):
            if isinstance(function, ast.FunctionDef):
                for node in ast.walk(function):
                    if isinstance(node, ast.Constant) and node.value in config.ENGINES:
                        named.setdefault(function.name, set()).add(node.value)
        self.assertEqual({name: engines.pop() for name, engines in named.items() if len(engines) == 1}, LEGACY_DEFAULTS)
        self.assertTrue(all(len(engines) <= 1 for engines in named.values()))

    def test_unknown_engine_has_no_implementation(self):
        with self.assertRaisesRegex(ValueError, "unknown engine"):
            common.implementation("fixture-engine")

    def test_saved_engine_no_longer_configured_degrades_on_reads(self):
        record = {"type": "item.completed", "item": {"id": "message-1", "type": "agent_message", "text": "Done."}}
        task = {"l2_engine": "fixture-engine", "session_id": "fixture-session"}
        self.assertIsNone(common.public_message_identity("fixture-engine", record))
        self.assertIsNone(common.window_hold("fixture-engine"))
        self.assertIsNone(common.session_context_source("fixture-project", "fixture-task", task))
        self.assertEqual(common.transcript_rows("fixture-engine", record, "/fixture"), [])
        self.assertIsNone(common.background_work("fixture-engine", [record]))


if __name__ == "__main__":
    unittest.main()
