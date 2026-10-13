"""Fictional transfer owners; real API, storage, routing and lifecycle transitions."""
from service_support import configure, serve
from tests.support import add_worktree, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, engines, server, state as S, tasks as T


def main():
    configure(expected_error=lambda message: "fixture termination could not be confirmed" in message)
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "worker_sends", "worker_termination",
                 "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    unavailable = set()
    unknown_termination = set()
    failed_stops = set()

    def stop_worker(engine, worker_id, *, job_root):
        if worker_id in failed_stops:
            raise RuntimeError("fixture termination could not be confirmed")
        return fake.stop_l2_worker(engine, worker_id, job_root=job_root)

    engines.stop_l2_worker = stop_worker
    engines.installation = lambda engine: {"available": engine not in unavailable,
                                           "why": "fictional engine unavailable" if engine in unavailable else "fictional installed engine"}
    engines.worker_termination = lambda task, **options: None if task["slug"] in unknown_termination else fake.worker_termination(task, **options)
    engines.sign_in = lambda engine: {"signed_in": True, "command": engines.SIGN_IN[engine][1]}
    engines.usage_hold = lambda *_args, **_kwargs: None
    # The old seat's recorded exhaustion is also a current engine hold; other seats remain available.
    engines.window_hold = lambda engine: "2099-01-01T00:00:00+00:00" if engine == config.ENGINES[0] else None
    engines.review_capability = lambda _engine: {"available": False, "why": "fixture has no reviewer"}
    project = "atlas"
    repo = make_repo(config.PROJECT_ROOTS[0] / project)
    with config.add_project(project, path=repo):
        pass
    projects = config.load_projects()
    projects[project]["routing"] = [[{"engine": engine}] for engine in config.ENGINES]
    config.save_projects(projects)
    fixture_tasks = {}
    for title, mode in (("Transfer running work", "running"), ("Transfer quota work", "quota"),
                        ("Transfer pinned work", "pinned"), ("Transfer question work", "question")):
        task = T.new(project, title, "Fictional transfer walkthrough.", paths=["README.md"],
                     hold_merge="Review the delivered change before merging.")
        slug = task["slug"]
        fixture_tasks[slug] = slug
        worktree = add_worktree(repo, slug)
        agent = f"fixture-{slug}"
        fake.workers[agent] = {"id": agent, "sessionId": agent, "state": "working", "status": "busy"}
        task = T.dispatch(project, slug, attempt=1, session_id=agent, agent_id=agent,
                          worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        T.message(project, slug, "l2", "Saved work is ready for the next engine.")
        if mode == "question":
            T.block(project, slug, "Keep the bounded validation scope?", actor="l2",
                    updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE})
            fake.workers[agent].update(state="done", status="exited")
        if mode == "quota":
            fake.workers[agent].update(state="failed", status="exited")
            T.block(project, slug, "Engine allowance exhausted.", actor="altd",
                    updates={"usage_limit": {"scope": "engine", "until": None,
                                             "why": "Engine allowance exhausted."}})
        if mode == "pinned":
            task["engine"] = config.ENGINES[0]
            S.save_task(project, task)

    class Handler(server.Handler):
        def do_POST(self):
            if self.path == "/fixture/transfer":
                body = self._body()
                slug = fixture_tasks.get(body.get("slug"))
                if slug is None:
                    return self._json({"error": "Unknown fixture task"}, 400)
                task = S.load_task(project, slug)
                if "unknown_termination" in body:
                    if body["unknown_termination"]:
                        unknown_termination.add(slug)
                        if body.get("stop_failure"):
                            failed_stops.add(task["agent_id"])
                    else:
                        unknown_termination.discard(slug)
                        if task["agent_id"] in failed_stops:
                            failed_stops.discard(task["agent_id"])
                            fake.stop_l2_worker(task["l2_engine"], task["agent_id"],
                                                job_root=dispatch.l2_job_root(project, slug))
                    return self._json(server.task_view(project, slug))
                if body.get("outcome") == "question":
                    T.block(project, slug, "Which validation scope should the new engine preserve?", actor="l2",
                            updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, expected_attempt=task["attempt"])
                elif body.get("outcome") == "report":
                    S.write_json(S.task_dir(project, slug) / "report.json",
                                 {"landed": {"prs": []}, "review": [], "blocked": ""})
                    fake.workers[task["agent_id"]].update(state="done", status="exited")
                elif body.get("outcome") == "unavailable":
                    unavailable.add(task["daemon_request"]["engine"])
                if body.get("ready"):
                    request = task["daemon_request"]
                    folder = S.task_dir(project, slug)
                    (folder / "progress.md").write_text("Saved the implementation. Next: verify the change.\n")
                    S.write_json(folder / f"handoff-{request['id']}.ready.json",
                                 {key: request[key] for key in ("id", "attempt", "agent_id", "session_id")})
                dispatch.run_task_operation(project, slug)
                task = S.load_task(project, slug)
                if body.get("launch") and task["state"] == "queued":
                    dispatch.run(project, slug)
                return self._json(server.task_view(project, slug))
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
