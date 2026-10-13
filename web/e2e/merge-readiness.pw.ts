import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "merge-readiness-service.py" });
const conversation = (page: import("@playwright/test").Page) => page.getByRole("region", { name: "Task conversation", exact: true });

test("unfinished held deliveries keep findings and waits without automatic approval", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/");
  await walk.state("01-needs-you-ready-and-independent-choice", {
    visible: [page.getByRole("article", { name: "Ready feature", exact: true }),
      page.getByRole("article", { name: "Independent choice", exact: true })],
    hidden: [page.getByRole("article", { name: "Unfinished feature", exact: true }),
      page.getByRole("article", { name: "Unfinished report", exact: true })],
  });
  const independent = page.getByRole("article", { name: "Independent choice", exact: true });
  await expect(independent.getByRole("button", { name: /^West/ })).toBeVisible();
  await expect(independent.getByRole("button", { name: "Approve merge", exact: true })).toHaveCount(0);
  for (const slug of ["unfinished-feature", "unfinished-report"]) {
    await walk.open(`/projects/atlas/tasks/${slug}`);
    const chat = conversation(page);
    await walk.state(`02-${slug}-no-approval`, {
      visible: [page.getByText("Merge held", { exact: true }).filter({ visible: true }).first(),
        chat.getByText("Shared API remains pending", { exact: true })],
      hidden: [chat.getByRole("button", { name: "Approve merge", exact: true }),
        chat.getByText("Your turn · review before merge", { exact: true }), page.getByText(/Your turn · review PR/)],
    });
    const task = await request.get(`/api/task/atlas/${slug}`).then(response => response.json());
    expect(task.hold_merge).toBe("Operator reviews the finished feature");
    expect(task.review.subjects.changes.open).toBe(1);
    if (slug === "unfinished-feature") {
      await expect(chat.getByText("Waiting for the shared API delivery.", { exact: true }).first()).toBeVisible();
    }
  }
  await walk.open("/projects/atlas/tasks/independent-choice");
  const chat = conversation(page);
  await walk.state("03-independent-question-with-prerequisite", {
    visible: [chat.getByText("Which preview region?", { exact: true }), chat.getByRole("button", { name: /^West/ })],
    hidden: [chat.getByRole("button", { name: "Approve merge", exact: true })],
  });
  await chat.getByRole("button", { name: /^West/ }).click();
  await chat.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect(chat.getByText("Sent · the L2 has your reply.", { exact: true })).toBeVisible();
  const saved = await request.get("/api/task/atlas/independent-choice").then(response => response.json());
  expect(saved.questions.find((question: { audience: string }) => question.audience === "l3").status).toBe("open");
  expect(saved.review.subjects.changes.open).toBe(1);
  expect(saved.hold_merge).toBe("Operator reviews the finished feature");
});

test("a stale approval writes nothing and readiness restoration permits retry", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/projects/atlas/tasks/ready-feature");
  const chat = conversation(page);
  await walk.state("04-ready-held-approval", {
    visible: [chat.getByRole("button", { name: "Approve merge", exact: true }),
      chat.getByRole("link", { name: "View PR #45", exact: true })], hidden: [],
  });
  // Keep the card's rendered snapshot while the real server record gains a prerequisite.
  await page.route("**/api/overview", route => route.abort());
  await page.route("**/api/task/atlas/ready-feature", route => route.abort());
  await request.post("/fixture/block-ready");
  const before = await request.get("/api/task/atlas/ready-feature").then(response => response.json());
  const rejected = page.waitForResponse(response => response.url().endsWith("/api/l2/message") && response.request().method() === "POST");
  await chat.getByRole("button", { name: "Approve merge", exact: true }).click();
  expect((await rejected).status()).toBe(409);
  await walk.state("05-stale-approval-not-sent", {
    visible: [chat.getByRole("button", { name: "Retry, Approve merge", exact: true }), chat.getByRole("alert")], hidden: [],
  });
  await expect(chat.getByRole("alert")).toHaveText("Not sent");
  const after = await request.get("/api/task/atlas/ready-feature").then(response => response.json());
  expect(after.messages).toEqual(before.messages);
  expect(after.questions).toEqual(before.questions);
  expect(after.hold_merge).toBe(before.hold_merge);
  expect(after.review.subjects.changes.open).toBe(1);
  await request.post("/fixture/clear-ready");
  const accepted = page.waitForResponse(response => response.url().endsWith("/api/l2/message") && response.request().method() === "POST");
  await chat.getByRole("button", { name: "Retry, Approve merge", exact: true }).click();
  expect((await accepted).status()).toBe(200);
  await walk.state("06-ready-approval-sent", {
    visible: [chat.getByRole("button", { name: "Approve merge, waiting for the L2", exact: true })],
    hidden: [chat.getByRole("alert")],
  });
});

test("denied approval preserves the card and disables its action", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/projects/atlas/tasks/ready-feature");
  const chat = conversation(page);
  await page.route("**/api/l2/message", route => route.fulfill({ status: 403,
    contentType: "application/json", body: JSON.stringify({ error: "Access denied" }) }));
  await chat.getByRole("button", { name: "Approve merge", exact: true }).click();
  await walk.state("07-approval-denied", {
    visible: [chat.getByRole("alert"), chat.getByRole("button", { name: "Approve merge", exact: true })],
    hidden: [chat.getByRole("button", { name: "Retry, Approve merge", exact: true })],
  });
  await expect(chat.getByRole("alert")).toHaveText("You cannot approve here.");
  await expect(chat.getByRole("button", { name: "Approve merge", exact: true })).toHaveAttribute("aria-disabled", "true");
});
