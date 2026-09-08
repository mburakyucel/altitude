import { createServer, type Server } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { test, expect } from "@playwright/test";
import { walkthrough } from "./walkthrough";

// Design evidence only: static fictional boards, no application API or provider execution.
let server: Server;
let origin: string;
const root = resolve("..");
test.beforeAll(async () => {
  server = createServer(async (req, res) => {
    const path = resolve(root, "." + new URL(req.url!, "http://localhost").pathname);
    try {
      if (!path.startsWith(root + sep)) throw new Error("Outside fixture tree");
      const content = await readFile(path);
      res.setHeader("Content-Type", ({ ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png" })[extname(path)] || "text/plain");
      res.end(content);
    } catch { res.writeHead(404); res.end("Not found"); }
  });
  await new Promise<void>(done => server.listen(0, "127.0.0.1", done));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Missing fixture port");
  origin = `http://127.0.0.1:${address.port}/design/wireframes/`;
});
test.afterAll(async () => { await new Promise<void>((done, reject) => server.close(error => error ? reject(error) : done())); });

test("conversation-first proposal: decisions and discussion", async ({ page }, info) => {
  const prefix = info.project.name === "phone" ? "Mobile" : "";
  const route = (scene: string) => origin + prefix + "ConversationFirst" + scene + ".html";
  const walk = walkthrough(page, info);
  const question = page.getByRole("heading", { name: "How long should we keep the old index?" });
  const accept = page.getByRole("link", { name: "Use 7 days & resume", exact: true });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  // Offline font fallback is part of the existing board contract.
  await page.route("https://fonts.**/*", route => route.abort());
  await walk.open(route("NeedsYou"));
  await walk.state("01-needs-you", { visible: [question, accept], hidden: [page.getByRole("textbox")] });
  await walk.state("02-question", {
    action: () => page.getByRole("link", { name: "Open L2 chat" }).click(),
    visible: [question, accept, page.getByText("The new index is ready.", { exact: false })],
    hidden: [page.getByText("Checks finished after the question", { exact: false })],
  });
  await expect(page.getByRole("textbox", { name: "Message the L2" })).toBeVisible();
  await page.getByRole("textbox").fill("Could we roll back after day seven?");
  await walk.state("03-followup", {
    action: () => page.getByRole("button", { name: "Send", exact: true }).click(),
    visible: [question, accept, page.getByText("Could we roll back after day seven?", { exact: true })],
    hidden: [page.getByText("Decision recorded", { exact: true })],
  });
  await page.getByText("Question still open · work waits for your decision", { exact: true }).scrollIntoViewIfNeeded();
  await walk.state("03-followup-answer", { visible: [page.getByText("We could rebuild from the snapshot", { exact: false })], hidden: [] });
  await page.getByRole("textbox").fill("Maybe two weeks, but I’m unsure about cost.");
  await walk.state("04-clarify", {
    action: () => page.getByRole("button", { name: "Send", exact: true }).click(),
    visible: [page.getByText("That means another week of storage cost. Would you like me to use 14 days, or keep comparing?", { exact: true })],
    hidden: [page.getByText("Decision recorded", { exact: true })],
  });
  await walk.open(route("Alternative"));
  await walk.state("05-alternative-draft", { visible: [page.getByRole("textbox")], hidden: [page.getByText("Decision recorded", { exact: true })] });
  await page.getByRole("link", { name: "View question", exact: true }).click();
  await expect(page.getByRole("textbox")).toHaveValue("Keep it for 14 days, then delete it. Go ahead.");
  await walk.state("06-typed-decision", {
    action: () => page.getByRole("button", { name: "Send", exact: true }).click(),
    visible: [page.getByText("14 days approved by you · 10:49", { exact: true }), page.getByText("Decision recorded", { exact: true })],
    hidden: [accept, page.getByRole("button", { name: /confirm/i })],
  });
  await page.goBack();
  await expect(page.getByRole("link", { name: "Use 7 days & resume", exact: true })).toBeHidden();
  await walk.open(route("NeedsYou"));
  await expect(page.getByText("14 days accepted · work will resume.", { exact: true })).toBeVisible();
  await expect(question).toBeHidden();
  await walk.open(route("NeedsYou") + "?reset");
  await walk.state("07-quick-acceptance", { action: () => accept.click(), visible: [page.getByText("Nothing needs your decision.", { exact: true })], hidden: [question, accept] });
  await walk.state("08-resumed", {
    action: () => page.getByRole("link", { name: "View conversation" }).click(),
    visible: [page.getByText("7 days approved by you · 10:49", { exact: true }), page.getByText("Work resumed", { exact: true }).last()], hidden: [accept],
  });
  await walk.state("09-evidence", { action: () => page.getByText("Activity & evidence", { exact: true }).click(), visible: [page.getByRole("link", { name: "PR #42", exact: true })], hidden: [] });
  await expect(page.getByRole("link", { name: "PR #42", exact: true })).toHaveAttribute("target", "_blank");
  await walk.open(route("Stale"));
  await walk.state("10-resolved-elsewhere", { visible: [page.getByText("This question was resolved in another conversation.", { exact: true })], hidden: [accept] });
  await walk.open(route("NeedsYou") + "?reset");
  await page.getByRole("link", { name: "Open L2 chat" }).click();
  await page.getByRole("link", { name: "Needs you", exact: true }).first().click();
  await expect(page).toHaveURL(route("NeedsYou"));
  await page.goForward();
  await expect(page).toHaveURL(route("Question"));
  await page.getByRole("textbox").fill("14 days");
  await walk.state("11-simple-answer", { action: () => page.getByRole("button", { name: "Send", exact: true }).click(), visible: [page.getByText("14 days", { exact: true }), page.getByText("Decision recorded", { exact: true })], hidden: [accept, page.getByRole("button", { name: /confirm/i })] });
  expect(errors).toEqual([]);
});

test("conversation-first proposal: phone and desktop state inventory", async ({ page }, info) => {
  test.setTimeout(90_000);
  const prefix = info.project.name === "phone" ? "Mobile" : "";
  const walk = walkthrough(page, info);
  await page.route("https://fonts.**/*", route => route.abort());
  const states: [string, string][] = [
    ["list-loading", "Loading decisions…"], ["list-error", "Could not load Needs you."],
    ["list-offline", "Offline · showing saved decisions."], ["discussion", "Your follow-up is in the L2 chat. Still awaiting your decision."],
    ["loading", "Loading the question…"], ["read-error", "Could not load this conversation."],
    ["cached-error", "Could not refresh. Showing saved discussion."], ["accepting", "Recording…"],
    ["accept-error", "Could not record your choice. Try again."],
    ["denied", "This connection cannot send messages or decisions. Reconnect to continue."],
    ["sending", "Sending…"], ["send-error", "Not sent. Your draft is still here. Retry with Send."],
    ["waiting", "Message saved · the L2 will answer when capacity is available."],
    ["reply-error", "The L2 could not answer. Your message is saved; the question is still open."],
    ["accepted-waiting", "Waiting for capacity to resume the L2. Your choice is saved."],
    ["no-recommendation", "No recommendation yet. Discuss the tradeoff with the L2 here."],
    ["simple-input", "Ask a question or say how to proceed."],
    ["new-reply", "Latest messages · L2 replied"],
    ["revised", "The question changed while this page was open."], ["missing", "This question is no longer available."],
    ["archived", "This task is complete. Its conversation stays readable."],
    ["listening", "Listening · 0:08"], ["transcribing", "Transcribing…"],
    ["dictated", "Ask a question or say how to proceed."], ["mic-denied", "Microphone access denied. You can keep typing."],
    ["voice-error", "Could not transcribe. Your typed draft is still here."], ["voice-unavailable", "Voice is unavailable. You can keep typing."],
  ];
  for (const [state, text] of states) {
    await walk.open(origin + prefix + "ConversationFirstStates.html?state=" + state + "&reset");
    const marker = page.getByText(text, { exact: true }).first();
    await marker.scrollIntoViewIfNeeded();
    await walk.state(state, { visible: [marker], hidden: [page.getByRole("combobox")] });
    if (["accepted-waiting", "archived", "no-recommendation", "revised"].includes(state)) {
      await expect(page.getByRole("link", { name: "Use 7 days & resume", exact: true })).toBeHidden();
    }
    if (state === "archived") await expect(page.getByRole("textbox")).toBeHidden();
    if (state === "send-error") await expect(page.getByRole("textbox")).toHaveValue("Could we roll back after day seven?");
    if (state === "denied") await expect(page.getByRole("textbox")).toBeDisabled();
    if (["list-loading", "list-error"].includes(state)) await expect(page.locator(".badge")).toHaveCount(0);
    if (["cached-error", "denied", "accepting"].includes(state)) await expect(page.getByRole("button", { name: /Use 7 days|Recording/ })).toBeDisabled();
    const overflow = await page.locator(".cf").evaluate(el => el.scrollWidth > el.clientWidth);
    expect(overflow, state + " must not scroll horizontally").toBe(false);
  }
  for (const scene of ["NeedsYou", "Question", "Followup", "Alternative", "Clarify", "Accepted", "Empty", "Stale"]) {
    await walk.open(origin + prefix + "ConversationFirst" + scene + ".html?dark&reset");
    await expect(page.locator(".root")).toHaveAttribute("data-theme", "dark");
    await walk.state("dark-" + scene, { visible: [page.getByRole("heading", { level: 1 })], hidden: [] });
  }
});

test("conversation-first proposal: recovery and voice actions", async ({ page }, info) => {
  const prefix = info.project.name === "phone" ? "Mobile" : "";
  const route = (state: string) => origin + prefix + "ConversationFirstStates.html?state=" + state + "&reset";
  const walk = walkthrough(page, info);
  await page.route("https://fonts.**/*", route => route.abort());
  await walk.open(route("waiting"));
  await page.getByRole("link", { name: "Use 7 days & resume", exact: true }).click();
  await expect(page.getByText("Decision recorded", { exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("link", { name: "Use 7 days & resume", exact: true })).toBeHidden();
  await expect(page.getByText("Decision recorded", { exact: true })).toBeVisible();
  await walk.open(route("read-error"));
  await page.getByRole("link", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("textbox")).toBeEnabled();
  await page.getByRole("textbox").fill("Keep it for 14 days,");
  await page.getByRole("button", { name: "Use microphone", exact: true }).click();
  await walk.state("voice-recording", { visible: [page.getByText("Listening · 0:08", { exact: true })], hidden: [page.getByRole("textbox")] });
  await walk.state("voice-cancelled", { action: () => page.getByRole("link", { name: "Cancel", exact: true }).click(), visible: [page.getByRole("textbox")], hidden: [page.getByText("Listening · 0:08", { exact: true })] });
  await expect(page.getByRole("textbox")).toHaveValue("Keep it for 14 days,");
  await page.getByRole("button", { name: "Use microphone", exact: true }).click();
  await page.getByRole("link", { name: "Stop", exact: true }).click();
  await expect(page.getByRole("textbox")).toHaveValue("Keep it for 14 days, then delete it. Go ahead.");
  await expect(page.getByText("Decision recorded", { exact: true })).toBeHidden();
  await walk.state("voice-stopped-to-draft", { visible: [page.getByRole("textbox")], hidden: [page.getByLabel("Audio waveform")] });
  await page.getByRole("textbox").fill("Keep it for 14 days,");
  await page.getByRole("button", { name: "Use microphone", exact: true }).click();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await walk.state("voice-sent", { visible: [page.getByText("Decision recorded", { exact: true })], hidden: [page.getByLabel("Audio waveform"), page.getByText("Transcribing…", { exact: true })] });
});
