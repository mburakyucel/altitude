"""Test-only server bootstrap. Real handlers/routing/storage, isolated engine I/O."""
import json
from pathlib import Path
import signal
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.support import SUITE  # imports the offline guards before application paths freeze
from altitude import access, config, engines, installation, server, terminal


def configure(*, expected_error=lambda _message: False):
    config.PROJECT_ROOTS = [SUITE / "projects"]
    assert (config.WEB_DIST / "index.html").is_file(), "Build this checkout with pnpm --dir web build first"
    config.ensure_root()
    # Walkthroughs exercise the upload composer against intercepted /api/transcribe, so this fictional
    # speech service is never contacted; the browser recognizer walkthrough overlays /api/voice itself.
    (config.ROOT / "settings.json").write_text(
        json.dumps({"voice": {"url": "https://speech.example.test/v1/audio/transcriptions"}}) + "\n")
    # server.spawn and Handler catch exceptions: forwarding tracebacks prevents false green.
    # A scenario may allow a specifically asserted existing failure; all others fail teardown.
    server.log = lambda message: print(message, file=sys.stderr) if (
        "Traceback (most recent call last)" in message and not expected_error(message)
    ) else None
    # The real selector reads one installed option; no selection/state machine is replaced.
    engines.installation = lambda engine: {
        "available": engine == config.ENGINES[0], "why": "deterministic browser engine fixture"}
    engines.claude_agents = lambda: []
    # First run's prerequisites: the installed engine is signed in and the GitHub CLI is not.
    engines.sign_in = lambda engine: {"signed_in": engine == config.ENGINES[0], "command": engines.SIGN_IN[engine][1]}
    installation._gh_signed_in = lambda: False
    engines.image_capability = lambda _engine: {"available": True, "why": "deterministic image fixture"}
    # The browser runs beside the harness, which may itself run inside an Altitude worker unit; the agent
    # check's own behaviour is covered in tests/test_terminal.py.
    terminal.agent_connection = lambda _peer, _local: False


def serve(handler=server.Handler, *, release=lambda: None):
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = False
    stopping = threading.Event()
    httpd.timeout = 0.5
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    # The browser is paired like any other: a code from the real store, redeemed for this run's device key.
    access.prepare()
    device, _ = access.redeem(access.issue_code()["code"], "Playwright browser")
    print(json.dumps({"url": f"http://127.0.0.1:{httpd.server_port}", "disposable": True, "device": device}), flush=True)
    try:
        # Finish registering an accepted request before cleanup joins its thread.
        while not stopping.is_set():
            httpd.handle_request()
    finally:
        release()
        httpd.server_close()
        # Requests may schedule queue drains as they finish. Join both before closing their sockets.
        deadline = time.monotonic() + 2
        while True:
            with server._bg_guard:
                workers = [worker for worker in server._bg.values() if worker.is_alive()]
            if not workers:
                break
            for worker in workers:
                worker.join(max(0, deadline - time.monotonic()))
            if time.monotonic() >= deadline:
                raise RuntimeError("Disposable background workflows did not finish before cleanup")
        server.stop_l3_verb_brokers()
