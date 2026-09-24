import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject, fixtureTask } from "./fixture-data";
import { walkthrough } from "./walkthrough";

test("chat headers leave room for reading with long task titles", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const task = await fixtureTask(request, project.name);
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const title = "Preserve existing pagination tokens while migrating the Atlas search index and verifying resumable backfills for every supported client";
  await page.route((url) => url.pathname === `/api/task/${project.name}/${task.slug}`, (route) => route.fulfill({ json: {
    ...task, title, state: "running", attempt: 2, hold_merge: "Review the completed migration before merging.",
    messages: Array.from({ length: 30 }, (_, index) => ({ id: `density-${index}`, at: "2026-09-08T12:00:00Z", role: "l2", text: `Migration checkpoint ${index + 1}: existing clients retain their pagination tokens while the backfill continues.` })),
  } }));
  const measurements = [];
  for (const viewport of phone ? [{ width: 390, height: 844 }] : [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }]) {
    await page.setViewportSize(viewport);
    await walk.open(`/projects/${project.name}/tasks/${task.slug}`);
    const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
    await expect(field).toBeVisible();
    await walk.state(`${viewport.width}-task`, { visible: [page.getByRole("heading", { name: title, exact: true }), field], hidden: [] });
    const header = await page.locator(phone ? ".phone-header" : ".task-header").boundingBox();
    const scroller = page.locator(".task-main .convo-scroll, .task-page[data-phone] .convo-scroll");
    const reading = await scroller.boundingBox();
    measurements.push({ viewport, task: header, reading });
    // Reading budgets include this long title and a merge hold; no clipped titles or smaller controls.
    expect(header!.height).toBeLessThanOrEqual(phone ? 54 : viewport.width < 1280 ? 120 : 96);
    expect(reading!.height).toBeGreaterThanOrEqual(phone ? 546 : viewport.height === 900 ? 650 : 500);
    await expect(field).toBeInViewport();
    expect(await page.locator("body").evaluate((node) => node.scrollWidth)).toBeLessThanOrEqual(viewport.width);
    if (!phone) {
      expect(await page.locator(".task-title").evaluate((node) => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
      await expect(page.locator(".task-header").getByText("Merge held", { exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Reject", exact: true })).toBeInViewport();
    }
    await field.fill("Keep this migration draft.");
    await expect(async () => {
      await scroller.evaluate((node) => { node.scrollTop = 170; });
      await expect(page.getByRole("button", { name: "Latest messages", exact: true })).toBeVisible({ timeout: 250 });
      expect(await scroller.evaluate((node) => node.scrollTop)).toBe(170);
    }).toPass();
    const details = page.getByRole("dialog", { name: "Task details", exact: true });
    const usage = page.getByRole("region", { name: "Task token usage", exact: true });
    await walk.state(`${viewport.width}-details`, {
      action: () => page.getByRole("button", { name: /Task details$/ }).click(),
      visible: [details, details.getByText(title, { exact: true }), usage, details.getByText("Review the completed migration before merging.")], hidden: [],
    });
    await walk.state(`${viewport.width}-restored`, {
      action: () => page.keyboard.press("Escape"), visible: [field], hidden: [details, usage],
    });
    await expect(field).toHaveValue("Keep this migration draft.");
    expect(await scroller.evaluate((node) => node.scrollTop)).toBe(170);
    const live = page.getByRole("region", { name: "Live session", exact: true });
    const toggle = page.getByRole("button", { name: "Live session", exact: true });
    if (phone) await page.getByRole("link", { name: "Live session", exact: true }).click();
    else if (await toggle.getAttribute("aria-pressed") !== "true") await toggle.click();
    await walk.state(`${viewport.width}-live`, { visible: [live], hidden: [] });
    if (phone) await page.getByRole("link", { name: "Conversation", exact: true }).click();
    else if (viewport.width < 1280) await page.keyboard.press("Escape");
    else await toggle.click();
    await expect(field).toHaveValue("Keep this migration draft.");
    await expect(live).toBeHidden();
    await field.fill("");
    await page.getByRole("button", { name: "Back", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${project.path}$`));
    await expect(page.locator(phone ? ".phone-header" : ".project-header")).toBeVisible();
    await walk.state(`${viewport.width}-project`, { visible: [page.getByRole("button", { name: "More actions" })], hidden: [] });
    const projectHeader = await page.locator(phone ? ".phone-header" : ".project-header").boundingBox();
    expect(projectHeader!.height).toBeLessThanOrEqual(phone ? 54 : 60);
    measurements.push({ viewport, project: projectHeader });
  }
  await info.attach("header-measurements", { body: JSON.stringify(measurements, null, 2), contentType: "application/json" });
});
