"""Real L3 queue transitions with a deterministic provider that takes a Send now message into its running turn."""
import json
import threading
import time

from service_support import configure, serve
from altitude import config, engines, l3, route, server, state as S


def main():
    configure()
    release, deliver, uncertain = threading.Event(), threading.Event(), threading.Event()
    calls, delivered = [], []
    exhausted = threading.Event()
    project = "atlas"
    repo = config.PROJECT_ROOTS[0] / project
    repo.mkdir(parents=True)
    with config.add_project(project, path=repo):
        pass
    l3.save_info(project, {"engine_last": config.ENGINES[0], "sessions": {
        config.ENGINES[0]: {"session_id": "fixture-send-now", "turns": 1,
                            "confinement_version": l3.L3_CONFINEMENT_VERSION}}})
    S.regen_state_md(project)

    def answer(_prompt, **options):
        active = l3._active[project]
        queue_ids = set(active.get("queue_ids", []))
        text = "\n\n".join(row["text"] for row in l3.chat_history(project, None)
                           if row["role"] == "user" and
                           (row.get("turn_id") == active["id"] or
                            queue_ids.intersection(row.get("queue_ids", []))))
        assert _prompt.endswith(text), "The grouped history must match the actual engine input"
        if exhausted.is_set():
            # The provider refuses before any output, as a spent usage window does.
            return {"text": "", "session_id": "", "error": "Fixture usage window exhausted.", "tools": [], "safe_to_retry": True,
                    "limited": {"scope": "engine", "why": "Fixture usage window exhausted.", "until": "2999-01-01T00:00:00+00:00"}}
        calls.append({"text": text, "resume": options.get("resume")})
        if options.get("on_start"):
            options["on_start"](None)
        if text == "Fixture system work":
            assert options.get("sends") is None, "System turns take in no Send now message"
            assert release.wait(30), "The system turn was not released"
        if text == "Keep working":
            if options.get("on_text"):
                options["on_text"]("Checking the current work.")
            sends = options.get("sends")
            if sends is None:
                assert release.wait(30), "The boundary-only turn was not released"
                return {"text": "Kept working.", "session_id": "fixture-send-now"}
            deadline = time.monotonic() + 30
            while not list(sends.glob("*.json")):  # what the engine's driver watches for
                if release.is_set():  # a walkthrough that sends nothing into this turn
                    return {"text": "Kept working.", "session_id": "fixture-send-now"}
                assert time.monotonic() < deadline, "Send now did not reach the running turn"
                time.sleep(0.05)
            assert deliver.wait(30), "The fixture delivery was not released"
            drop = next(sends.glob("*.json"))
            message = json.loads(drop.read_text())
            outcome = "unconfirmed" if uncertain.is_set() else "delivered"
            drop.rename(drop.with_suffix(f".{outcome}"))
            delivered.append(message["text"])
            options["on_send"](message["id"], outcome, "Checking the current work.")
            assert release.wait(30), "The turn was not released"
            reply = f"Read: {message['text']}."
            if options.get("on_text"):
                options["on_text"](reply)
            return {"text": reply, "session_id": "fixture-send-now"}
        return {"text": f"{text} answered.", "session_id": "fixture-send-now"}

    engines.claude_print = answer

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/status":
                return self._json({"calls": calls, "delivered": delivered})
            return super().do_GET()

        def do_POST(self):
            if self.path == "/fixture/interrupted-history":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                for turn, text, reply, interrupted in (
                    ("partial", "Keep working", "Two checks failed on the review branch, and the first log", True),
                    ("partial-next", "Deliver this next", "Deliver this next answered.", False),
                    ("silent", "Hold on", "", True),
                    ("silent-next", "Use the other branch", "Use the other branch answered.", False),
                ):
                    l3.chat_log(project, "user", text, trigger="chat", turn_id=turn)
                    l3.chat_log(project, "assistant", reply, trigger="chat", turn_id=turn, interrupted=interrupted)
                return self._json({"ok": True})
            if self.path == "/fixture/uncertain":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                uncertain.set()
                return self._json({"ok": True})
            if self.path == "/fixture/boundary":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                engines.coordinator_native_send = lambda _engine: False
                return self._json({"ok": True})
            if self.path == "/fixture/system":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                l3.queue_message(project, "Fixture system work", trigger="restart")
                server.request_l3_drain(project)
                return self._json({"ok": True})
            if self.path in ("/fixture/release", "/fixture/deliver"):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                (release if self.path == "/fixture/release" else deliver).set()
                return self._json({"ok": True})
            if self.path == "/fixture/exhausted":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                exhausted.set()
                return self._json({"ok": True})
            if self.path in ("/fixture/recovered", "/fixture/recovered-ready"):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                exhausted.clear()
                route.note_limit(config.ENGINES[0], {"scope": "engine", "why": "Fixture window reset.",
                                                     "until": "2000-01-01T00:00:00+00:00"})
                if self.path == "/fixture/recovered":
                    server.request_l3_drain(project)
                return self._json({"ok": True})
            if self.path == "/fixture/unavailable":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                engines.installation = lambda _engine: {"available": False, "why": "Fixture engine unavailable"}
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler, release=lambda: (deliver.set(), release.set()))


if __name__ == "__main__":
    main()
