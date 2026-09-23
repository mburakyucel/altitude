import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject, fixtureTask } from "./fixture-data";
import { walkthrough } from "./walkthrough";

// Explicit presentation/transport overlays. Backend transitions have separate deterministic evidence.
test("cross-engine review stays in task chat through request, result and failure states", async ({ page, request }, info) => {
  test.setTimeout(90_000);
  info.annotations.push({ type: "evidence boundary", description: "Review records and POST outcomes are presentation overlays; this walkthrough verifies app interactions, not backend review execution." });
  const project = await fixtureProject(request);
  const task = await fixtureTask(request, project.name);
  const snapshot = { head: "7b4a2f1", base: "43fce29", tree: "tree-for-pagination", context_hash: "brief-and-decisions-through-1026" };
  const review = { id: "review-pagination", requested_at: "2026-09-22T10:26:00Z", requested_by: "l2", state: "requested", engine_label: "Engine B", model: "Default", focus: "Check cursor expiry and pagination boundaries.", coverage: "unknown", can_cancel: false, can_withdraw: true, can_retry: false, can_review_latest: false,
    snapshot, reconciled: null as null | { head: string; base: string; tree: string; context_hash: string; reason: string }, error: null as null | string,
    result: null as null | { text: string; findings: { id: string; severity: string; title: string; body: string }[]; limitations: string[] }, dispositions: [] as { finding_id: string; disposition: string; reason: string }[] };
  let existing = false;
  let available = true;
  let denied = false;
  let lost = false;
  let saving = false;
  let posts = 0;
  let finishPost: (() => void) | undefined;
  const messages = [{ id: "intro", role: "l2", text: "The cursor validation is ready. I’m checking the expiration and empty-page cases.", at: "2026-09-22T10:24:00Z" }];
  await page.route((url) => url.pathname === `/api/task/${project.name}/${task.slug}`, (route) => route.fulfill({ json: {
    ...task, title: "Keep pagination stable", state: "running", question: null, questions: [], question_group: null, fault: null, blocked_reason: "", hold_merge: "Operator review of the finished feature.",
    messages: [...messages, ...(existing ? [{ id: "review-anchor", role: "system", text: "Review requested", at: review.requested_at, review_id: review.id }] : [])],
    review: { available, why: available ? "" : "A second engine is unavailable.", engine_label: "Engine B", model: "Default", allowance_known: false, latest: existing ? review : null, history: existing ? [review] : [] },
  } }));
  await page.route("**/api/task/review", async (route) => {
    posts++;
    if (denied) { await route.fulfill({ status: 403, json: { error: "denied" } }); return; }
    if (saving) await new Promise<void>((resolve) => { finishPost = resolve; });
    existing = true;
    const body = route.request().postDataJSON();
    if (["request", "retry", "rerun"].includes(body.action)) { review.state = "requested"; review.can_retry = false; review.can_review_latest = false; review.can_withdraw = true; }
    if (body.action === "cancel") { review.state = "cancelled"; review.can_cancel = false; review.can_retry = true; }
    if (body.action === "withdraw") { expect(body.reason).toBe("Proceed with the existing checks"); review.state = "withdrawn"; review.can_withdraw = false; review.can_retry = false; }
    if (lost) { await route.abort("failed"); return; }
    await route.fulfill({ json: { ok: true, review } });
  });
  const walk = walkthrough(page, info);
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const menu = page.getByRole("dialog", { name: "Task details" });
  const openMenu = () => page.getByRole("button", { name: "Task details", exact: true }).click();
  const row = page.locator(`[data-review-id="${review.id}"]`);
  const details = row.locator("summary");
  const poll = async () => { await expect.poll(async () => page.locator(".task-review-status").textContent()).toContain(review.state === "requested" ? "waiting for L2" : review.state === "running" ? "is reviewing" : review.state === "completed" ? "review complete" : review.state === "cancelled" ? "cancelled" : review.state === "withdrawn" ? "withdrawn" : "failed"); };
  await walk.open(`/projects/${project.name}/tasks/${task.slug}`);
  if (info.project.name === "desktop") await page.getByRole("button", { name: "Live session", exact: true }).click();
  await field.fill("Keep the existing pagination contract.");
  await walk.state("01-empty-chat", { visible: [field], hidden: [row] });
  await openMenu();
  await walk.state("02-request-in-details", { visible: [menu.getByRole("button", { name: "Request cross-engine review" }), menu.getByText(/remaining allowance is unknown/)], hidden: [] });
  saving = true;
  await menu.getByRole("button", { name: "Request cross-engine review" }).click();
  await expect.poll(() => posts).toBe(1);
  await expect(menu.getByRole("button", { name: "Request cross-engine review" })).toBeDisabled();
  await walk.state("03-saving", { visible: [menu.getByText("Saving review request…")], hidden: [row] });
  finishPost!(); saving = false;
  await expect(menu).toBeHidden();
  await walk.state("04-requested", { visible: [row, field], hidden: [menu] });
  await expect(field).toHaveValue("Keep the existing pagination contract.");
  review.state = "running"; review.can_cancel = true;
  await poll();
  await walk.state("05-running", { visible: [row.getByText("Engine B is reviewing")], hidden: [row.getByRole("button", { name: "Cancel review" })] });
  await details.click();
  await walk.state("06-running-details", { visible: [row.getByRole("button", { name: "Cancel review" }), row.getByText(/Reviewed head:/)], hidden: [] });
  await row.getByRole("button", { name: "Cancel review" }).click(); await poll();
  await walk.state("07-cancelled", { visible: [row.getByText("Review cancelled · request still needs a decision"), row.getByRole("button", { name: "Retry review" })], hidden: [row.getByRole("button", { name: "Cancel review" })] });
  await row.getByRole("button", { name: "Retry review" }).click(); await poll();
  review.state = "failed"; review.can_retry = true; review.error = "The review engine exited before returning findings.";
  await poll();
  await walk.state("08-failed", { visible: [row.getByText(review.error), row.getByRole("button", { name: "Retry review" })], hidden: [] });
  await row.getByRole("button", { name: "Skip review…" }).click();
  await row.getByRole("textbox", { name: "Reason for skipping review" }).fill("Proceed with the existing checks");
  await walk.state("09-skip-confirmation", { visible: [row.getByRole("button", { name: "Skip review", exact: true })], hidden: [] });
  await row.getByRole("button", { name: "Skip review", exact: true }).click(); await poll();
  await walk.state("10-withdrawn", { visible: [row.getByText("Review request withdrawn")], hidden: [row.getByRole("button", { name: "Retry review" }), row.getByRole("button", { name: "Skip review", exact: true })] });
  review.state = "completed"; review.error = null; review.coverage = "earlier"; review.can_review_latest = true;
  review.result = { text: "Two pagination findings.", findings: [{ id: "expiry", severity: "high", title: "Expired cursors restart at page one", body: "Return the agreed expiration error to avoid duplicate results." }, { id: "empty", severity: "low", title: "Empty pages may lose the cursor", body: "Confirm the storage contract." }], limitations: ["The reviewer did not run project tests."] };
  review.dispositions = [{ finding_id: "expiry", disposition: "fixed", reason: "Added the expiration response and regression test." }, { finding_id: "empty", disposition: "dismissed", reason: "The agreed storage contract ends pagination on an empty page." }];
  await poll();
  await details.click();
  await walk.state("11-earlier-coverage-collapsed", { visible: [row.getByText(/Work changed after review/)], hidden: [row.getByText(/high · Expired/)] });
  review.coverage = "assessed"; review.reconciled = { ...snapshot, head: "91a83b2", reason: "I checked the fix and the added regression test against the final candidate." }; review.can_review_latest = false;
  await expect(row.getByText("Reviewed an earlier revision; L2 assessed the later edits.")).toBeVisible();
  await details.click();
  await walk.state("12-findings-and-l2-dispositions", { visible: [row.getByText(/high · Expired/), row.getByText(/Added the expiration response/), row.getByText(/The agreed storage contract/), row.getByText(/L2 assessed head:/)], hidden: [row.getByRole("button", { name: "Review latest" })] });
  await details.click(); await openMenu();
  await walk.state("13-already-reviewed", { visible: [menu.getByRole("button", { name: "View review" })], hidden: [menu.getByRole("button", { name: "Request cross-engine review" })] });
  await page.keyboard.press("Escape");
  await expect(field).toHaveValue("Keep the existing pagination contract.");
  existing = false; available = false;
  await expect(row).toBeHidden(); await openMenu();
  await walk.state("14-unavailable", { visible: [menu.getByText("A second engine is unavailable.")], hidden: [row] });
  await expect(menu.getByRole("button", { name: "Request cross-engine review" })).toBeDisabled();
  available = true; denied = true;
  await expect(menu.getByRole("button", { name: "Request cross-engine review" })).toBeEnabled();
  await menu.getByRole("button", { name: "Request cross-engine review" }).click();
  await walk.state("15-denied", { visible: [menu.getByText("You do not have permission to request or change this review.")], hidden: [row] });
  denied = false; lost = true;
  await menu.getByRole("button", { name: "Request cross-engine review" }).click();
  await expect(menu.getByRole("button", { name: "View review" })).toBeVisible();
  await walk.state("16-lost-receipt-reconciled", { visible: [menu.getByRole("button", { name: "View review" })], hidden: [menu.getByRole("button", { name: "Request cross-engine review" })] });
  await page.keyboard.press("Escape");
  await expect(field).toHaveValue("Keep the existing pagination contract.");
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
    review: { available: true, why: "", engine_label: "Engine B", model: "Default", allowance_known: true, latest: null, history: [] } };
  await page.route((url) => url.pathname === `/api/task/${project.name}/${task.slug}`, async (route) => {
    if (readGate) await readGate;
    await route.fulfill({ json: record });
  });
  await page.route("**/api/task/review", async (route) => {
    readGate = new Promise((resolve) => { releaseRead = resolve; });
    await route.abort("failed");
  });
  await page.route("**/api/transcribe", (route) => route.fulfill({ json: { text: "Please check expiry." } }));
  const walk = walkthrough(page, info);
  const field = page.getByRole("textbox", { name: "Message the L2", exact: true });
  const menu = page.getByRole("dialog", { name: "Task details" });
  await walk.open(`/projects/${project.name}/tasks/${task.slug}`);
  await walk.state("01-loading-task", { visible: [page.getByLabel("Loading", { exact: true })], hidden: [field] });
  releaseRead(); readGate = null;
  await field.fill("Keep my draft.");
  await page.getByRole("button", { name: "Task details", exact: true }).click();
  await menu.getByRole("button", { name: "Request cross-engine review" }).click();
  await walk.state("02-uncertain-checking", { visible: [menu.getByText(/Review request unconfirmed/)], hidden: [page.locator(".task-review-row")] });
  await expect(menu.getByRole("button", { name: "Request cross-engine review" })).toBeDisabled();
  releaseRead(); readGate = null;
  await expect(menu.getByRole("button", { name: "Request cross-engine review" })).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(field).toHaveValue("Keep my draft.");
  await page.getByRole("button", { name: "Start voice input", exact: true }).click();
  await expect(page.locator('.composer[data-phase="listening"]')).toBeVisible();
  await page.getByRole("button", { name: "Task details", exact: true }).click();
  await walk.state("03-listening-with-details", { visible: [menu, page.getByRole("button", { name: "Stop voice input", exact: true })], hidden: [page.locator(".task-review-row")] });
  await page.getByRole("button", { name: "Close task details", exact: true }).click();
  await expect(page.locator('.composer[data-phase="listening"]')).toBeVisible();
  await page.waitForTimeout(700);
  await page.getByRole("button", { name: "Stop voice input", exact: true }).click();
  await expect(field).toHaveValue("Keep my draft. Please check expiry.");
  await walk.state("04-transcription-in-draft", { visible: [field, page.getByRole("button", { name: "Start voice input", exact: true })], hidden: [page.getByRole("button", { name: "Stop voice input", exact: true }), page.getByText("Transcribing…", { exact: true })] });
});
