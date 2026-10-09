import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { test } from "./fixtures";

// The README and walkthrough illustrations (design/readme/README.md). With ALTITUDE_README_IMAGES naming a
// folder, the journey also writes its captures there at 2× and records the phone run as phone-walkthrough.webm.
const images = process.env.ALTITUDE_README_IMAGES ? resolve(process.env.ALTITUDE_README_IMAGES) : undefined;
test.use({
  serviceScript: "readme-service.py", deviceScaleFactor: 2, locale: "en-US", timezoneId: "UTC", colorScheme: "light",
  ...(images ? { video: async ({}, use, info) => use(info.project.name === "phone"
    ? { mode: "on", size: { width: 390, height: 844 } } : "off") } : {}),
});
test.setTimeout(90_000);

test("project, work, task steering, live session and a decision", async ({ page }, info) => {
  const phone = info.project.name === "phone";
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  async function capture(name: string) {
    await page.evaluate(() => document.fonts.ready);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), `${name}: horizontal overflow`).toBe(false);
    if (!images) return;
    await mkdir(images, { recursive: true });
    await page.screenshot({ path: resolve(images, `${name}-${info.project.name}.png`), animations: "disabled" });
    if (phone) await page.waitForTimeout(1400); // the recording holds each state long enough to read
  }
  const nav = page.getByRole("navigation", { name: phone ? "Primary" : "Rail", exact: true });
  const work = page.getByRole("region", { name: "Work", exact: true });
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const live = page.getByRole("region", { name: "Live session", exact: true });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });

  await page.goto("/projects/atlas");
  await expect(page.getByRole("region", { name: "Conversation", exact: true })).toContainText("Three L2 owners");
  if (!phone) await expect(work).toContainText("Preserve client compatibility");
  await capture("project");
  if (phone) {
    await nav.getByRole("link", { name: "Work", exact: true }).click();
    await expect(work).toContainText("Preserve client compatibility");
    await capture("work");
  }

  await work.locator('a[href="/projects/atlas/tasks/preserve-client-compatibility"]').first().click();
  await expect(conversation).toContainText("bind each token");
  if (!phone) {
    const toggle = page.getByRole("button", { name: "Live session", exact: true });
    if (await toggle.getAttribute("aria-pressed") !== "true") await toggle.click();
    await expect(live).toContainText("compatibility boundary");
  }
  await capture("task");
  await field.fill("Include the rollback case in the PR notes.");
  await conversation.getByRole("button", { name: "Send", exact: true }).click();
  await expect(conversation.locator(".bubble").filter({ hasText: "Include the rollback case" })).toBeVisible();
  await expect(field).toHaveValue("");
  if (phone) {
    await page.getByRole("navigation", { name: "Task views" }).getByRole("link", { name: "Live session", exact: true }).click();
    await expect(live).toContainText("compatibility boundary");
    await capture("session");
  }

  await nav.getByRole("link", { name: /Needs you/ }).click();
  await expect(page.getByRole("heading", { name: "Needs you", exact: true })).toBeVisible();
  const decision = page.getByRole("article", { name: "Build resumable index backfill", exact: true });
  await expect(decision).toContainText("How long should we keep the old index for rollback?");
  await capture("decision");
  await decision.getByRole("button", { name: "7 days", exact: true }).click();
  await page.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect(decision).toBeHidden();
  await expect(page.getByText("Nothing needs you.", { exact: true })).toBeVisible();
  if (images && phone) await page.waitForTimeout(1400);
  expect(errors).toEqual([]);

  const video = page.video();
  if (images && video) {
    await page.close();
    await video.saveAs(resolve(images, "phone-walkthrough.webm"));
  }
});
