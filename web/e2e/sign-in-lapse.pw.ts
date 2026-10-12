import { test } from "./fixtures";
import { expect, type Page } from "@playwright/test";
import { walkthrough } from "./walkthrough";

/*
 * A lapsed engine sign-in in project chat (docs/OPERATIONS.md, engine sign-in recovery): real coordinator turns
 * against provider fixtures that refuse Claude as its CLI does. The operator sees one plain line per lapse and
 * either the other engine's answer or their message waiting; the provider's own error text never appears.
 */

test.use({ serviceScript: "sign-in-lapse-service.py" });

const answering = "Claude's sign-in has lapsed, so Codex is answering for now. " +
  "To sign in, run claude auth login on the computer running Altitude.";
const waiting = "Claude's sign-in has lapsed and no other engine can answer, so your message waits and sends once " +
  "you are signed in again. To sign in, run claude auth login on the computer running Altitude.";

function views(page: Page) {
  const main = page.getByRole("main");
  const convo = main.getByRole("region", { name: "Conversation", exact: true });
  return {
    convo,
    field: main.getByRole("textbox", { name: /^Message L3 about /, includeHidden: true }),
    send: main.getByRole("button", { name: "Send", exact: true }),
    notice: (text: string) => convo.locator(".sys-line").filter({ hasText: text }),
    provider: convo.getByText(/OAuth token revoked/),
  };
}

test("a lapsed sign-in answers on the other engine and says so once", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  await walk.open("/projects/atlas");
  await walk.state("01-before-the-lapse", {
    visible: [v.convo.getByText("The release notes stay short.")],
    hidden: [v.notice("sign-in has lapsed"), v.provider],
  });
  await v.field.fill("It says activation failed");
  await walk.state("02-answered-on-the-other-engine", {
    action: () => v.send.click(),
    visible: [v.convo.getByText("The activation is waiting for the next quiet point."), v.notice(answering),
      v.notice(answering).getByText("claude auth login", { exact: true })],
    hidden: [v.provider, v.convo.getByText("L3 could not answer this turn.")],
  });
  expect(await v.notice(answering).locator(".sys-text").innerText()).toBe(answering);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await v.field.fill("Anything else?");
  await walk.state("03-later-message-adds-no-second-line", {
    action: () => v.send.click(),
    visible: [v.convo.getByText("Nothing else failed.")],
    hidden: [v.provider],
  });
  await expect(v.convo.locator(".sys-line").filter({ hasText: "sign-in has lapsed" })).toHaveCount(1);
  const card = v.convo.getByRole("article");
  await walk.state("04-notice-opened", {
    action: () => v.notice(answering).getByRole("button", { name: "Show", exact: true }).click(),
    visible: [card.getByText(answering)],
    hidden: [v.notice(answering)],
  });
  await walk.state("05-notice-folded", {
    action: () => card.getByRole("button", { name: "Hide", exact: true }).click(),
    visible: [v.notice(answering)],
    hidden: [card],
  });
});

test("with no other engine the message waits under one plain line", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const v = views(page);
  await walk.open("/projects/harbor");
  const message = v.convo.locator(".msg-row").filter({ hasText: "Oauth token for what?" });
  await v.field.fill("Oauth token for what?");
  await walk.state("01-message-waits", {
    action: () => v.send.click(),
    visible: [message, v.notice(waiting), v.convo.getByRole("button", { name: "Send now", exact: true })],
    hidden: [v.provider, v.convo.getByText("L3 could not answer this turn."), message.locator(".spinner")],
  });
  expect(await v.notice(waiting).locator(".sys-text").innerText()).toBe(waiting);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
