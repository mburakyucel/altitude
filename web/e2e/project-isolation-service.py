"""Disposable real chat/queue/history service; controlled providers never launch a worker."""
import json
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.support import SUITE  # isolate homes before Altitude freezes its paths
from altitude import config, engines, l3, server, state as S


def main():
    config.PROJECT_ROOTS = [SUITE / "projects"]
    server.log = lambda *args: None
    engines.claude_agents = lambda: []
    l3._select = lambda *args: {"engine": config.ENGINES[0], "why": "disposable walkthrough"}
    gates = {name: threading.Event() for name in ("alpha", "beta")}
    calls = []
    guard = threading.Lock()

    def answer(_prompt, **options):
        project = options["extra_env"]["ALTITUDE_PROJECT"]
        text = l3.chat_history(project, 1)[0]["text"]
        with guard:
            attempt = 1 + sum(row["project"] == project and row["text"] == text for row in calls)
            calls.append({"project": project, "text": text, "resume": options.get("resume")})
        if options.get("on_start"):
            options["on_start"](None)
        delayed = text in ("Alpha running request", "Beta running request", "Alpha accepted failure")
        if delayed and attempt == 1:
            if options.get("on_text") and text != "Alpha accepted failure":
                options["on_text"](f"{project.title()} partial reply.")
            if not gates[project].wait(45):
                return {"error": "Disposable provider was not released"}
            if text == "Alpha accepted failure":
                return {"error": "Disposable provider failed after acceptance"}
        reply = f"{text} answered."
        return {"text": reply, "session_id": f"fixture-{project}-session"}

    engines.claude_print = answer
    for name in gates:
        repo = config.PROJECT_ROOTS[0] / name
        repo.mkdir(parents=True)
        (repo / "README.md").write_text("Fictional project for isolated browser acceptance.\n")
        with config.add_project(name, path=repo):
            pass
        l3.save_info(name, {"session_id": f"fixture-{name}-session", "engine_last": config.ENGINES[0],
                           "sessions": {config.ENGINES[0]: {"session_id": f"fixture-{name}-session", "turns": 1,
                                                          "confinement_version": l3.L3_CONFINEMENT_VERSION}}})
        if name == "alpha":
            l3.chat_log(name, "assistant", "Alpha saved history.", trigger="chat")
        S.regen_state_md(name)

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/calls":
                with guard:
                    return self._json({"calls": list(calls)})
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith("/fixture/release/"):
                project = self.path.rsplit("/", 1)[-1]
                if project not in gates:
                    return self._json({"error": "Unknown fictional project"}, 404)
                gates[project].set()
                return self._json({"ok": True})
            return super().do_POST()

    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(json.dumps({"url": f"http://127.0.0.1:{httpd.server_port}", "disposable": True}), flush=True)
    try:
        httpd.serve_forever()
    finally:
        for gate in gates.values():
            gate.set()
        server.stop_l3_verb_brokers()
        httpd.server_close()


if __name__ == "__main__":
    main()
