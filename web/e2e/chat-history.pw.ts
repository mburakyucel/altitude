import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Human conversation stays in view through a burst of system events, and a pending report waits for an
 * available L3 without adding rows, then is handled once. The service seeds a fictional burst larger
 * than the page's history limit.
 */
test.use({ serviceScript: "chat-history-service.py" });

test("human messages survive a system-event burst and a pending report waits quietly for L3", async ({ page, request }, info) => {
  test.setTimeout(60_000);
  const walk = walkthrough(page, info);
  const fixture = async (path: string) => {
    const response = await request.post(`/fixture/${path}`, { data: {} });
    expect(response.ok()).toBe(true);
    return response.json();
  };
  const first = page.getByText("Is the fictional backup plan ready?", { exact: true });
  const latest = page.getByText("The north mirror.", { exact: true });
  const group = page.getByText(/^L3 handled \d+ system events between your messages$/);
  const handled = page.getByText("Fictional report reviewed; nothing waits on you.", { exact: true });
  const failed = page.getByText("L3 could not handle a landed report for Fictional mirror").first();

  await walk.open("/projects/atlas");
  await walk.state("01-human-history-above-collapsed-burst", { visible: [first, latest, group], hidden: [failed, handled] });

  const waiting = await fixture("ticks");
  expect(waiting.handled).toBeNull();
  expect((await fixture("ticks")).rows).toBe(waiting.rows);
  await page.reload();
  await walk.state("02-unavailable-l3-adds-nothing", { visible: [first, latest, group], hidden: [handled] });

  await fixture("recover");
  const recovered = await fixture("ticks");
  expect(recovered.handled).not.toBeNull();
  expect(recovered.rows).toBe(waiting.rows + 2);
  expect((await fixture("ticks")).rows).toBe(recovered.rows);
  await page.reload();
  await walk.state("03-report-handled-once", { visible: [first, latest, group], hidden: [failed] });

  await walk.state("04-system-events-expanded", {
    action: () => group.locator("..").getByRole("button", { name: "Show" }).click(),
    visible: [first, latest, failed, handled],
    hidden: [group],
  });
});
