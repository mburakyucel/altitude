"""Real chat turn, stream and poll with a deterministic provider that writes, keeps working, writes more, then ends."""
import threading

from service_support import configure, serve
from altitude import config, engines, l3, server, state as S

FIRST = "A clear request, so I'll ground it in the code first."
MORE = "\n\nFound the cause: the page hid its indicator after the first words."


def main():
    configure()
    more, end = threading.Event(), threading.Event()
    project = "atlas"
    repo = config.PROJECT_ROOTS[0] / project
    repo.mkdir(parents=True)
    with config.add_project(project, path=repo):
        pass
    l3.save_info(project, {"session_id": "fixture-working", "engine_last": config.ENGINES[0], "sessions": {
        config.ENGINES[0]: {"session_id": "fixture-working", "turns": 1,
                            "confinement_version": l3.L3_CONFINEMENT_VERSION}}})
    l3.chat_log(project, "user", "What is left this week?", trigger="chat", turn_id="earlier")
    l3.chat_log(project, "assistant", "Nothing is waiting on you.", trigger="chat", turn_id="earlier")
    S.regen_state_md(project)

    def answer(prompt, **options):
        if options.get("on_start"):
            options["on_start"](None)
        if not prompt.endswith("Fix the indicator"):
            return {"text": "Queued note answered.", "session_id": "fixture-working"}
        # The turn's first words, then tool work, then more words: the turn is running throughout.
        options["on_text"](FIRST)
        assert more.wait(45), "The fixture was not told to continue"
        options["on_text"](MORE)
        assert end.wait(45), "The fixture was not told to end"
        return {"text": FIRST + MORE, "session_id": "fixture-working"}

    engines.claude_print = answer

    class Handler(server.Handler):
        def do_POST(self):
            if self.path in ("/fixture/more", "/fixture/end"):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                (more if self.path == "/fixture/more" else end).set()
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler, release=lambda: (more.set(), end.set()))


if __name__ == "__main__":
    main()
