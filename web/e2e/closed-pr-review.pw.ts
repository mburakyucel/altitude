import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Issue #575: a held PR closed without merging stops asking for review in Needs you, the task
 * conversation and the Work row, while the task's independent question stays answerable.
 */
test.use({ serviceScript: "closed-pr-review-service.py" });
test.setTimeout(90_000);

test("a held PR closed without merging leaves review; the independent question stays", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const primary = page.getByRole("navigation", { name: phone ? "Primary" : "Rail", exact: true });
  const badge = (count: number) => primary.getByRole("link", { name: /Needs you/ }).locator(".badge").filter({ hasText: new RegExp(`^${count}$`) });
  const main = page.getByRole("main");
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const review = conversation.locator("[data-review-pr='42']");
  const reviewText = page.getByText("Review PR #42 before merge", { exact: true });
  const question = page.getByText("Which region should the installer default to?", { exact: true });
  const asked = conversation.getByText("Which region should the installer default to?", { exact: true }).first();
  const work = page.getByRole("region", { name: "Work", exact: true }).getByRole("link", { name: /^Runtime port ·/ });

  await walk.open("/");
  await walk.state("01-needs-you-question-and-review", { visible: [question, reviewText, badge(2)], hidden: [] });
  await walk.open("/projects/atlas/tasks/runtime-port");
  await walk.state("02-chat-question-and-review", {
    visible: [review, asked,
      main.getByText("Your turn · 1 question · review PR #42").first()],
    hidden: [],
  });

  expect(await (await request.post("/fixture/close")).json()).toEqual({ recorded: true });
  await walk.open("/projects/atlas/tasks/runtime-port");
  await walk.state("03-chat-closed-pr-asks-no-review", {
    visible: [asked, main.getByText("Your turn · 1 question").first(), badge(1)],
    hidden: [review, reviewText, main.getByText(/review PR #42/)],
  });
  await walk.open("/");
  await walk.state("04-needs-you-question-only", { visible: [question, badge(1)], hidden: [reviewText] });
  await walk.open("/projects/atlas?tab=work");
  await walk.state("05-work-row-question-only", {
    visible: [work.getByText("Your turn · 1 question", { exact: true })],
    hidden: [work.getByText(/review PR/)],
  });
});
