"""Disposable file-backed prose walkthrough; no live home, worker, or model calls."""
from service_support import configure, serve
from tests.support import git
from altitude import config, l3, state as S, tasks


def prose(who):
    return (f"Saved {who} references: PR #250, issue #247, #248, other/repo#12.\n\n"
            "Existing [review](https://github.com/example/project/pull/251) and "
            "https://github.com/example/project/issues/252\n\n"
            "Code `PR #900`.\n\n~~~\nissue #901\n~~~")


def main():
    configure()
    for name in ("alpha", "beta"):
        repo = config.PROJECT_ROOTS[0] / name
        repo.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=repo)
        if name == "alpha":
            git("remote", "add", "origin", "https://github.com/example/project.git", cwd=repo)
        (repo / "README.md").write_text("Fictional browser acceptance project.\n")
        with config.add_project(name, path=repo):
            pass
        l3.save_info(name, {"session_id": f"fixture-{name}", "engine_last": config.ENGINES[0], "turns": 1})
        l3.chat_log(name, "assistant", prose("L3"), trigger="chat", turn_id="saved-chat")
        S.regen_state_md(name)

    task = tasks.new("alpha", "Reference task", "Fictional saved conversation.")
    slug = task["slug"]
    tasks.block("alpha", slug, "Saved walkthrough task.", actor="l2", updates={"waiting_on": "l3"})
    tasks.message("alpha", slug, "l2", prose("L2"))
    tasks.message("alpha", slug, "l3", "L3 task reply: pull request #253.", wake_blocked=False)
    directory = S.task_dir("alpha", slug)
    S.write_json(directory / "report.json", {
        "landed": {"prs": [{"number": 260, "title": "Saved report PR", "merged": True, "merge_sha": "fixture"}],
                   "main_runs": [], "deploy": "failed: waiting for issue #261"},
        "review": [{"summary": "Reviewed PR #262", "disposition": "fixed", "reason": "See other/repo#263"}],
        "blocked": "Waiting for issue #264", "fyi": ["Track #265; `issue #902` stays code."],
    })
    (directory / "report.md").write_text("Report notes reference PR #270.\n\n```\nissue #903\n```\n")
    (directory / "digest.md").write_text("Digest references issue #271.\n")
    l3.chat_log("alpha", "user", f"Task: {slug}\nVerdict: passed\nProblems: none\nPRs: PR #260",
                trigger="report-landed", turn_id="saved-system", slug=slug)
    l3.chat_log("alpha", "assistant", "Saved system result: PR #260 landed.",
                trigger="report-landed", turn_id="saved-system", slug=slug)

    decision = tasks.new("alpha", "Reference decision", "Fictional saved decision.")
    slug = decision["slug"]
    tasks.block("alpha", slug, "Should PR #250 fix issue #247 or other/repo#12?", actor="l2")
    tasks.escalate("alpha", slug, "Should PR #250 fix issue #247 or other/repo#12?\n"
                   "Option A: Continue with PR #250.\nOption B: Wait for issue #247.\n"
                   "I recommend A because #248 is ready.")
    tasks.message("alpha", slug, tasks.OPERATOR_MESSAGE_ROLE, "Explain the references.", wake_blocked=False)
    tasks.message("alpha", slug, "l2", "Decision answer: PR #272 and other/repo#273.\n\n~~~\nissue #904\n~~~")

    serve()


if __name__ == "__main__":
    main()
