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

test("conversation-first design: independent questions, quick choices and one conversation", async ({ page }, info) => {
  const prefix = info.project.name === "phone" ? "Mobile" : "";
  const route = (scene: string) => origin + prefix + "ConversationFirst" + scene + ".html";
  const walk = walkthrough(page, info);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("https://fonts.**/*", route => route.abort());
  const group = page.getByRole("article", { name: "Index rollout questions", exact: true });
  const recipient = page.getByRole("heading", { name: "Who should receive the rollout report?", exact: true });
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  await walk.open(route("NeedsYou") + "?reset");
  await walk.state("01-needs-you", {
    visible: [group, recipient, page.getByRole("button", { name: "Use recommendations", exact: true })],
    hidden: [field, page.locator('[data-pick][aria-pressed="true"]')],
  });
  await page.getByRole("link", { name: "Open L2 chat", exact: false }).click();
  await expect(page).toHaveURL(route("Group"));
  await expect(group.getByRole("heading")).toHaveCount(3);
  await expect(page.getByRole("button", { name: /^Send \d answers?$/ })).toBeHidden();
  await group.getByRole("button", { name: "14 days", exact: true }).click();
  await expect(page.getByRole("button", { name: "Use recommendations", exact: true })).toBeHidden();
  await group.getByRole("button", { name: "East", exact: true }).click();
  await walk.state("03-group", {
    visible: [group, recipient, page.getByRole("button", { name: "Send 2 answers", exact: true }), field],
    hidden: [page.getByText("Decision recorded", { exact: true }), page.getByRole("button", { name: "Use recommendations", exact: true })],
  });
  await page.getByRole("button", { name: "Send 2 answers", exact: true }).click();
  await expect(recipient).toBeVisible();
  await expect(page.getByRole("heading", { name: "Where should the backup live?", exact: true })).toBeHidden();
  await expect(page.getByText("Backup region: East.", { exact: true })).toBeVisible();
  await field.fill("Release team");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Decision recorded", { exact: true })).toBeVisible();
  await page.goBack();
  await expect(recipient).toBeHidden();
  await expect(page.getByText("Decision recorded", { exact: true })).toBeVisible();

  await walk.open(route("Question") + "?reset");
  await walk.state("02-single", { visible: [page.getByRole("article", { name: "Open question", exact: true }), page.getByRole("button", { name: "7 days · recommended", exact: true }), field], hidden: [page.getByRole("button", { name: /^Send \d/ })] });
  await page.getByRole("button", { name: "14 days", exact: true }).click();
  await expect(page.getByText("Decision recorded", { exact: true })).toBeVisible();
  await expect(page.getByText("14 days", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /confirm/i })).toHaveCount(0);

  await walk.open(route("Group") + "?reset");
  await field.fill("Could we roll back after day seven?");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await walk.state("04-followup", {
    visible: [page.getByText("Discussion leaves every question open.", { exact: true }), page.getByText("Could we roll back after day seven?", { exact: true }), field],
    hidden: [page.getByText("Decision recorded", { exact: true })],
  });
  await field.fill("Keep 14 days; use snapshots so region no longer matters.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await walk.state("05-partial", {
    visible: [recipient, page.getByText("Retention: 14 days.", { exact: true }), page.getByText("Backup region: Closed: snapshots replace the regional backup.", { exact: true }), field],
    hidden: [page.getByRole("heading", { name: "Where should the backup live?", exact: true }), page.getByRole("button", { name: "Use recommendations", exact: true })],
  });
  await field.fill("Release team");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await walk.state("06-accepted", { visible: [page.getByText("All three questions resolved.", { exact: true }), page.getByText("Decision recorded", { exact: true }), page.getByText("Work resumed", { exact: true }), field], hidden: [recipient] });
  await page.getByText("Activity & evidence", { exact: true }).click();
  await expect(page.getByRole("link", { name: "PR #42", exact: true })).toHaveAttribute("target", "_blank");

  await walk.open(route("NeedsYou") + "?reset");
  await page.getByRole("button", { name: "Use recommendations", exact: true }).click();
  await walk.state("recommendations-leave-plain-question", { visible: [recipient, page.getByText("Retention: 7 days.", { exact: true }), page.getByText("Backup region: West.", { exact: true })], hidden: [page.getByRole("button", { name: "Use recommendations", exact: true })] });
  await walk.open(route("Group") + "?reset");
  await group.getByRole("button", { name: "14 days", exact: true }).click();
  await group.getByRole("button", { name: "14 days", exact: true }).click();
  await expect(page.locator('[data-pick][aria-pressed="true"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Use recommendations", exact: true })).toBeVisible();
  await group.getByRole("button", { name: "14 days", exact: true }).click();
  await page.getByRole("button", { name: "Send 1 answer", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Where should the backup live?", exact: true })).toBeVisible();
  await expect(recipient).toBeVisible();
  await page.getByRole("button", { name: "Use recommendations", exact: true }).click();
  await expect(page.getByText("Retention: 14 days.", { exact: true })).toBeVisible();
  await expect(page.getByText("Backup region: West.", { exact: true })).toBeVisible();
  await walk.open(route("Group") + "?reset");
  await field.fill("Keep 14 days, use West, and send the report to the release team.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("All three questions resolved.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /confirm/i })).toHaveCount(0);
  await walk.open(route("NeedsYou"));
  await expect(page.getByText("Nothing needs your decision.", { exact: true })).toBeVisible();
  await expect(group).toBeHidden();
  await expect(page.getByRole("button", { name: "Use recommendations", exact: true })).toBeHidden();
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
    if (prefix && ["sending", "simple-input", "dictated"].includes(state)) {
      await walk.state(state, { visible: [page.getByRole("textbox")], hidden: [marker, page.getByRole("combobox")] });
      if (state === "sending") await expect(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
    } else {
      await marker.scrollIntoViewIfNeeded();
      await walk.state(state, { visible: [marker], hidden: [page.getByRole("combobox")] });
    }
    if (["accepted-waiting", "archived", "no-recommendation", "revised"].includes(state)) {
      await expect(page.getByRole("button", { name: "Use 7 days & resume", exact: true })).toBeHidden();
    }
    if (state === "archived") await expect(page.getByRole("textbox")).toBeHidden();
    if (state === "send-error") await expect(page.getByRole("textbox")).toHaveValue("Could we roll back after day seven?");
    if (state === "denied") await expect(page.getByRole("textbox")).toBeDisabled();
    if (["list-loading", "list-error"].includes(state)) await expect(page.locator(".badge")).toHaveCount(0);
    if (["cached-error", "denied", "accepting"].includes(state)) await expect(page.getByRole("button", { name: /Use 7 days|Recording/ })).toBeDisabled();
    const overflow = await page.locator(".cf").evaluate(el => el.scrollWidth > el.clientWidth);
    expect(overflow, state + " must not scroll horizontally").toBe(false);
  }
  for (const scene of ["NeedsYou", "Question", "Group", "Followup", "Partial", "Accepted"]) {
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
  await page.getByRole("button", { name: "Use 7 days & resume", exact: true }).click();
  await expect(page.getByText("Decision recorded", { exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("button", { name: "Use 7 days & resume", exact: true })).toBeHidden();
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
