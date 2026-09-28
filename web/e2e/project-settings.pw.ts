import { expect, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

type Field = { setting: string; value: string | null; default: string };
type Preference = { setting: string; value: string | null; choices: { value: string; label: string }[] };
type Defaults = { l3_engine: string | null; l2_preference: Preference; roles: { role: string; engines: { engine: string; label: string; model: Field; effort: Field & { choices: { value: string; label: string }[] } }[] }[] };

async function defaults(request: APIRequestContext, name: string) {
  const response = await request.get(`/api/defaults/${encodeURIComponent(name)}`);
  expect(response.ok()).toBe(true);
  return await response.json() as Defaults;
}
/** Every saved value keyed by its setting; engine names come from the API, never this file. */
const values = (view: Defaults) => Object.fromEntries(view.roles.flatMap((row) => row.engines.flatMap((e) =>
  [[e.model.setting, e.model.value], [e.effort.setting, e.effort.value]])));

test("Settings holds project models and effort; the menu only links there and the draft survives", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const initial = await defaults(request, project.name);
  const [first, second] = initial.roles[0]!.engines;
  const l2First = initial.roles[1]!.engines[0]!;
  const draft = main.getByRole("textbox", { name: /^Message L3 about /, includeHidden: true });
  const menu = page.getByRole("menu", { name: "Project actions" });
  const row = main.getByRole("region", { name: "This project" }).getByRole("link", { name: new RegExp(project.name) });
  const l3First = main.getByRole("group", { name: `L3 on ${first!.label}` });
  const l3Second = main.getByRole("group", { name: `L3 on ${second!.label}` });
  const l2Group = main.getByRole("group", { name: `L2 on ${l2First.label}` });
  /** Each field reports its own save, so a group can show two statuses at once. */
  const saved = (label: string) => l3First.locator(".default-field", { has: page.getByLabel(label) }).getByText("Saved.");

  await walk.open(project.path);
  await draft.fill("Keep my project draft");
  await walk.state("01-menu-links-to-settings", {
    action: () => page.getByRole("button", { name: "More actions" }).click(),
    visible: [menu, menu.getByRole("menuitem", { name: "Settings…", exact: true })],
    hidden: [page.getByRole("dialog")],
  });
  await expect(menu.getByRole("combobox")).toHaveCount(0);
  await walk.state("02-this-project-row", {
    action: () => menu.getByRole("menuitem", { name: "Settings…", exact: true }).click(),
    visible: [row, main.getByRole("region", { name: "Network" })], hidden: [menu, l3First],
  });
  await walk.state("03-project-settings", {
    action: () => row.click(),
    visible: [main.getByRole("combobox", { name: "L3 engine" }), l3First, l3Second, l2Group, main.getByText(/^Last turn: /)],
    hidden: [row],
  });
  await expect(l3First.getByLabel("Effort")).toHaveValue("");
  await expect(l3First.getByLabel("Model")).toHaveAttribute("placeholder", `Default: ${first!.model.default}`);
  await expect(l2Group.getByLabel("Effort")).toHaveValue("");

  const choice = first!.effort.choices.at(-1)!;
  await walk.state("04-l3-effort-saved", {
    action: () => l3First.getByLabel("Effort").selectOption(choice.value),
    visible: [saved("Effort")], hidden: [l3Second.getByText("Saved."), l2Group.getByText("Saved.")],
  });
  await l3First.getByLabel("Model").fill("fixture-model");
  await walk.state("05-model-saved-on-enter", {
    action: () => l3First.getByLabel("Model").press("Enter"),
    visible: [saved("Model")], hidden: [l3First.getByRole("alert")],
  });
  await expect(l3First.getByLabel("Model")).toHaveValue("fixture-model");
  expect(values(await defaults(request, project.name))).toEqual({
    ...values(initial), [first!.effort.setting]: choice.value, [first!.model.setting]: "fixture-model",
  });

  await walk.state("06-back-to-settings", {
    action: () => main.getByRole("link", { name: "‹ Settings", exact: true }).click(), visible: [row], hidden: [l3First],
  });
  if (info.project.name === "phone") await main.getByRole("button", { name: "‹ Back", exact: true }).click();
  else await page.getByRole("link", { name: project.name, exact: true }).click();
  await expect(draft).toHaveValue("Keep my project draft");

  await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
  await walk.state("07-persisted-on-direct-visit", { visible: [l3First], hidden: [saved("Model"), saved("Effort")] });
  await expect(l3First.getByLabel("Effort")).toHaveValue(choice.value);
  await expect(l3First.getByLabel("Model")).toHaveValue("fixture-model");
  await expect(l3Second.getByLabel("Effort")).toHaveValue("");
  await l3First.getByLabel("Model").fill("");
  await l3First.getByLabel("Effort").focus();
  await expect(l3First.getByLabel("Model")).toHaveValue("");
  await l3First.getByLabel("Effort").selectOption("");
  await walk.state("08-default-restored", { visible: [saved("Model"), saved("Effort")], hidden: [] });
  await expect.poll(async () => values(await defaults(request, project.name))).toEqual(values(initial));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("the L3 engine pin saves from Settings and reads back", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const pin = page.getByRole("main").getByRole("combobox", { name: "L3 engine" });
  const { roles } = await defaults(request, project.name);
  const engine = roles[0]!.engines[0]!;
  await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
  await walk.state("01-auto", { visible: [pin], hidden: [] });
  await expect(pin).toHaveValue("");
  await walk.state("02-pinned", { action: () => pin.selectOption(engine.engine), visible: [pin], hidden: [page.getByRole("alert")] });
  await expect.poll(async () => (await defaults(request, project.name)).l3_engine).toBe(engine.engine);
  await page.reload();
  await expect(pin).toHaveValue(engine.engine);
  await pin.selectOption("");
  await expect.poll(async () => (await defaults(request, project.name)).l3_engine).toBeNull();
});

test("L2 provider priority saves, survives reload, leaves L3 alone and restores Auto", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const { l2_preference: preference } = await defaults(request, project.name);
  const [preferred] = preference.choices;
  const card = main.getByRole("group", { name: "L2 provider priority" });
  const select = card.getByRole("combobox", { name: "Provider priority" });
  const l3 = main.getByRole("combobox", { name: "L3 engine" });
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
    await walk.state("01-auto-default", { visible: [select, card.getByText(/^Auto uses the default distribution/)], hidden: [card.getByText("Saved.")] });
    await expect(select).toHaveValue("");
    await walk.state("02-preferred-saved", {
      action: () => select.selectOption(preferred!.value),
      visible: [card.getByText("Saved."), card.getByText(`Fresh tasks start on ${preferred!.label} when it is available`, { exact: false })],
      hidden: [card.getByRole("alert")],
    });
    await expect.poll(async () => (await defaults(request, project.name)).l2_preference.value).toBe(preferred!.value);
    await page.reload();
    await card.scrollIntoViewIfNeeded();
    await walk.state("03-persisted-after-reload", { visible: [select], hidden: [card.getByText("Saved.")] });
    await expect(select).toHaveValue(preferred!.value);
    await expect(l3).toHaveValue("");
    expect((await defaults(request, project.name)).l3_engine).toBeNull();
    deny = true;
    await select.selectOption("");
    await expect(select).toBeDisabled();
    await walk.state("04-saving", { visible: [card.getByText("Saving…")], hidden: [] });
    release();
    await walk.state("05-denied-keeps-saved", {
      visible: [card.getByText("Changing settings is denied."), card.getByRole("button", { name: "Retry save" })],
      hidden: [card.getByText("Saving…"), card.getByText("Saved.")],
    });
    await expect(select).toHaveValue(preferred!.value);
    expect((await defaults(request, project.name)).l2_preference.value).toBe(preferred!.value);
    deny = false;
    await walk.state("06-auto-restored", {
      action: () => card.getByRole("button", { name: "Retry save" }).click(),
      visible: [card.getByText("Saved."), card.getByText(/^Auto uses the default distribution/)], hidden: [card.getByRole("alert")],
    });
    await expect(select).toHaveValue("");
    expect((await defaults(request, project.name)).l2_preference.value).toBeNull();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { release(); }
});

test("project settings loading and read failure can retry", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
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
    await walk.state("01-loading", { visible: [main.getByText("Loading settings…")], hidden: [main.getByLabel("Effort").first()] });
    release();
    await walk.state("02-read-error", {
      visible: [main.getByText("Could not load settings."), main.getByRole("button", { name: "Retry", exact: true })],
      hidden: [main.getByText("Loading settings…"), main.getByLabel("Effort").first()],
    });
    fail = false;
    await walk.state("03-recovered", {
      action: () => main.getByRole("button", { name: "Retry", exact: true }).click(),
      visible: [main.getByLabel("Effort").first(), main.getByRole("combobox", { name: "L3 engine" })], hidden: [main.getByRole("alert")],
    });
  } finally { release(); }
});

test("saving disables the field and a denied write keeps the saved value", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const main = page.getByRole("main");
  const { roles } = await defaults(request, project.name);
  const engine = roles[1]!.engines[0]!;
  const group = main.getByRole("group", { name: `L2 on ${engine.label}` });
  const effort = group.getByLabel("Effort");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let deny = true;
  await page.route("**/api/defaults", async (route) => {
    if (!deny) return route.fallback();
    await gate;
    return route.fulfill({ status: 403, json: { error: "Changing settings is denied." } });
  });
  try {
    await walk.open(`/settings/projects/${encodeURIComponent(project.name)}`);
    await effort.selectOption("native");
    await expect(effort).toBeDisabled();
    await walk.state("01-saving", { visible: [group.getByText("Saving…")], hidden: [] });
    release();
    await walk.state("02-denied", {
      visible: [group.getByText("Changing settings is denied."), group.getByRole("button", { name: "Retry save" })],
      hidden: [group.getByText("Saving…")],
    });
    await expect(effort).toBeEnabled();
    await expect(effort).toHaveValue("");
    expect(values(await defaults(request, project.name))[engine.effort.setting]).toBeNull();
    deny = false;
    await walk.state("03-retry-saved", {
      action: () => group.getByRole("button", { name: "Retry save" }).click(),
      visible: [group.getByText("Saved.")], hidden: [group.getByRole("alert")],
    });
    await expect(effort).toHaveValue("native");
    expect(values(await defaults(request, project.name))[engine.effort.setting]).toBe("native");
  } finally { release(); }
});
