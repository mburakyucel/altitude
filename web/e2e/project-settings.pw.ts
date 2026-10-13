import { expect, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

type Field = { setting: string; value: string | null; default: string };
type Choice = { engine?: string; model?: string; effort?: string } | null;
type Defaults = {
  l2_engine: string | null; l2_preference: string | null; l3_choice: Choice;
  engines: { value: string; label: string }[];
  models: { engine: string; model: string | null; label: string }[];
  roles: { role: string; engines: { engine: string; label: string; model: Field; effort: Field & { choices: { value: string; label: string }[] } }[] }[];
};

async function defaults(request: APIRequestContext, name: string) {
  const response = await request.get(`/api/defaults/${encodeURIComponent(name)}`);
  expect(response.ok()).toBe(true);
  return await response.json() as Defaults;
}
test("project settings keeps engine selection separate from global models", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const initial = await defaults(request, project.name);
  const engine = initial.engines[0]!;
  await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
  const select = main.getByRole("combobox", { name: "Coordinator engine", exact: true });
  await walk.state("01-project-engine-controls", {
    visible: [select, main.getByRole("combobox", { name: "Tasks routing" }), main.getByRole("link", { name: "Models and effort · All projects", exact: true })],
    hidden: [main.getByRole("heading", { name: "Auto defaults", exact: true }), main.getByRole("combobox", { name: / effort$/ })],
  });
  await select.selectOption(engine.value);
  await expect.poll(async () => (await defaults(request, project.name)).l3_choice).toEqual({ engine: engine.value });
  await page.reload();
  await expect(select).toHaveValue(engine.value);
  await walk.state("02-engine-preference-saved", { visible: [select], hidden: [main.getByRole("alert")] });
  await select.selectOption("");
  await expect.poll(async () => (await defaults(request, project.name)).l3_choice).toBeNull();
  await walk.state("03-auto-restored", { visible: [select], hidden: [main.getByRole("alert")] });
});

test("Tasks routing Prefer and Only save, survive reload and restore Auto", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const { engines } = await defaults(request, project.name);
  const preferred = engines[0]!;
  const card = main.getByRole("region", { name: "Routing", exact: true });
  const select = card.getByRole("combobox", { name: "Tasks routing" });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let deny = false;
  await page.route("**/api/defaults", async (route) => {
    if (!deny) return route.fallback();
    await gate;
    return route.fulfill({ status: 403, json: { error: "Changing settings is denied." } });
  });
  try {
    await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
    await card.scrollIntoViewIfNeeded();
    await walk.state("01-auto-default", { visible: [select, card.getByText(/^The order Auto tries/)], hidden: [card.getByRole("status"), card.getByRole("alert")] });
    await expect(select).toHaveValue("");
    await expect(select.locator("option")).toHaveText(["Auto", ...engines.map((e) => `Prefer ${e.label}`), ...engines.map((e) => `Only ${e.label}`)]);
    await walk.state("02-preferred-saved", {
      action: () => select.selectOption(`prefer:${preferred.value}`),
      visible: [select], hidden: [card.getByRole("alert"), card.getByRole("status", { name: "Saving…", exact: true })],
    });
    await expect.poll(async () => (await defaults(request, project.name)).l2_preference).toBe(preferred.value);
    await page.reload();
    await card.scrollIntoViewIfNeeded();
    await walk.state("03-persisted-after-reload", { visible: [select], hidden: [card.getByRole("status", { name: "Saving…", exact: true })] });
    await expect(select).toHaveValue(`prefer:${preferred.value}`);

    // Only replaces Prefer: both settings are written, so routing never holds both.
    await select.selectOption(`only:${preferred.value}`);
    await expect.poll(async () => {
      const view = await defaults(request, project.name);
      return [view.l2_engine, view.l2_preference];
    }).toEqual([preferred.value, null]);
    await expect(select).toHaveValue(`only:${preferred.value}`);

    deny = true;
    await select.selectOption("");
    await expect(select).toBeDisabled();
    await walk.state("04-saving", { visible: [card.getByRole("status", { name: "Saving…", exact: true })], hidden: [] });
    release();
    await walk.state("05-denied-keeps-saved", {
      visible: [card.getByRole("alert").filter({ hasText: "Changing settings is denied." })],
      hidden: [card.getByRole("status", { name: "Saving…", exact: true })],
    });
    await expect(select).toHaveValue(`only:${preferred.value}`);
    expect((await defaults(request, project.name)).l2_engine).toBe(preferred.value);
    deny = false;
    await walk.state("06-auto-restored", {
      action: () => select.selectOption(""),
      visible: [select], hidden: [card.getByRole("alert"), card.getByRole("status", { name: "Saving…", exact: true })],
    });
    await expect(select).toHaveValue("");
    await expect.poll(async () => {
      const view = await defaults(request, project.name);
      return [view.l2_engine, view.l2_preference];
    }).toEqual([null, null]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { release(); }
});

test("a routing change made elsewhere is refused and the page shows the current routing", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const { engines } = await defaults(request, project.name);
  expect(engines.length, "The walkthrough needs two configured engines").toBeGreaterThan(1);
  const [elsewhere, here] = engines;
  const card = page.getByRole("main").getByRole("region", { name: "Routing", exact: true });
  const select = card.getByRole("combobox", { name: "Tasks routing" });
  await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
  await expect(select).toHaveValue("");
  // Another window keeps tasks on one engine after this page read Auto.
  expect((await request.post("/api/defaults", { data: { project: project.name, setting: "l2_engine", value: elsewhere!.value } })).ok()).toBe(true);
  await walk.state("01-changed-elsewhere", {
    action: () => select.selectOption(`only:${here!.value}`),
    visible: [card.getByRole("alert").filter({ hasText: "Changed in another window. The page shows the current routing." })], hidden: [card.getByRole("status", { name: "Saving…", exact: true })],
  });
  expect((await defaults(request, project.name)).l2_engine).toBe(elsewhere!.value);
  await expect(select).toHaveValue(`only:${elsewhere!.value}`);
});

test("project settings loading and read failure can retry; the project section stays", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const remove = main.getByRole("region", { name: "Project", exact: true }).getByRole("button", { name: "Remove…", exact: true });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let fail = true;
  await page.route(`**/api/defaults/${project.name}`, async (route) => {
    if (!fail) return route.fallback();
    await gate;
    return route.fulfill({ status: 503, json: { error: "Settings unavailable" } });
  });
  try {
    await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
    await walk.state("01-loading", { visible: [main.getByRole("status", { name: "Loading settings…", exact: true })], hidden: [main.getByRole("combobox", { name: "Coordinator engine", exact: true })] });
    release();
    await walk.state("02-read-error", {
      visible: [main.getByText("Could not load settings."), main.getByRole("button", { name: "Retry", exact: true }), remove],
      hidden: [main.getByRole("status", { name: "Loading settings…", exact: true }), main.getByRole("combobox", { name: "Coordinator engine", exact: true }), main.getByRole("region", { name: "Routing", exact: true })],
    });
    fail = false;
    await walk.state("03-recovered", {
      action: () => main.getByRole("button", { name: "Retry", exact: true }).click(),
      visible: [main.getByRole("combobox", { name: "Coordinator engine", exact: true }), main.getByRole("combobox", { name: "Tasks routing" }), remove], hidden: [main.getByRole("alert")],
    });
  } finally { release(); }
});
