"""Disposable real service on a fresh computer: no coding agent, no agent sandbox, no validation runner and a
local repository that is not on GitHub yet. Only engine installation and execution are controlled."""
from service_support import configure, serve
from tests.support import git
from altitude import config, engines, incidents, installation, l3, platform, project_setup, server, state as S


def main():
    configure()
    installed = False
    engines.installation = lambda engine: {"available": installed and engine == config.ENGINES[0],
                                           "why": "deterministic browser engine fixture"}
    engines.common.installation = engines.installation
    engines.sign_in = lambda engine: {"signed_in": installed and engine == config.ENGINES[0],
                                      "command": engines.SIGN_IN[engine][1]}
    installation._gh_signed_in = lambda: True
    platform.sandbox_tool = lambda: {"enabled": False, "command": "sudo apt install bubblewrap",
                                     "detail": "Install bubblewrap (sudo apt install bubblewrap)"}
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

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/evidence":
                return self._json({"faults": S.read_json(incidents.FAULTS, {}) or {},
                                   "history": l3.chat_history("atlas") if config.is_managed("atlas") else []})
            return super().do_GET()

        def do_POST(self):
            nonlocal installed
            if self.path == "/fixture/install-tool":
                installed = True
                # The daemon's next setup check, as its project tick runs it.
                project_setup.maintain("atlas")
                with server._bg_guard:
                    worker = server._bg.get("start:atlas")
                if worker:
                    worker.join(10)
                return self._json(project_setup.observe("atlas"))
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
