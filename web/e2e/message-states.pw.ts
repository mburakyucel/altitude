import { test } from "./fixtures";
import { expect, type Page, type Route, type TestInfo } from "@playwright/test";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/*
 * Every state of the operator's message in the project conversation carries on the message itself (SPEC.md
 * §3.6, §4.2): sending, sent while L3 answers, queued with Remove and Send now, removing, not removed, sending
 * now, unconfirmed handoff, kept while no engine can run, not sent and unconfirmed. Every POST is intercepted
 * and every read is overlaid with fictional rows; nothing reaches L3.
 */

type ChatView = Record<string, unknown> & { history: unknown[]; active: unknown; busy: boolean; queued?: unknown[] };

async function overlayChat(page: Page, name: string, patch: (view: ChatView) => ChatView) {
  await page.route((url) => url.pathname === `/api/chat/${name}`, async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: patch((await response.json()) as ChatView) });
  });
}

function views(page: Page, info: TestInfo) {
  const main = page.getByRole("main");
  return {
    phone: info.project.name === "phone",
    main,
    convo: main.getByRole("region", { name: "Conversation", exact: true }),
    field: main.getByRole("textbox", { name: /^Message L3 about /, includeHidden: true }),
    send: main.getByRole("button", { name: "Send", exact: true }),
  };
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
});

test("a failed send stays on its message: not sent with Retry and Edit, unconfirmed with Copy to draft", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const text = "UI walkthrough: summarize the open pull requests";
  let mode: "hold" | "refuse" | "drop" = "hold";
  let releaseHold: () => void = () => {};
  const held = new Promise<void>((resolve) => (releaseHold = resolve));
  await page.route((url) => url.pathname === "/api/chat", async (route: Route) => {
    if (mode === "hold") await held;
    if (mode === "drop") return route.abort("connectionreset");
    return route.fulfill({ status: 409, json: { error: "No L3 for this project" } });
  });
  await overlayChat(page, project.name, (live) => ({ ...live, active: null, busy: false, queued: [] }));

  await walk.open(project.path);
  const row = v.convo.locator(".msg-row").filter({ hasText: text });
  await v.field.fill(text);
  await walk.state("01-sending", {
    action: () => v.send.click(),
    visible: [row],
    hidden: [row.locator(".spinner")],
  });
  await expect(row).toHaveAttribute("data-pending", "true");
  await expect(row.getByRole("status", { name: "Sending", exact: true })).toHaveCount(1);
  mode = "refuse";
  releaseHold();
  const notSent = row.getByRole("alert").filter({ hasText: "Not sent: no L3 for this project." });
  await walk.state("02-not-sent", {
    visible: [notSent, row.getByRole("button", { name: "Retry", exact: true }), row.getByRole("button", { name: "Edit", exact: true })],
    hidden: [v.main.locator(".composer-hint").filter({ hasText: "Not sent" })],
  });
  await expect(v.field).toHaveValue("");

  mode = "drop";
  await row.getByRole("button", { name: "Retry", exact: true }).click();
  // The pointer leaves the row, so the capture shows the state rather than the hovered time.
  await page.mouse.move(1, 1);
  const copy = row.getByRole("button", { name: "Copy to draft", exact: true });
  await walk.state("03-unconfirmed", {
    visible: [row.getByText("Unconfirmed", { exact: true }), copy],
    hidden: [notSent, row.getByRole("button", { name: "Retry", exact: true }), row.getByRole("button", { name: "Edit", exact: true })],
  });
  await expect(v.convo.locator(".msg-row").filter({ hasText: text })).toHaveCount(1);

  // The unconfirmed original stays: it may already have arrived.
  await v.field.fill("Also the failing checks");
  await walk.state("04-copy-to-draft", {
    action: () => copy.click(),
    visible: [v.field, row.getByText("Unconfirmed", { exact: true })],
    hidden: [],
  });
  await expect(v.field).toHaveValue(`${text}\nAlso the failing checks`);
});

test("a message L3 is busy for waits queued with Remove and Send now, and each later state shows on it", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const at = new Date().toISOString();
  const user = (turn: string, text: string, delivery?: string) => ({ role: "user", text, at, turn_id: turn, trigger: "chat",
    ...(delivery ? { delivery: { state: delivery, at } } : {}) });
  const reply = (turn: string, text: string) => ({ role: "assistant", text, at, turn_id: turn, trigger: "chat" });
  const first = "Draft the release notes for the next version";
  const second = "Also list the breaking changes";
  const third = "Keep it under one page";
  const queue = (extra: Record<string, unknown> = {}) => [
    { id: "q-second", text: second, at, trigger: "chat", ...extra },
    { id: "q-third", text: third, at, trigger: "chat", ...extra },
  ];
  const running = { id: "turn-a", trigger: "chat", started_at: at };
  let stage: Record<string, unknown> = { history: [user("turn-a", first)], active: running, busy: true, queued: [] };
  await overlayChat(page, project.name, (live) => ({ ...live, send_now_reason: null, ...stage }));
  let releaseRemove: () => void = () => {};
  let removeHeld = new Promise<void>((resolve) => (releaseRemove = resolve));
  await page.route((url) => url.pathname === "/api/chat/remove", async (route) => {
    await removeHeld;
    return route.fulfill({ status: 503, json: { error: "Queue temporarily unavailable" } });
  });

  await walk.open(project.path);
  const row = (text: string) => v.convo.locator(".msg-row").filter({ hasText: text });
  await walk.state("05-sent-answering", { visible: [row(first), v.convo.getByRole("status", { name: "L3 is answering", exact: true })], hidden: [row(first).getByRole("status", { name: "Sending" })] });

  stage = { ...stage, queued: queue() };
  const sendNow = v.convo.getByRole("button", { name: "Send now", exact: true });
  await walk.state("06-queued", {
    action: () => page.evaluate(() => window.dispatchEvent(new Event("focus"))),
    visible: [row(second).getByRole("button", { name: "Remove", exact: true }), row(third).getByRole("button", { name: "Remove", exact: true }), sendNow],
    hidden: [],
  });
  await expect(sendNow).toHaveCount(1);

  const removeThird = row(third).getByRole("button", { name: "Remove", exact: true });
  // The pressed × spins until the server answers; the message itself keeps its outline, with no second cue.
  await walk.state("07-removing", { action: () => removeThird.click(), visible: [row(third).getByRole("button", { name: "Removing", exact: true })], hidden: [] });
  await expect(row(third)).not.toHaveAttribute("data-pending");
  releaseRemove();
  await page.mouse.move(1, 1);
  await walk.state("08-not-removed", {
    visible: [row(third).getByRole("alert").filter({ hasText: "Not removed: queue temporarily unavailable." }), row(third).getByRole("button", { name: "Retry", exact: true })],
    hidden: [page.locator(".toast")],
  });

  await page.unrouteAll({ behavior: "wait" });
  await overlayChat(page, project.name, (live) => ({ ...live, send_now_reason: null, ...stage }));
  await page.route((url) => url.pathname === "/api/chat/remove", async (route) => {
    stage = { ...stage, queued: queue().slice(0, 1) };
    return route.fulfill({ json: { ok: true } });
  });
  await walk.state("09-removed", {
    action: () => row(third).getByRole("button", { name: "Retry", exact: true }).click(),
    visible: [row(second).getByRole("button", { name: "Remove", exact: true }), sendNow],
    hidden: [row(third), v.convo.getByRole("alert")],
  });

  // Send now spins while its request is out and shows its clock only once the server holds the press; a failed
  // request returns it to ready and says what happened below it.
  let releaseSendNow: () => void = () => {};
  const sendNowHeld = new Promise<void>((resolve) => (releaseSendNow = resolve));
  await page.route((url) => url.pathname === "/api/chat/send-now", async (route) => {
    await sendNowHeld;
    return route.abort("connectionreset");
  }, { times: 1 });
  await walk.state("09b-send-now-requesting", {
    action: () => sendNow.click(),
    visible: [v.convo.getByRole("button", { name: "Sending now", exact: true })],
    hidden: [v.convo.locator(".send-now[data-waiting]")],
  });
  releaseSendNow();
  await walk.state("09c-send-now-unconfirmed", {
    visible: [v.convo.getByRole("alert").filter({ hasText: "Send now unconfirmed. Check this message’s status before trying again." }), sendNow],
    hidden: [],
  });

  stage = { ...stage, queued: queue({ send_now: true, send_now_reason: "Runs next after this turn" }) };
  await walk.state("10-send-now-waiting", {
    action: () => page.evaluate(() => window.dispatchEvent(new Event("focus"))),
    visible: [sendNow, row(third).getByRole("button", { name: "Remove", exact: true })],
    hidden: [v.convo.locator(".send-now-help").filter({ hasText: "Runs next after this turn" })],
  });
  await expect(sendNow).toHaveAttribute("data-waiting", "");
  await walk.state("10b-send-now-waiting-reason", {
    action: () => sendNow.click({ force: true }),
    visible: [v.convo.locator(".send-now-help").filter({ hasText: "Runs next after this turn" })],
    hidden: [],
  });

  stage = { ...stage, queued: queue({ send_now: true, sending: "fixture-send", send_now_reason: "Sending into the current turn" }) };
  await walk.state("11-sending-now", {
    action: () => page.evaluate(() => window.dispatchEvent(new Event("focus"))),
    visible: [row(second), row(third)],
    hidden: [row(second).getByRole("button", { name: "Remove", exact: true }), sendNow, row(third).getByRole("alert")],
  });
  // Claimed messages are on their way: faded, with no spinner, until the session acknowledges them.
  await expect(row(second)).toHaveAttribute("data-pending", "true");
  await expect(row(second).locator(".spinner")).toHaveCount(0);

  stage = { history: [user("turn-a", first), reply("turn-a", "Here is a first draft of the notes."), user("turn-b", second, "delivered"),
    user("turn-c", third, "unconfirmed")], active: { id: "turn-c", trigger: "chat", started_at: at }, busy: true, queued: [] };
  await walk.state("12-unconfirmed-handoff", {
    action: () => page.evaluate(() => window.dispatchEvent(new Event("focus"))),
    visible: [row(third).getByText("Unconfirmed", { exact: true })],
    hidden: [sendNow, row(second).getByText("Unconfirmed", { exact: true })],
  });

  stage = { history: [user("turn-a", first), reply("turn-a", "Here is a first draft of the notes."), user("turn-k", second)], active: null, busy: false,
    queued: [{ id: "q-kept", text: second, at, trigger: "chat", turn_id: "turn-k" }], send_now_reason: "No engine can run L3 right now" };
  await walk.state("13-kept", {
    action: () => page.evaluate(() => window.dispatchEvent(new Event("focus"))),
    visible: [row(second), sendNow],
    hidden: [row(second).getByRole("button", { name: "Remove", exact: true }), v.convo.locator(".send-now-help").filter({ hasText: "No engine can run L3 right now" })],
  });
  await walk.state("13b-kept-reason", {
    action: () => sendNow.click({ force: true }),
    visible: [v.convo.locator(".send-now-help").filter({ hasText: "No engine can run L3 right now" })],
    hidden: [],
  });
});

test("Edit returns the words, a long reason wraps, leaving keeps the failure, and Retry never moves a reader", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const at = new Date(Date.now() - 60_000).toISOString();
  const earlier = Array.from({ length: 12 }, (_, index) => [
    { role: "user", text: `Earlier question ${index + 1} about the release checklist`, at, turn_id: `old-${index}`, trigger: "chat" },
    { role: "assistant", text: `Earlier answer ${index + 1}: the checklist item is done and recorded.`, at, turn_id: `old-${index}`, trigger: "chat" },
  ]).flat();
  const stored: unknown[] = [];
  const reason = "This project's L3 is paused while its installation finishes an update, so new messages wait until it is back";
  let mode: "refuse" | "accept" = "refuse";
  await page.route((url) => url.pathname === "/api/chat", async (route: Route) => {
    if (mode === "refuse") return route.fulfill({ status: 409, json: { error: reason } });
    const { text, request_id: requestId } = route.request().postDataJSON() as { text: string; request_id: string };
    stored.push({ role: "user", text, at: new Date().toISOString(), turn_id: "turn-new", trigger: "chat", request_id: requestId },
      { role: "assistant", text: "Sent and answered once.", at: new Date().toISOString(), turn_id: "turn-new", trigger: "chat" });
    const lines = [{ turn: { id: "turn-new", started_at: new Date().toISOString(), trigger: "chat" } }, { t: "Sent and answered once." }, { done: { turn_id: "turn-new" } }];
    return route.fulfill({ status: 200, contentType: "application/x-ndjson", body: `${lines.map((line) => JSON.stringify(line)).join("\n")}\n` });
  });
  await overlayChat(page, project.name, (live) => ({ ...live, history: [...earlier, ...stored], active: null, busy: false, queued: [] }));

  await walk.open(project.path);
  const first = "Tag the release once the checks pass";
  const row = (text: string) => v.convo.locator(".msg-row").filter({ hasText: text });
  const edit = (text: string) => row(text).getByRole("button", { name: "Edit", exact: true });
  const retry = (text: string) => row(text).getByRole("button", { name: "Retry", exact: true });
  await v.field.fill(first);
  await v.send.click();
  const line = row(first).locator(".message-delivery");
  await page.mouse.move(1, 1);
  await walk.state("14-long-reason", {
    visible: [row(first).getByRole("alert").filter({ hasText: `Not sent: ${reason[0].toLowerCase()}${reason.slice(1)}.` }), retry(first), edit(first)],
    hidden: [],
  });
  // The reason wraps between words inside the column; its actions stay together on one line.
  const [lineBox, columnBox, retryBox, editBox] = await Promise.all([line.boundingBox(), v.convo.boundingBox(), retry(first).boundingBox(), edit(first).boundingBox()]);
  expect(lineBox!.x + lineBox!.width).toBeLessThanOrEqual(columnBox!.x + columnBox!.width);
  expect(Math.abs(retryBox!.y - editBox!.y)).toBeLessThan(2);
  if (v.phone) expect((await row(first).getByRole("alert").boundingBox())!.height).toBeGreaterThan(30);

  await walk.state("15-edit-into-empty-field", { action: () => edit(first).click(), visible: [v.field], hidden: [row(first)] });
  await expect(v.field).toHaveValue(first);
  await expect(v.field).toBeFocused();

  await v.send.click();
  await expect(edit(first)).toBeVisible();
  const added = "Then post the notes";
  await v.field.fill(added);
  await walk.state("16-edit-ahead-of-a-draft", { action: () => edit(first).click(), visible: [v.field], hidden: [row(first)] });
  await expect(v.field).toHaveValue(`${first}\n${added}`);

  // Leaving and returning keeps the failure on the message: the Work tab on the phone, Settings on desktop.
  const words = `${first}\n${added}`;
  await v.send.click();
  await expect(retry(added)).toBeVisible();
  if (v.phone) {
    const nav = page.getByRole("navigation", { name: "Primary", exact: true });
    await nav.getByRole("link", { name: "Work", exact: true }).click();
    await expect(page.getByRole("region", { name: "Work", exact: true })).toBeVisible();
    await walk.state("17-back-still-not-sent", {
      action: () => nav.getByRole("link", { name: "Chat", exact: true }).click(),
      visible: [row(added).getByRole("alert").filter({ hasText: "Not sent:" }), retry(added), edit(added)],
      hidden: [],
    });
  } else {
    await page.locator(".rail").getByRole("link", { name: "Settings", exact: true }).click();
    await expect(v.convo).toBeHidden();
    await walk.state("17-back-still-not-sent", {
      action: () => page.goBack(),
      visible: [row(added).getByRole("alert").filter({ hasText: "Not sent:" }), retry(added), edit(added)],
      hidden: [],
    });
  }
  await expect(v.field).toHaveValue("");

  // Retry returns the message to sending in place; its confirmation arrives while the operator reads older
  // messages, and nothing on screen moves.
  let releaseStream: () => void = () => {};
  const streamHeld = new Promise<void>((resolve) => (releaseStream = resolve));
  mode = "accept";
  await page.route((url) => url.pathname === "/api/chat", async (route) => { await streamHeld; return route.fallback(); }, { times: 1 });
  await retry(added).click();
  await expect(row(added)).toHaveAttribute("data-pending", "true");
  const scroll = v.convo.locator(".convo-scroll");
  await scroll.evaluate((node) => { node.scrollTop = node.scrollHeight - node.clientHeight - 200; });
  await expect.poll(() => scroll.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeGreaterThan(150);
  const anchor = row("Earlier question 12 about");
  const before = (await anchor.boundingBox())!.y;
  releaseStream();
  await walk.state("18-retried-confirmed-in-place", {
    visible: [anchor],
    hidden: [row(added).getByRole("alert")],
  });
  await expect(v.convo.locator(".reply", { hasText: "Sent and answered once." })).toHaveCount(1);
  await expect(v.convo.locator(".msg-row[data-pending]")).toHaveCount(0);
  expect(Math.abs((await anchor.boundingBox())!.y - before)).toBeLessThan(2);
  await expect(v.convo.locator(".msg-row").filter({ hasText: words.split("\n")[1] })).toHaveCount(1);
});

test("a new row enters with a short rise; rows already there, a remount and reduced motion do not", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const v = views(page, info);
  const at = new Date(Date.now() - 60_000).toISOString();
  const history: unknown[] = [
    { role: "user", text: "What is left before the release?", at, turn_id: "turn-1", trigger: "chat" },
    { role: "assistant", text: "Two checks and the notes.", at, turn_id: "turn-1", trigger: "chat" },
  ];
  await overlayChat(page, project.name, (live) => ({ ...live, history, active: null, busy: false, queued: [] }));
  // The page reads again when it becomes visible; an idle conversation otherwise polls slowly.
  const refresh = () => page.evaluate(() => {
    for (const state of ["hidden", "visible"]) {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
      window.dispatchEvent(new Event("visibilitychange"));
    }
  });

  await walk.open(project.path);
  const opened = v.convo.locator(".msg-row").filter({ hasText: "What is left before the release?" });
  await walk.state("19-opened-without-entrance", { visible: [opened], hidden: [] });
  await expect(v.convo.locator("[data-enter]")).toHaveCount(0);

  // A message from another device and its reply arrive while the conversation is on screen.
  history.push(
    { role: "user", text: "Post the notes when they are ready", at: new Date().toISOString(), turn_id: "turn-2", trigger: "chat" },
    { role: "assistant", text: "Posted the notes.", at: new Date().toISOString(), turn_id: "turn-2", trigger: "chat" },
  );
  const reply = v.convo.locator(".msg-row").filter({ hasText: "Posted the notes." });
  await walk.state("20-new-reply-enters", { action: refresh, visible: [reply], hidden: [] });
  await expect(reply).toHaveAttribute("data-enter", "true");
  await expect(opened).not.toHaveAttribute("data-enter");
  expect(await reply.evaluate((node) => getComputedStyle(node).animationName)).toBe("row-enter");
  expect(await reply.evaluate((node) => getComputedStyle(node).animationDuration)).toBe("0.16s");

  // Returning to the conversation shows what is there without replaying an entrance.
  if (v.phone) {
    const nav = page.getByRole("navigation", { name: "Primary", exact: true });
    await nav.getByRole("link", { name: "Work", exact: true }).click();
    await nav.getByRole("link", { name: "Chat", exact: true }).click();
  } else {
    await page.locator(".rail").getByRole("link", { name: "Settings", exact: true }).click();
    await expect(v.convo).toBeHidden();
    await page.goBack();
  }
  await walk.state("21-remount-without-entrance", { visible: [reply], hidden: [] });
  await expect(v.convo.locator("[data-enter]")).toHaveCount(0);

  await page.emulateMedia({ reducedMotion: "reduce" });
  history.push(
    { role: "user", text: "Did the checks pass?", at: new Date().toISOString(), turn_id: "turn-3", trigger: "chat" },
    { role: "assistant", text: "The checks passed.", at: new Date().toISOString(), turn_id: "turn-3", trigger: "chat" },
  );
  const calm = v.convo.locator(".msg-row").filter({ hasText: "The checks passed." });
  await walk.state("22-reduced-motion-at-once", { action: refresh, visible: [calm], hidden: [] });
  expect(await calm.evaluate((node) => getComputedStyle(node).animationName)).toBe("none");
});
