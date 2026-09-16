"""Planned-task acceptance with real storage, dispatch and Git; workers stay at the engine seam."""
from service_support import configure, serve
from tests.support import make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, engines, server, state as S, tasks as T


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    project = "atlas"
    repo = make_repo(config.PROJECT_ROOTS[0] / project)
    with config.add_project(project, path=repo, wip=1):
        pass
    dependency = T.new(project, "Migrate the index", "Complete the fictional index migration.")
    dispatch.run(project, dependency["slug"])
    planned = T.new(project, "Check index compatibility", "Keep the original pagination contract.",
                    wait="the index migration and browser checks to land")
    dependent = T.new(project, "Measure migrated index", "Measure the completed index migration.",
                      after=dependency["slug"])
    server.dispatch_waiting(project)
    denied = False

    def complete(slug):
        row = S.load_task(project, slug)
        fake.stop_l2_worker(row["l2_engine"], row["agent_id"], job_root=None)
        T.report(project, slug, {"verdict": "ok"})
        T.done(project, slug, digest="The fictional prerequisite is complete.")

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/status":
                return self._json({"calls": [{"prompt": row["prompt"]} for row in fake.calls],
                    "pending": T.pending(project, planned["slug"]),
                    "request": (S.task_dir(project, planned["slug"]) / "request.md").read_text()})
            return super().do_GET()

        def do_POST(self):
            nonlocal denied
            if self.path == "/api/l2/message" and denied:
                self._body()
                return self._json({"error": "Fixture permission denied"}, 403)
            if self.path != "/fixture/control":
                return super().do_POST()
            action = self._body()["action"]
            if action == "release":
                T.release(project, planned["slug"], "The operator verified both prerequisites.")
                server.dispatch_waiting(project)
            elif action == "complete-dependency":
                complete(dependency["slug"])
                server.dispatch_waiting(project)
            elif action == "complete-planned":
                complete(planned["slug"])
                server.dispatch_waiting(project)
            elif action == "empty":
                for row in S.list_tasks(project):
                    if row.get("agent_id"):
                        fake.stop_l2_worker(row["l2_engine"], row["agent_id"], job_root=None)
                    T.reject(project, row["slug"], "Fictional work no longer needed.")
            elif action == "deny":
                denied = True
            elif action == "allow":
                denied = False
            else:
                return self._json({"error": "Unknown fixture action"}, 400)
            return self._json({"ok": True})

    serve(Handler)


if __name__ == "__main__":
    main()
