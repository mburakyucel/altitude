import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test("status cues: loading, saving, failure and saved on the name form", async ({ page, request }, info) => {
  const machine = await (await request.get("/api/machine")).json();
  let releaseRead!: () => void;
  const reading = new Promise<void>((resolve) => { releaseRead = resolve; });
  await page.route("**/api/machine", async (route) => {
    await reading;
    await route.fulfill({ json: machine });
  });
  const walk = walkthrough(page, info);
  await walk.open("/settings/name");
  const loading = page.getByRole("status", { name: "Loading…", exact: true });
  await walk.state("01-loading", { visible: [loading], hidden: [page.getByRole("textbox", { name: "Your name" })] });
  await expect(loading).not.toHaveAttribute("aria-busy", "true");
  await expect(loading.locator(".spinner")).toBeVisible();
  await expect(loading.locator(".sr-only")).toHaveText("Loading…");
  releaseRead();
  const field = page.getByRole("textbox", { name: "Your name" });
  await field.fill("Example operator");
  let releaseSave!: () => void;
  const saving = new Promise<void>((resolve) => { releaseSave = resolve; });
  let deny = true;
  await page.route("**/api/operator-name", async (route) => {
    await saving;
    if (deny) await route.fulfill({ status: 403, json: { error: "Fixture access denied" } });
    else await route.continue();
  });
  const save = page.getByRole("button", { name: "Save", exact: true });
  const before = await save.boundingBox();
  await save.click();
  const pending = page.getByRole("button", { name: "Saving…", exact: true });
  await walk.state("02-saving", { visible: [pending, field], hidden: [loading] });
  await expect(pending).toBeDisabled();
  await expect(pending.locator(".busy-label > span").first()).toHaveCSS("visibility", "hidden");
  expect((await pending.boundingBox())!.width).toBe(before!.width);
  releaseSave();
  await walk.state("03-denied", { visible: [page.getByRole("alert"), save, field], hidden: [pending] });
  await expect(field).toHaveValue("Example operator");
  deny = false;
  await save.click();
  await walk.state("04-saved", { visible: [page.getByRole("status", { name: "Saved.", exact: true }), save], hidden: [page.getByRole("alert")] });
  expect((await (await request.get("/api/machine")).json()).operator).toBe("Example operator");
});

test("status cues: unavailable switch explains itself without changing settings", async ({ page, request }, info) => {
  const machine = await (await request.get("/api/machine")).json();
  const reason = "A terminal is not supported by this fictional installation";
  await page.route("**/api/machine", (route) => route.fulfill({ json: { ...machine, terminal_unavailable: reason } }));
  const posts: string[] = [];
  page.on("request", (request) => { if (request.method() === "POST") posts.push(request.url()); });
  const walk = walkthrough(page, info);
  await walk.open("/settings");
  const control = page.getByRole("switch", { name: "Terminal", exact: true });
  await expect(control).toHaveAttribute("aria-disabled", "true");
  await expect(control).toHaveAttribute("aria-checked", "false");
  await expect(control).toHaveAccessibleDescription(`Not available here: ${reason}.`);
  const detail = page.getByText(`Not available here: ${reason}.`, { exact: true });
  await expect(detail).toHaveClass("sr-only");
  await walk.state("05-unavailable", { visible: [control], hidden: [] });
  await control.focus();
  await page.keyboard.press("Enter");
  await expect(detail).not.toHaveClass("sr-only");
  await walk.state("06-reason-on-press", { visible: [control, detail], hidden: [] });
  expect(posts).toEqual([]);
  await expect(control).toHaveAttribute("aria-checked", "false");
});
