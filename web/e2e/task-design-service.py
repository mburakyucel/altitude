"""Fixed proposal snapshots through real task/storage/HTTP paths; fictional work and engine I/O."""
import shutil

from service_support import configure, serve
from tests.support import REPO, add_worktree, git, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, server, state as S, tasks as T


QUESTION = "May I implement this conversation layout?"
HOLD = "Operator reviews the design presentation and final PR."


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    with config.add_project("atlas", path=repo):
        pass
    row = T.new("atlas", "Conversation layout", "Review the fictional conversation proposal.", hold_merge=HOLD)
    slug = row["slug"]
    worktree = add_worktree(repo, slug)
    T.dispatch("atlas", slug, attempt=1, session_id="fixture-design", agent_id="fixture-design",
               worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
    folder = worktree / "design" / "wireframes"
    folder.mkdir(parents=True)
    captures = folder / "captures"
    captures.mkdir()
    with (worktree / ".gitignore").open("a") as stream:
        stream.write("design/wireframes/captures/\n")
    shutil.copyfile(REPO / "docs/images/project-phone.png", captures / "phone.png")
    shutil.copyfile(REPO / "docs/images/project-desktop.png", folder / "desktop.png")
    assert git("check-ignore", "design/wireframes/captures/phone.png", cwd=worktree).strip()
    assert "design/wireframes/desktop.png" in git("ls-files", "--others", "--exclude-standard", cwd=worktree)
    assert not git("ls-files", "--", "design/wireframes", cwd=worktree)
    proposal = folder / "proposal.md"
    proposal.write_text("Keep the conversation easy to read. The question and its reply share one place.\n\n"
                        "**Captured states:** Phone and desktop.\n\n<script>window.proposalExecuted = true</script>\n")
    design = {"title": "Conversation layout", "proposal": "design/wireframes/proposal.md",
              "images": [{"title": "Phone conversation", "path": "design/wireframes/captures/phone.png"},
                         {"title": "Desktop conversation", "path": "design/wireframes/desktop.png"}]}

    def present():
        T.block("atlas", slug, QUESTION, actor="l2", expected_attempt=1,
                updates={"waiting_on": "burak"}, recommendation="Use the captured conversation layout.",
                recommendation_label="Use this design", design=design)

    def later_updates():
        for index in range(20):
            T.message("atlas", slug, "l2", f"Walkthrough update {index + 1}: "
                      "The saved implementation preview includes phone and desktop states. "
                      "The question remains open and the merge hold is unchanged.")

    T.message("atlas", slug, "l2", "The screenshots show the proposed conversation layout on phone and desktop.")
    present()

    class Handler(server.Handler):
        def do_POST(self):
            if self.path == "/fixture/checkpoint":
                question = S.load_task("atlas", slug)["questions"][-1]
                response = question["response"]
                assert response["text"] == "Use the captured conversation layout."
                T.resolve_question("atlas", slug, question["id"], question["revision"], response["message_id"],
                                   disposition="answered", reason=response["text"], expected_attempt=1)
                return self._json({"ok": True})
            if self.path == "/fixture/park":
                # The owner parks its saved group again after reading a reply; open questions return as asked again.
                with server._bg_guard:
                    resumed = server._bg.get(f"resume:atlas:{slug}")
                if resumed:
                    resumed.join(10)
                    assert not resumed.is_alive(), "The fixture resume did not finish"
                task = S.load_task("atlas", slug)
                if task["state"] == "blocked":
                    T.resume("atlas", slug)
                T.block("atlas", slug, T._groups(task)[-1]["reason"], actor="l2", expected_attempt=1)
                return self._json(T.question_group_view("atlas", S.load_task("atlas", slug)))
            if self.path == "/fixture/implementation-review":
                question = S.load_task("atlas", slug)["questions"][-1]
                answer = T.message("atlas", slug, T.OPERATOR_MESSAGE_ROLE, "Use the proposed layout.")
                T.resolve_question("atlas", slug, question["id"], question["revision"], answer["id"],
                                   disposition="answered", reason="Use the proposed layout.", expected_attempt=1)
                T.resume("atlas", slug)
                proposal.write_text("Review the implemented conversation layout before merging. "
                                    "The earlier proposal is approved; this implementation still needs review.")
                T.block("atlas", slug, "Review the implemented conversation layout before merging?",
                        actor="l2", expected_attempt=1, updates={"waiting_on": "burak"},
                        design={**design, "title": "Conversation layout — implementation review"})
                later_updates()
                return self._json({"ok": True})
            if self.path == "/fixture/group-review":
                T.resume("atlas", slug)
                T.block("atlas", slug, "Review the implementation and choose a release date.",
                        actor="l2", expected_attempt=1, updates={"waiting_on": "burak"},
                        questions={"questions": [{"question": "Should the release be on Monday?"}]})
                later_updates()
                return self._json({"ok": True})
            if self.path == "/fixture/resolve-question":
                question_id = self._body()["id"]
                question = next(q for q in S.load_task("atlas", slug)["questions"] if q["id"] == question_id)
                answer = T.message("atlas", slug, T.OPERATOR_MESSAGE_ROLE, "Yes, use this approach.")
                T.resolve_question("atlas", slug, question_id, question["revision"], answer["id"],
                                   disposition="answered", reason="Use this approach.", expected_attempt=1)
                return self._json({"ok": True})
            if self.path == "/fixture/revise":
                T.resume("atlas", slug)
                proposal.write_text("The revised proposal keeps replies together and moves the question below the explanation.")
                present()
                return self._json({"ok": True})
            if self.path == "/fixture/proposal-v5":
                T.resume("atlas", slug)
                proposal.write_text("Proposal v5 uses tabs for settings, model choice and menus.")
                T.block("atlas", slug, "Approve the tabbed design (v5)?", actor="l2", expected_attempt=1,
                        updates={"waiting_on": "burak"}, recommendation="Build the v5 tabbed proposal.",
                        recommendation_label="Build v5", design={**design, "title": "Proposal v5: settings, model choice and menus"})
                return self._json({"ok": True})
            if self.path == "/fixture/damage-snapshot":
                question = S.load_task("atlas", slug)["questions"][-1]
                image = question["design"]["images"][0]
                saved = S.task_dir("atlas", slug) / "designs" / image["name"]
                saved.write_bytes(b"Changed snapshot")
                return self._json({"ok": True})
            if self.path == "/fixture/edit-worktree":
                proposal.write_text("Unpresented worktree edit: this must never replace the saved proposal.")
                (captures / "phone.png").unlink()
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
