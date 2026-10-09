"""Create task under an L3 reply: the real coordinator verbs, queue and task store with a scripted provider."""
import json
import threading

from service_support import configure, serve
from altitude import config, engines, l3, server, state as S

TITLE = "Refresh Needs you as soon as an answer is sent"


def main():
    configure()
    project = "atlas"
    repo = config.PROJECT_ROOTS[0] / project
    repo.mkdir(parents=True)
    with config.add_project(project, path=repo):
        pass
    l3.save_info(project, {"engine_last": config.ENGINES[0], "sessions": {
        config.ENGINES[0]: {"session_id": "fixture-create-task", "turns": 1,
                            "confinement_version": l3.L3_CONFINEMENT_VERSION}}})
    S.regen_state_md(project)
    mode = {"hold": False, "fail": False}
    release = threading.Event()
    created = []

    def alt(*args, stdin=""):
        reply = server.l3_verb_request(project, {"kind": "alt", "args": list(args), "stdin": stdin})
        created.append({"args": list(args), "returncode": reply["returncode"], "stderr": reply["stderr"]})
        return reply

    def answer(_prompt, **options):
        turn_id = l3.active(project)["id"]
        text = next(row["text"] for row in l3.chat_history(project, None)
                    if row.get("turn_id") == turn_id and row["role"] == "user")
        if options.get("on_start"):
            options["on_start"](None)
        if text == "Fixture system work":
            assert release.wait(30), "The system turn was not released"
            return {"text": "Checked the fixture.", "session_id": "fixture-create-task"}
        if text.startswith("Create task: "):
            if mode["hold"]:
                assert release.wait(30), "The Create task turn was not released"
            if mode["fail"]:
                return {"error": "The fixture engine could not answer."}
            # One press makes one task: the second creation in the same turn is refused.
            for _ in range(2):
                alt("task", "new", "--title", TITLE, "-", stdin="Refetch the Needs you count once an answer is sent.")
            return {"text": "Created it from our conversation.", "session_id": "fixture-create-task"}
        if text.endswith("bug?"):
            assert alt("task", "offer", TITLE)["returncode"] == 0
            return {"text": "Yes, a small one: the count refreshes only on the next poll after you answer.",
                    "session_id": "fixture-create-task"}
        return {"text": "Glad it helped.", "session_id": "fixture-create-task"}

    engines.claude_print = answer

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/status":
                return self._json({"verbs": created, "tasks": [
                    {"slug": task["slug"], "title": task["title"], "offer_turn": task.get("offer_turn")}
                    for task in S.list_tasks(project, include_archive=True)]})
            return super().do_GET()

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0"))) if self.path.startswith("/fixture/") else b""
            if self.path == "/fixture/mode":
                release.clear()
                mode.update(json.loads(body))
                return self._json(mode)
            if self.path == "/fixture/system":
                release.clear()
                l3.queue_message(project, "Fixture system work", trigger="restart")
                server.request_l3_drain(project)
                return self._json({"ok": True})
            if self.path == "/fixture/release":
                release.set()
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler, release=release.set)


if __name__ == "__main__":
    main()
