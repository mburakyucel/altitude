"""Real L3 queue transitions with a deterministic provider that takes a Send now message into its running turn."""
import json
import threading
import time

from service_support import configure, serve
from altitude import config, engines, l3, server, state as S


def main():
    configure()
    release, deliver = threading.Event(), threading.Event()
    calls, delivered = [], []
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
        turn_id = l3.active(project)["id"]
        text = next(row["text"] for row in l3.chat_history(project, None)
                    if row.get("turn_id") == turn_id and row["role"] == "user")
        calls.append({"text": text, "resume": options.get("resume")})
        if options.get("on_start"):
            options["on_start"](None)
        if text == "Fixture system work":
            assert options.get("sends") is None, "System turns take in no Send now message"
            assert release.wait(30), "The system turn was not released"
        if text == "Keep working":
            if options.get("on_text"):
                options["on_text"]("Checking the current work.")
            sends = options["sends"]
            deadline = time.monotonic() + 30
            while not list(sends.glob("*.json")):  # what the engine's driver watches for
                assert time.monotonic() < deadline, "Send now did not reach the running turn"
                time.sleep(0.05)
            assert deliver.wait(30), "The fixture delivery was not released"
            drop = next(sends.glob("*.json"))
            message = json.loads(drop.read_text())
            drop.rename(drop.with_suffix(".delivered"))
            delivered.append(message["text"])
            options["on_send"](message["id"], "delivered", "Checking the current work.")
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
            if self.path == "/fixture/system":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                l3.queue_message(project, "Fixture system work", trigger="restart")
                server.request_l3_drain(project)
                return self._json({"ok": True})
            if self.path in ("/fixture/release", "/fixture/deliver"):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                (release if self.path == "/fixture/release" else deliver).set()
                return self._json({"ok": True})
            if self.path == "/fixture/unavailable":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                engines.installation = lambda _engine: {"available": False, "why": "Fixture engine unavailable"}
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler, release=lambda: (deliver.set(), release.set()))


if __name__ == "__main__":
    main()
