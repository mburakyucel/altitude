import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

test("effort defaults save independently and survive reload", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const settings = page.getByRole("region", { name: "Reasoning effort" });
  const l3 = settings.getByLabel("L3 effort");
  const l2 = settings.getByLabel("L2 effort");
  await walk.open(project.path);
  const draft = page.getByRole("textbox", { name: /message/i });
  await draft.fill("Keep my project draft");
  await walk.state("01-details-closed", { visible: [draft], hidden: [settings] });
  await walk.state("02-defaults", {
    action: () => page.getByRole("button", { name: "More actions" }).click(),
    visible: [settings, l3, l2], hidden: [],
  });
  await expect(l3).toHaveValue("");
  await expect(l2).toHaveValue("");
  await expect(settings.getByRole("button", { name: /microphone|record|listen/i })).toHaveCount(0);
  await l3.selectOption("native");
  await expect(settings.getByRole("status")).toHaveText("Effort saved.");
  await expect(l2).toHaveValue("");
  await l2.selectOption("high");
  await expect(l2).toHaveValue("high");
  await walk.state("03-saved", { visible: [settings.getByText("Effort saved.")], hidden: [] });
  const saved = await (await request.get(`/api/effort/${project.name}`)).json();
  expect(saved).toMatchObject({ l3: "native", l2: "high" });
  await walk.state("04-dismissed", {
    action: () => page.keyboard.press("Escape"), visible: [draft], hidden: [settings],
  });
  await expect(draft).toHaveValue("Keep my project draft");
  await page.reload();
  await page.getByRole("button", { name: "More actions" }).click();
  await expect(l3).toHaveValue("native");
  await expect(l2).toHaveValue("high");
  await walk.state("05-persisted", { visible: [settings], hidden: [] });
  await l3.selectOption("");
  await expect(l3).toHaveValue("");
  await expect(l2).toHaveValue("high");
  expect(await (await request.get(`/api/effort/${project.name}`)).json()).toMatchObject({ l3: null, l2: "high" });
  await walk.state("06-default-restored", { visible: [settings], hidden: [] });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("effort loading and read failure can retry", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let fail = true;
  await page.route(`**/api/effort/${project.name}`, async (route) => {
    if (!fail) return route.fallback();
    await gate;
    return route.fulfill({ status: 503, json: { error: "Settings unavailable" } });
  });
  const settings = page.getByRole("region", { name: "Reasoning effort" });
  try {
    await walk.open(project.path);
    await walk.state("01-loading", {
      action: () => page.getByRole("button", { name: "More actions" }).click(),
      visible: [settings.getByText("Loading effort settings…")], hidden: [settings.getByLabel("L3 effort")],
    });
    release();
    await walk.state("02-read-error", {
      visible: [settings.getByText("Could not load effort settings."), settings.getByRole("button", { name: "Retry", exact: true })],
      hidden: [settings.getByText("Loading effort settings…"), settings.getByLabel("L3 effort")],
    });
    fail = false;
    await walk.state("03-recovered", {
      action: () => settings.getByRole("button", { name: "Retry", exact: true }).click(),
      visible: [settings.getByLabel("L3 effort"), settings.getByLabel("L2 effort")],
      hidden: [settings.getByRole("alert")],
    });
  } finally { release(); }
});

test("unsupported pinned-engine effort is rejected and retries after choosing Auto", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const overview = await (await request.get("/api/overview")).json();
  // The fixture's first engine does not support Ultra; labels still come from the seam.
  const engine = overview.engines[0] as { engine: string; label: string };
  expect((await request.post("/api/l3/engine", { data: { project: project.name, engine: engine.engine } })).ok()).toBe(true);
  expect((await request.post("/api/effort", { data: { project: project.name, role: "l3", effort: "native" } })).ok()).toBe(true);
  const walk = walkthrough(page, info);
  const settings = page.getByRole("region", { name: "Reasoning effort" });
  const l3 = settings.getByLabel("L3 effort");
  await walk.open(project.path);
  await page.getByRole("button", { name: "More actions" }).click();
  await expect(l3).toHaveValue("native");
  const [rejected] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/api/effort") && response.request().method() === "POST"),
    l3.selectOption("ultra"),
  ]);
  expect(rejected.status()).toBe(400);
  await walk.state("01-unsupported-choice", {
    visible: [settings.getByText(`${engine.label} does not support reasoning effort ultra; choose a supported level or Native`),
      settings.getByRole("button", { name: "Retry save" })], hidden: [settings.getByText("Effort saved.")],
  });
  await expect(l3).toHaveValue("native");
  expect(await (await request.get(`/api/effort/${project.name}`)).json()).toMatchObject({ l3: "native", l2: null });
  const [unpinned] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/api/l3/engine") && response.request().method() === "POST"),
    page.locator(".project-details, .project-settings").getByRole("combobox", { name: "L3 engine" }).selectOption(""),
  ]);
  expect(unpinned.ok()).toBe(true);
  await walk.state("02-supported-retry", {
    action: () => settings.getByRole("button", { name: "Retry save" }).click(),
    visible: [settings.getByText("Effort saved.")], hidden: [settings.getByRole("alert")],
  });
  await expect(l3).toHaveValue("ultra");
  expect(await (await request.get(`/api/effort/${project.name}`)).json()).toMatchObject({ l3: "ultra", l2: null });
});

test("effort saving disables controls and denied writes retain the saved value", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let deny = true;
  await page.route("**/api/effort", async (route) => {
    if (!deny) return route.fallback();
    await gate;
    return route.fulfill({ status: 403, json: { error: "Changing effort is denied." } });
  });
  const settings = page.getByRole("region", { name: "Reasoning effort" });
  const l3 = settings.getByLabel("L3 effort");
  const l2 = settings.getByLabel("L2 effort");
  try {
    await walk.open(project.path);
    await page.getByRole("button", { name: "More actions" }).click();
    await l3.selectOption("native");
    await expect(l3).toBeDisabled();
    await expect(l2).toBeDisabled();
    await walk.state("01-saving", { visible: [settings.getByText("Saving effort…")], hidden: [] });
    release();
    await walk.state("02-denied", {
      visible: [settings.getByText("Changing effort is denied."), settings.getByRole("button", { name: "Retry save" })],
      hidden: [settings.getByText("Saving effort…")],
    });
    await expect(l3).toBeEnabled();
    await expect(l3).toHaveValue("");
    expect(await (await request.get(`/api/effort/${project.name}`)).json()).toMatchObject({ l3: null, l2: null });
    deny = false;
    await walk.state("03-retry-saved", {
      action: () => settings.getByRole("button", { name: "Retry save" }).click(),
      visible: [settings.getByText("Effort saved.")], hidden: [settings.getByRole("alert")],
    });
    await expect(l3).toHaveValue("native");
    expect(await (await request.get(`/api/effort/${project.name}`)).json()).toMatchObject({ l3: "native", l2: null });
  } finally { release(); }
});
