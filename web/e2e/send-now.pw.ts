import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.describe("L2 Send now", () => {
  test.use({ serviceScript: "l2-progress-service.py" });

  test("delivers the queued group into the running turn once, without stopping it", async ({ page, request }, info) => {
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
      await expect(convo.getByRole("button", { name: "Send now", exact: true })).toBeVisible();
    };
    let releaseRead!: () => void;
    const readGate = new Promise<void>((resolve) => { releaseRead = resolve; });
    await page.route(`**/api/task/atlas/${slug}`, async (route) => { await readGate; await route.continue(); }, { times: 1 });
    try {
      await walk.open(`/projects/atlas/tasks/${slug}`);
      await walk.state("l2-00-loading", { visible: [page.getByLabel("Loading", { exact: true })], hidden: [page.getByRole("button", { name: "Send now", exact: true })] });
    } finally { releaseRead(); }
    await walk.state("l2-01-empty-queue", { visible: [field], hidden: [convo.getByRole("button", { name: "Send now", exact: true })] });
    await send("Earlier instruction joins too.");
    await send("Check this immediately.");
    const queuedIds = (await status()).tasks[0].pending.map((message: { id: string }) => message.id);
    await expect(convo.getByRole("button", { name: "Send now", exact: true })).toHaveCount(1);
    const selected = row("Check this immediately.");
    const button = selected.getByRole("button", { name: "Send now", exact: true });
    await walk.state("l2-02-queued-actions", { visible: [button, selected.getByRole("button", { name: "Remove", exact: true })], hidden: [] });
    if (info.project.name === "phone") expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await button.click();
    await walk.state("l2-03-sending-now", { visible: [selected.getByRole("status", { name: "Sending", exact: true })], hidden: [selected.getByText("Delivered to session"), selected.getByRole("button", { name: /^Send(ing)? now/ }), selected.getByRole("button", { name: "Remove", exact: true })] });
    expect((await status()).tasks[0].state).toBe("running");
    expect((await status()).tasks[0].send_now.ids).toEqual(queuedIds);
    await control("deliver-send-now");
    await walk.state("l2-04-delivered", { visible: [selected], hidden: [selected.getByRole("button", { name: "Send now", exact: true }), selected.getByRole("button", { name: "Remove", exact: true })] });
    const after = await status();
    expect(after.tasks[0].state).toBe("running");
    expect(after.calls.filter((call: { prompt?: string }) => call.prompt?.includes("Check this immediately."))).toHaveLength(0);
    await expect(row("Earlier instruction joins too.").locator(".bubble")).not.toHaveAttribute("data-state");
    await expect(selected.locator(".bubble")).not.toHaveAttribute("data-state");
    expect(after.tasks[0].pending).toEqual([]);
    const delivered = await (await request.get(`/api/task/atlas/${slug}`)).json();
    const receipts = delivered.messages.filter((message: { id: string }) => queuedIds.includes(message.id));
    expect(receipts.map((message: { id: string }) => message.id)).toEqual(queuedIds);
    expect(receipts.map((message: { delivery: { state: string } }) => message.delivery.state)).toEqual(["delivered", "delivered"]);
    await send("Remove this later message.");
    await row("Remove this later message.").getByRole("button", { name: "Remove", exact: true }).click();
    await walk.state("l2-05-removed", { visible: [selected], hidden: [row("Remove this later message."), convo.getByRole("button", { name: "Send now", exact: true })] });
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
    await walk.state("l2-06-denied", { visible: [convo.getByText("You do not have permission to send this message now.")], hidden: [] });
    await page.route("**/api/l2/send-now", (route) => route.abort(), { times: 1 });
    await button.click();
    await walk.state("l2-07-unconfirmed", { visible: [convo.getByText("Send now unconfirmed. Check this message’s status before trying again.")], hidden: [] });
    expect((await request.post("/fixture/control", { data: { slug, mode: "blocked" } })).ok()).toBe(true);
    await page.reload();
    await expect(button).toHaveAttribute("aria-disabled", "true");
    const reason = convo.locator(".send-now-help");
    await walk.state("l2-08-question-wait-unavailable", { visible: [button, convo.getByText("Keep this steering.", { exact: true })], hidden: [reason] });
    // An unavailable Send now stays pressable (aria-disabled) so pressing it can show its reason; Playwright treats it as disabled.
    await button.click({ force: true });
    await walk.state("l2-09-unavailable-reason-on-press", { visible: [button, reason], hidden: [] });
    await expect(button).toHaveAccessibleDescription(await reason.innerText());
  });
});

test.describe("L3 Send now", () => {
  test.use({ serviceScript: "send-now-service.py" });

  test("removes accepted priority while system work continues and engines become unavailable", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const status = async () => (await (await request.get("/fixture/status")).json());
    expect((await request.post("/fixture/system")).ok()).toBe(true);
    await expect.poll(async () => (await status()).calls.length).toBe(1);
    await walk.open("/projects/atlas");
    const convo = page.getByRole("region", { name: "Conversation", exact: true });
    await page.getByRole("textbox", { name: "Message L3 about atlas" }).fill("Remove accepted priority");
    await convo.getByRole("button", { name: /^(Send|Queue)$/ }).click();
    await convo.getByRole("button", { name: "Send now", exact: true }).click();
    const row = convo.locator(".queued-row").filter({ hasText: "Remove accepted priority" });
    await expect(row.getByRole("button", { name: "Remove", exact: true })).toBeEnabled();
    expect((await request.post("/fixture/unavailable")).ok()).toBe(true);
    await page.reload();
    await expect(row.getByRole("button", { name: "Remove", exact: true })).toBeEnabled();
    await walk.state("l3-10-accepted-priority-removable", { visible: [convo.getByRole("button", { name: "Sending now", exact: true }), row.getByRole("button", { name: "Remove", exact: true })], hidden: [] });
    await row.getByRole("button", { name: "Remove", exact: true }).click();
    await walk.state("l3-11-priority-removed-before-claim", { visible: [page.getByRole("textbox", { name: "Message L3 about atlas" })], hidden: [row] });
    expect((await request.post("/fixture/release")).ok()).toBe(true);
    await expect.poll(async () => (await (await request.get("/api/chat/atlas")).json()).active).toBeNull();
    expect((await status()).calls.map((call: { text: string }) => call.text)).toEqual(["Fixture system work"]);
  });

  test("keeps a message sent while no engine can run and answers it first beneath its bubble", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const status = async () => (await (await request.get("/fixture/status")).json());
    const convo = page.getByRole("region", { name: "Conversation", exact: true });
    const field = page.getByRole("textbox", { name: "Message L3 about atlas" });
    const turn = convo.locator(".turn").filter({ hasText: "Are you there?" });
    expect((await request.post("/fixture/exhausted")).ok()).toBe(true);
    await walk.open("/projects/atlas");
    await field.fill("Are you there?");
    await convo.getByRole("button", { name: "Send", exact: true }).click();
    await walk.state("l3-12-kept-during-engine-hold", {
      visible: [turn.locator('.bubble[data-state="queued"]'), convo.getByRole("button", { name: "Send now", exact: true }),
        convo.getByRole("button", { name: "Send now", exact: true })],
      hidden: [convo.getByText(/could not answer/), convo.getByText(/usage window/), turn.getByRole("button", { name: "Remove", exact: true }),
        convo.getByRole("list", { name: "Queued messages" })],
    });
    await expect(convo.getByRole("button", { name: "Send now", exact: true })).toHaveAttribute("aria-disabled", "true");
    expect((await request.post("/fixture/system")).ok()).toBe(true);
    await page.reload();
    await walk.state("l3-13-kept-after-reload-ahead-of-system-work", {
      visible: [turn.locator('.bubble[data-state="queued"]'), convo.locator(".queued-row").filter({ hasText: "Fixture system work" })],
      hidden: [convo.getByText(/could not answer/)],
    });
    await expect(convo.getByText("Are you there?", { exact: true })).toHaveCount(1);
    expect((await status()).calls).toEqual([]);
    expect((await request.post("/fixture/recovered")).ok()).toBe(true);
    await walk.state("l3-14-reply-beneath-kept-message", {
      visible: [turn.getByText("Are you there? answered.", { exact: true })],
      hidden: [turn.locator('.bubble[data-state="queued"]'), convo.getByRole("button", { name: "Send now", exact: true })],
    });
    await expect.poll(async () => (await status()).calls.map((call: { text: string }) => call.text)).toEqual(["Are you there?", "Fixture system work"]);
    expect((await request.post("/fixture/release")).ok()).toBe(true);
  });

  test("one Send now delivers kept and newer messages together in order", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const convo = page.getByRole("region", { name: "Conversation", exact: true });
    const field = page.getByRole("textbox", { name: "Message L3 about atlas" });
    const send = async (text: string) => {
      await field.fill(text);
      await convo.getByRole("button", { name: "Send", exact: true }).click();
    };
    expect((await request.post("/fixture/exhausted")).ok()).toBe(true);
    await walk.open("/projects/atlas");
    await send("Keep the first instruction.");
    const kept = convo.locator(".turn").filter({ hasText: "Keep the first instruction." });
    await expect(kept.locator('.bubble[data-state="queued"]')).toBeVisible();
    await send("Then the second instruction.");
    await send("Finally the third instruction.");
    const action = convo.getByRole("button", { name: "Send now", exact: true });
    await expect(action).toHaveCount(1);
    await walk.state("l3-mixed-queue", { visible: [action, kept], hidden: [kept.getByRole("button", { name: "Remove", exact: true })] });
    const before = await (await request.get("/api/chat/atlas")).json();
    const keptId = before.history.find((row: { text: string }) => row.text === "Keep the first instruction.").turn_id;
    expect((await request.post("/fixture/recovered-ready")).ok()).toBe(true);
    await page.reload();
    await expect(action).not.toHaveAttribute("aria-disabled", "true");
    await action.click();
    const combined = "Keep the first instruction.\n\nThen the second instruction.\n\nFinally the third instruction.";
    await walk.state("l3-mixed-delivered", { visible: [convo.getByText("Finally the third instruction. answered.", { exact: true })], hidden: [action, convo.locator('.bubble[data-state="queued"]')] });
    const after = await (await request.get("/api/chat/atlas")).json();
    expect(after.history.filter((row: { role: string }) => row.role === "user").map((row: { text: string }) => row.text)).toEqual([
      "Keep the first instruction.", "Then the second instruction.", "Finally the third instruction.",
    ]);
    expect(after.history.find((row: { text: string }) => row.text === "Keep the first instruction.").turn_id).toBe(keptId);
    expect((await (await request.get("/fixture/status")).json()).calls.map((row: { text: string }) => row.text)).toEqual([combined]);
    await page.reload();
    await expect(convo.getByText("Keep the first instruction.", { exact: true })).toHaveCount(1);
    await expect(action).toHaveCount(0);
  });

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
    await walk.state("l3-08-after-system-work", { visible: [convo.getByRole("button", { name: "Sending now", exact: true })], hidden: [convo.locator(".send-now-help")] });
    await convo.getByRole("button", { name: "Sending now", exact: true }).click({ force: true });
    await walk.state("l3-08b-reason-on-press", { visible: [convo.getByText("Runs next after system work", { exact: true })], hidden: [] });
    expect((await status()).calls).toHaveLength(1);
    expect((await status()).delivered).toEqual([]);
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
    await expect(button).toHaveAttribute("aria-disabled", "true");
    await walk.state("l3-07-unavailable", { visible: [button, convo.locator(".queued-row").getByText("Keep the queued text", { exact: true })], hidden: [] });
  });

  test("retains uncertain delivery receipts after settlement and reload", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const convo = page.getByRole("region", { name: "Conversation", exact: true });
    const field = page.getByRole("textbox", { name: "Message L3 about atlas" });
    const status = async () => (await (await request.get("/fixture/status")).json());
    expect((await request.post("/fixture/uncertain")).ok()).toBe(true);
    await walk.open("/projects/atlas");
    await field.fill("Keep working");
    await convo.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(async () => (await status()).calls.length).toBe(1);
    await field.fill("Possibly received");
    await convo.getByRole("button", { name: /^(Send|Queue)$/ }).click();
    await convo.getByRole("button", { name: "Send now", exact: true }).click();
    expect((await request.post("/fixture/deliver")).ok()).toBe(true);
    await walk.state("l3-15-unconfirmed-receipt", { visible: [convo.getByText("Unconfirmed", { exact: true })], hidden: [convo.locator(".queued-row")] });
    expect((await request.post("/fixture/release")).ok()).toBe(true);
    await expect.poll(async () => (await (await request.get("/api/chat/atlas")).json()).active).toBeNull();
    await page.reload();
    await walk.state("l3-16-unconfirmed-reloaded", { visible: [convo.getByText("Unconfirmed", { exact: true })], hidden: [convo.locator(".queued-row")] });
    expect((await status()).calls).toHaveLength(1);
    await expect(convo.locator(".bubble").filter({ hasText: "Possibly received" })).toHaveCount(1);
  });

  test("promotes the queued group for a boundary-only coordinator without interrupting", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const convo = page.getByRole("region", { name: "Conversation", exact: true });
    const field = page.getByRole("textbox", { name: "Message L3 about atlas" });
    const row = (text: string) => convo.locator(".queued-row").filter({ hasText: text });
    const status = async () => (await (await request.get("/fixture/status")).json());
    expect((await request.post("/fixture/boundary")).ok()).toBe(true);
    await walk.open("/projects/atlas");
    await field.fill("Keep working");
    await convo.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(async () => (await status()).calls.length).toBe(1);
    for (const text of ["First queued message", "Second queued message"]) {
      await field.fill(text);
      await convo.getByRole("button", { name: /^(Send|Queue)$/ }).click();
      await expect(row(text)).toBeVisible();
    }
    await convo.getByRole("button", { name: "Send now", exact: true }).click();
    await page.reload();
    await walk.state("l3-13-turn-boundary", { visible: [row("First queued message"), convo.getByRole("button", { name: "Sending now", exact: true })], hidden: [] });
    expect((await status()).calls).toHaveLength(1);
    expect((await status()).delivered).toEqual([]);
    expect((await request.post("/fixture/release")).ok()).toBe(true);
    await walk.state("l3-14-boundary-delivered", { visible: [convo.getByText("Second queued message answered.", { exact: true })], hidden: [row("First queued message"), row("Second queued message")] });
    expect((await status()).calls.map((call: { text: string }) => call.text)).toEqual(["Keep working", "First queued message\n\nSecond queued message"]);
    await expect(convo.locator(".bubble").filter({ hasText: /^First queued message$/ })).toHaveCount(1);
    await expect(convo.locator(".bubble").filter({ hasText: /^Second queued message$/ })).toHaveCount(1);
  });

  test("delivers the queued group into the running chat turn and keeps each receipt", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const convo = page.getByRole("region", { name: "Conversation", exact: true });
    const field = page.getByRole("textbox", { name: "Message L3 about atlas" });
    const row = (text: string) => convo.locator(".queued-row").filter({ hasText: text });
    const status = async () => (await (await request.get("/fixture/status")).json());
    let releaseRead!: () => void;
    const readGate = new Promise<void>((resolve) => { releaseRead = resolve; });
    await page.route("**/api/chat/atlas?*", async (route) => { await readGate; await route.continue(); }, { times: 1 });
    await page.route((url) => url.pathname === "/api/project/atlas", async (route) => { await readGate; await route.continue(); }, { times: 1 });
    try {
      await walk.open("/projects/atlas");
      if (info.project.name === "desktop") await expect(page.getByRole("region", { name: "Work", exact: true }).getByLabel("Loading", { exact: true })).toBeVisible();
      await walk.state("l3-00-loading", { visible: [convo.getByLabel("Loading", { exact: true })], hidden: [convo.getByRole("button", { name: "Send now", exact: true })] });
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
    await walk.state("l3-02-removed-and-queued", { visible: [convo.getByRole("button", { name: "Send now", exact: true })], hidden: [row("Remove this message")] });
    await convo.getByRole("button", { name: "Send now", exact: true }).click();
    await walk.state("l3-03-sending-into-turn", { visible: [convo.getByRole("button", { name: "Sending now" }), row("Deliver this next").getByRole("status", { name: "Sending", exact: true })], hidden: [row("Deliver this next").getByRole("button", { name: "Remove", exact: true })] });
    expect((await request.post("/fixture/deliver")).ok()).toBe(true);
    await walk.state("l3-04-joined-turn", { visible: [convo.getByText("Checking the current work.", { exact: true }), convo.locator(".turn").getByText("Deliver this next", { exact: true })], hidden: [row("Deliver this next")] });
    expect((await status()).delivered).toEqual(["Earlier queued message\n\nDeliver this next"]);
    expect((await request.post("/fixture/release")).ok()).toBe(true);
    await walk.state("l3-12-turn-answers-sent-message", { visible: [convo.getByText("Read: Earlier queued message", { exact: true }), convo.getByText("Deliver this next.", { exact: true })], hidden: [row("Deliver this next"), row("Earlier queued message")] });
    await expect.poll(async () => (await (await request.get("/api/chat/atlas")).json()).active).toBeNull();
    expect((await status()).calls.map((call: { text: string }) => call.text)).toEqual(["Keep working"]);
    await expect(convo.getByText("Deliver this next", { exact: true })).toHaveCount(1);
    await expect(convo.getByText("Earlier queued message", { exact: true })).toHaveCount(1);
    await expect(convo.getByText("Checking the current work.", { exact: true })).toHaveCount(1);
  });

  test("shows saved interrupted replies quietly, with and without partial output", async ({ page, request }, info) => {
    const walk = walkthrough(page, info);
    const convo = page.getByRole("region", { name: "Conversation", exact: true });
    expect((await request.post("/fixture/interrupted-history")).ok()).toBe(true);
    const turn = (text: string) => convo.locator(".turn").filter({ has: page.locator(".bubble", { hasText: text }) });
    await walk.open("/projects/atlas");
    await walk.state("l3-07-interrupted-partial", {
      visible: [turn("Keep working").getByText("Two checks failed on the review branch, and the first log", { exact: false })],
      hidden: [convo.getByText(/Interrupted/), convo.locator(".queued-row")],
    });
    await walk.state("l3-08-interrupted-empty", {
      visible: [turn("Hold on").locator(".bubble"), turn("Use the other branch").getByText("Use the other branch answered.", { exact: true })],
      hidden: [convo.getByText(/Interrupted|could not answer/), convo.locator(".queued-row"), turn("Hold on").locator(".reply")],
    });
    // The two operator messages sit back to back, as consecutive messages do.
    const first = (await turn("Hold on").locator(".bubble").boundingBox())!;
    const next = (await turn("Use the other branch").locator(".bubble").boundingBox())!;
    expect(next.y - (first.y + first.height)).toBeLessThanOrEqual(12);
    expect(next.y - (first.y + first.height)).toBeGreaterThanOrEqual(4);
  });
});
