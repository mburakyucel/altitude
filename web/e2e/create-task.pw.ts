import { expect, type Page, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// Create task under an L3 reply (SPEC.md §3.3), against the real coordinator verbs, queue and task store.
const TITLE = "Refresh Needs you as soon as an answer is sent";
const QUESTION = "The Needs you badge lags after I answer. Is that a bug?";
const PRESS = `Create task: ${TITLE}`;

test.use({ serviceScript: "create-task-service.py" });

function chat(page: Page, request: APIRequestContext) {
  const convo = page.getByRole("region", { name: "Conversation", exact: true });
  const field = page.getByRole("textbox", { name: "Message L3 about atlas" });
  const status = async () => (await (await request.get("/fixture/status")).json()) as {
    tasks: { slug: string; title: string; offer_turn: string | null }[];
    verbs: { args: string[]; returncode: number; stderr: string }[];
  };
  return {
    convo, field, status,
    button: convo.getByRole("button", { name: "Create task", exact: true }),
    // The same button, by the state it shows (SPEC.md §3.3).
    state: (name: string) => convo.getByRole("button", { name, exact: true }),
    press: convo.getByText(PRESS, { exact: true }),
    title: convo.getByText(TITLE, { exact: true }),
    typing: convo.getByRole("status", { name: "L3 is answering" }),
    async ask(text: string, answer: string) {
      await field.fill(text);
      await convo.getByRole("button", { name: /^(Send|Queue)$/ }).click();
      await expect(convo.getByText(answer, { exact: true })).toBeVisible();
      await expect.poll(async () => (await (await request.get("/api/chat/atlas")).json()).active).toBeNull();
    },
    async mode(data: { hold?: boolean; fail?: boolean }) {
      expect((await request.post("/fixture/mode", { data: { hold: false, fail: false, ...data } })).ok()).toBe(true);
    },
  };
}

const OFFERED = "Yes, a small one: the count refreshes only on the next poll after you answer.";

test("an offering reply creates one task from one press", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const c = chat(page, request);
  await walk.open("/projects/atlas");
  await c.ask("Thanks for the update.", "Glad it helped.");
  await walk.state("create-task-01-reply-without-action", { visible: [c.convo.getByText("Glad it helped.", { exact: true })], hidden: [c.button] });
  await c.ask(QUESTION, OFFERED);
  await walk.state("create-task-02-reply-with-action", { visible: [c.button, c.title], hidden: [] });
  await expect(c.button).toHaveAccessibleDescription(TITLE);
  if (info.project.name === "phone") {
    // The button looks quiet; its touch area still meets the 44px target.
    const hit = await c.button.evaluate((node) => Number.parseFloat(getComputedStyle(node, "::after").height) || node.getBoundingClientRect().height);
    expect(hit).toBeGreaterThanOrEqual(44);
  }
  await c.field.fill("A draft that stays");
  await c.mode({ hold: true });
  let releasePost!: () => void;
  const posted = new Promise<void>((resolve) => { releasePost = resolve; });
  await page.route("**/api/chat", async (route) => {
    if (route.request().method() === "POST" && route.request().postDataJSON()?.offer_turn) await posted;
    await route.fallback();
  }, { times: 1 });
  await c.button.click();
  const working = c.state("Create task, working");
  await walk.state("create-task-03-pressed-working", { visible: [working], hidden: [c.press] });
  releasePost();
  await expect(working).toBeFocused();
  await walk.state("create-task-04-l3-working", { visible: [working, c.typing], hidden: [c.press, c.title] });
  await expect(working).toBeFocused();
  await expect(c.field).toHaveValue("A draft that stays");
  expect((await request.post("/fixture/release")).ok()).toBe(true);
  const card = c.convo.locator(".task-card").filter({ hasText: TITLE });
  await walk.state("create-task-05-task-created", { visible: [c.state("Task created"), card, c.convo.getByText("Created it from our conversation.", { exact: true })], hidden: [c.button, c.typing, c.press] });
  const { tasks, verbs } = await c.status();
  expect(tasks.map((task) => task.title)).toEqual([TITLE]);
  expect(tasks[0].offer_turn).toMatch(/^[0-9a-f]{12}$/);
  expect(verbs.filter((verb) => verb.args[1] === "new").map((verb) => verb.returncode !== 0 && /already created task/.test(verb.stderr))).toEqual([false, true]);
  // A stale window pressing again is refused by Altitude, not turned into a second task.
  const again = await request.post("/api/chat", { data: { project: "atlas", offer_turn: tasks[0].offer_turn } });
  expect(again.status()).toBe(409);
  await page.reload();
  await expect(card).toBeVisible();
  await expect(c.state("Task created")).toBeVisible();
  await expect(c.button).toBeHidden();
});

test("a press waits while L3 is busy, and Remove brings the action back", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const c = chat(page, request);
  await walk.open("/projects/atlas");
  await c.ask(QUESTION, OFFERED);
  expect((await request.post("/fixture/system")).ok()).toBe(true);
  await expect.poll(async () => (await (await request.get("/api/chat/atlas")).json()).active?.trigger).toBe("restart");
  await c.button.click();
  const waiting = c.state("Create task, waiting for L3");
  const remove = c.convo.getByRole("button", { name: "Remove", exact: true });
  await walk.state("create-task-06-waiting-for-l3", { visible: [waiting, remove], hidden: [c.press, c.convo.getByRole("list", { name: "Queued messages" })] });
  await expect(waiting).toBeFocused();
  if (info.project.name === "phone") {
    const hit = await remove.evaluate((node) => Number.parseFloat(getComputedStyle(node, "::after").height));
    expect(hit).toBeGreaterThanOrEqual(44);
  }
  await remove.click();
  await walk.state("create-task-07-removed-action-back", { visible: [c.button, c.title], hidden: [waiting, remove] });
  expect((await request.post("/fixture/release")).ok()).toBe(true);
  await expect.poll(async () => (await (await request.get("/api/chat/atlas")).json()).active).toBeNull();
  expect((await c.status()).tasks).toEqual([]);
});

test("refused, unconfirmed and failed presses never create a duplicate", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const c = chat(page, request);
  await walk.open("/projects/atlas");
  await c.ask(QUESTION, OFFERED);
  await page.route("**/api/chat", (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 409, json: { error: "The conversation has moved on, so this was not sent." } })
    : route.fallback(), { times: 1 });
  await c.button.click();
  await walk.state("create-task-08-not-sent", { visible: [c.button, c.convo.getByRole("alert").filter({ hasText: "The conversation has moved on, so this was not sent." })], hidden: [] });
  await page.route("**/api/chat", (route) => route.request().method() === "POST" ? route.abort() : route.fallback(), { times: 1 });
  await c.button.click();
  await walk.state("create-task-09-unconfirmed-not-saved", { visible: [c.button, c.convo.getByRole("alert").filter({ hasText: /^Not sent$/ })], hidden: [] });
  await c.mode({ fail: true });
  await c.button.click();
  const retry = c.state("Retry, Create task");
  await walk.state("create-task-10-l3-failed", { visible: [retry], hidden: [c.button, c.press, c.convo.getByText("L3 could not answer this turn.")] });
  await c.mode({});
  await retry.click();
  const card = c.convo.locator(".task-card").filter({ hasText: TITLE });
  await walk.state("create-task-11-retried-task-created", { visible: [c.state("Task created"), card], hidden: [c.button, retry] });
  expect((await c.status()).tasks.map((task) => task.title)).toEqual([TITLE]);
});

test("typing an answer instead retires the action", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const c = chat(page, request);
  await walk.open("/projects/atlas");
  await c.ask(QUESTION, OFFERED);
  await expect(c.button).toBeVisible();
  await c.ask("Not now, thanks.", "Glad it helped.");
  await walk.state("create-task-12-answered-by-typing", { visible: [c.convo.getByText(OFFERED, { exact: true })], hidden: [c.button, c.title] });
  expect((await c.status()).tasks).toEqual([]);
});
