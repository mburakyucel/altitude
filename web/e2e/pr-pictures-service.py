"""A held PR's pictures on its merge card through real task storage, Git and HTTP; fictional PRs and pictures."""
import shutil

from service_support import configure, serve
from tests.support import REPO, add_worktree, git, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, state as S, tasks as T, verify


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    # The reported tasks continue only while their fictional PRs are open; no GitHub call is made.
    verify.gh = lambda *_args, **_kwargs: {"state": "OPEN"}
    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    images = REPO / "docs" / "images"

    def place(root, pictures):
        for path, source in pictures.items():
            target = root / path
            if source is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(images / source, target)

    # Main already has the pictures the PRs replace or remove.
    place(repo, {"docs/overview.png": "project-desktop.png", "docs/phone.png": "project-phone.png",
                 "docs/banner.png": "decision-desktop.png", "docs/logo.svg": "altitude.svg"})
    git("add", "docs", cwd=repo)
    git("commit", "-q", "-m", "pictures", cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    git("remote", "set-url", "origin", "https://github.com/example/atlas.git", cwd=repo)
    with config.add_project("atlas", path=repo):
        pass

    def held(title, number, pictures, text=None):
        """A task whose committed head changes `pictures` (path to a docs/images source, None to remove) under a merge
        hold, delivered as PR `number`."""
        slug = T.new("atlas", title, "Fictional work for the PR pictures walkthrough, never sent to a provider.")["slug"]
        worktree = add_worktree(repo, slug)
        T.dispatch("atlas", slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                   worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        place(worktree, pictures)
        if text:
            (worktree / "docs" / "guide.md").write_text(text)
        git("add", "-A", "docs", cwd=worktree)
        git("commit", "-q", "-m", title, cwd=worktree)
        T.set_hold_merge("atlas", slug, "Operator review of the finished change")
        row = S.load_task("atlas", slug)
        row.update(delivery={"number": number, "head": git("rev-parse", "HEAD", cwd=worktree).strip(),
                             "base": git("rev-parse", "origin/main", cwd=repo).strip(),
                             "branch": f"worktree-{slug}", "at": S.now()}, prs=[number])
        S.save_task("atlas", row)
        return slug, worktree, row

    def report(slug, row):
        S.write_json(S.task_dir("atlas", slug) / "report.json", {
            "landed": {"prs": [{"number": row["prs"][-1], "merged": False}], "main_runs": [], "deploy": "not-applicable"},
            "review": [], "blocked": ""})
        T.report("atlas", slug, {"verdict": "ok", "delivery": row["delivery"], "owner": T.report_owner(row), "prs": row["prs"]})

    # The generated merge card: two replaced pictures, one new, one removed, and a vector it cannot show.
    slug, _, row = held("Refresh the README pictures", 51, {
        "docs/overview.png": "task-desktop.png", "docs/phone.png": "task-phone.png", "docs/review.webp": "feature-review.webp",
        "docs/banner.png": None, "docs/logo.svg": "balance.svg"})
    T.message("atlas", slug, "l2", "The README pictures are refreshed; the PR waits for your review.")
    report(slug, row)

    # The owner's merge question with a composed picture it shows beside the one picture its PR adds.
    slug, worktree, _ = held("Polish the help page", 52, {"docs/help.png": "session-phone.png"})
    shutil.copyfile(images / "work-phone.png", worktree / "composed.png")
    T.block("atlas", slug, "Merge the help page?", actor="l2", expected_attempt=1,
            updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE},
            questions={"questions": [{"question": "Merge the help page? The composed picture shows it in the app.",
                                      "review": "changes", "recommended_key": "merge", "options": [
                {"key": "merge", "label": "Approve merge", "text": "Merge the help page."},
                {"key": "changes", "label": "Request changes", "text": "Tell me what to change first."}]}]},
            show=T.shown_files("atlas", S.load_task("atlas", slug), ["composed.png"]))

    # A text-only PR keeps the card it always had.
    slug, _, row = held("Fix the timer copy", 53, {}, text="The timer counts down from five minutes.\n")
    report(slug, row)
    serve()


if __name__ == "__main__":
    main()
