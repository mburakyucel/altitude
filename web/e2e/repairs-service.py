"""One finished, fictional terminal repair, written where `alt repair` keeps its records; the real API serves it."""
import uuid

from service_support import configure, serve
from altitude import config, repair, state as S


def main():
    configure()
    repo = config.PROJECT_ROOTS[0] / "atlas"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("Fictional browser project.\n")
    with config.add_project("atlas", path=repo):
        pass
    ident = uuid.uuid4().hex
    folder = repair.home() / ident
    folder.mkdir(parents=True)
    S.atomic_write(folder / "brief.md", "# Repair brief\n\nFictional brief.\n")
    S.write_json(folder / "repair.json", {
        "id": ident, "route": "terminal", "engine": config.ENGINES[0], "problem": "Voice input stopped working.",
        "update": None, "version": None, "title": "Voice input stopped working.", "started": S.now(), "ended": S.now(),
        "report": {"wrong": "The speech model folder was missing.", "changed": "Set voice input up again.",
                   "remains": "Nothing."},
        "check": {"at": S.now(), "version": None, "running": None, "matches": None, "failing": []},
        "outcome": "confirm"})

    serve()


if __name__ == "__main__":
    main()
