"""Review status on proposal and merge questions through real task storage and HTTP; fictional saved reviews."""
import shutil

from service_support import configure, serve
from tests.support import REPO, add_worktree, git, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, server, state as S, tasks as T, verify




def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    # The reported merge task continues only while its fictional PR is open; no GitHub call is made.
    verify.gh = lambda *_args, **_kwargs: {"state": "OPEN"}
    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    git("remote", "set-url", "origin", "https://github.com/example/atlas.git", cwd=repo)
    with config.add_project("atlas", path=repo):
        pass

    def running(title):
        slug = T.new("atlas", title, "Fictional work for the review status walkthrough, never sent to a provider.")["slug"]
        worktree = add_worktree(repo, slug)
        T.dispatch("atlas", slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                   worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        return slug, worktree

    def design_question(title, question):
        slug, worktree = running(title)
        folder = worktree / "design" / "wireframes"
        folder.mkdir(parents=True)
        shutil.copyfile(REPO / "docs/images/project-phone.png", folder / "phone.png")
        (folder / "proposal.md").write_text("Fictional layout proposal for the walkthrough.\n")
        T.message("atlas", slug, "l2", "The screenshots show the proposed layout on phone and desktop.")
        T.block("atlas", slug, question, actor="l2", expected_attempt=1, updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE},
                recommendation="Use the captured layout.", recommendation_label="Use this design",
                design={"title": f"{title} proposal", "proposal": "design/wireframes/proposal.md",
                        "images": [{"title": "Phone", "path": "design/wireframes/phone.png"}]})
        return slug

    def review(slug, subject, findings, dispositions, *, design=None, assessed=True, head=None):
        task = S.load_task("atlas", slug)
        # A saved review reads the task's current commit, so its card covers the current version, unless it names another.
        head = head or git("rev-parse", "HEAD", cwd=task["worktree"]).strip()
        snapshot = {"head": head, "base": head, "tree": "1" * 40, "context_hash": "2" * 64}
        identity = f"{slug[:10]}-{subject}"
        at = T._conversation_time()
        task.setdefault("reviews", []).append({
            "id": identity, "requested_at": at, "requested_by": "l2", "subject": subject, "state": "completed",
            "focus": "", "engine_label": "Second engine", "same_engine": False, "finished_at": at,
            "result": {"text": "The approach holds; the findings below were raised.", "findings": findings, "limitations": []},
            "dispositions": dispositions, "snapshot": {**snapshot, **({"design": {"id": design}} if design else {})},
            **({"reconciled": {**snapshot, "reason": "Checked each finding against the saved version.", "at": at}} if assessed else {}),
            "message": {"id": identity, "at": at, "role": "system", "by": "l2", "review_id": identity,
                        "text": f"Adversarial {subject} review requested."}})
        S.save_task("atlas", task)
        # Later updates put the review card well above the question at the end of the conversation.
        for index in range(12):
            T.message("atlas", slug, "l2", f"Fixture check {index + 1}: the saved version still passes the walkthrough checks.")

    finding = lambda key, title: {"id": key, "severity": "medium", "title": title, "body": f"{title}.", "path": None, "line": None}

    settings = design_question("Settings layout", "Build the settings layout as shown?")
    review(settings, "proposal", [finding("contrast", "Muted labels lack contrast"), finding("order", "Model choice comes too late")],
           [{"finding_id": "contrast", "disposition": "fixed", "reason": "Labels use the primary text colour."},
            {"finding_id": "order", "disposition": "dismissed", "reason": "The order follows the existing settings page."}],
           design=S.load_task("atlas", settings)["questions"][-1]["design"]["id"])
    design_question("Compact header", "Build the compact header as shown?")

    pagination, worktree = running("Keep pagination stable")
    T.message("atlas", pagination, "l2", "The pagination fix is delivered and checked; the PR waits for your review.")
    T.set_hold_merge("atlas", pagination, "Operator review of the finished feature")
    row = S.load_task("atlas", pagination)
    row.update(delivery={"number": 42, "head": git("rev-parse", "HEAD", cwd=worktree).strip(), "at": S.now()}, prs=[42])
    S.save_task("atlas", row)
    review(pagination, "changes", [finding("expiry", "Expired cursors restart pagination")],
           [{"finding_id": "expiry", "disposition": "open", "reason": "Needs the API owner's decision."}])
    S.write_json(S.task_dir("atlas", pagination) / "report.json", {
        "landed": {"prs": [{"number": 42, "merged": False}], "main_runs": [], "deploy": "not-applicable"},
        "review": [], "blocked": ""})
    T.report("atlas", pagination, {"verdict": "ok", "delivery": row["delivery"], "owner": T.report_owner(row), "prs": [42]})

    # The longest line: a merge question naming the held PR, whose review read an earlier commit and awaits L2.
    notes, worktree = running("Release notes")
    T.set_hold_merge("atlas", notes, "Operator review of the published release notes")
    row = S.load_task("atlas", notes)
    row.update(delivery={"number": 43, "head": git("rev-parse", "HEAD", cwd=worktree).strip(), "at": S.now()}, prs=[43])
    S.save_task("atlas", row)
    review(notes, "changes", [finding("tone", "Headline overstates the fix"), finding("link", "Upgrade link is missing"),
                              finding("date", "Release date is unset")], [], assessed=False, head="b" * 40)
    T.block("atlas", notes, "Merge PR #43?", actor="l2", updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE},
            questions={"questions": [{"question": "Merge PR #43?", "options": [
                {"key": "approve", "label": "Approve merge", "text": "Approved: merge PR #43."},
                {"key": "changes", "label": "Request changes", "text": "Hold PR #43 for changes."}], "recommended_key": "approve"}]})

    region, _ = running("Backup region")
    T.block("atlas", region, "Where should the backup live?", actor="l2", updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE},
            questions={"questions": [{"question": "Where should the backup live?", "options": [
                {"key": "west", "label": "West", "text": "Use the west region."},
                {"key": "east", "label": "East", "text": "Use the east region."}], "recommended_key": "west"}]})

    serve(server.Handler)


if __name__ == "__main__":
    main()
