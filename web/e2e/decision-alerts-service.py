"""Real storage and change stream with a fixture escalation the walkthrough can publish on demand."""
from service_support import configure, serve
from tests.support import add_worktree, make_repo
from altitude import config, server, tasks as T


def main():
    configure()
    repos = {}
    for project, folder in (("atlas", "atlas"), ("beacon", "second-project/beacon")):  # one origin.git per parent
        repos[project] = make_repo(config.PROJECT_ROOTS[0] / folder)
        with config.add_project(project, path=repos[project]):
            pass

    def asking(project: str, title: str, question: str, escalated: bool = True) -> str:
        slug = T.new(project, title, "Fictional alert work, never sent to a live provider.")["slug"]
        worktree = add_worktree(repos[project], slug)
        T.dispatch(project, slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                   worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        T.block(project, slug, question, actor="l2", questions={"questions": [{"question": question}]})
        if escalated:  # a block already waits on the operator; escalation republishes the same decision
            T.escalate(project, slug, question, questions={"questions": [{"question": question}]})
        return slug

    asking("atlas", "Choose backup retention", "How long should backups stay?")

    class Handler(server.Handler):
        def do_POST(self):
            if self.path == "/fixture/decision":
                body = self._body()
                return self._json({"slug": asking(body.get("project", "beacon"), body["title"], body["question"],
                                                  body.get("escalated", True))})
            if self.path == "/fixture/escalate":
                body = self._body()
                [row] = [d for d in T.decisions(body["project"]) if d["slug"] == body["slug"]]
                T.escalate(body["project"], body["slug"], row["question"],
                           questions={"questions": [{"question": row["question"]}]})
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
