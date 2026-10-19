"""Disposable real service on a fresh computer: no coding agent, no agent sandbox, no validation runner and a
local repository that is not on GitHub yet. Only engine installation and execution are controlled: the spec
installs the agent and the sandbox by creating their marker files, and a setup check runs as the daemon's
project tick runs it."""
import threading
from service_support import configure, serve
from tests.support import SUITE, git
from altitude import config, engines, incidents, installation, platform, project_setup


def main():
    configure()
    tools = SUITE / "first-timer-tools"
    tools.mkdir()
    agent, sandbox = tools / "agent", tools / "sandbox"
    engines.installation = lambda engine: {"available": agent.exists() and engine == config.ENGINES[0],
                                           "why": "deterministic browser engine fixture"}
    engines.common.installation = engines.installation
    engines.sign_in = lambda engine: {"signed_in": agent.exists() and engine == config.ENGINES[0],
                                      "command": engines.SIGN_IN[engine][1]}
    installation._gh_signed_in = lambda: True
    platform.sandbox_tool = lambda: {"enabled": sandbox.exists(), "command": "sudo apt install bubblewrap", "scoped": True,
                                     "detail": "/usr/bin/bwrap" if sandbox.exists() else "Install bubblewrap (sudo apt install bubblewrap)"}
    platform.validation_unavailable = lambda: "validation runs need podman and slirp4netns on this computer"
    engines.claude_print = lambda _prompt, **_options: {"text": "The project conversation is ready.",
                                                         "session_id": "fixture-first-timer-session"}
    repo = config.PROJECT_ROOTS[0] / "atlas"
    repo.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "README.md").write_text("Fictional project that is not on GitHub yet.\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "Initial project", cwd=repo)

    stopping = threading.Event()

    def tick():
        while not stopping.wait(0.5):
            if config.is_managed("atlas"):
                project_setup.maintain("atlas")

    ticker = threading.Thread(target=tick)
    ticker.start()

    def release():
        stopping.set()
        ticker.join()

    serve(release=release, ready={"agent": str(agent), "sandbox": str(sandbox), "faults": str(incidents.FAULTS)})


if __name__ == "__main__":
    main()
