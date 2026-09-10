"""Real private image admission/storage and conversations with deterministic native engine I/O."""
import hashlib
import threading
from pathlib import Path

from service_support import configure, serve
from altitude import config, engines, images, l3, server, state as S, tasks as T


def main():
    configure()
    gates = {name: threading.Event() for name in ("alpha", "beta")}
    for gate in gates.values():
        gate.set()
    calls = []
    mode = {"value": "normal"}
    engines.image_capability = lambda _engine: {"available": mode["value"] != "unavailable",
                                               "why": "Image input unavailable in this fixture."}

    def answer(_prompt, **options):
        project = options["extra_env"]["ALTITUDE_PROJECT"]
        human = next(row for row in reversed(l3.chat_history(project, None)) if row["role"] == "user")
        refs = options.get("images", [])
        observed = [{"id": image["id"], "name": image["name"],
                     "sha256": hashlib.sha256(Path(image["path"]).read_bytes()).hexdigest()}
                    for image in refs]
        assert all(row["sha256"] == image["sha256"] for row, image in zip(observed, refs))
        calls.append({"project": project, "text": human["text"], "images": observed,
                      "resume": options.get("resume"), "turn_id": human["turn_id"]})
        if options.get("on_start"):
            options["on_start"](None)
        if not gates[project].wait(40):
            return {"error": "Fictional busy turn was not released."}
        if mode["value"] == "fail-turn":
            mode["value"] = "normal"
            return {"error": "Image delivery failed in the deterministic engine fixture."}
        return {"text": f"I can inspect {len(observed)} image(s). {human['text']}",
                "session_id": f"fixture-{project}-session"}

    engines.claude_print = answer
    for name in gates:
        repo = config.PROJECT_ROOTS[0] / name
        repo.mkdir(parents=True)
        (repo / "README.md").write_text("Fictional image conversation.\n")
        with config.add_project(name, path=repo):
            pass
        l3.save_info(name, {"engine_last": config.ENGINES[0], "session_id": f"fixture-{name}-session",
                           "sessions": {config.ENGINES[0]: {"session_id": f"fixture-{name}-session",
                                                          "confinement_version": l3.L3_CONFINEMENT_VERSION}}})
        l3.chat_log(name, "assistant", f"{name.title()} image conversation ready.", trigger="chat")
        S.regen_state_md(name)
    task = T.new("alpha", "Image task", "Inspect operator images.")
    task.update(state="running", attempt=1, l2_engine=config.ENGINES[0],
                session_id="fixture-task-session", agent_id="fixture-worker", dispatched=S.now())
    S.save_task("alpha", task)
    S.regen_state_md("alpha")

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/calls":
                return self._json({"calls": list(calls)})
            if self.path.startswith("/api/images/") and len(self.path.split("?")[0].split("/")) == 5:
                if mode["value"] in ("denied", "missing"):
                    return self._json({"error": "Image access denied." if mode["value"] == "denied" else "Image unavailable."},
                                      403 if mode["value"] == "denied" else 404)
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith(("/fixture/release/", "/fixture/pause/")):
                action, project = self.path.split("/")[-2:]
                (gates[project].set if action == "release" else gates[project].clear)()
                return self._json({"ok": True})
            if self.path == "/fixture/image-mode":
                mode["value"] = self._body()["mode"]
                return self._json({"ok": True})
            if self.path == "/fixture/task-deliver":
                pending = T.pending("alpha", "image-task")
                with S.project_lock("alpha"):
                    refs = images.resolve("alpha", [image for row in pending for image in row.get("images", [])], task="image-task")
                T.take_inbox("alpha", "image-task", {row["id"] for row in pending}, running_only=True)
                T.message("alpha", "image-task", "l2", f"I can inspect {len(refs)} attached image(s).", expected_attempt=1)
                return self._json({"images": [image["id"] for image in refs]})
            if self.path == "/fixture/archive-task":
                with S.project_lock("alpha"):
                    archived = S.load_task("alpha", "image-task")
                    archived.update(state="done", finished=S.now())
                    S.save_task("alpha", archived)
                    T._archive("alpha", "image-task")
                    S.regen_state_md("alpha")
                return self._json({"ok": True})
            if mode["value"] == "refused" and self.path in ("/api/chat", "/api/l2/message"):
                self._body(max_bytes=images.MAX_BODY)
                return self._json({"error": "This image was refused by the fixture."}, 422)
            return super().do_POST()

    serve(Handler, release=lambda: [gate.set() for gate in gates.values()])


if __name__ == "__main__":
    main()
