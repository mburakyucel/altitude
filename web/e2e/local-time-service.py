"""Disposable file-backed local-time walkthrough: stored UTC stamps and written UTC times; no live home or model calls."""
from service_support import configure, serve
from tests.support import git
from altitude import config, l3, state as S, tasks

# A January evening in Los Angeles (PST): written clocks are read on the message's own day, not today's.
REPLY_AT = "2026-01-15T00:30:00+00:00"
REPLY = ("Restart pending since 23:58Z. Checks finished at 2026-01-14T23:12:04+00:00; "
         "the quiet window is 22:20–22:45 UTC.\n\n"
         "The record keeps `2026-01-14T23:58:42+00:00`; a bare 23:58 has no zone.")


def main():
    configure()
    repo = config.PROJECT_ROOTS[0] / "alpha"
    repo.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=repo)
    (repo / "README.md").write_text("Fictional browser acceptance project.\n")
    with config.add_project("alpha", path=repo):
        pass
    l3.save_info("alpha", {"session_id": "fixture-alpha", "engine_last": config.ENGINES[0], "turns": 1})
    l3.chat_log("alpha", "assistant", REPLY, trigger="chat", turn_id="saved-chat", at=REPLY_AT)
    tasks.fyi("alpha", None, "This L2 resumes after 2026-01-15T02:00:00+00:00.")
    S.regen_state_md("alpha")

    task = tasks.new("alpha", "Time task", "Fictional saved conversation.")
    tasks._conversation_time = lambda: REPLY_AT
    tasks.message("alpha", task["slug"], "l2", "Checks pass as of 23:40Z.")
    tasks.block("alpha", task["slug"], "Saved walkthrough task.", actor="l2", updates={"waiting_on": "l3"})
    serve()


if __name__ == "__main__":
    main()
