#!/usr/bin/python3
"""Base-trusted unittest entry point. Candidate Makefiles and runners are never consulted."""
from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", choices=("base", "candidate"), required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--tests", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve(strict=True)
    tests = Path(args.tests).resolve(strict=True)
    if not tests.is_relative_to(root) or tests.name != "tests":
        raise SystemExit("trusted runner refused a test directory outside its suite root")
    os.chdir(root)
    sys.path.insert(0, str(root))

    suite = unittest.defaultTestLoader.discover(str(tests))
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    fields = {
        "label": args.label,
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "successful": int(result.wasSuccessful() and result.testsRun > len(result.skipped)),
    }
    print("ALTITUDE_TRUSTED_RESULT " + " ".join(f"{key}={value}" for key, value in fields.items()), flush=True)
    return 0 if fields["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
