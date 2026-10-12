import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { test } from "./fixtures";

// The README's feature illustrations (design/readme/README.md#features): a pull request waiting for the operator's
// review, a fault the coordinator repairs, and improvement suggestions. With ALTITUDE_README_IMAGES naming a folder,
// each state is written there as `<feature>-<viewport>.png` at 2×.
const images = process.env.ALTITUDE_README_IMAGES ? resolve(process.env.ALTITUDE_README_IMAGES) : undefined;
test.use({ serviceScript: "readme-features-service.py", deviceScaleFactor: 2, locale: "en-US", timezoneId: "UTC", colorScheme: "light" });
test.setTimeout(90_000);

test("review before merge, a repaired fault and improvement suggestions", async ({ page, request }, info) => {
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

  // The other engine's review is done, both findings fixed; the PR waits for the operator's merge.
  await page.goto(phone ? "/projects/atlas?tab=work" : "/projects/atlas");
  await page.getByRole("region", { name: "Work", exact: true })
    .getByRole("link", { name: /Add per-tenant limits to the public API/ }).first().click();
  await expect(conversation).toContainText("Per-tenant limits are ready in PR #214");
  await expect(conversation.getByRole("button", { name: "Approve merge", exact: true })).toBeVisible();
  await expect(conversation).toContainText("2 findings, both resolved");
  if (phone) {  // the phone shows the same review in Needs you, where its Approve merge stays in view
    await nav.getByRole("link", { name: /Needs you/ }).click();
    const review = page.getByRole("article", { name: "Add per-tenant limits to the public API", exact: true });
    await expect(review).toContainText("Review PR #214 before merge");
    await review.evaluate((card) => card.scrollIntoView({ block: "end" }));
  } else {  // the conversation alone, with the review's findings open: this owner's session has finished
    await conversation.getByRole("button", { name: "Show implementation review details", exact: true }).click();
    await expect(conversation).toContainText("alternate engine");
    const panel = page.getByRole("button", { name: "Live session", exact: true });
    if (await panel.getAttribute("aria-pressed") === "true") await panel.click();
    await conversation.getByRole("button", { name: "Approve merge", exact: true }).scrollIntoViewIfNeeded();
  }
  await capture("review");

  // A machine problem pauses the sign-on task; the coordinator repairs it and the task resumes.
  const signOn = page.getByRole("region", { name: "Work", exact: true })
    .getByRole("link", { name: /Add SAML single sign-on/ }).first();
  await page.goto(phone ? "/projects/atlas?tab=work" : "/projects/atlas");
  await expect(signOn).toContainText("A system problem paused work.");
  await signOn.scrollIntoViewIfNeeded();
  await capture("heal-1-fault");
  expect((await request.post("/fixture/repair")).ok()).toBe(true);
  await page.goto(phone ? "/projects/atlas?tab=work" : "/projects/atlas");  // the coordinator's turn reaches the chat on its next load
  await expect(signOn).toContainText("L2 working");
  if (phone) await nav.getByRole("link", { name: "Chat", exact: true }).click();
  const chat = page.getByRole("region", { name: "Conversation", exact: true });
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
  expect(errors).toEqual([]);
});
