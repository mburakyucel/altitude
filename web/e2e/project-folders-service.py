"""Disposable altd with fictional folders in a throwaway home; only the provider is simulated."""
import atexit

from service_support import configure, serve
from tests.support import git
from altitude import config, engines


def main():
    configure()
    config.PROJECT_ROOTS = [config.HOME / "Projects"]
    config.PROJECT_ROOTS[0].mkdir(parents=True)
    for name in ("atlas", "notes", "new-idea"):
        (config.HOME / "code" / name).mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=config.HOME / "code" / "atlas")
    (config.HOME / "code" / "notes" / "todo.md").write_text("Fictional file the browser never lists.\n")
    locked = config.HOME / "locked"
    locked.mkdir()
    locked.chmod(0)
    atexit.register(locked.chmod, 0o700)  # runs before the suite's cleanup removes the home
    engines.claude_print = lambda _prompt, **_options: {"text": "The project conversation is ready.",
                                                         "session_id": "fixture-folders-session"}
    serve()


if __name__ == "__main__":
    main()
