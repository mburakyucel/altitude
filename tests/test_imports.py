"""Every module altd loads must import; a syntax error in server.py must fail `make test`, not the service start."""
import importlib
import os
import py_compile
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ALTITUDE_HOME", tempfile.mkdtemp(prefix="altitude-imports-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestEverythingImports(unittest.TestCase):
    def test_altitude_package_imports(self):
        for p in sorted((ROOT / "altitude").glob("*.py")):
            with self.subTest(module=p.name):
                importlib.import_module(f"altitude.{p.stem}")

    def test_entry_points_and_hooks_compile(self):
        for p in [ROOT / "bin" / "alt", *sorted((ROOT / "hooks").glob("*.py"))]:
            with self.subTest(file=p.name):
                py_compile.compile(str(p), doraise=True)


if __name__ == "__main__":
    unittest.main()
