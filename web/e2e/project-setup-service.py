"""Disposable real Git/setup/API service; only external engine execution is controlled."""
import shutil
import threading

from service_support import configure, serve
from tests.support import SUITE, git
from altitude import config, engines, git_policy, l3, project_setup, server, state as S, tasks as T


def main():
    configure()
    # Guard composition generations belong to a disposable installation, never this checkout.
    installation = SUITE / "installation"
    installation.mkdir()
    shutil.copytree(config.HOOKS, installation / "hooks")
    git("init", "-q", "-b", "main", cwd=installation)
    git("add", "hooks", cwd=installation)
    git("commit", "-qm", "Fixture installation", cwd=installation)
    git("commit", "--allow-empty", "-qm", "Fixture upgrade", cwd=installation)
    config.REPO = installation
    gate = threading.Event()
    calls = []
    fail_intro = False

    def answer(_prompt, **options):
        project = options["extra_env"]["ALTITUDE_PROJECT"]
        calls.append({"project": project, "resume": options.get("resume")})
        if options.get("on_start"):
            options["on_start"](None)
        if project == "new-project":
            if not gate.wait(40):
                return {"error": "The browser did not release the fixture engine"}
            if fail_intro:
                return {"error": "Fixture engine authentication was refused"}
        return {"text": "The project conversation is ready.", "session_id": f"fixture-{project}-session"}

    engines.claude_print = answer
    for name in ("atlas", "new-project"):
        repo = config.PROJECT_ROOTS[0] / name
        repo.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=repo)
        git("config", "commit.gpgsign", "false", cwd=repo)
        (repo / "README.md").write_text("Fictional project for setup acceptance.\n")
        (repo / "AGENTS.md").write_text("Preserve this project's existing instructions.\n")
        git("add", "-A", cwd=repo)
        git("commit", "-qm", "Initial project", cwd=repo)
        remote = SUITE / "remotes" / f"{name}.git"
        remote.parent.mkdir(exist_ok=True)
        git("init", "-q", "--bare", str(remote), cwd=repo)
        git("remote", "add", "origin", str(remote), cwd=repo)
        git("push", "-qu", "origin", "main", cwd=repo)
    repo = config.PROJECT_ROOTS[0] / "atlas"
    with config.add_project("atlas", path=repo):
        pass
    l3.chat_log("atlas", "assistant", "Saved project history.", trigger="chat")
    l3.save_info("atlas", {"session_id": "fixture-atlas-session", "turns": 3,
                         "engine_last": config.ENGINES[0], "sessions": {
                             config.ENGINES[0]: {"session_id": "fixture-atlas-session", "turns": 3,
                                                "confinement_version": l3.L3_CONFINEMENT_VERSION}}})
    task = T.new("atlas", "Keep existing work", "Never dispatch this fictional task.", hold_merge="Operator review")
    project_setup.request("atlas", "repair", actor="operator")
    project_setup.run("atlas")
    S.regen_state_md("atlas")

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/evidence":
                custom = repo / ".git/hooks/pre-commit"
                return self._json({"calls": list(calls), "hooks": git_policy.inspect_hooks(repo),
                                   "custom": custom.read_text() if custom.exists() else None,
                                   "history": l3.chat_history("atlas"), "task": S.load_task("atlas", task["slug"]),
                                   "session": l3.info("atlas").get("session_id")})
            return super().do_GET()

        def do_POST(self):
            nonlocal fail_intro
            if self.path == "/fixture/release-intro":
                gate.set()
                return self._json({"ok": True})
            if self.path == "/fixture/fail-intro":
                fail_intro = True
                gate.set()
                return self._json({"ok": True})
            if self.path == "/fixture/wait-intro":
                # Observe the scripted outcome after both real workflows persist their results.
                for key in ("setup:new-project", "start:new-project"):
                    with server._bg_guard:
                        worker = server._bg[key]
                    worker.join(10)
                    assert not worker.is_alive(), f"The fixture workflow did not finish: {key}"
                return self._json({"ok": True})
            if self.path == "/fixture/retry-intro":
                fail_intro = False
                return self._json({"ok": True})
            if self.path == "/fixture/notes":
                notes = config.PROJECT_ROOTS[0] / "notes"
                notes.mkdir()
                (notes / "README.md").write_text("A conversation-only folder.\n")
                return self._json({"path": str(notes)})
            if self.path.startswith("/fixture/prepare/"):
                scenario = self.path.rsplit("/", 1)[-1]
                if scenario == "clear-lock":
                    (repo / ".git/config.lock").unlink()
                    return self._json({"ok": True})
                if scenario == "empty-discovery":
                    with config.add_project("new-project", path=config.PROJECT_ROOTS[0] / "new-project"):
                        pass
                    return self._json({"ok": True})
                git("config", "--local", "--unset", "core.hooksPath", cwd=repo)
                if scenario == "stale":
                    sha = git("rev-parse", "HEAD~1", cwd=config.REPO).strip()
                    old = config.REPO / ".altitude-source" / sha / "hooks"
                    git("config", "--local", "core.hooksPath", str(old), cwd=repo)
                elif scenario == "custom":
                    custom = repo / ".git/hooks/pre-commit"
                    custom.write_text("#!/bin/sh\nprintf 'custom hook ran\\n' >> .git/custom-hook-ran\nexit 0\n")
                    custom.chmod(0o755)
                elif scenario == "failure":
                    (repo / ".git/config.lock").write_text("Fixture holds Git configuration lock.\n")
                elif scenario == "interrupted":
                    project_setup.request("atlas", "repair", actor="operator")
                    operation = project_setup.read("atlas")["operation"]
                    project_setup.save("atlas", operation={**operation, "state": "running", "step": "guards"})
                elif scenario != "missing":
                    return self._json({"error": "Unknown fixture scenario"}, 400)
                return self._json(project_setup.observe("atlas"))
            if self.path == "/fixture/run-custom":
                # Run the selected hook through Git; custom output proves composition is active.
                git("switch", "-qc", "fixture-topic", cwd=repo)
                git("commit", "--allow-empty", "-qm", "Verify both hook sets", cwd=repo)
                return self._json({"ran": (repo / ".git/custom-hook-ran").read_text()})
            return super().do_POST()

    serve(Handler, release=gate.set)


if __name__ == "__main__":
    main()
