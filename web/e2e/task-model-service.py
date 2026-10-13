"""Fictional owners launched through the real dispatch and routing; workers stay at the engine seam."""
from service_support import configure, serve
from tests.support import make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, engines, server, state as S, tasks as T


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "worker_sends", "worker_termination",
                 "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    engines.installation = lambda engine: {"available": True, "why": "fictional installed engine"}
    engines.common.installation = engines.installation
    engines.sign_in = lambda engine: {"signed_in": True, "command": engines.SIGN_IN[engine][1]}
    engines.usage_hold = lambda *_args, **_kwargs: None
    engines.window_hold = lambda _engine: None
    project = "atlas"
    repo = make_repo(config.PROJECT_ROOTS[0] / project)
    with config.add_project(project, path=repo):
        pass
    codex, claude = "codex", "claude"
    assert set(config.ENGINES) == {codex, claude}
    # Nothing is configured: each owner launches on Altitude's resolved default for its engine.
    # Each row records what the engine reported, as the monitor would; nothing reported yet is (None, None).
    for title, engine, reported in (("Codex launched", codex, (None, None)),
                                    ("Codex confirmed", codex, ("gpt-6.1-sol", "high")),
                                    ("Claude confirmed", claude, ("claude-opus-5-5", None)),
                                    ("Codex finished", codex, ("gpt-6.1-sol", "high"))):
        task = T.new(project, title, "Fictional owner for the task model walkthrough.", engine=engine)
        dispatch.run(project, task["slug"])
        saved = S.load_task(project, task["slug"])
        saved.update(engine_model=reported[0], engine_reasoning_effort=reported[1])
        S.save_task(project, saved)
        T.message(project, task["slug"], "l2", "Working on the fictional change.")
        if title == "Codex finished":
            saved = S.load_task(project, task["slug"])
            fake.stop_l2_worker(saved["l2_engine"], saved["agent_id"], job_root=None)
            T.report(project, task["slug"], {"verdict": "ok"})
            T.done(project, task["slug"], digest="The fictional change is complete.")
    serve(server.Handler)


if __name__ == "__main__":
    main()
