import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.describe("L2 Send now", () => {
  test.use({ serviceScript: "l2-progress-service.py" });

  test("interrupts current work and confirms the selected message without duplication", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const status = async () => (await (await request.get("/fixture/status")).json());
    const slug = (await status()).tasks[0].slug;
    const control = async (mode: string) => expect((await request.post("/fixture/control", { data: { slug, mode } })).ok()).toBe(true);
    const convo = page.getByRole("region", { name: "Task conversation", exact: true });
    const field = convo.getByRole("textbox", { name: "Message the L2" });
    const row = (text: string) => convo.locator(".msg-row").filter({ hasText: text });
    const send = async (text: string) => {
      await field.fill(text);
      await convo.getByRole("button", { name: "Send", exact: true }).click();
      await expect(row(text).getByRole("button", { name: "Send now", exact: true })).toBeVisible();
    };
    let releaseRead!: () => void;
    const readGate = new Promise<void>((resolve) => { releaseRead = resolve; });
    await page.route(`**/api/task/atlas/${slug}`, async (route) => { await readGate; await route.continue(); }, { times: 1 });
    try {
      await walk.open(`/projects/atlas/tasks/${slug}`);
      await walk.state("l2-00-loading", { visible: [page.getByLabel("Loading", { exact: true })], hidden: [page.getByRole("button", { name: "Send now", exact: true })] });
    } finally { releaseRead(); }
    await walk.state("l2-01-empty-queue", { visible: [field], hidden: [convo.getByRole("button", { name: "Send now", exact: true })] });
    await send("Earlier instruction stays queued.");
    await send("Check this immediately.");
    const selected = row("Check this immediately.");
    const button = selected.getByRole("button", { name: "Send now", exact: true });
    await walk.state("l2-02-queued-actions", { visible: [button, selected.getByRole("button", { name: "Remove", exact: true }), selected.getByText("Interrupts current work like Stop, including attached reviews.")], hidden: [] });
    if (info.project.name === "phone") expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await control("hold-stop");
    try {
      await button.click();
      await walk.state("l2-03-sending-now", { visible: [selected.getByRole("button", { name: "Sending now…" })], hidden: [selected.getByText("Delivered to session")] });
      await expect(selected.getByRole("button", { name: "Sending now…" })).toBeDisabled();
    } finally { await control("release-stop"); }
    await walk.state("l2-04-delivered", { visible: [selected.getByText("Delivered to session")], hidden: [selected.getByRole("button", { name: "Send now", exact: true }), selected.getByRole("button", { name: "Remove", exact: true })] });
    expect((await status()).calls.filter((call: { prompt?: string }) => call.prompt?.includes("Check this immediately."))).toHaveLength(1);
    await expect(row("Earlier instruction stays queued.").getByRole("button", { name: "Remove", exact: true })).toBeVisible();
    await row("Earlier instruction stays queued.").getByRole("button", { name: "Remove", exact: true }).click();
    await walk.state("l2-05-removed", { visible: [convo.getByText("Message removed", { exact: true })], hidden: [convo.getByRole("button", { name: "Send now", exact: true })] });
    expect((await status()).tasks[0].edit).toContain("An existing edit stays");
  });

  test("shows denied and unconfirmed requests and explains an operator question wait", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const slug = (await (await request.get("/fixture/status")).json()).tasks[0].slug;
    const convo = page.getByRole("region", { name: "Task conversation", exact: true });
    const button = convo.getByRole("button", { name: "Send now", exact: true });
    await walk.open(`/projects/atlas/tasks/${slug}`);
    await convo.getByRole("textbox", { name: "Message the L2" }).fill("Keep this steering.");
    await convo.getByRole("button", { name: "Send", exact: true }).click();
    await page.route("**/api/l2/send-now", (route) => route.fulfill({ status: 403, json: { error: "Fixture denied" } }), { times: 1 });
    await button.click();
    await walk.state("l2-06-denied", { visible: [convo.getByText("You do not have permission to send this message now.")], hidden: [convo.getByText("Delivered to session")] });
    await page.route("**/api/l2/send-now", (route) => route.abort(), { times: 1 });
    await button.click();
    await walk.state("l2-07-unconfirmed", { visible: [convo.getByText("Send now unconfirmed. Check this message’s status before trying again.")], hidden: [convo.getByText("Delivered to session")] });
    expect((await request.post("/fixture/control", { data: { slug, mode: "blocked" } })).ok()).toBe(true);
    await page.reload();
    await expect(button).toBeDisabled();
    await walk.state("l2-08-question-wait-unavailable", { visible: [button, convo.getByText("Keep this steering.", { exact: true })], hidden: [] });
  });
});

test.describe("L3 Send now", () => {
  test.use({ serviceScript: "send-now-service.py" });

  test("shows Runs next after system work without interrupting the system turn", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const status = async () => (await (await request.get("/fixture/status")).json());
    expect((await request.post("/fixture/system")).ok()).toBe(true);
    await expect.poll(async () => (await status()).calls.length).toBe(1);
    await walk.open("/projects/atlas");
    const convo = page.getByRole("region", { name: "Conversation", exact: true });
    await page.getByRole("textbox", { name: "Message L3 about atlas" }).fill("Follow the system work");
    await convo.getByRole("button", { name: /^(Send|Queue)$/ }).click();
    await convo.getByRole("button", { name: "Send now", exact: true }).click();
    const row = convo.locator(".queued-row").filter({ hasText: "Follow the system work" });
    await walk.state("l3-08-after-system-work", { visible: [row.getByRole("button", { name: "Sending now…" }), row.getByText("Runs next after system work", { exact: true })], hidden: [] });
    expect((await status()).calls).toHaveLength(1);
    expect((await status()).stopped).toBe(false);
    expect((await request.post("/fixture/release")).ok()).toBe(true);
    await walk.state("l3-09-system-finished-then-delivered", { visible: [convo.getByText("Follow the system work answered.", { exact: true })], hidden: [row] });
    expect((await status()).calls.map((call: { text: string }) => call.text)).toEqual(["Fixture system work", "Follow the system work"]);
  });

  test("retains queued text on denied and uncertain requests and explains unavailable delivery", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const convo = page.getByRole("region", { name: "Conversation", exact: true });
    const field = page.getByRole("textbox", { name: "Message L3 about atlas" });
    const button = convo.getByRole("button", { name: "Send now", exact: true });
    await walk.open("/projects/atlas");
    await field.fill("Keep working");
    await convo.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(async () => (await (await request.get("/fixture/status")).json()).calls.length).toBe(1);
    await field.fill("Keep the queued text");
    await convo.getByRole("button", { name: /^(Send|Queue)$/ }).click();
    await page.route("**/api/chat/send-now", (route) => route.fulfill({ status: 403, json: { error: "Fixture denied" } }), { times: 1 });
    await button.click();
    await walk.state("l3-05-denied", { visible: [convo.getByText("You do not have permission to send this message now.")], hidden: [] });
    await page.route("**/api/chat/send-now", (route) => route.abort(), { times: 1 });
    await button.click();
    await walk.state("l3-06-unconfirmed", { visible: [convo.getByText("Send now unconfirmed. Check this message’s status before trying again.")], hidden: [] });
    expect((await request.post("/fixture/unavailable")).ok()).toBe(true);
    await page.reload();
    await expect(button).toBeDisabled();
    await walk.state("l3-07-unavailable", { visible: [button, convo.locator(".queued-row").getByText("Keep the queued text", { exact: true })], hidden: [] });
  });

  test("interrupts a chat turn, delivers the selected row first and keeps one receipt", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const convo = page.getByRole("region", { name: "Conversation", exact: true });
    const field = page.getByRole("textbox", { name: "Message L3 about atlas" });
    const row = (text: string) => convo.locator(".queued-row").filter({ hasText: text });
    const status = async () => (await (await request.get("/fixture/status")).json());
    let releaseRead!: () => void;
    const readGate = new Promise<void>((resolve) => { releaseRead = resolve; });
    await page.route("**/api/chat/atlas?*", async (route) => { await readGate; await route.continue(); }, { times: 1 });
    try {
      await walk.open("/projects/atlas");
      await walk.state("l3-00-loading", { visible: [page.getByLabel("Loading", { exact: true })], hidden: [page.getByRole("button", { name: "Send now", exact: true })] });
    } finally { releaseRead(); }
    await walk.state("l3-01-empty-queue", { visible: [field], hidden: [convo.getByRole("button", { name: "Send now", exact: true })] });
    await field.fill("Keep working");
    await convo.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(async () => (await status()).calls.length).toBe(1);
    for (const text of ["Earlier queued message", "Deliver this next", "Remove this message"]) {
      await field.fill(text);
      await convo.getByRole("button", { name: /^(Send|Queue)$/ }).click();
      await expect(row(text)).toBeVisible();
    }
    await row("Remove this message").getByRole("button", { name: "Remove", exact: true }).click();
    await walk.state("l3-02-removed-and-queued", { visible: [row("Deliver this next").getByRole("button", { name: "Send now", exact: true })], hidden: [row("Remove this message")] });
    await row("Deliver this next").getByRole("button", { name: "Send now", exact: true }).click();
    await expect.poll(async () => (await status()).stopped).toBe(true);
    await walk.state("l3-03-sending-now", { visible: [row("Deliver this next").getByRole("button", { name: "Sending now…" })], hidden: [] });
    await expect(row("Deliver this next").getByRole("button", { name: "Remove", exact: true })).toBeDisabled();
    expect((await request.post("/fixture/release")).ok()).toBe(true);
    await walk.state("l3-04-delivered", { visible: [convo.getByText("Deliver this next answered.", { exact: true })], hidden: [row("Deliver this next")] });
    await expect.poll(async () => (await status()).calls.length).toBe(3);
    expect((await status()).calls.map((call: { text: string }) => call.text)).toEqual(["Keep working", "Deliver this next", "Earlier queued message"]);
    await expect(convo.getByText("Deliver this next", { exact: true })).toHaveCount(1);
  });
});
