"""Complete fictional text outputs and spans, including malformed forms and finite long-input probes."""
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, incidents, platform, tasks

CORPUS = json.loads((Path(__file__).parent / "fixtures/text-parity.json").read_text())


class TestTextParity(AltitudeCase):
    def test_complete_dilemma_outputs_and_marker_spans(self):
        for case in CORPUS["dilemmas"]:
            with self.subTest(text=case["text"]):
                self.assertEqual(tasks.parse_dilemma(case["text"]), case["output"])
                self.assertEqual([list(mark) for mark in tasks._option_marks(case["text"])], case["marks"])
                inline = tasks._inline_recommendation(case["text"])
                self.assertEqual(list(inline) if inline else None, case["inline"])
                self.assertEqual(tasks._prose_recommendation(case["text"]), case["key"])

    def test_serial_spans_redaction_and_publication_rejection_share_recognition(self):
        with mock.patch.object(config, "operator_name", return_value=None), \
             mock.patch.object(config, "load_projects", return_value={}), \
             mock.patch.object(platform, "local_names", return_value={}):
            for case in CORPUS["serials"]:
                with self.subTest(text=case["text"]):
                    self.assertEqual([list(match) for match in incidents._serials(case["text"])], case["matches"])
                    self.assertEqual(incidents._redact_serials(case["text"]), case["redacted"])
                    self.assertEqual(incidents.sanitize(case["text"]), case["redacted"])
                    if case["matches"]:
                        with self.assertRaisesRegex(ValueError, "serial numbers"):
                            incidents.check_public(case["text"])
                    else:
                        incidents.check_public(case["text"])
                    incidents.check_public(case["redacted"])

    def test_credential_redaction_precedes_serial_recognition(self):
        with mock.patch.object(config, "operator_name", return_value=None), \
             mock.patch.object(config, "load_projects", return_value={}), \
             mock.patch.object(platform, "local_names", return_value={}):
            self.assertEqual(incidents.sanitize("serial token=FICTION123456789"), "serial [REDACTED]")

    def test_failure_summary_outputs_and_prefix_spans(self):
        for case in CORPUS["failures"]:
            with self.subTest(text=case["text"]):
                self.assertEqual(incidents.failure_line(case["text"]), case["output"])
                self.assertEqual(incidents._JSON_START.split(case["text"], 1), case["split"])
                self.assertEqual([list(match.span()) for match in incidents._JSON_START.finditer(case["text"])],
                                 case["spans"])

    def test_long_incomplete_text_finishes_within_each_child_deadline(self):
        # These exercise real recognition, not provider or runtime state. Each independent child has a deadline.
        code = """
import sys
from altitude import incidents, tasks
size = int(sys.argv[1])
kind = sys.argv[2]
if kind == 'labels':
    assert not list(tasks._option_marks('Option A' + ' ' * size))
    assert not list(tasks._option_marks('Option A (' * size))
    assert not list(tasks._option_marks('Option A (' * size + ')' + ' ' * size))
elif kind == 'inline':
    assert tasks._inline_recommendation(' ' * size) is None
    assert tasks._inline_recommendation('(' * size) is None
    assert tasks._inline_recommendation('(' * size + ')') is None
elif kind == 'recommendation':
    assert tasks._prose_recommendation('recommend' * size) is None
    assert tasks._prose_recommendation('recommend ' + 'option ' * size) is None
    assert tasks._prose_recommendation('recommend ' + 'option ' * size + 'B') == 'B'
elif kind == 'serial':
    assert not list(incidents._serials('serial' + ' ' * size))
    assert not list(incidents._serials('serial-' * size))
    assert not list(incidents._serials('serial-' * size + ' 1234'))
    assert list(incidents._serials('serial-' * size + ' serial 12345'))
elif kind == 'failure':
    assert incidents.failure_line('0' * size) == '0' * 300
    assert incidents.failure_line('word' * size + '":') == ''
elif kind == 'redaction':
    path, email = incidents._REDACTIONS[1][0], incidents._REDACTIONS[3][0]
    assert not incidents._CREDENTIAL.search('a-' * size)
    assert incidents._CREDENTIAL.search('a-' * size + 'token=FICTION1234')
    assert not path.search('x' * size)
    assert path.search('x' * size + '/.altitude/state.json')
    assert not email.search('a' * size) and not email.search('a.' * size)
    assert email.search('a.' * size + 'a@example.org')
"""
        root = Path(__file__).resolve().parent.parent
        for kind in ("labels", "inline", "recommendation", "serial", "failure", "redaction"):
            for size in (16384, 65536):
                with self.subTest(kind=kind, size=size):
                    completed = subprocess.run([sys.executable, "-B", "-c", code, str(size), kind],
                                               cwd=root, capture_output=True, text=True, timeout=2)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
