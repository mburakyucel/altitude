"""Disposable real chat/queue/history service; controlled providers never launch a worker."""
import socket
import threading

from service_support import configure, serve
from altitude import config, engines, l3, server, state as S


def main():
    configure()
    gates = {name: threading.Event() for name in ("alpha", "beta")}
    disconnect = threading.Event()
    queued_gate = threading.Event()
    stream_gate = threading.Event()
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
        delayed = text in ("Alpha running request", "Beta running request", "Alpha accepted failure", "Alpha disconnected request", "Alpha stalled request")
        if delayed and attempt == 1:
            if options.get("on_text") and text != "Alpha accepted failure":
                options["on_text"](f"{project.title()} partial reply.")
            if not gates[project].wait(45):
                return {"error": "Disposable provider was not released"}
            if text == "Alpha accepted failure":
                return {"error": "Disposable provider failed after acceptance"}
        if text == "Alpha first navigation request\n\nAlpha second navigation request":
            if not queued_gate.wait(45):
                return {"error": "Disposable queued provider was not released"}
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
        def _stream_send(self, obj):
            if obj.get("done") and any(row.get("text") == "Alpha stalled request" for row in l3.chat_history("alpha", 60)):
                if not stream_gate.wait(45):
                    raise TimeoutError("The browser did not release the stalled response")
            super()._stream_send(obj)
            if obj.get("turn") and l3.chat_history("alpha", 1)[0]["text"] == "Alpha disconnected request":
                # Flush the real durable turn receipt before interrupting its chunked response.
                if not disconnect.wait(45):
                    raise TimeoutError("The browser did not release the transport interruption")
                self.connection.shutdown(socket.SHUT_RDWR)
                self.close_connection = True
                raise BrokenPipeError("Controlled interruption after acceptance")

        def do_GET(self):
            if self.path == "/fixture/calls":
                with guard:
                    return self._json({"calls": list(calls)})
            return super().do_GET()

        def do_POST(self):
            if self.path == "/fixture/release-stream":
                stream_gate.set()
                return self._json({"ok": True})
            if self.path == "/fixture/release-queued":
                queued_gate.set()
                return self._json({"ok": True})
            if self.path == "/fixture/disconnect":
                disconnect.set()
                return self._json({"ok": True})
            if self.path.startswith("/fixture/release/"):
                project = self.path.rsplit("/", 1)[-1]
                if project not in gates:
                    return self._json({"error": "Unknown fictional project"}, 404)
                gates[project].set()
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler, release=lambda: [gate.set() for gate in [*gates.values(), disconnect, queued_gate, stream_gate]])


if __name__ == "__main__":
    main()
