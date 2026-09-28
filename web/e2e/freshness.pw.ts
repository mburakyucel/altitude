import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Freshness through the change stream (issue #223). The page clock is installed paused and only ever
 * advanced in small steps, staying under the 20-second poll: every update below comes from the stream,
 * never from polling. The L3 answer stays held server-side, so the chat stream is open throughout.
 */
test.use({ serviceScript: "freshness-service.py" });

const POLL_MS = 20_000;

test("decisions and task transitions arrive during an open chat stream and after a stream outage", async ({ page, request }, info) => {
  test.setTimeout(90_000);
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const start = Date.now();
  await page.clock.install({ time: start });
  // The installed clock runs until paused; a busy host can carry it past any fixed offset from start.
  await page.clock.pauseAt(Date.now() + 1000);
  const fixture = async (path: string, data: object = {}) => {
    const response = await request.post(`/fixture/${path}`, { data });
    expect(response.ok()).toBe(true);
    return response.json();
  };
  // Timers advance only here; the assertion inside proves no poll interval could have elapsed.
  const settle = async (visible: ReturnType<Page["getByText"]>, step = 100, timeout = 10_000) => {
    await expect(async () => {
      await page.clock.runFor(step);
      await expect(visible).toBeVisible({ timeout: 100 });
    }).toPass({ timeout });
    expect(await page.evaluate(() => Date.now()) - start).toBeLessThan(POLL_MS);
  };
  const nav = page.getByRole("navigation", { name: phone ? "Primary" : "Rail" });
  const badge = (count: number) => nav.locator(".badge").getByText(String(count), { exact: true });
  const field = page.getByRole("textbox", { name: /^Message L3/ });
  const held = page.getByText("Checking the saved migration notes.");
  const work = page.getByRole("region", { name: "Work", exact: true });
  const counts = (current: number, done: number) => work.getByText(`${current} current · ${done} done this week`);
  const openStreams = async () => (await fixture("streams")).open;

  await walk.open("/projects/atlas");
  await settle(field);
  await expect.poll(openStreams).toBe(1);
  await walk.state("01-connected-no-decisions", { visible: [field], hidden: [nav.locator(".badge")] });

  await field.fill("Are the migration notes current?");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await settle(held);
  await walk.state("02-chat-stream-open", { visible: [held], hidden: [nav.locator(".badge")] });

  await fixture("decision", { title: "Choose backup retention" });
  await settle(badge(1));
  await walk.state("03-decision-arrives-during-chat-stream", { visible: [held, badge(1)], hidden: [] });

  if (phone) await nav.getByRole("link", { name: "Work", exact: true }).click();
  else if (!(await work.isVisible())) await page.getByRole("button", { name: "Work panel" }).click();
  await settle(counts(2, 0));
  await fixture("transition");
  await settle(counts(1, 1));
  await walk.state("04-task-transition-arrives", { visible: [counts(1, 1), badge(1)], hidden: [counts(2, 0)] });

  expect((await fixture("outage")).dropped).toBe(1);
  await fixture("decision", { title: "Choose restore drill" });
  // EventSource retries natively after the server's `retry` delay, meets the refused restart and closes.
  await page.waitForTimeout(4_000);
  await page.clock.runFor(1_000);
  expect(await openStreams()).toBe(0);
  await walk.state("05-outage-keeps-last-view", { visible: [badge(1), counts(1, 1)], hidden: [badge(2)] });

  await fixture("recover");
  await settle(badge(2), 500, 30_000);
  await expect.poll(openStreams).toBe(1);
  await walk.state("06-reconnect-refreshes-missed-decision", { visible: [badge(2), counts(1, 1)], hidden: [badge(1)] });

  await fixture("answer");
  if (phone) await nav.getByRole("link", { name: "Chat", exact: true }).click();
  await settle(page.getByText("The saved migration notes are current."));
  await walk.state("07-chat-answer-completes", { visible: [page.getByText("The saved migration notes are current."), badge(2)], hidden: [held] });
  // The turn's background drain finishes before the disposable service is torn down.
  await expect.poll(async () => {
    const chat = await (await request.get("/api/chat/atlas")).json();
    return Boolean(chat.active || chat.busy || chat.queued?.length);
  }, { timeout: 15_000 }).toBe(false);
});
