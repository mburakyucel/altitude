import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject, fixtureTask } from "./fixture-data";
import { fixtureHost } from "./hostVoice";
import { walkthrough } from "./walkthrough";

type Finding = { id: string; severity: string; title: string; body: string };
type Snapshot = { head: string; base: string; tree: string; context_hash: string };
const snapshot: Snapshot = { head: "7b4a2f1", base: "43fce29", tree: "tree-for-pagination", context_hash: "brief-and-decisions-through-1026" };
const findings: Finding[] = [
  { id: "expiry", severity: "high", title: "Expired cursors restart at page one", body: "Return the agreed expiration error to avoid duplicate results." },
  { id: "empty", severity: "low", title: "Empty pages may lose the cursor", body: "Confirm the storage contract." },
];
const verdict = "Expired cursors silently restart pagination instead of failing explicitly.";

// Explicit presentation/transport overlays. Backend transitions have separate deterministic evidence.
test("adversarial review walks every state in task details and the conversation", async ({ page, request }, info) => {
  test.setTimeout(120_000);
  info.annotations.push({ type: "evidence boundary", description: "Review records and POST outcomes are presentation overlays; this walkthrough verifies app interactions, not backend review execution." });
  const project = await fixtureProject(request);
  const task = await fixtureTask(request, project.name);
  const review = {
    id: "review-pagination", subject: "changes", requested_at: "2026-09-22T10:26:00Z", requested_by: "operator", state: "requested",
    engine_label: "Engine A", model: "Default", same_engine: true, fallback_reason: "", focus: "Check cursor expiry and pagination boundaries.",
    coverage: "unknown", earlier: false, waiting: "owner" as string | null, cancel_requested: false, withdrawn_by: null as string | null,
    can_cancel: false, can_withdraw: true, can_again: false, snapshot: snapshot as Snapshot | null, reconciled: null as null | (Snapshot & { reason: string }),
    error: null as null | string, result: null as null | { text: string; findings: Finding[]; limitations: string[] },
    dispositions: [] as { finding_id: string; disposition: string; reason: string }[], unresolved: [] as string[],
  };
  let existing = false;
  let why = "";
  let denied = false;
  let lost = false;
  let saving = false;
  const posts: Record<string, unknown>[] = [];
  let finishPost: (() => void) | undefined;
  const messages = [{ id: "intro", role: "l2", text: "The cursor validation is ready. I’m checking the expiration and empty-page cases.", at: "2026-09-22T10:24:00Z" }];
  await page.route((url) => url.pathname === `/api/task/${project.name}/${task.slug}`, (route) => route.fulfill({ json: {
    ...task, title: "Keep pagination stable", state: "running", question: null, questions: [], question_group: null, fault: null, blocked_reason: "", hold_merge: "Operator review of the finished feature.",
    messages: [...messages, ...(existing ? [{ id: "review-anchor", role: "system", text: "Review requested", at: review.requested_at, review_id: review.id }] : [])],
    review: { history: existing ? [review] : [], subjects: {
      proposal: { available: !why, why, latest: null },
      changes: { available: existing ? review.can_again : !why, why, latest: existing ? review : null } } },
  } }));
  await page.route("**/api/task/review", async (route) => {
    const body = route.request().postDataJSON();
    posts.push(body);
    if (denied) { await route.fulfill({ status: 403, json: { error: "denied" } }); return; }
    if (saving) await new Promise<void>((resolve) => { finishPost = resolve; });
    existing = true;
    if (body.action === "request") Object.assign(review, { state: "requested", requested_by: "operator", waiting: "owner", can_again: false, can_withdraw: true, error: null });
    if (body.action === "cancel") Object.assign(review, { state: "cancelled", can_cancel: false, can_again: true });
    if (body.action === "withdraw") { expect(body).not.toHaveProperty("reason"); Object.assign(review, { state: "withdrawn", withdrawn_by: "operator", can_withdraw: false, can_again: true }); }
    if (lost) { await route.abort("failed"); return; }
    await route.fulfill({ json: { ok: true, review } });
  });
  const walk = walkthrough(page, info);
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const menu = page.getByRole("dialog", { name: "Task details" });
  const box = (name: string) => menu.getByRole("region", { name });
  const implementation = box("Implementation review");
  const openMenu = async () => { await page.getByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }).click(); await expect(menu).toBeVisible(); };
  const closeMenu = async () => { await page.keyboard.press("Escape"); await expect(menu).toBeHidden(); };
  const card = page.locator(`[data-review-id="${review.id}"]`);
  const toggle = card.getByRole("button", { name: /implementation review details$/ });
  await walk.open(`/projects/${project.name}/tasks/${task.slug}`);
  if (info.project.name === "desktop") await page.getByRole("button", { name: "Live session", exact: true }).click();
  await field.fill("Keep the existing pagination contract.");

  await openMenu();
  await walk.state("01-no-review-yet", { visible: [box("Proposal review").getByText("Not reviewed yet"), implementation.getByRole("button", { name: "Request" })], hidden: [card] });
  // Affordance (SPEC §1.1): the request buttons carry a visible border at rest.
  expect(await implementation.getByRole("button", { name: "Request" }).evaluate((el) => getComputedStyle(el).borderColor)).not.toBe("rgba(0, 0, 0, 0)");
  saving = true;
  await implementation.getByRole("button", { name: "Request" }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({ action: "request", subject: "changes" });
  await expect(implementation.getByRole("button", { name: "Request" })).toBeDisabled();
  await walk.state("02-saving", { visible: [menu.getByText("Saving review request…")], hidden: [card] });
  finishPost!(); saving = false;
  await expect(menu).toBeHidden();
  await walk.state("03-queued-by-you", { visible: [card.getByText("· requested by you"), card.getByText("Queued. L2 starts it after its current step.")], hidden: [menu] });
  await expect(field).toHaveValue("Keep the existing pagination contract.");
  review.waiting = "resume";
  await walk.state("04-queued-until-resume", { visible: [card.getByText("Queued. L2 starts it when it resumes.")], hidden: [card.getByText("Queued. L2 starts it after its current step.")] });
  review.waiting = "reviewer";
  await walk.state("05-waiting-for-reviewer", { visible: [card.getByText("Waiting for the reviewer: another review is running on this machine.")], hidden: [card.getByText("Queued. L2 starts it when it resumes.")] });
  review.requested_by = "l2"; review.waiting = "owner";
  await walk.state("06-requested-by-l2", { visible: [card.getByText("· requested by L2"), card.getByText("L2 asked for a review of its implementation and starts it shortly.")], hidden: [card.getByText("· requested by you")] });
  await openMenu();
  await walk.state("07-details-while-queued", { visible: [implementation.getByText("· requested by L2"), implementation.getByRole("button", { name: "View", exact: true })], hidden: [implementation.getByRole("button", { name: "Request" })] });
  await closeMenu();

  Object.assign(review, { state: "running", waiting: null, can_cancel: true, can_withdraw: false });
  await walk.state("08-in-progress", { visible: [card.getByText("· in progress"), card.getByText("Reviewing the implementation…"), card.getByRole("button", { name: "Stop" })], hidden: [card.getByText("· requested by L2")] });
  await card.getByRole("button", { name: "Stop" }).click();
  await expect.poll(() => posts.length).toBe(2);
  expect(posts[1]).toMatchObject({ action: "cancel", review_id: review.id });
  await walk.state("09-stopped", { visible: [card.getByText("· didn’t finish"), card.getByText("Stopped before it finished."), card.getByRole("button", { name: "Try again" })], hidden: [card.getByRole("button", { name: "Stop" })] });
  Object.assign(review, { state: "failed", error: "The review engine exited before returning findings." });
  await walk.state("10-failed", { visible: [card.getByText("The review engine exited before returning findings."), card.getByRole("button", { name: "Try again" })], hidden: [card.getByText("Stopped before it finished.")] });
  await card.getByRole("button", { name: "Try again" }).click();
  await expect.poll(() => posts.length).toBe(3);
  expect(posts[2]).toMatchObject({ action: "request", review_id: review.id, subject: "changes" });
  await expect(card.getByText("· requested by you")).toBeVisible();

  Object.assign(review, { state: "completed", waiting: null, can_withdraw: true, coverage: "current",
    result: { text: verdict, findings, limitations: ["The reviewer did not run project tests."] } });
  await walk.state("11-done-l2-responding", { visible: [card.getByText(verdict), card.getByText("L2 is responding")], hidden: [card.getByText("· requested by you")] });
  Object.assign(review, { coverage: "assessed", reconciled: { ...snapshot, head: "91a83b2", reason: "I checked the fix against the final candidate." }, unresolved: ["expiry"],
    dispositions: [{ finding_id: "expiry", disposition: "open", reason: "The expiration contract needs the storage owner's decision." }, { finding_id: "empty", disposition: "dismissed", reason: "The agreed storage contract ends pagination on an empty page." }] });
  await walk.state("12-open-findings", { visible: [card.getByText("1 open, blocks merge")], hidden: [card.getByText("L2 is responding"), card.getByText("Expired cursors restart at page one")] });
  await openMenu();
  await walk.state("13-details-open-findings", { visible: [implementation.getByText(verdict), implementation.getByText("1 open, blocks merge")], hidden: [implementation.getByRole("button", { name: "Review again" })] });
  await implementation.getByRole("button", { name: "View", exact: true }).click();
  await expect(menu).toBeHidden();
  await expect(card).toBeFocused();
  await walk.state("14-opened-findings", { visible: [card.getByText("Expired cursors restart at page one"), card.getByText("Open", { exact: true }), card.getByText(/needs the storage owner/), card.getByText("Engine A · same engine as the task"), card.getByRole("button", { name: "Skip review" })], hidden: [card.getByText(/Reviewed head/)] });
  await card.getByRole("button", { name: "Technical details" }).click();
  await walk.state("15-technical-details", { visible: [card.getByText(/Reviewed head 7b4a2f1/), card.getByText(/L2 assessed head 91a83b2/)], hidden: [] });
  await toggle.click();
  Object.assign(review, { unresolved: [], can_again: true, dispositions: [{ finding_id: "expiry", disposition: "fixed", reason: "Added the expiration response and regression test." }, review.dispositions[1]!] });
  await walk.state("16-cleared", { visible: [card.getByText("2 findings, both resolved")], hidden: [card.getByText("1 open, blocks merge"), card.getByText("Expired cursors restart at page one")] });
  review.earlier = true;
  await walk.state("17-earlier-version", { visible: [card.getByText(/earlier version/)], hidden: [] });
  await openMenu();
  await walk.state("18-details-review-again", { visible: [implementation.getByRole("button", { name: "Review again" }), implementation.getByText(/earlier version/)], hidden: [implementation.getByRole("button", { name: "Request" })] });
  await closeMenu();

  await toggle.click();
  await card.getByRole("button", { name: "Skip review" }).click();
  await walk.state("19-skip-confirmation", { visible: [card.getByText(/no longer blocks merging/), card.getByRole("group", { name: "Skip review" })], hidden: [card.getByRole("textbox")] });
  await card.getByRole("group", { name: "Skip review" }).getByRole("button", { name: "Skip review" }).click();
  await expect.poll(() => posts.length).toBe(4);
  expect(posts[3]).toEqual({ project: project.name, slug: task.slug, action: "withdraw", review_id: review.id });
  await walk.state("20-skipped", { visible: [card.getByText("· skipped"), card.getByText("Skipped by you")], hidden: [card.getByRole("group", { name: "Skip review" })] });
  await expect(field).toHaveValue("Keep the existing pagination contract.");

  existing = false; why = "No reviewer is available.";
  await expect(card).toBeHidden();
  await openMenu();
  await walk.state("21-unavailable", { visible: [implementation.getByText("No reviewer is available.")], hidden: [implementation.getByRole("button", { name: "Request" }), box("Proposal review").getByRole("button")] });
  why = ""; denied = true;
  await implementation.getByRole("button", { name: "Request" }).click();
  await walk.state("22-denied", { visible: [menu.getByText("You do not have permission to request or change this review.")], hidden: [card] });
  denied = false; lost = true;
  Object.assign(review, { earlier: false });
  await implementation.getByRole("button", { name: "Request" }).click();
  await expect(implementation.getByRole("button", { name: "View", exact: true })).toBeVisible();
  await walk.state("23-lost-receipt-reconciled", { visible: [implementation.getByText("· requested by you")], hidden: [implementation.getByRole("button", { name: "Request" })] });
  await closeMenu();
  await expect(field).toHaveValue("Keep the existing pagination contract.");
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});

test("only a kind's latest review shows, with earlier iterations inside it", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const task = await fixtureTask(request, project.name);
  const base = { subject: "proposal", requested_by: "l2", state: "completed", engine_label: "Engine B", model: "Default", same_engine: false, fallback_reason: "", focus: "",
    coverage: "assessed", earlier: false, can_cancel: false, unresolved: [], snapshot: { ...snapshot, proposal: { id: "plan", at: "2026-09-22T10:20:00Z", text: "Filter deleted records before applying the page limit." } } };
  const first = { ...base, id: "first", requested_at: "2026-09-22T10:22:00Z", can_withdraw: false, can_again: false, reconciled: { ...snapshot, reason: "Revised." },
    result: { text: "The plan drops the cursor when the last page is empty.", findings: [findings[1]!] }, dispositions: [{ finding_id: "empty", disposition: "fixed", reason: "The revised plan keeps it." }] };
  const second = { ...base, id: "second", previous: "first", requested_at: "2026-09-22T10:40:00Z", can_withdraw: true, can_again: false, reconciled: { ...snapshot, reason: "Open." },
    result: { text: "Expired cursors restart pagination silently.", findings: [findings[0]!] }, unresolved: ["expiry"],
    dispositions: [{ finding_id: "expiry", disposition: "open", reason: "Needs the storage owner's decision." }] };
  // An additional review replaces none: the second review's open finding stays in the merge gate.
  const third = { ...base, id: "third", additional: true, requested_at: "2026-09-22T10:50:00Z", can_withdraw: true, can_again: false, reconciled: { ...snapshot, reason: "No findings." },
    result: { text: "Sound plan; no material problems remain.", findings: [] }, dispositions: [] };
  await page.route((url) => url.pathname === `/api/task/${project.name}/${task.slug}`, (route) => route.fulfill({ json: {
    ...task, title: "Keep pagination stable", state: "running", question: null, questions: [], question_group: null,
    messages: [{ id: "plan", role: "l2", text: "Filter deleted records before applying the page limit.", at: "2026-09-22T10:20:00Z" },
      { id: "a1", role: "system", text: "Review requested", at: first.requested_at, review_id: "first" },
      { id: "a2", role: "system", text: "Review requested", at: second.requested_at, review_id: "second" },
      { id: "a3", role: "system", text: "Review requested", at: third.requested_at, review_id: "third" }],
    review: { history: [first, second, third], subjects: { proposal: { available: false, why: "", latest: third, open: 1 }, changes: { available: true, why: "", latest: null, open: 0 } } },
  } }));
  const walk = walkthrough(page, info);
  await walk.open(`/projects/${project.name}/tasks/${task.slug}`);
  const card = page.locator('[data-review-id="third"]');
  await walk.state("01-latest-iteration-only", { visible: [card.getByText("Sound plan; no material problems remain."), card.getByText(/Review 3/), card.getByText("1 open in an earlier review, blocks merge")], hidden: [page.locator('[data-review-id="first"]'), page.locator('[data-review-id="second"]')] });
  await card.getByRole("button", { name: "Show proposal review details" }).click();
  const earlier = card.getByRole("button", { name: /^Review 2/ });
  await walk.state("02-earlier-iterations-inside", { visible: [card.getByText("Earlier reviews"), card.getByText(/The plan drops the cursor/), earlier.getByText("1 open, blocks merge"), card.getByText("Engine B · alternate engine").last()], hidden: [card.getByText("Needs the storage owner's decision.")] });
  await earlier.click();
  const entry = card.locator(".review-earlier-entry").nth(1);
  await walk.state("03-earlier-review-opened", { visible: [entry.getByText("Expired cursors restart at page one"), entry.getByText("Needs the storage owner's decision."), entry.getByRole("button", { name: "Skip review" })], hidden: [] });
  await page.getByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }).click();
  const box = page.getByRole("dialog", { name: "Task details" }).getByRole("region", { name: "Proposal review" });
  await walk.state("04-details-earlier-open", { visible: [box.getByText("1 open in an earlier review, blocks merge")], hidden: [box.getByRole("button", { name: "Review again" })] });
  await page.keyboard.press("Escape");
  await card.getByRole("button", { name: "Technical details" }).last().click();
  await expect(card.getByText("Filter deleted records before applying the page limit.").last()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});

test("review loading and uncertain receipt preserve listening and draft", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const task = await fixtureTask(request, project.name);
  await page.addInitScript("AudioContext.prototype.resume = () => new Promise(() => {});");
  let releaseRead!: () => void;
  let readGate: Promise<void> | null = new Promise((resolve) => { releaseRead = resolve; });
  const record = { ...task, title: "Keep pagination stable", state: "running", question: null, questions: [], question_group: null,
    messages: [{ id: "intro", role: "l2", text: "Checking pagination." }],
    review: { subjects: { proposal: { available: true, why: "", latest: null }, changes: { available: true, why: "", latest: null } }, history: [] } };
  await page.route((url) => url.pathname === `/api/task/${project.name}/${task.slug}`, async (route) => {
    if (readGate) await readGate;
    await route.fulfill({ json: record });
  });
  await page.route("**/api/task/review", async (route) => {
    readGate = new Promise((resolve) => { releaseRead = resolve; });
    await route.abort("failed");
  });
  const host = await fixtureHost(page);
  host.final = "Please check expiry.";
  const walk = walkthrough(page, info);
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const menu = page.getByRole("dialog", { name: "Task details" });
  const request_ = menu.getByRole("region", { name: "Implementation review" }).getByRole("button", { name: "Request" });
  await walk.open(`/projects/${project.name}/tasks/${task.slug}`);
  await walk.state("01-loading-task", { visible: [page.getByLabel("Loading", { exact: true })], hidden: [field] });
  releaseRead(); readGate = null;
  await field.fill("Keep my draft.");
  await page.getByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }).click();
  await request_.click();
  await walk.state("02-uncertain-checking", { visible: [menu.getByText(/Review request unconfirmed/)], hidden: [page.locator(".review-card")] });
  await expect(request_).toBeDisabled();
  releaseRead(); readGate = null;
  await expect(request_).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(field).toHaveValue("Keep my draft.");
  await page.getByRole("button", { name: "Start voice input", exact: true }).click();
  await expect(page.locator('.composer[data-phase="listening"]')).toBeVisible();
  await page.getByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }).click();
  await walk.state("03-listening-with-details", { visible: [menu, page.getByRole("button", { name: "Stop voice input", exact: true })], hidden: [page.locator(".review-card")] });
  await page.getByRole("button", { name: "Close task details", exact: true }).click();
  await expect(page.locator('.composer[data-phase="listening"]')).toBeVisible();
  await expect(field).toHaveValue(/^Keep my draft\. check/, { timeout: 5000 });
  await page.getByRole("button", { name: "Stop voice input", exact: true }).click();
  await expect(field).toHaveValue("Keep my draft. Please check expiry.");
  await walk.state("04-transcription-in-draft", { visible: [field, page.getByRole("button", { name: "Start voice input", exact: true })], hidden: [page.getByRole("button", { name: "Stop voice input", exact: true }), page.getByText("Transcribing…", { exact: true })] });
});
