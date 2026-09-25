import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

test("the Altitude mark names the product in both themes and serves as the tab and Home Screen icon", async ({ page, request }, info) => {
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const project = await fixtureProject(request);
  const brand = phone ? page.locator(".phone-title") : page.locator(".rail-brand");
  const mark = brand.locator("svg.brand-mark");

  for (const theme of ["light", "dark"]) {
    if (theme === "dark") await page.addInitScript(() => localStorage.setItem("altitude.theme", "dark"));
    await walk.open("/");
    await walk.state(`${theme}-needs-you`, { visible: [mark, brand.getByText("Altitude", { exact: true })], hidden: [] });
    // The tile follows the theme's accent; the glyph stays legible on it.
    const accent = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--accent").trim());
    expect(accent).toBe(theme === "dark" ? "#6366f1" : "#4f46e5");
    if (phone) {
      await expect(page.getByRole("heading", { name: "Altitude", level: 1 })).toBeVisible();
      // A project tab names the project instead; the mark is only on the global tabs.
      await walk.state(`${theme}-project`, {
        action: () => page.getByRole("link", { name: "Chat", exact: true }).click(),
        visible: [page.getByRole("heading", { name: project.name, level: 1 })],
        hidden: [page.locator(".phone-header .brand-mark")],
      });
    }
  }

  const icon = page.locator('link[rel="icon"]');
  await expect(icon).toHaveAttribute("type", "image/svg+xml");
  for (const [href, type] of [[await icon.getAttribute("href"), "image/svg+xml"], [await page.locator('link[rel="apple-touch-icon"]').getAttribute("href"), "image/png"]]) {
    const response = await request.get(href!);
    expect(response.ok(), `${href} must be served`).toBe(true);
    expect(response.headers()["content-type"]).toContain(type);
  }
});
