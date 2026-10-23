"""One fictional agent chat that has answered, written where Altitude keeps chats; the real API serves it."""
from service_support import configure, serve
from altitude import chats, config, state as S


def main():
    configure()
    repo = config.PROJECT_ROOTS[0] / "atlas"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("Fictional browser project.\n")
    with config.add_project("atlas", path=repo):
        pass
    ident, at = "0a1b2c3d4e5f", S.now()
    chats.folder("atlas", ident).mkdir(parents=True)
    chats._save("atlas", {
        "id": ident, "name": "Why is the export slow?", "created": at, "updated": at, "request": "fixture-0001",
        "choice": None, "option": None, "engine": config.ENGINES[0], "model": None, "session": "fixture-session",
        "base": None, "archived": None, "seq": 3, "requests": ["fixture-0001"], "events": [],
        "messages": [{"id": "fixture-message", "at": at, "text": "Why is the export slow?", "turn": 1, "seq": 1}],
        "turns": [{"n": 1, "seq": 2, "state": "done", "worker": None, "started": at, "ended": at,
                   "engine": config.ENGINES[0], "model": None, "answer": "It reads every row twice.", "error": None,
                   "opened": "started"}]})

    serve()


if __name__ == "__main__":
    main()
