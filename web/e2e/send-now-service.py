"""Real L3 queue transitions with a deterministic interruptible provider."""
import threading

from service_support import configure, serve
from altitude import config, engines, l3, server, state as S


def main():
    configure()
    release = threading.Event()
    stopped = threading.Event()
    calls = []
    interrupts = []
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
            assert options.get("interrupt") is None, "System turns must not be interruptible"
            assert release.wait(30), "The system turn was not released"
        if text in ("Keep working", "Hold on"):
            partial = ("Checking the current work. Two checks failed on the review branch, and the first log"
                       if text == "Keep working" else "")
            if partial and options.get("on_text"):
                options["on_text"](partial)
            interrupt = options.get("interrupt")
            assert interrupt is not None, "Chat turns expose their engine interrupt event"
            interrupts.append(interrupt)
            assert interrupt.wait(30), "Send now did not interrupt the fixture turn"
            stopped.set()
            assert release.wait(30), "The interrupted turn was not released"
            return {"interrupted": True, "error": None,
                    "text": partial, "session_id": "fixture-send-now"}
        return {"text": f"{text} answered.", "session_id": "fixture-send-now"}

    engines.claude_print = answer

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/status":
                return self._json({"calls": calls, "stopped": stopped.is_set()})
            return super().do_GET()

        def do_POST(self):
            if self.path == "/fixture/system":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                l3.queue_message(project, "Fixture system work", trigger="restart")
                server.request_l3_drain(project)
                return self._json({"ok": True})
            if self.path == "/fixture/release":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                release.set()
                return self._json({"ok": True})
            if self.path == "/fixture/unavailable":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                engines.installation = lambda _engine: {"available": False, "why": "Fixture engine unavailable"}
                return self._json({"ok": True})
            return super().do_POST()

    def cleanup():
        for event in interrupts:
            event.set()
        release.set()

    serve(Handler, release=cleanup)


if __name__ == "__main__":
    main()
