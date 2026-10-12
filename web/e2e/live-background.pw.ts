import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "live-background-service.py" });

test("a session without background work shows no tray", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const { plain } = await (await request.get("/fixture/status")).json();
  await walk.open(`/projects/atlas/tasks/${plain}/live`);
  const live = page.getByRole("region", { name: "Live session", exact: true });
  await walk.state("01-no-background-work", {
    visible: [live.getByText("I am tidying the pagination copy.")],
    hidden: [live.getByRole("region", { name: "In the background" }), live.getByText(/^background ·/)],
  });
});

for (const index of [0, 1]) {
  test(`background work runs beside the stream and ends with its outcome with configured engine ${index + 1}`, async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const { engine, slug, helpers, suite: suiteLabel, build } = (await (await request.get("/fixture/status")).json()).sessions[index];
    const control = async (mode: string) => {
      expect((await request.post("/fixture/control", { data: { engine, mode } })).ok()).toBe(true);
    };
    await control("start");
    await page.clock.install();
    await walk.open(`/projects/atlas/tasks/${slug}/live`);
    const live = page.getByRole("region", { name: "Live session", exact: true });
    const tray = live.getByRole("region", { name: "In the background" });
    const suite = tray.locator(".live-background-item").filter({ hasText: suiteLabel });
    await walk.state("01-running-beside-the-stream", {
      visible: [tray, suite.getByText(/^running · 4 min$/), live.getByText("The docs now describe the saved page."),
        live.locator('.session-tool[data-background="running"]').getByText("background", { exact: true }).first()],
      hidden: [tray.getByRole("button", { name: /^Show all/ })],
    });
    if (helpers) {
      const review = tray.locator(".live-background-item").filter({ hasText: "Review the pagination change" });
      await expect(review.getByText(/^running · 3 min$/)).toBeVisible();
      await control("review-done");
      await walk.state("02-helper-done-stays-a-minute", { visible: [review.getByText(/^done in 3 min$/), suite.getByText(/^running/)], hidden: [] });
    }
    await control("suite-failed");
    await walk.state("03-failed-outcome", {
      visible: [suite.getByText(/^failed after 4 min$/), live.locator('.session-tool[data-background="failed"]').first()],
      hidden: [suite.getByText(/^running/)],
    });
    await control("many");
    await walk.state("04-several-fold-under-show-all", { visible: [tray.getByRole("button", { name: /^Show all \d$/ })], hidden: [] });
    await tray.getByRole("button", { name: /^Show all/ }).click();
    const list = tray.getByRole("list");
    await list.evaluate((node) => { node.scrollTop = node.scrollHeight; });
    await walk.state("05-show-all-scrolls-in-a-bounded-tray", { visible: [tray.getByRole("button", { name: "Show fewer" }), tray.getByText(build, { exact: true })], hidden: [] });
    const [trayHeight, panelHeight] = await Promise.all([tray.evaluate((node) => node.getBoundingClientRect().height),
      live.evaluate((node) => node.getBoundingClientRect().height)]);
    expect(trayHeight).toBeLessThan(panelHeight / 2);
    expect(await tray.getByRole("button", { name: "Show fewer" }).evaluate((node) => node.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
    await tray.getByRole("button", { name: "Show fewer" }).click();
    await expect(tray.locator(".live-background-item")).toHaveCount(3);
    await page.clock.fastForward(65_000);
    await walk.state("06-outcomes-leave-after-a-minute", {
      visible: [tray.locator('.live-background-item[data-status="running"]').first(), live.locator('.session-tool[data-background="failed"]').first()],
      hidden: [suite],
    });
  });
}
