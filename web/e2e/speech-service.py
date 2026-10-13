"""Real application service with only compatible-server inference replaced."""
import runpy
from pathlib import Path

import service_support
from altitude import speech


def transcribe(setting, pcm):
    assert isinstance(pcm, bytes) and len(pcm) > 0 and len(pcm) % 2 == 0
    assert setting["url"].startswith("https://speech.example.test/")
    model = setting.get("model", "")
    if model in {"service_auth", "service_unavailable", "service_response", "service_empty"}:
        raise speech.ServiceError(model)
    return "check the build"


speech.transcribe_service = transcribe
runpy.run_path(str(Path(__file__).with_name("acceptance-service.py")), run_name="__main__")
