import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * The Validation runs switch in Settings (docs/DEVELOPMENT.md#validation-runner) against the real machine
 * settings, walked at both widths: on after install, off at the press while the save is held, a refused change
 * that puts the switch back with Retry, back on by Retry, and a computer without the runner, where the row is absent. Only the runner's availability and the failed save are
 * fixtures, so the walk does not depend on Podman being installed where it runs.
 */
test.use({ serviceScript: "terminal-service.py" });

test("validation runs switch on and off, keeps its place after a refused change and leaves Settings without a runner", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  let unavailable: string | null = null;
  await page.route(/\/api\/(machine|validation-access)$/, async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), validation_unavailable: unavailable } });
  });
  const toggle = page.getByRole("switch", { name: /Validation runs/ });
  const detail = page.getByText(/throwaway, isolated runs on this computer/);
  const refused = page.getByRole("alert").filter({ hasText: "Validation settings are busy" });

  await walk.open("/settings");
  await walk.state("01-on-after-install", { visible: [toggle, detail], hidden: [refused] });
  await expect(toggle).toBeChecked();
  await expect(toggle).toBeEnabled();

  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/validation-access", async (route) => { await held; await route.fallback(); }, { times: 1 });
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  await expect(toggle).toBeDisabled();
  await walk.state("02a-off-at-the-press", { visible: [toggle, detail], hidden: [refused] });
  release();
  await expect(toggle).toBeEnabled();
  await expect(toggle).not.toBeChecked();
  await walk.state("02-off", { visible: [toggle, detail], hidden: [refused] });
  await page.reload();
  await expect(toggle).not.toBeChecked();

  await page.route("**/api/validation-access", (route) => route.fulfill({ status: 409, json: { error: "Validation settings are busy. Try again." } }), { times: 1 });
  await toggle.click();
  const retry = refused.getByRole("button", { name: "Retry", exact: true });
  await walk.state("03-refused-change", { visible: [toggle, refused, retry], hidden: [] });
  await expect(toggle).not.toBeChecked();
  await expect(toggle).toBeEnabled();

  await retry.click();
  await expect(toggle).toBeChecked();
  await walk.state("04-back-on", { visible: [toggle, detail], hidden: [refused] });

  unavailable = "Podman is not installed";
  await page.reload();
  await walk.state("05-unavailable", { visible: [page.getByRole("link", { name: /Prerequisites/ })], hidden: [toggle, detail, page.getByText(/Not available here/)] });
});
