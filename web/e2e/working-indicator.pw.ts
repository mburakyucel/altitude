import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "working-indicator-service.py" });

test.setTimeout(60_000);

// The operator's report: the coordinator's first words arrived, the turn kept working, and the page looked finished.
test("the typing indicator stays under a streamed reply until the turn ends, across a queued message and a reload", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const main = page.getByRole("main");
  const convo = main.getByRole("region", { name: "Conversation", exact: true });
  const field = main.getByRole("textbox", { name: "Message L3 about atlas" });
  const typing = convo.getByRole("status", { name: "L3 is answering", exact: true });
  const status = main.locator(phone ? ".phone-status" : ".project-header p[aria-live]");
  const first = convo.locator(".reply").filter({ hasText: "A clear request, so I'll ground it in the code first." });
  const more = convo.locator(".reply").filter({ hasText: "Found the cause: the page hid its indicator after the first words." });
  const queued = convo.locator(".queued-row").filter({ hasText: "A note for afterwards" });
  const answering = phone ? "L3 · Answering" : /^L3 is answering/;

  await walk.open("/projects/atlas");
  await field.fill("Fix the indicator");
  await convo.getByRole("button", { name: "Send", exact: true }).click();
  await walk.state("01-first-words-still-working", { visible: [first, typing], hidden: [more] });
  await expect(status).toContainText(answering);
  // The indicator follows the words it continues.
  expect(await first.evaluate((reply, dots) => Boolean(reply.compareDocumentPosition(dots!) & Node.DOCUMENT_POSITION_FOLLOWING),
    await typing.elementHandle())).toBe(true);

  await field.fill("A note for afterwards");
  await convo.getByRole("button", { name: "Queue", exact: true }).click();
  await walk.state("02-queued-message-still-working", { visible: [first, typing, queued, convo.getByRole("button", { name: "Send now", exact: true })], hidden: [more] });

  // The fixture control: its first press lets the turn write more, its second ends the turn.
  expect((await request.put("/fixture")).ok()).toBe(true);
  await walk.state("03-more-words-appended-still-working", { visible: [first, typing], hidden: [] });
  await expect(convo.locator(".reply").filter({ hasText: "Found the cause" }).locator("p")).toHaveCount(2);
  await expect(typing).toHaveCount(1);

  // A reload reads the running turn from the server: the message, the indicator and the active label.
  await page.reload();
  await walk.state("04-reloaded-mid-turn", { visible: [convo.locator(".bubble").filter({ hasText: "Fix the indicator" }), typing, queued], hidden: [] });
  await expect(status).toContainText(answering);

  expect((await request.put("/fixture")).ok()).toBe(true);
  await walk.state("05-turn-ended", {
    visible: [first, convo.locator(".reply").filter({ hasText: "Queued note answered." })],
    hidden: [typing, queued],
  });
  await expect(status).toContainText(phone ? "L3 · Ready" : /^L3 answered/);
  await expect(first).toHaveCount(1);
  await expect(convo.getByText("A clear request, so I'll ground it in the code first.", { exact: true })).toHaveCount(1);
  await expect(convo.getByText("Found the cause: the page hid its indicator after the first words.", { exact: true })).toHaveCount(1);
});
