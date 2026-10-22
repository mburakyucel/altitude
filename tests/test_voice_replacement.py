"""The voice runtime replacement check (`scripts/voice_replacement.py`) against fixture runtimes and a fixture worker,
so the suite keeps the validation phase working between its real runs with `alt task validate`."""
from __future__ import annotations

import hashlib
import io
import unittest
import zipfile

from tests.support import AltitudeCase
from altitude import platform, speech
from scripts import voice_replacement as phase

#: Holds a file of its runtime open, as the real worker maps its runtime's libraries, and hears silence. Its load
#: takes half a second, as the real model's takes several, so the check sees a worker exit well before any deletion.
FIXTURE_WORKER = r'''
import json, sys, time
from pathlib import Path
held = open(Path(sys.argv[1]) / "model" / "encoder.onnx", "rb")
time.sleep(0.5)
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request["op"] in ("audio", "finish"):
        print(json.dumps({"id": request["id"], "text": "", **({"final": True} if request["op"] == "finish" else {})}),
              flush=True)
'''


def _wheel(version: str) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        bundle.writestr("onnxruntime/__init__.py", f"__version__ = {version!r}\n")
        # Larger than a held download sends while the check takes its next step.
        bundle.writestr("onnxruntime/padding.bin", bytes(64 << 10))
    return data.getvalue()


class TestVoiceReplacementCheck(AltitudeCase):
    def setUp(self):
        super().setUp()
        if platform.speech_runtime()[0] is None:
            raise unittest.SkipTest("voice has no runtime on this host")
        worker = self.tmp / "worker.py"
        worker.write_text(FIXTURE_WORKER)
        self.worker = worker
        self.patch(speech, "ROOT", self.tmp / "speech")
        self.patch(speech, "WORKER", worker)
        self.patch(speech, "WATCH_S", 0.05)
        self.patch(speech, "SUPPORTED", {})
        self.patch(speech, "manifest", return_value=(None, "patched by the check"))
        self.patch(platform, "available_memory", return_value=None)
        self.assets = self.tmp / "assets"
        self.assets.mkdir()

    def files(self, contents: dict[str, bytes]) -> list:
        entries = []
        for name, content in contents.items():
            sha = hashlib.sha256(content).hexdigest()
            (self.assets / sha).write_bytes(content)
            entries.append((name, f"https://files.example/{name}", len(content), sha))
        return entries

    def test_the_check_passes_through_every_update_path_with_fixture_runtimes(self):
        model = {"model/encoder.onnx": b"weights" * 1000}
        current = self.files({**model, "wheels/onnxruntime-1.28.0.whl": _wheel("1.28.0")})
        prior = self.files({**model, "wheels/onnxruntime-1.22.0.whl": _wheel("1.22.0")})

        report = phase.Check(self.assets, current, prior, self.tmp / "results", self.worker, say=lambda line: None).run()

        self.assertTrue(report["passed"], report)
        self.assertTrue(report["admitted_for_this_run"])
        self.assertEqual(len(report["checks"]), 11, report["checks"])
        wheel = current[1][2]
        self.assertEqual((report["automatic_transfer_bytes"], report["retry_transfer_bytes"]), (wheel, wheel))
        self.assertIn("PASS", (self.tmp / "results/voice-replacement.log").read_text())
        self.assertEqual(sorted(p.name for p in speech.ROOT.glob("runtime-*")), [report["replacement"]])


class TestEarlierRuntime(unittest.TestCase):
    def test_the_earlier_runtime_is_the_current_pins_with_onnxruntime_122(self):
        for key in speech.WHEELS:
            current = [*speech.MODEL_FILES, speech._DECODER, *speech.WHEELS[key]]
            prior = phase.earlier(key, current)
            changed = [entry for entry in prior if entry not in current]
            self.assertEqual([name for name, *_ in changed], [phase.EARLIER[key][0]])
            self.assertIn("onnxruntime-1.22.0-", changed[0][0])
        # On Linux the earlier runtime is the one this release supports; no Mac runtime precedes the current one.
        for key, supported in speech.SUPPORTED.items():
            self.assertEqual(supported, (speech._digest(phase.earlier(key, [*speech.MODEL_FILES, speech._DECODER,
                                                                             *speech.WHEELS[key]])),))
        self.assertFalse([key for key in speech.SUPPORTED if key.startswith("macos-")])
