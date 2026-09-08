"""Disposable, file-backed altd for destructive walkthroughs; only the provider is simulated."""
import sys

from service_support import configure, serve
from altitude import config, engines, l3, state as S, tasks as T


def main():
    # Existing API semantics: racing reads after detach return unknown-project 500, while the UI
    # shows Project not managed. The removal spec asserts that exact response; no other 500 is allowed.
    configure(expected_error=lambda message: (
        not config.is_managed("sample-project")
        and message.startswith(("GET /api/project/sample-project: ", "GET /api/chat/sample-project?limit=60: "))
        and 'KeyError: "unknown project \'sample-project\'; register it first (alt project add)"' in message
    ))

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
    serve()


if __name__ == "__main__":
    main()
