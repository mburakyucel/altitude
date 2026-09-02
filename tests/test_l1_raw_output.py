"""Helper result evidence is closed and bounded before it can become a marker."""
import os
import tempfile
import unittest

os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-helper-result-")

from altitude import engines, l1, tasks as T


def _result(**changes):
    result = {"provider_session_id": "thread-1", "status": "complete", "summary": "done",
              "error": None, "patch": None, "findings": [], "usage": {}}
    result.update(changes)
    return result


class TestHelperResultBoundary(unittest.TestCase):
    def test_raw_paths_and_unknown_provider_fields_are_not_part_of_the_contract(self):
        with self.assertRaisesRegex(T.TransitionError, "unknown or missing"):
            l1._result({**_result(), "raw": {"stdout": "/private/path"}})  # noqa: SLF001

    def test_result_evidence_is_bounded_before_atomic_marker_persistence(self):
        with self.assertRaisesRegex(T.TransitionError, "bounded evidence"):
            l1._result(_result(summary="x" * (engines.RAW_CAPTURE_CAP + 1)))  # noqa: SLF001

    def test_nonfinite_or_mutable_input_is_rejected_or_detached(self):
        source = _result(usage={"tokens": 3})
        detached = l1._result(source)  # noqa: SLF001
        source["usage"]["tokens"] = 9
        self.assertEqual(detached["usage"]["tokens"], 3)
        with self.assertRaisesRegex(T.TransitionError, "invalid JSON"):
            l1._result(_result(usage={"cost": float("nan")}))  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
