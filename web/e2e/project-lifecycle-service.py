"""Disposable, file-backed altd for destructive walkthroughs; only the provider is simulated."""
import json
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.support import SUITE  # isolates HOME/ALTITUDE_HOME before importing Altitude
from altitude import config, engines, l3, server, state as S, tasks as T


def main():
    config.PROJECT_ROOTS = [SUITE / "projects"]
    server.log = lambda *args: None
    engines.claude_agents = lambda: []
    l3._select = lambda *args: {"engine": config.ENGINES[0], "why": "disposable walkthrough"}

    def answer(_prompt, **options):
        assert options.get("resume") == "fixture-saved-session", "reattachment must retain the provider session"
        return {"text": "Queued request answered.", "session_id": "fixture-saved-session"}

    engines.claude_print = answer
    names = ["sample-project"] if sys.argv[1:] == ["single"] else ["sample-project", "busy-project"]
    for name in names:
        repo = config.PROJECT_ROOTS[0] / name
        repo.mkdir(parents=True)
        (repo / "README.md").write_text("Disposable project. Keep this repository.\n")
        with config.add_project(name, path=repo):
            pass
        l3.chat_log(name, "assistant", "Saved project history.", trigger="chat")
        l3.chat_log(name, "error", "An old turn failed.", trigger="chat")
        l3.save_info(name, {"session_id": "fixture-saved-session", "engine_last": config.ENGINES[0],
                           "sessions": {config.ENGINES[0]: {"session_id": "fixture-saved-session", "turns": 1,
                                                          "confinement_version": l3.L3_CONFINEMENT_VERSION}}})
        task = T.new(name, "Existing work", "Disposable task, never dispatched.")
        if name == "sample-project":
            T.reject(name, task["slug"], "already finished")
            l3.queue_message(name, "Saved queued request", trigger="chat", role="burak")
        S.regen_state_md(name)
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(json.dumps({"url": f"http://127.0.0.1:{httpd.server_port}", "disposable": True}), flush=True)
    try:
        httpd.serve_forever()
    finally:
        server.stop_l3_verb_brokers()
        httpd.server_close()


if __name__ == "__main__":
    main()
