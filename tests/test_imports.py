"""Every module altd loads must import; a syntax error in server.py must fail `make test`, not the service start."""
import importlib
import py_compile
import unittest

from tests.support import REPO


class TestEverythingImports(unittest.TestCase):
    def test_altitude_package_imports(self):
        for p in sorted((REPO / "altitude").glob("*.py")):
            with self.subTest(module=p.name):
                importlib.import_module(f"altitude.{p.stem}")

    def test_entry_points_and_hooks_compile(self):
        for p in [REPO / "bin" / "alt", *sorted((REPO / "hooks").glob("*.py"))]:
            with self.subTest(file=p.name):
                py_compile.compile(str(p), doraise=True)


if __name__ == "__main__":
    unittest.main()
