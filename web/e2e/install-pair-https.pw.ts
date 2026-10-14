import { expect } from "@playwright/test";
import { httpsTest as test } from "../playwright.config";
import { walkthrough } from "./walkthrough";

// NSS import belongs only to the throwaway Linux Chromium home. WebKit presentation is walked separately.
test.use({ paired: false, serviceScript: "install-pair-service.py" });
test.skip(process.platform !== "linux", "Disposable NSS trust is Linux Chromium evidence; native Mac trust stays on the change's issue.");

test("fresh verified installation prints a private link that pairs over trusted HTTPS into First run @chromium", async ({ page, altitude }, info) => {
  const walk = walkthrough(page, info);
  const order = ["is installed and running", "1. Trust Altitude's certificate", "2. Open Altitude with this private link", "Terminal (optional):"]
    .map((line) => altitude.output!.indexOf(line));
  expect(order.every((at, index) => at >= 0 && (index === 0 || at > order[index - 1]))).toBe(true);
  let pairs = 0;
  let leaked = false;
  const secret = new URL(altitude.pairing!).hash.slice(6);
  page.on("request", sent => {
    if (sent.url().endsWith("/api/pair")) pairs++;
    else if (sent.url().includes(secret) || sent.postData()?.includes(secret) || Object.values(sent.headers()).some(value => value.includes(secret))) leaked = true;
  });
  await walk.open(altitude.pairing!);
  await walk.state("01-paired-first-run", { visible: [page.getByRole("heading", { name: "Welcome to Altitude" })], hidden: [page.getByLabel("Pairing code"), page.getByRole("heading", { name: "Pair this device" })] });
  expect(pairs).toBe(1);
  expect(leaked).toBe(false);
  expect(new URL(page.url()).hash).toBe("");
  const stored = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, history: history.state, text: document.body.textContent }));
  expect(stored.includes(secret)).toBe(false);
  const devices = await page.evaluate(async () => (await fetch("/api/devices")).json());
  expect(devices.devices).toHaveLength(1);
  const active = await page.context().cookies();
  expect(active.some(cookie => cookie.name === "altitude_device" && cookie.httpOnly && cookie.secure && cookie.sameSite === "Strict")).toBe(true);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Welcome to Altitude" })).toBeVisible();
  expect(pairs).toBe(1);
  await page.goto(altitude.pairing!);
  await expect(page.getByRole("heading", { name: "Welcome to Altitude" })).toBeVisible();
  expect(pairs).toBe(1);
  expect((await page.evaluate(async () => (await fetch("/api/devices")).json())).devices).toHaveLength(1);
});

test("reopening the original installer link in a fresh trusted context keeps the restart handoff @chromium", async ({ context, altitude }, info) => {
  const before = await context.newPage();
  await before.route("**/api/access", route => route.abort());
  await before.goto(altitude.pairing!);
  await expect(before.getByText("Could not reach Altitude.")).toBeVisible();
  expect(new URL(before.url()).hash).toBe("");
  await before.close();
  const after = await context.browser()!.newContext({ ignoreHTTPSErrors: false, viewport: info.project.use.viewport, userAgent: info.project.use.userAgent });
  try {
    const page = await after.newPage();
    await page.goto(altitude.pairing!);
    await expect(page.getByRole("heading", { name: "Welcome to Altitude" })).toBeVisible();
    expect(new URL(page.url()).hash).toBe("");
  } finally { await after.close(); }
});
