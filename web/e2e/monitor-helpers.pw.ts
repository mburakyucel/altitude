import { test } from "./fixtures";
import { expect } from "@playwright/test";
import { walkthrough } from "./walkthrough";

// Presentation overlays supply passive native evidence; real engine accounting and archive
// transitions are exercised in the Python suite. Nothing here launches a provider or worker.
type Json = Record<string, unknown>;
const parent = "owner-session-with-a-long-native-identity-11111111111111111111111111111111";
const child = "direct-helper-with-a-long-native-identity-22222222222222222222222222222222";
const helper = (over: Json = {}) => ({
  engine: "fixture-engine", session_id: child, parent_session_id: parent, owner_session_id: parent,
  role: "delegated", parentage: "thread", depth: 1, attempts: [1, 2], attempt: 1, status: "observed",
  total_tokens: 200, input_tokens: 150, output_tokens: 50, cache_read_tokens: 100, reasoning_tokens: 10,
  notes: [], ...over,
});
const usage = (helpers: Json[], over: Json = {}) => ({
  status: "partial", total_tokens: 1_300, input_tokens: 1_100, output_tokens: 200,
  checked_at: new Date().toISOString(), observed_at: new Date().toISOString(), notes: [],
  sessions: [{ engine: "fixture-engine", session_id: parent, role: "owner", attempt: 1, status: "observed", total_tokens: 1_000, input_tokens: 900, output_tokens: 100 }, ...helpers],
  helpers: { status: "partial", observed_count: helpers.length, direct_count: 1, descendant_count: 1, unclassified_count: 0, total_tokens: 300, sessions: helpers },
  ...over,
});

test("expanded L2 monitoring: available, partial, unknown, empty, retained error and collapsed helper evidence", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const baseline = await (await request.get("/api/monitor")).json();
  const l2 = baseline.sessions.find((row: Json) => row.kind === "l2");
  expect(l2, "The real fixture includes an L2 task").toBeTruthy();
  const available = [helper(), helper({ session_id: "descendant-helper", parent_session_id: child, depth: 2, total_tokens: 100, input_tokens: 50, output_tokens: 50, cache_read_tokens: null, reasoning_tokens: null })];
  let observation: Json | undefined = usage(available);
  await page.clock.install();
  await page.route("**/api/monitor*", (route) => route.fulfill({ json: {
    ...baseline, sessions: [{ ...l2, token_usage: observation }, ...baseline.sessions.filter((row: Json) => row.kind !== "l2")],
  } }));
  const toggle = page.getByRole("button", { name: "L2 usage details", exact: true });
  const details = page.locator(".token-details");
  const helpers = page.getByRole("region", { name: "L1 helpers observed", exact: true });
  const direct = page.getByRole("region", { name: `Helper session ${child}`, exact: true });
  const descendant = page.getByRole("region", { name: "Helper session descendant-helper", exact: true });
  const noEvidence = page.getByText("Helper evidence unavailable.", { exact: true });

  await walk.open("/monitor");
  await walk.state("01-collapsed", { visible: [toggle, page.getByText(`${l2.project} / ${l2.slug}`, { exact: true })], hidden: [details, helpers, page.getByText("1,300 tokens processed", { exact: true })] });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await walk.state("02-expanded-available", {
    action: async () => {
      await toggle.click();
      await helpers.getByRole("heading", { name: "L1 helpers observed", exact: true }).evaluate((node) => node.scrollIntoView({ block: "start" }));
    },
    visible: [helpers, direct.getByText(/Direct helper · 200 tokens/), descendant.getByText(/Descendant helper · depth 2 · 100 tokens/), helpers.getByText("Attributable helper tokens", { exact: true }), direct.getByText("Owner attempt context: 1, 2.", { exact: true })],
    hidden: [noEvidence, page.getByText("No helpers observed.", { exact: true })],
  });
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(helpers.locator("dt", { hasText: /^Unique helpers$/ }).locator("+ dd")).toHaveText("2");
  await expect(helpers.locator("dt", { hasText: /^Attributable helper tokens$/ }).locator("+ dd")).toHaveText("300");
  const layout = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    clipped: [...document.querySelectorAll(".token-session-id, .token-session-label")].filter((el) => el.scrollWidth > el.clientWidth + 1).length,
  }));
  expect(layout.overflow, "expanded helper records fit the viewport").toBeLessThanOrEqual(0);
  expect(layout.clipped, "native identities and parentage wrap").toBe(0);
  await walk.state("02b-direct-helper-audit-scrolled", {
    action: async () => {
      await direct.evaluate((node) => node.scrollIntoView({ block: "start" }));
      await expect(direct.getByText(`Parent ${parent} · L2 owner ${parent}`, { exact: true })).toBeInViewport();
    },
    visible: [direct.getByText(`Session ${child}`, { exact: true }), direct.getByText("Owner attempt context: 1, 2.", { exact: true })], hidden: [],
  });
  await walk.state("02c-descendant-audit-scrolled", {
    action: async () => {
      await descendant.evaluate((node) => node.scrollIntoView({ block: "start" }));
      await expect(descendant.getByText(`Parent ${child} · L2 owner ${parent}`, { exact: true })).toBeInViewport();
    },
    visible: [descendant.getByText(/Descendant helper · depth 2 · 100 tokens/)], hidden: [],
  });

  const unavailable = helper({ session_id: "owner-linked-helper", parentage: "owner", depth: null, total_tokens: null, input_tokens: null, output_tokens: null, cache_read_tokens: null, reasoning_tokens: null, status: "unknown", provider_total_tokens: 600 });
  observation = usage([...available, unavailable], {
    total_tokens: 1_900,
    sessions: [...usage(available).sessions, { ...unavailable, role: "provider", total_tokens: 600 }],
    helpers: { status: "partial", observed_count: 3, direct_count: 1, descendant_count: 1, unclassified_count: 1, total_tokens: 300, sessions: [...available, unavailable] },
  });
  await walk.state("03-partial-unavailable-counters", {
    action: () => page.clock.fastForward(20_001),
    visible: [helpers.getByText("1 owner-linked · depth unknown.", { exact: true }), helpers.getByText(/Owner-linked helper · depth unknown · tokens unknown/), helpers.getByText("Unsplit provider total: 600 tokens · may include descendants; not attributable to this helper alone.", { exact: true })], hidden: [noEvidence],
  });
  await expect(helpers.locator("dt", { hasText: /^Unique helpers$/ }).locator("+ dd")).toHaveText("3");
  await expect(helpers.locator("dt", { hasText: /^Attributable helper tokens$/ }).locator("+ dd")).toHaveText("300");
  const ownerLinked = page.getByRole("region", { name: "Helper session owner-linked-helper", exact: true });
  await walk.state("03b-owner-link-unsplit-audit-scrolled", {
    action: async () => {
      await ownerLinked.evaluate((node) => node.scrollIntoView({ block: "start" }));
      await expect(ownerLinked.getByText(/Unsplit provider total:/)).toBeInViewport();
    },
    visible: [ownerLinked.getByText(`Owner linkage ${parent} · L2 owner ${parent}`, { exact: true }), ownerLinked.getByText("Owner attempt context: 1, 2.", { exact: true })], hidden: [],
  });

  observation = { ...observation, notes: ["Usage collection unavailable; prior observations retained."] };
  await walk.state("04-collector-error-retains-evidence", {
    action: async () => {
      await page.clock.fastForward(20_001);
      await details.getByText("Usage collection unavailable; prior observations retained.", { exact: true }).scrollIntoViewIfNeeded();
    },
    visible: [helpers, direct, details.locator(".token-readout").getByText("1,900 tokens processed", { exact: true }), details.getByText("Usage collection unavailable; prior observations retained.", { exact: true })], hidden: [noEvidence],
  });
  observation = usage([], { helpers: { status: "partial", observed_count: 0, direct_count: 0, descendant_count: 0, unclassified_count: 0, total_tokens: null, sessions: [] } });
  await walk.state("05-observed-empty", {
    action: () => page.clock.fastForward(20_001),
    visible: [helpers.getByText("No helpers observed.", { exact: true }), helpers.getByText(/Partial discovery:/)], hidden: [direct, descendant, noEvidence],
  });
  await expect(helpers.locator("dt", { hasText: /^Attributable helper tokens$/ }).locator("+ dd")).toHaveText("Unknown");
  observation = undefined;
  await walk.state("06-unknown-evidence", {
    action: () => page.clock.fastForward(20_001),
    visible: [helpers, noEvidence, details.getByText("Token usage unknown", { exact: true })], hidden: [direct, descendant, page.getByText("No helpers observed.", { exact: true })],
  });
  await expect(helpers.locator("dt", { hasText: /^Unique helpers$/ }).locator("+ dd")).toHaveText("Unknown");
  await walk.state("07-collapse-removes-audit", { action: () => toggle.click(), visible: [toggle], hidden: [details, helpers, noEvidence] });
  await expect(page.locator(".token-details")).toHaveCount(0);
});
