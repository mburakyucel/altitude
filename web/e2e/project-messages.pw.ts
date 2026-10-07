import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "project-message-service.py", launchOptions: { chromiumSandbox: true } });

test("coordinator exchanges stay folded, move from inbox to history and preserve task controls", async ({ page, request }, info) => {
  test.setTimeout(120_000);
  const walk = walkthrough(page, info);
  const action = async (name: string) => { const response = await request.post(`/fixture/${name}`, { data: {} }); expect(response.ok()).toBe(true); };
  const line = (summary: string) => page.locator(".sys-line").filter({ hasText: summary });
  const card = page.getByRole("article", { name: /^Coordinator message/ });
  const composer = page.getByRole("textbox", { name: "Message L3 about lab" });
  const chatRequest = (url: URL) => url.pathname === "/api/chat/lab";
  let release: () => void = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route(chatRequest, async route => { await gate; await route.continue(); });
  await walk.open("/projects/lab");
  await walk.state("00-loading", { visible: [page.getByLabel("Loading", { exact: true })], hidden: [line("Local probe result"), card] });
  release();
  await walk.state("01-empty", { visible: [composer], hidden: [line("Local probe result"), card] });
  await expect(page.getByLabel("Loading", { exact: true })).toBeHidden();
  await page.unroute(chatRequest);
  await action("send");
  await page.reload();
  await walk.state("02-inbox-folded", { visible: [line("Local probe result"), composer], hidden: [card, page.getByText("Fictional local probe exited", { exact: false })] });
  await expect(line("Local probe result")).toContainText("atlas → lab");
  await expect(line("Local probe result")).toContainText("next ordinary turn");
  await expect(page.getByRole("button", { name: "Send now", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Remove", exact: true })).toHaveCount(0);
  await walk.state("03-inbox-expanded", { action: () => line("Local probe result").getByRole("button", { name: "Show", exact: true }).click(),
    visible: [card, card.getByText("Information only", { exact: false }), card.getByText("Fictional local probe exited", { exact: false })], hidden: [line("Local probe result")] });
  await expect(card.getByRole("link", { name: "Open task" })).toHaveCount(0);
  await walk.state("04-inbox-refolded", { action: () => card.getByRole("button", { name: "Hide", exact: true }).click(), visible: [line("Local probe result")], hidden: [card] });
  await action("supply");
  await page.reload();
  await walk.state("05-supplied", { visible: [line("Local probe result")], hidden: [page.getByRole("list", { name: "Queued messages" }), card] });
  await expect(line("Local probe result")).toHaveCount(1);
  await expect(line("Local probe result")).toContainText("Incoming");
  await action("reply");
  await walk.open("/projects/atlas");
  await walk.state("06-source-and-reply-inbox", { visible: [line("Local probe result"), line("Fix status")], hidden: [card] });
  await expect(line("Local probe result")).toContainText("Sent");
  await action("supply-reply");
  await page.reload();
  await walk.state("07-reply-expanded", { action: () => line("Fix status").getByRole("button", { name: "Show", exact: true }).click(),
    visible: [card, card.getByText("Fix merged; local activation remains pending.")], hidden: [page.getByRole("list", { name: "Queued messages" })] });
  const rejected = await request.post("/fixture/refuse", { data: {} });
  expect(rejected.status()).toBe(400);
  expect((await rejected.json()).error).toContain("recognized credentials");
  await page.reload();
  await walk.state("08-refused-no-row", { visible: [line("Local probe result"), line("Fix status")], hidden: [line("Refused diagnostic"), card] });
  await action("change-registration");
  await walk.open("/projects/lab");
  await walk.state("09-registration-changed", { visible: [line("Another probe"), page.getByText("Registration changed · not supplied", { exact: false })], hidden: [card] });
  await page.route("**/api/chat/lab*", route => route.fulfill({ status: 503, contentType: "application/json", body: '{"error":"Fictional read unavailable"}' }));
  await page.reload();
  await walk.state("10-read-error", { visible: [page.getByText("Could not load the conversation.", { exact: false })], hidden: [line("Another probe"), card] });
});
