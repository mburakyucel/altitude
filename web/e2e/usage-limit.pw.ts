import { test } from "./fixtures";
import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { walkthrough } from "./walkthrough";

/*
 * A used-up usage window in project chat (docs/SESSION_LIFECYCLE.md, automatic routing): real coordinator turns
 * against a reading that puts the engine L3 would use at its limit. The operator sees one plain line naming the
 * engine, its reset in local time and either the engine answering instead or that the message waits until then.
 * A waiting message stays queued when sent now and goes out on the L3 choice the operator picks meanwhile.
 */

test.use({ serviceScript: "usage-limit-service.py" });

// The reset is two hours ahead, so near midnight it shows with its date.
const clock = String.raw`(?:[A-Z][a-z]{2} \d{1,2}, )?\d{1,2}:\d{2}(?:\s?[AP]M)?`;
const answering = new RegExp(String.raw`^Claude's usage limit is reached until ${clock}, so Codex is answering for now\.$`);
const waiting = new RegExp(String.raw`^Claude's usage limit is reached until ${clock} and no other engine can answer, ` +
  String.raw`so your message waits until then\.$`);

function views(page: Page) {
  const main = page.getByRole("main");
  const convo = main.getByRole("region", { name: "Conversation", exact: true });
  return {
    convo,
    field: main.getByRole("textbox", { name: /^Message L3 about /, includeHidden: true }),
    send: main.getByRole("button", { name: "Send", exact: true }),
    notice: convo.locator(".sys-line").filter({ hasText: "usage limit is reached" }),
  };
}

test("a used-up window answers on the other engine and says so once", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  await walk.open("/projects/atlas");
  await walk.state("01-before-the-message", {
    visible: [v.convo.getByText("The release notes stay short.")],
    hidden: [v.notice],
  });
  await v.field.fill("Is the release out?");
  await walk.state("02-answered-on-the-other-engine", {
    action: () => v.send.click(),
    visible: [v.convo.getByText("The release is waiting for the next quiet point."), v.notice],
    hidden: [v.convo.getByText("L3 could not answer this turn.")],
  });
  expect(await v.notice.locator(".sys-text").innerText()).toMatch(answering);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await v.field.fill("Anything else?");
  await walk.state("03-later-message-adds-no-second-line", {
    action: () => v.send.click(),
    visible: [v.convo.getByText("Nothing else is pending.")],
    hidden: [],
  });
  await expect(v.notice).toHaveCount(1);
});

/** The first listed option of the engine the fixture leaves runnable, as the Models dialog labels it. */
async function otherModel(request: APIRequestContext, project: string) {
  const view = await (await request.get(`/api/defaults/${project}`)).json() as {
    engines: { value: string }[]; models: { engine: string; model: string | null; label: string }[] };
  return view.models.find((row) => row.engine === view.engines[1]!.value)!;
}

test("with no other engine the message waits under one line with the reset", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  await walk.open("/projects/harbor");
  const message = v.convo.locator(".msg-row").filter({ hasText: "Is the release out?" });
  await v.field.fill("Is the release out?");
  await walk.state("01-message-waits", {
    action: () => v.send.click(),
    visible: [message, v.notice, v.convo.getByRole("button", { name: "Send now", exact: true })],
    hidden: [v.convo.getByText("L3 could not answer this turn."), message.locator(".spinner")],
  });
  expect(await v.notice.locator(".sys-text").innerText()).toMatch(waiting);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  const sendNow = v.convo.getByRole("button", { name: "Send now", exact: true });
  await walk.state("02-send-now-keeps-it-queued", {
    action: () => sendNow.press("Enter"),  // muted: a press shows why it cannot send
    visible: [message, sendNow, page.getByText("No engine is available. The message stays queued.")],
    hidden: [v.convo.getByText("L3 could not answer this turn.")],
  });
  await expect(v.notice).toHaveCount(1);

  const other = await otherModel(request, "harbor");
  const dialog = page.getByRole("dialog", { name: "Models · L3 · harbor only", exact: true });
  await page.getByRole("main").getByRole("button", { name: /^L3 model: / }).click();
  await dialog.getByRole("radio", { name: new RegExp(`^${other.label}`) }).check();
  await walk.state("03-another-l3-choice-sends-it", {
    action: () => dialog.getByRole("button", { name: "Use for L3 in harbor", exact: true }).click(),
    visible: [message, v.convo.getByText("The release is waiting for the next quiet point.")],
    hidden: [dialog, sendNow, v.convo.getByText("L3 could not answer this turn.")],
  });
  await expect(v.notice).toHaveCount(1);
});
