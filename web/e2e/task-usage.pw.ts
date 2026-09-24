import { test } from "./fixtures";
import { expect } from "@playwright/test";
import { fixtureProject, fixtureTask } from "./fixture-data";
import { walkthrough } from "./walkthrough";

// Real task/service data underlies each view. Only accounting observations and read failures are
// overlaid: the walkthrough never runs an agent or changes a task to generate consumption.
const usage = (over: Record<string, unknown> = {}) => ({
  status: "partial", total_tokens: 1_200, input_tokens: 1_000, output_tokens: 200,
  cache_read_tokens: 800, cache_write_tokens: null, reasoning_tokens: 50,
  checked_at: new Date().toISOString(), observed_at: new Date().toISOString(), finalized_at: null,
  notes: ["Helper coverage is unknown."],
  sessions: [
    { engine: "engine-a", session_id: "owner-record", attempt: 1, role: "owner", status: "observed", total_tokens: 1_000, input_tokens: 900, output_tokens: 100, notes: [] },
    { engine: "engine-a", session_id: "helper-record", parent_session_id: "owner-record", attempt: 1, role: "delegated", status: "observed", total_tokens: 200, input_tokens: 100, output_tokens: 100, notes: [] },
  ], ...over,
});

test("task consumption: unknown, live, expanded, updated, stale, missing source, collapsed", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const task = await fixtureTask(request, project.name);
  const path = `/projects/${project.name}/tasks/${task.slug}`;
  const walk = walkthrough(page, info);
  await page.clock.install();
  let observation: ReturnType<typeof usage> | undefined;
  await page.route((url) => url.pathname === `/api/task/${project.name}/${task.slug}`, (route) => route.fulfill({ json: { ...task, state: "running", token_usage: observation } }));
  const region = page.getByRole("region", { name: "Task token usage", exact: true });
  const toggle = region.getByRole("button");
  const details = region.locator(".token-details");

  await walk.open(path);
  await page.getByRole("button", { name: /Task details$/ }).click();
  await walk.state("01-unknown", { visible: [region.getByText("Token usage unknown"), region.getByText("Unknown coverage"), region.getByText("Freshness unknown")], hidden: [details] });
  observation = usage();
  await page.clock.fastForward(20_001);
  await walk.state("02-live-partial", { visible: [region.getByText("1,200 observed tokens", { exact: true }), region.getByText("Partial coverage"), region.getByText(/Checked/)], hidden: [details] });
  await walk.state("03-counters-and-coverage", {
    action: () => toggle.click(),
    visible: [details, details.getByText("Cache read · in input").first(), details.getByText("Cache write · in input").first(), details.getByText("Reasoning · in output").first()], hidden: [],
  });
  const parentage = details.getByText("Session helper-record · parent owner-record");
  await walk.state("03b-helper-parentage-scrolled", {
    action: async () => {
      await parentage.scrollIntoViewIfNeeded();
      await expect(parentage).toBeInViewport();
    },
    visible: [details.getByText(/Delegated · attempt 1/), parentage], hidden: [],
  });
  const preservedObservation = observation.observed_at;
  observation = usage({ total_tokens: 1_500, output_tokens: 500, observed_at: preservedObservation,
    sessions: observation.sessions.map((session) => session.role === "owner" ? { ...session, total_tokens: 1_300, output_tokens: 400 } : session),
  });
  await page.clock.fastForward(20_001);
  await walk.state("04-live-increase-keeps-details", { action: () => details.evaluate((node) => { node.scrollTop = 0; }), visible: [toggle.getByText("1,500 observed tokens", { exact: true }), details], hidden: [] });
  observation = usage({ ...observation, checked_at: new Date(Date.now() - 300_000).toISOString() });
  await page.clock.fastForward(20_001);
  await walk.state("05-stale-retains-total", { visible: [toggle.getByText("1,500 observed tokens", { exact: true }), region.getByText(/Stale · checked/)], hidden: [] });
  observation = usage({ ...observation, checked_at: new Date().toISOString(), notes: ["Source removed; retained observations are partial."] });
  await page.clock.fastForward(20_001);
  await walk.state("06-missing-source-retains-total", { visible: [toggle.getByText("1,500 observed tokens", { exact: true }), details.getByText("Source removed; retained observations are partial.")], hidden: [] });
  await walk.state("07-collapse", { action: () => toggle.click(), visible: [toggle.getByText("1,500 observed tokens", { exact: true })], hidden: [details] });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
});

test("final consumption remains in task and report with multiple engines and unsplit provider coverage", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const task = await fixtureTask(request, project.name);
  const path = `/projects/${project.name}/tasks/${task.slug}`;
  const walk = walkthrough(page, info);
  const final = usage({ total_tokens: 1_800, input_tokens: 1_500, output_tokens: 300, finalized_at: "2026-09-01T12:00:00Z", checked_at: "2026-09-01T12:00:00Z" });
  final.sessions.push({ engine: "engine-b", session_id: "provider-record", attempt: 2, role: "provider", status: "partial", total_tokens: 600, input_tokens: 500, output_tokens: 100, notes: [] });
  await page.route((url) => url.pathname === `/api/task/${project.name}/${task.slug}`, (route) => route.fulfill({ json: { ...task, state: "done", token_usage: final, report_json: { landed: {}, review: [] } } }));
  const region = page.getByRole("region", { name: "Task token usage", exact: true });
  const details = region.locator(".token-details");
  await walk.open(path);
  await page.getByRole("button", { name: /Task details$/ }).click();
  await walk.state("01-final-task", { visible: [region.getByText("1,800 observed tokens", { exact: true }), region.getByText(/Finalized/)], hidden: [region.getByText(/Stale/), details] });
  await walk.state("02-final-engine-details", {
    action: () => region.getByRole("button").click(),
    visible: [details, details.getByRole("heading", { name: "engine-a 1,200 observed tokens" })], hidden: [],
  });
  const provider = details.getByText(/Provider total · helpers unsplit · attempt 2/);
  await walk.state("02b-final-provider-scrolled", {
    action: async () => {
      await provider.scrollIntoViewIfNeeded();
      await expect(provider).toBeInViewport();
    },
    visible: [details.getByRole("heading", { name: "engine-b 600 observed tokens" }), provider], hidden: [],
  });
  await walk.open(`${path}/report`);
  await walk.state("03-retained-report", { visible: [page.getByRole("heading", { name: "Report", exact: true }), region.getByText("1,800 observed tokens", { exact: true }), region.getByText(/Finalized/)], hidden: [details] });
  await walk.state("04-report-details", { action: () => region.getByRole("button").click(), visible: [details, details.getByText("Partial totals sum available counters and are a lower bound.")], hidden: [] });
  await walk.state("04b-report-provider-scrolled", {
    action: async () => {
      await provider.scrollIntoViewIfNeeded();
      await expect(provider).toBeInViewport();
    },
    visible: [provider], hidden: [],
  });
  await walk.state("05-report-collapse", { action: () => region.getByRole("button").click(), visible: [region], hidden: [details] });
});

test("task usage waits for loading and recovers with the task read", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const task = await fixtureTask(request, project.name);
  const path = `/projects/${project.name}/tasks/${task.slug}`;
  const walk = walkthrough(page, info);
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => (release = resolve));
  let fail = false;
  await page.route((url) => url.pathname === `/api/task/${project.name}/${task.slug}`, async (route) => {
    await gate;
    await route.fulfill(fail ? { status: 500, json: { error: "unavailable" } } : { json: { ...task, token_usage: usage() } });
  });
  const region = page.getByRole("region", { name: "Task token usage", exact: true });
  await walk.open(path);
  await walk.state("01-loading", { visible: [page.getByRole("main").getByLabel("Loading", { exact: true })], hidden: [region] });
  release();
  await walk.state("02-loaded", {
    action: () => page.getByRole("button", { name: /Task details$/ }).click(),
    visible: [region], hidden: [page.getByRole("main").getByLabel("Loading", { exact: true })],
  });
  fail = true;
  await walk.open(path);
  const error = page.getByText("Could not load the task.");
  await walk.state("03-read-error", { action: () => error.waitFor({ timeout: 30_000 }), visible: [error, page.getByRole("button", { name: "Retry", exact: true })], hidden: [region] });
  fail = false;
  await walk.state("04-retry-restores-usage", { action: async () => {
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await page.getByRole("button", { name: /Task details$/ }).click();
  }, visible: [region, region.getByText("1,200 observed tokens", { exact: true })], hidden: [error] });
});
