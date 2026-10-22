import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { test } from "./fixtures";

// The README's feature illustrations (design/readme/README.md#feature-pictures): a task created from the
// coordinator's offer, a design proposal with its review, a task page with its question and live session, a pull
// request waiting for the operator's review, a command typed into the task's terminal, a fault the coordinator
// repairs, improvement suggestions, and a project's routines with the tasks one suggested. With ALTITUDE_README_IMAGES naming a folder, each state is written there as
// `<feature>-<viewport>.png` at 2×.
const images = process.env.ALTITUDE_README_IMAGES ? resolve(process.env.ALTITUDE_README_IMAGES) : undefined;
test.use({ serviceScript: "readme-features-service.py", deviceScaleFactor: 2, locale: "en-US", timezoneId: "UTC", colorScheme: "light" });
test.setTimeout(90_000);

test("a created task, a proposal review, the task view, review before merge, a repaired fault, improvement suggestions and routines", async ({ page, request }, info) => {
  const phone = info.project.name === "phone";
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  async function capture(name: string) {
    await page.evaluate(() => document.fonts.ready);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), `${name}: horizontal overflow`).toBe(false);
    if (!images) return;
    await mkdir(images, { recursive: true });
    await page.screenshot({ path: resolve(images, `${name}-${info.project.name}.png`), animations: "disabled" });
  }
  const nav = page.getByRole("navigation", { name: phone ? "Primary" : "Rail", exact: true });
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });

  // The coordinator's reply offers a task; one press creates it, and its card appears under the reply.
  await page.goto("/projects/atlas");
  const chat = page.getByRole("region", { name: "Conversation", exact: true });
  const offer = chat.getByRole("button", { name: "Create task", exact: true });
  await expect(offer).toBeVisible();
  await capture("create-1-offer");
  await offer.click();
  const created = chat.locator(".task-card").filter({ hasText: "Hold cursors during the index switch" });
  await expect(created).toBeVisible();
  await expect(chat.getByRole("button", { name: "Task created", exact: true })).toBeVisible();
  // The coordinator's turn has ended: its reply shows and it no longer answers.
  await expect(chat.getByText("Started it, with the empty searches at the switch in its brief.", { exact: true })).toBeVisible();
  await expect(chat.getByRole("status", { name: "L3 is answering" })).toBeHidden();
  await capture("create-2-created");

  // The synonyms editor's owner asks to build its design; Needs you shows the proposal, its document and its review.
  await nav.getByRole("link", { name: /Needs you/ }).click();
  const proposal = page.getByRole("article", { name: "Add a synonyms editor for search admins", exact: true });
  await expect(proposal).toContainText("Build the synonyms editor as shown?");
  await expect(proposal.getByRole("button", { name: "Approve proposal", exact: true })).toBeEnabled();  // Needs you has read its current list
  await proposal.evaluate((card) => card.scrollIntoView({ block: "start" }));
  await capture("proposal");
  await proposal.getByRole("link", { name: /Open L2 chat/ }).click();
  await expect(conversation).toContainText("Here is the editor I propose");
  await conversation.getByRole("button", { name: "Show proposal review details", exact: true }).click();
  await conversation.locator(`[data-review-id="readme-synonyms-proposal"]`).evaluate((card) => card.scrollIntoView({ block: "start" }));
  await capture("proposal-review");

  // The task view: the typo-tolerance owner's conversation and open question, beside its live session on a computer.
  await page.goto(phone ? "/projects/atlas?tab=work" : "/projects/atlas");
  await page.getByRole("region", { name: "Work", exact: true })
    .getByRole("link", { name: /Add typo tolerance to search/ }).first().click();
  await expect(conversation).toContainText("Allow two typos in long words?");
  if (!phone) {
    const toggle = page.getByRole("button", { name: "Live session", exact: true });
    if (await toggle.getAttribute("aria-pressed") !== "true") await toggle.click();
    await expect(page.getByRole("region", { name: "Live session", exact: true })).toContainText("One choice is yours");
  }
  const latest = conversation.getByRole("button", { name: "Latest messages", exact: true });
  if (await latest.isVisible()) await latest.click();  // the conversation's end: the question and its answers
  await expect(latest).toBeHidden();
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());  // the jump to the question focuses it
  await capture("task-view");

  // The other engine's review is done, both findings fixed; the PR waits for the operator's merge.
  await page.goto(phone ? "/projects/atlas?tab=work" : "/projects/atlas");
  await page.getByRole("region", { name: "Work", exact: true })
    .getByRole("link", { name: /Add per-tenant limits to the public API/ }).first().click();
  await expect(conversation).toContainText("Per-tenant limits are ready in PR #214");
  await expect(conversation.getByRole("button", { name: "Approve merge", exact: true })).toBeVisible();
  await expect(conversation).toContainText("2 findings, both resolved");
  // The review's findings, open, then the same PR in Needs you with its Approve merge.
  await conversation.getByRole("button", { name: "Show implementation review details", exact: true }).click();
  await expect(conversation).toContainText("alternate engine");
  await conversation.locator(`[data-review-id="readme-limits-changes"]`).evaluate((card) => card.scrollIntoView({ block: "start" }));
  await capture("review-findings");
  await nav.getByRole("link", { name: /Needs you/ }).click();
  const review = page.getByRole("article", { name: "Add per-tenant limits to the public API", exact: true });
  await expect(review).toContainText("Review PR #214 before merge");
  await expect(review.getByRole("button", { name: "Approve merge", exact: true })).toBeEnabled();
  await review.evaluate((card) => card.scrollIntoView({ block: "start" }));
  await capture("review");

  // The owner hands over a step only the operator can take; it arrives typed in the task's terminal, waiting for Enter.
  expect((await request.post("/api/terminal-access", { data: { enabled: true } })).ok()).toBe(true);
  await page.goto(phone ? "/projects/atlas?tab=work" : "/projects/atlas");
  await page.getByRole("region", { name: "Work", exact: true })
    .getByRole("link", { name: /Sign billing webhook payloads/ }).first().click();
  const command = conversation.getByRole("group", { name: "Command" });
  await expect(command.locator("pre")).toHaveText("make deploy ENV=staging");
  await command.getByRole("button", { name: "Open in terminal" }).click();
  await expect(page).toHaveURL(/\/terminal$/);
  const screen = page.locator(".terminal-screen .xterm-rows");
  await expect(screen).toContainText("atlas $ make deploy ENV=staging");
  await page.waitForTimeout(1_000);  // longer than the page's settle time: nothing more arrives
  // Ctrl+L clears the line naming the fixture's temporary folder; the typed command stays at the prompt.
  await page.locator(".terminal-screen").click();
  await page.keyboard.press("Control+l");
  await expect(screen).not.toContainText("Runs as you");
  await expect(screen).toContainText("atlas $ make deploy ENV=staging");
  await capture("terminal-2-typed");

  // A machine problem pauses the sign-on task; the coordinator repairs it and the task resumes.
  const signOn = page.getByRole("region", { name: "Work", exact: true })
    .getByRole("link", { name: /Add SAML single sign-on/ }).first();
  await page.goto(phone ? "/projects/atlas?tab=work" : "/projects/atlas");
  await expect(signOn).toContainText("A system problem paused work.");
  await signOn.scrollIntoViewIfNeeded();
  await capture("heal-1-fault");
  expect((await request.put("/fixture/repair")).ok()).toBe(true);
  await page.goto(phone ? "/projects/atlas?tab=work" : "/projects/atlas");  // the coordinator's turn reaches the chat on its next load
  await expect(signOn).toContainText("L2 working");
  await signOn.scrollIntoViewIfNeeded();  // the resumed task joins the end of the working list
  if (phone) await nav.getByRole("link", { name: "Chat", exact: true }).click();
  await expect(chat).toContainText("resumed the task");
  await chat.getByRole("button", { name: "Show", exact: true }).last().click();
  await expect(chat.getByRole("article", { name: /Add SAML single sign-on/ })).toContainText("L3 replied");
  await capture("heal-2-repaired");

  // Improvement suggestions in the Altitude project's chat, each one a task away.
  await page.goto("/projects/altitude");
  const suggestions = page.getByRole("article", { name: /^Improvement suggestions/ });
  await expect(suggestions).toContainText("Owners re-ask questions you already answered");
  await expect(suggestions.getByRole("button", { name: "Make a task" })).toHaveCount(3);
  await suggestions.evaluate((card) => card.scrollIntoView({ block: "start" }));
  await capture("improve");
  await suggestions.getByRole("button", { name: "Make a task" }).first().click();
  await expect(suggestions).toContainText("L3 is opening a task");
  await capture("improve-accepted");

  // Harbor's routines: the built-in dependency check found something, and its suggested tasks wait in
  // Needs you, each a task away.
  await page.goto("/projects/harbor/agents");
  await expect(page.getByRole("heading", { level: 1, name: "Routines", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /^Dependency check/ })).toContainText("Found something");
  await expect(page.getByRole("button", { name: "Turn on" })).toHaveCount(2);  // Build health and What landed
  await capture("routines");
  await nav.getByRole("link", { name: /Needs you/ }).click();
  const routine = page.getByRole("article", { name: "Suggested tasks from Dependency check", exact: true });
  await expect(routine.getByRole("button", { name: "Make a task" })).toHaveCount(2);
  await routine.evaluate((card) => card.scrollIntoView({ block: "start" }));
  await capture("routines-suggestion");
  expect(errors).toEqual([]);
});
