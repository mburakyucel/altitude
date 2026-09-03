"""The Chat composer pins the project's L3 engine through one endpoint; the chat view shows the pin."""
import http.client
import json
import threading
import unittest

from tests.support import AltitudeCase
from altitude import config, server


class TestL3EnginePin(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_TIMERS", "0")
        self.patch(server, "overview", new=lambda: {"state": "ready"})
        self.patch(server, "log", new=lambda *a, **k: None)
        server.Handler._seen_clients.clear()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def _call(self, method, path, body=None):
        host, port = self.httpd.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"Content-Type": "application/json"} if body is not None else {})
        res = conn.getresponse()
        return res.status, json.loads(res.read() or b"null")

    def test_pin_is_set_shown_and_cleared(self):
        status, view = self._call("GET", f"/api/chat/{self.project}")
        self.assertEqual(status, 200); self.assertIsNone(view["engine"])
        status, out = self._call("POST", "/api/l3/engine", {"project": self.project, "engine": "codex"})
        self.assertEqual((status, out["engine"]), (200, "codex"))
        self.assertEqual(config.project(self.project)["l3_engine"], "codex")
        self.assertEqual(self._call("GET", f"/api/chat/{self.project}")[1]["engine"], "codex")
        status, _ = self._call("POST", "/api/l3/engine", {"project": self.project, "engine": None})
        self.assertEqual(status, 200); self.assertNotIn("l3_engine", config.project(self.project))
        status, out = self._call("POST", "/api/l3/engine", {"project": self.project, "engine": "gemini"})
        self.assertEqual(status, 400); self.assertIn("engine must be one of", out["error"])


if __name__ == "__main__":
    unittest.main()
