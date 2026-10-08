import { expect, type APIRequestContext } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "captures-service.py" });

async function fixture(request: APIRequestContext) {
  const response = await request.get("/fixture/captures");
  expect(response.ok()).toBe(true);
  return response.json() as Promise<{ slug: string; single: string; pair: string }>;
}

test("an L2 reply's captures open in their own tab, an unavailable capture retries, and Back returns to the conversation", async ({ page, request }, info) => {
  const { slug, single, pair } = await fixture(request);
  const taskPath = `/projects/atlas/tasks/${slug}`;
  const walk = walkthrough(page, info);
  const convo = page.getByRole("region", { name: "Task conversation", exact: true });
  const watchOne = convo.getByRole("link", { name: "Watch capture · Phone send", exact: true });
  const watchTwo = convo.getByRole("link", { name: "Watch 2 captures", exact: true });
  await walk.open(taskPath);
  await walk.state("01-reply-capture-links", {
    visible: [convo.getByText("Run 4 recorded the desktop send and a reconnect.", { exact: true }), watchOne, watchTwo], hidden: [],
  });
  await expect(convo.getByRole("link", { name: /^Watch / })).toHaveCount(2);
  await expect(watchOne).toHaveAttribute("href", `${taskPath}/captures/${single}`);
  await expect(watchTwo).toHaveAttribute("href", `${taskPath}/captures/${pair}`);
  await expect(watchTwo).toHaveAttribute("target", "_blank");
  // SPEC §1.1: a text link reads as clickable at rest.
  await expect(watchTwo).toHaveCSS("text-decoration-line", "underline");
  if (info.project.name === "phone") expect((await watchTwo.boundingBox())!.height).toBeGreaterThanOrEqual(44);

  const opened = page.waitForEvent("popup");
  await watchTwo.click();
  const viewer = await opened;
  const view = walkthrough(viewer, info);
  await expect(viewer).toHaveURL(new RegExp(`${taskPath}/captures/${pair}$`));
  await expect(page).toHaveURL(new RegExp(`${taskPath}$`));
  const heading = viewer.getByRole("heading", { name: "Captures from validation run 4", exact: true });
  const back = viewer.getByRole("link", { name: "← Back to conversation", exact: true });
  const send = viewer.getByRole("figure", { name: "Desktop send", exact: true });
  const reconnect = viewer.getByRole("figure", { name: "Desktop reconnect", exact: true });
  const retry = reconnect.getByRole("button", { name: "Retry capture", exact: true });
  await view.state("02-ready-and-unavailable-captures", {
    visible: [heading, back, send.getByRole("img", { name: "Desktop send", exact: true }), send.getByText(/^\d+ KiB · 1\.8 s · 3 frames$/),
      reconnect.getByRole("alert").filter({ hasText: "Capture unavailable." }), retry],
    hidden: [viewer.getByText("Loading captures…", { exact: true }), send.getByText("Loading capture…", { exact: true }), reconnect.getByRole("img")],
  });
  // The GIF decodes and plays at its recorded size rather than being stretched to the page.
  expect(await send.getByRole("img").evaluate((image: HTMLImageElement) => [image.naturalWidth, image.naturalHeight, image.clientWidth])).toEqual([120, 80, 120]);

  expect((await request.post("/fixture/restore-capture")).ok()).toBe(true);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await viewer.route((url) => url.searchParams.get("retry") === "1", async (route) => { await gate; await route.continue(); }, { times: 1 });
  await retry.click();
  try {
    await view.state("03-retrying-capture", {
      visible: [reconnect.getByText("Loading capture…", { exact: true })], hidden: [retry, reconnect.getByRole("alert")],
    });
  } finally { release(); }
  await view.state("04-capture-recovered", {
    visible: [reconnect.getByRole("img", { name: "Desktop reconnect", exact: true }), reconnect.getByText(/^\d+ KiB · 1\.2 s · 2 frames$/)],
    hidden: [retry, reconnect.getByText("Loading capture…", { exact: true })],
  });

  await back.click();
  await expect(viewer).toHaveURL(new RegExp(`${taskPath}([?#]|$)`));
  await view.state("05-back-to-conversation", {
    visible: [viewer.getByRole("region", { name: "Task conversation", exact: true }).getByRole("link", { name: "Watch 2 captures", exact: true })],
    hidden: [heading, back],
  });
  await viewer.close();
});

test("the captures page loads, explains a reply without captures and returns to the conversation", async ({ page, request }, info) => {
  const { slug, single } = await fixture(request);
  const taskPath = `/projects/atlas/tasks/${slug}`;
  const walk = walkthrough(page, info);
  const unavailable = page.getByRole("heading", { name: "Capture unavailable", exact: true });
  const back = page.getByRole("link", { name: "← Back to conversation", exact: true });
  const heading = page.getByRole("heading", { name: "Captures from validation run 3", exact: true });
  await walk.open(`${taskPath}/captures/${"0".repeat(32)}`);
  await walk.state("01-page-unavailable", {
    visible: [unavailable, page.getByText("These captures could not be loaded.", { exact: false }), page.getByRole("button", { name: "Retry", exact: true }), back],
    hidden: [page.getByRole("figure"), page.getByText("Loading captures…", { exact: true })],
  });
  await expect(back).toHaveAttribute("href", taskPath);

  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`**/api/captures/atlas/${slug}/${single}`, async (route) => { await gate; await route.continue(); }, { times: 1 });
  await walk.open(`${taskPath}/captures/${single}`);
  try {
    await walk.state("02-loading-captures", { visible: [page.getByText("Loading captures…", { exact: true }), back], hidden: [heading, unavailable] });
  } finally { release(); }
  const phone = page.getByRole("figure", { name: "Phone send", exact: true });
  await walk.state("03-single-capture", {
    visible: [heading, phone.getByRole("img", { name: "Phone send", exact: true }), phone.getByText(/^\d+ KiB · 1\.8 s · 3 frames$/)],
    hidden: [page.getByText("Loading captures…", { exact: true }), unavailable, phone.getByText("Loading capture…", { exact: true })],
  });
  await back.click();
  await expect(page).toHaveURL(new RegExp(`${taskPath}([?#]|$)`));
  await walk.state("04-back-to-conversation", {
    visible: [page.getByRole("region", { name: "Task conversation", exact: true }).getByRole("link", { name: "Watch capture · Phone send", exact: true })],
    hidden: [heading],
  });
});
