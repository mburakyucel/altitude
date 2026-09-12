"""Test-only server bootstrap. Real handlers/routing/storage, isolated engine I/O."""
import json
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.support import SUITE  # imports the offline guards before application paths freeze
from altitude import config, engines, server


def configure(*, expected_error=lambda _message: False):
    config.PROJECT_ROOTS = [SUITE / "projects"]
    assert (config.WEB_DIST / "index.html").is_file(), "Build this checkout with pnpm --dir web build first"
    config.ensure_root()
    # server.spawn and Handler catch exceptions: forwarding tracebacks prevents false green.
    # A scenario may allow a specifically asserted existing failure; all others fail teardown.
    server.log = lambda message: print(message, file=sys.stderr) if (
        "Traceback (most recent call last)" in message and not expected_error(message)
    ) else None
    # The real selector reads one installed option; no selection/state machine is replaced.
    engines.installation = lambda engine: {
        "available": engine == config.ENGINES[0], "why": "deterministic browser engine fixture"}
    engines.claude_agents = lambda: []
    engines.image_capability = lambda _engine: {"available": True, "why": "deterministic image fixture"}


def serve(handler=server.Handler, *, release=lambda: None):
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(json.dumps({"url": f"http://127.0.0.1:{httpd.server_port}", "disposable": True}), flush=True)
    try:
        httpd.serve_forever()
    finally:
        release()
        server.stop_l3_verb_brokers()
        httpd.server_close()
