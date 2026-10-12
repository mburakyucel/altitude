import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { devices, expect, type Page } from "@playwright/test";
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

// The README's opening loop: the phone answers the backfill decision while the desktop project view watches the
// task go back to work. One run drives both screens on the same service; ALTITUDE_README_IMAGES receives
// `opening-<frame>-desktop.png` and `opening-<frame>-phone.png` for design/readme/opening.py.
test.describe(() => {
  // The phone project skips before its fixtures start: a service stopped mid-startup can overrun the cleanup limit.
  test.skip(({ isMobile }) => isMobile, "one run drives the desktop and the phone together");
  test("the phone answers a decision and the desktop sees the task resume", async ({ page, browser, altitude }) => {
    const errors: string[] = [];
    const phoneContext = await browser.newContext({
      ...devices["Pixel 7"], viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, baseURL: altitude.url,
      locale: "en-US", timezoneId: "UTC", colorScheme: "light",
    });
    await phoneContext.addCookies([{ name: "altitude_device", value: altitude.device, url: altitude.url }]);
    await phoneContext.route(/^https?:\/\/(?!127\.0\.0\.1[:/])/, route => route.abort());
    const phone = await phoneContext.newPage();
    try {
      for (const each of [page, phone]) each.on("pageerror", (error) => errors.push(error.message));
      async function frame(name: string) {
        for (const [view, each] of [["desktop", page], ["phone", phone]] as [string, Page][]) {
          await each.evaluate(() => document.fonts.ready);
          if (images) {
            await mkdir(images, { recursive: true });
            await each.screenshot({ path: resolve(images, `opening-${name}-${view}.png`), animations: "disabled" });
          }
        }
      }
      const backfill = page.getByRole("region", { name: "Work", exact: true })
        .locator('a[href^="/projects/atlas/tasks/build-resumable-index-backfill"]').first();
      const decision = phone.getByRole("article", { name: "Build resumable index backfill", exact: true });

      await page.goto("/projects/atlas");
      await expect(backfill).toContainText("Your turn");
      await phone.goto("/");
      await expect(decision).toContainText("How long should we keep the old index for rollback?");
      await frame("1-question");
      await decision.getByRole("button", { name: "7 days", exact: true }).click();
      await expect(phone.getByRole("button", { name: "Send 1 answer", exact: true })).toBeEnabled();
      await frame("2-chosen");
      await phone.getByRole("button", { name: "Send 1 answer", exact: true }).click();
      await expect(phone.getByText("Nothing needs you.", { exact: true })).toBeVisible();
      await expect(backfill).not.toContainText("Your turn");
      await expect(backfill).toContainText("L2 replying to you");
      await backfill.scrollIntoViewIfNeeded(); // the resumed task joins the end of the working list
      await frame("3-resumed");
      expect(errors).toEqual([]);
    } finally {
      await phoneContext.close();
    }
  });
});
