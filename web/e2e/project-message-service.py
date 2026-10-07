"""Real information-message storage/broker, fictional projects and provider I/O only."""
import json

from service_support import configure, serve
from tests.support import make_repo
from altitude import config, engines, l3, server


def main():
    configure()
    for project in ("atlas", "lab"):
        repo = make_repo(config.PROJECT_ROOTS[0] / project / "repo")
        with config.add_project(project, path=repo, l3_engine=config.ENGINES[0]):
            pass
    def execute(_prompt, **options):
        return {"text": "Fictional reconciliation complete; no task action.", "session_id": "fixture-message-session",
                "reported_session_id": "fixture-message-session", "usage": {}}
    engines.claude_print = execute
    engines.codex_exec = execute
    exchange = None

    def send(sender, target, text, summary, request_id, reply_to=None):
        args = ["project", "message", target, text, "--summary", summary, "--request-id", request_id]
        if reply_to:
            args += ["--reply-to", reply_to]
        return json.loads(server.l3_verb_request(sender, {"kind": "alt", "args": args})["stdout"])

    class Handler(server.Handler):
        def do_POST(self):
            nonlocal exchange
            if not self.path.startswith("/fixture/"):
                return super().do_POST()
            action = self.path.removeprefix("/fixture/")
            self._body()
            try:
                if action == "send":
                    result = send("atlas", "lab", "Fictional local probe exited before provider execution. Review fixture-review.",
                                  "Local probe result", "probe-1")
                    exchange = result["project_message"]["exchange_id"]
                elif action == "supply":
                    result = l3.turn("lab", "Ordinary reconciliation", trigger="restart")
                elif action == "reply":
                    result = send("lab", "atlas", "Fix merged; local activation remains pending.", "Fix status", "reply-1", exchange)
                elif action == "supply-reply":
                    result = l3.turn("atlas", "Ordinary status request")
                elif action == "refuse":
                    result = send("atlas", "lab", "password=fictional-sensitive", "Refused diagnostic", "refused-1")
                elif action == "change-registration":
                    send("atlas", "lab", "Second fictional diagnostic.", "Another probe", "probe-2")
                    projects = config.load_projects()
                    projects["lab"]["path"] = str(config.PROJECT_ROOTS[0] / "replacement")
                    config.save_projects(projects)
                    result = {"changed": True}
                else:
                    return self._json({"error": "Unknown fixture action"}, 404)
                return self._json(result)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)

    serve(Handler)


if __name__ == "__main__":
    main()
