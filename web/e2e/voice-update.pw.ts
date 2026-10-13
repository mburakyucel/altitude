import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { fixtureHost, READY } from "./hostVoice";
import { walkthrough } from "./walkthrough";

// Presentation overlays only: these exercise the real Settings/composer components and audio worklet,
// not runtime installation, promotion or cleanup. Backend transition evidence lives in Python tests.
const downloading = { state: "downloading", done_bytes: 12_000_000, download_bytes: 40_000_000 };
const updating = (update: Record<string, unknown>, state = "ready") => ({ ...READY, state, update });
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });

const remove = (page: Page) => page.getByRole("button", { name: /^Remove voice \(/ });
const inUse = (page: Page, title: string) => page.getByRole("main").locator(".voice-option")
  .filter({ has: page.getByRole("heading", { name: title, exact: true }) }).getByText("In use", { exact: true });

test("voice update presentation: all phases and outcomes keep supported voice installed", async ({ page }, info) => {
  const host = await fixtureHost(page, updating({ state: "preparing" }));
  const walk = walkthrough(page, info);
  const cancel = button(page, "Cancel update");
  const retry = button(page, "Retry update");
  const update = button(page, "Update voice");
  // Running phases are one line with Cancel update; Remove voice returns when the update ends.
  const phases = [
    { name: "01-preparing", update: { state: "preparing" }, text: "Updating…" },
    { name: "02-downloading", update: downloading, text: "Updating · 12 MB of 40 MB" },
    { name: "03-waiting", update: { state: "waiting" }, text: "Updating · finishes after dictation" },
    { name: "04-checking", update: { state: "checking" }, text: "Updating…" },
  ];
  for (const phase of phases) {
    host.state = updating(phase.update);
    await walk.open("/settings/voice");
    await walk.state(phase.name, {
      visible: [page.getByText(phase.text, { exact: true }), cancel, inUse(page, "This computer")],
      hidden: [remove(page), retry, update, page.getByRole("alert"), button(page, "Set up voice")],
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (phase.update.state === "downloading") {
      await expect(page.getByRole("progressbar", { name: "Updating voice", exact: true })).toHaveAttribute("value", "12000000");
    } else {
      await expect(page.getByRole("progressbar")).toHaveCount(0);
    }
  }
  const outcomes = [
    { name: "05-failed", update: { state: "failed", reason: "Download failed. Try again." }, text: "Update did not finish. Download failed.", action: retry },
    { name: "06-cancelled", update: { state: "cancelled" }, text: null, action: update },
    { name: "07-resource-refusal", update: { state: "failed", reason: "Voice needs 2.5 GB of free memory to check the update." }, text: "Update did not finish. Voice needs 2.5 GB of free memory to check the update.", action: retry },
  ];
  for (const outcome of outcomes) {
    host.state = updating(outcome.update);
    await walk.open("/settings/voice");
    await walk.state(outcome.name, {
      visible: [...(outcome.text ? [page.getByText(outcome.text, { exact: true })] : []), outcome.action, remove(page)],
      hidden: [cancel, page.getByRole("alert"), page.getByText(/cancelled/i), button(page, "Set up voice")],
    });
    await expect(outcome.action).not.toHaveClass(/btn-primary/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  host.state = READY;
  await walk.open("/settings/voice");
  await walk.state("08-completed", {
    visible: [remove(page), page.getByText("While you dictate, the speech process uses about 2 GB of memory.", { exact: true })],
    hidden: [retry, cancel, update, page.getByRole("progressbar")],
  });
  host.state = updating(downloading);
  await walk.open("/settings");
  await walk.state("09-overview-supported", {
    visible: [page.getByRole("link", { name: "Voice input This computer", exact: true })],
    hidden: [page.getByText("This computer · not set up", { exact: true })],
  });
  expect(host.requests).toEqual([]);
});

test("voice update presentation: cancel, denied removal, update again and removal show authoritative outcomes", async ({ page }, info) => {
  const host = await fixtureHost(page, updating(downloading));
  const walk = walkthrough(page, info);
  let release = () => {};
  let pending: Promise<void> | null = null;
  let denyRemove = true;
  const actions: string[] = [];
  await page.route((url) => url.pathname === "/api/voice/host", async (route) => {
    const { action } = route.request().postDataJSON();
    actions.push(action);
    await pending;
    if (action === "remove" && denyRemove) return route.fulfill({ status: 403, json: { error: "Fixture voice removal denied" } });
    host.state = action === "cancel" ? updating({ state: "cancelled" })
      : action === "setup" ? updating(downloading) : { state: "absent", download_bytes: 701_000_000 };
    return route.fulfill({ json: { backend: host.backend, selection: "fixture-host", host: host.state } });
  });
  const hold = () => { pending = new Promise<void>((resolve) => { release = resolve; }); };
  try {
    await walk.open("/settings/voice");
    hold();
    await button(page, "Cancel update").click();
    await walk.state("01-cancel-pending", {
      visible: [page.getByRole("img", { name: "Cancelling update", exact: true })],
      hidden: [button(page, "Update voice"), remove(page)],
    });
    expect(actions).toEqual(["cancel"]);
    release();
    await walk.state("02-cancelled", {
      visible: [button(page, "Update voice"), remove(page)],
      hidden: [page.getByRole("progressbar"), button(page, "Cancel update"), page.getByRole("alert"), page.getByText(/cancelled/i)],
    });
    await page.reload();
    await expect(button(page, "Update voice")).toBeVisible();
    await remove(page).click();
    await walk.state("03-remove-denied", {
      visible: [page.getByRole("alert").filter({ hasText: "Fixture voice removal denied" }), button(page, "Update voice"), remove(page), inUse(page, "This computer")],
      hidden: [button(page, "Set up voice")],
    });
    hold();
    await button(page, "Update voice").click();
    await walk.state("04-update-pending", {
      visible: [page.getByRole("img", { name: "Starting update", exact: true })],
      hidden: [button(page, "Cancel update")],
    });
    await expect(remove(page)).toBeDisabled();
    release();
    await expect(button(page, "Cancel update")).toBeVisible();
    await button(page, "Cancel update").click();
    await expect(remove(page)).toBeVisible();
    denyRemove = false;
    hold();
    await remove(page).click();
    await walk.state("05-removal-pending", {
      visible: [page.getByRole("img", { name: "Removing voice", exact: true })], hidden: [],
    });
    await expect(button(page, "Update voice")).toBeDisabled();
    release();
    await walk.state("06-removed", {
      visible: [button(page, "Set up voice")],
      hidden: [button(page, "Cancel update"), button(page, "Update voice"), remove(page), page.getByRole("progressbar"), page.getByRole("alert")],
    });
    expect(actions).toEqual(["cancel", "remove", "setup", "cancel", "remove"]);
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("voice update presentation: unsupported runtime points to update without starting capture", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const host = await fixtureHost(page, updating(downloading, "outdated"));
  const walk = walkthrough(page, info);
  await walk.open(project.path);
  await button(page, "Start voice input").click();
  const viewUpdate = page.getByRole("link", { name: "View update", exact: true });
  await walk.state("01-unavailable-during-update", {
    visible: [page.getByText("Voice is updating on this computer.", { exact: false }), viewUpdate],
    hidden: [button(page, "Stop voice input")],
  });
  await viewUpdate.click();
  await walk.state("02-required-update", {
    visible: [page.getByText("Voice needs an update.", { exact: true }), page.getByText("Updating · 12 MB of 40 MB", { exact: true }), button(page, "Cancel update")],
    hidden: [button(page, "Set up voice"), remove(page)],
  });
  await walk.open("/settings");
  await expect(page.getByRole("link", { name: "Voice input This computer · update required", exact: true })).toBeVisible();
  host.state = updating({ state: "failed", reason: "Download failed. Try again." }, "outdated");
  await walk.open("/settings/voice");
  await walk.state("03-unusable-failure-alert", {
    visible: [page.getByRole("alert").filter({ hasText: "Voice needs an update. Download failed." }), button(page, "Update voice")],
    hidden: [button(page, "Cancel update"), button(page, "Retry update")],
  });
  await expect(button(page, "Update voice")).toHaveClass(/btn-primary/);
  await walk.open(project.path);
  await button(page, "Start voice input").click();
  await walk.state("04-needs-explicit-update", {
    visible: [page.getByText("Voice needs an update on this computer.", { exact: false }), page.getByRole("link", { name: "Update voice", exact: true })],
    hidden: [viewUpdate, button(page, "Stop voice input")],
  });
  expect(host.requests).toEqual([]);
});

test("voice update presentation: choosing browser leaves the cancelled update's action when switching back", async ({ page }, info) => {
  const host = await fixtureHost(page, updating(downloading));
  const walk = walkthrough(page, info);
  await page.route((url) => url.pathname === "/api/voice", async (route) => {
    if (route.request().method() === "POST") {
      host.backend = route.request().postDataJSON().backend;
      if (host.backend === "browser") host.state = updating({ state: "cancelled" });
    }
    return route.fulfill({ json: { backend: host.backend, selection: `fixture-${host.backend}`, host: host.state } });
  });
  await walk.open("/settings/voice");
  await button(page, "Use browser recognition").click();
  await walk.state("01-browser-shows-cancelled-host-update", {
    visible: [inUse(page, "Browser recognition"), button(page, "Use this computer"), button(page, "Update voice"), remove(page)],
    hidden: [button(page, "Cancel update"), page.getByRole("progressbar")],
  });
  await button(page, "Use this computer").click();
  await walk.state("02-host-keeps-manual-update", {
    visible: [inUse(page, "This computer"), button(page, "Update voice")],
    hidden: [button(page, "Cancel update"), button(page, "Use this computer"), page.getByRole("progressbar")],
  });
  expect(host.requests).toEqual([]);
});

test("voice update presentation: supported recording survives update outcomes and Send clears transcript", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const host = await fixtureHost(page, updating(downloading));
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const field = main.getByRole("textbox", { name: /^Message L3 about /, includeHidden: true });
  const sent: string[] = [];
  await page.route((url) => url.pathname === "/api/chat", async (route) => {
    sent.push(route.request().postDataJSON().text);
    await route.fulfill({ json: { ok: true } });
  });
  await walk.open(project.path);
  await button(page, "Start voice input").click();
  await expect(field).toHaveValue("check the build", { timeout: 5000 });
  const outcomes = [
    { name: "01-waiting-while-listening", state: updating({ state: "waiting" }) },
    { name: "02-failure-while-listening", state: updating({ state: "failed", reason: "Download failed." }) },
    { name: "03-cancelled-while-listening", state: updating({ state: "cancelled" }) },
    { name: "04-completed-while-listening", state: READY },
  ];
  for (const outcome of outcomes) {
    host.state = outcome.state;
    await walk.state(outcome.name, {
      visible: [button(page, "Stop voice input"), button(page, "Cancel voice input"), main.locator('.composer[data-phase="listening"]')],
      hidden: [page.getByRole("link", { name: "View update", exact: true }), page.getByText("Download failed.", { exact: true })],
    });
  }
  await button(page, "Send").click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatch(/^Heard \d+ chunks\.$/);
  await walk.state("05-sent-clears-transcript", {
    visible: [button(page, "Start voice input")],
    hidden: [button(page, "Stop voice input"), button(page, "Cancel voice input"), main.locator(".composer-wave")],
  });
  await expect(field).toHaveValue("");
});

test("voice update handoff: new recording catches up after verification without a connection error", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  await fixtureHost(page, updating({ state: "checking" }));
  let checking = true;
  await page.route((url) => url.pathname === "/api/voice/live", async (route) => {
    if (checking) return route.fulfill({ status: 425, json: { error: "Voice is checking an update. Try again shortly." } });
    return route.fallback();
  });
  const walk = walkthrough(page, info);
  const field = page.getByRole("main").getByRole("textbox", { name: /^Message L3 about /, includeHidden: true });
  await walk.open(project.path);
  await button(page, "Start voice input").click();
  await walk.state("01-capture-waits-for-verification", {
    visible: [page.getByText("Catching up…", { exact: true }), button(page, "Stop voice input")],
    hidden: [page.getByText("Connection lost — still recording. Your words will catch up.", { exact: true })],
  });
  checking = false;
  await expect(field).toHaveValue("check the build", { timeout: 5000 });
  await button(page, "Stop voice input").click();
  await expect(field).toHaveValue(/^Heard \d+ chunks\.$/);
  await walk.state("02-retained-words-land", {
    visible: [button(page, "Start voice input")], hidden: [button(page, "Stop voice input")],
  });
});

test("voice update presentation: disconnected recording replays with the same selection after replacement", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const host = await fixtureHost(page, updating(downloading));
  const walk = walkthrough(page, info);
  const field = page.getByRole("main").getByRole("textbox", { name: /^Message L3 about /, includeHidden: true });
  await walk.open(project.path);
  await button(page, "Start voice input").click();
  await expect(field).toHaveValue("check the build", { timeout: 5000 });
  host.offline = true;
  const lost = page.getByText("Connection lost — still recording. Your words will catch up.", { exact: true });
  await walk.state("01-disconnected-during-update", {
    visible: [lost, button(page, "Stop voice input")], hidden: [],
  });
  host.state = READY;
  host.forget = true;
  let release = () => {};
  host.holdAudio = new Promise<void>((resolve) => { release = resolve; });
  try {
    host.offline = false;
    await walk.state("02-replaying-after-replacement", {
      visible: [page.getByText("Catching up…", { exact: true }), button(page, "Stop voice input")],
      hidden: [lost, page.getByText("Voice settings changed", { exact: false })],
    });
    expect(host.opened).toBe(2);
    release();
    host.holdAudio = null;
    await expect(page.getByRole("status", { name: "Listening… Stop to add text, or Send.", exact: true })).toBeVisible();
    await button(page, "Stop voice input").click();
    await expect(field).toHaveValue(/^Heard \d+ chunks\.$/);
    await walk.state("03-stop-lands-replayed-recording", {
      visible: [button(page, "Start voice input")],
      hidden: [button(page, "Stop voice input"), page.getByText("Catching up…", { exact: true })],
    });
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
});
