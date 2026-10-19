import { expect, type Page, type Route } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/*
 * Altitude's own Claude sign-in from Settings › Prerequisites (SPEC.md §3.12): the real service runs a fixture
 * setup screen (tests/fake_token_setup.py) on a terminal of its own and reads its token, which never reaches the page.
 * Each state is walked at phone and desktop width; Opening and Signing in are held at the network for their capture.
 */

test.use({ serviceScript: "sign-in-service.py" });

const LINK = (n: number) => `https://claude.com/cai/oauth/authorize?code=true&client_id=fixture&state=fixture-${n}&code_challenge=x${n}`;
const CODE = "fixture-code#fixture-state";
const TOKEN_TAIL = "fixtureToken_0123456789";
const EARLIER = " Your earlier sign-in still works.";

/** Hold the next sign-in request with this action until the returned release is called. */
async function hold(page: Page, action: string) {
  let release!: () => void;
  const released = new Promise<void>((resolve) => { release = resolve; });
  let held = false;
  await page.route("**/api/sign-in/*", async (route: Route) => {
    if (!held && route.request().method() === "POST" && route.request().postDataJSON()?.action === action) {
      held = true;
      await released;
    }
    await route.fallback();
  });
  return release;
}

test("the operator signs Altitude in to Claude from the page without seeing a token", async ({ page, request, service }, info) => {
  test.setTimeout(120_000);
  const walk = walkthrough(page, info);
  const row = page.locator(".onboarding-check").filter({ has: page.locator("strong", { hasText: /^Claude (not )?signed in$/ }) });
  const v = {
    signIn: row.getByRole("button", { name: "Sign in", exact: true }),
    again: row.getByRole("button", { name: "Sign in again", exact: true }),
    opening: row.getByRole("button", { name: "Opening Claude sign-in…" }),
    link: row.getByRole("link", { name: "Open Claude sign-in", exact: true }),
    field: row.getByRole("textbox", { name: "Paste the code Claude shows" }),
    finish: row.getByRole("button", { name: "Finish sign-in", exact: true }),
    signingIn: row.getByRole("button", { name: "Signing in…" }),
    cancel: row.getByRole("button", { name: "Cancel", exact: true }),
    tryAgain: row.getByRole("button", { name: "Try again", exact: true }),
    alert: (text: string) => row.getByRole("alert").filter({ hasText: text }),
    done: row.getByText("Signed in. Altitude's Claude runs use this sign-in for a year.", { exact: true }),
    command: row.getByText("alt sign-in claude"),
  };
  const noToken = async () => expect(await page.content()).not.toContain(TOKEN_TAIL);

  await page.route("**/api/sign-in/*", (route) => route.fulfill({ status: 500, json: { error: "fixture" } }));
  await walk.open("/settings/prerequisites");
  await walk.state("00-sign-in-unreadable", {
    visible: [row.getByText("Claude not signed in", { exact: true }), row.getByRole("alert").filter({ hasText: "Could not check the sign-in." })],
    hidden: [v.signIn, v.command],
  });
  await page.unroute("**/api/sign-in/*");
  await walk.state("01-signed-out", {
    action: () => row.getByRole("button", { name: "Retry" }).click(),
    visible: [row.getByText("Claude not signed in", { exact: true }), row.getByText("Needs attention"), v.signIn,
      page.getByText("Run any missing command in a terminal there, or use Sign in, then check again. Altitude never asks for your password or a token.", { exact: false })],
    hidden: [v.command, v.field, v.link],
  });
  if (info.project.name === "phone") {
    const [button, block] = await Promise.all([v.signIn.boundingBox(), row.locator(".sign-in").boundingBox()]);
    expect(button!.width).toBeCloseTo(block!.width, 0);
  }

  let release = await hold(page, "start");
  await walk.state("02-opening", {
    action: () => v.signIn.click(),
    visible: [v.opening],
    hidden: [v.signIn, v.field],
  });
  await expect(v.opening).toBeDisabled();
  release();
  await walk.state("03-waiting", {
    visible: [v.link, v.field, v.cancel, v.finish, row.getByText("Approve Altitude on Claude's page."),
      row.getByText("The code works once, for this sign-in only.")],
    hidden: [v.opening, v.signIn],
  });
  await expect(v.link).toHaveAttribute("href", LINK(1));
  await expect(v.link).toHaveAttribute("target", "_blank");
  await expect(v.finish).toBeDisabled();
  await page.reload();
  await walk.state("03c-waiting-after-reload", {
    visible: [v.field, v.link],
    hidden: [v.signIn],
  });
  await expect(v.link).toHaveAttribute("href", LINK(1));
  await walk.state("03b-waiting-pasted", {
    action: () => v.field.fill("fixture-wrong#code"),
    visible: [v.finish],
    hidden: [v.signingIn],
  });
  await expect(v.finish).toBeEnabled();
  await walk.state("05-code-rejected", {
    action: () => v.finish.click(),
    visible: [v.alert("Claude didn't accept that code."), v.tryAgain],
    hidden: [v.field, v.link, v.finish],
  });
  await expect(v.alert(EARLIER.trim())).toHaveCount(0);

  await v.tryAgain.click();
  await v.field.fill(CODE);
  release = await hold(page, "finish");
  await walk.state("04-finishing", {
    action: () => v.field.press("Enter"),
    visible: [v.signingIn],
    hidden: [v.finish],
  });
  await expect(v.field).toBeDisabled();
  await expect(v.cancel).toBeDisabled();
  release();
  await walk.state("07-signed-in", {
    visible: [v.done, row.getByText("Claude signed in", { exact: true }), row.getByText("Ready")],
    hidden: [v.field, v.signingIn, v.again, row.getByRole("alert")],
  });
  await noToken();
  await page.reload();
  await walk.state("08-signed-in-later", {
    visible: [v.again, row.getByText("Claude signed in", { exact: true })],
    hidden: [v.done, v.signIn],
  });

  await v.again.click();
  await walk.state("09-second-sign-in-failed", {
    action: async () => { await v.field.fill("fixture-mode:two"); await v.finish.click(); },
    visible: [v.alert("Altitude couldn't follow Claude's sign-in. If this repeats, Altitude needs an update for this Claude version." + EARLIER), v.tryAgain],
    hidden: [v.field],
  });
  await noToken();

  await v.tryAgain.click();
  const releaseCancel = await hold(page, "cancel");
  await walk.state("09b-cancelling", {
    action: () => v.cancel.click(),
    visible: [row.getByRole("button", { name: "Cancelling…" }), v.finish],
    hidden: [v.signingIn],
  });
  await walk.state("10-cancelled", {
    action: () => releaseCancel(),
    visible: [v.again],
    hidden: [v.field, v.link, row.getByRole("alert")],
  });

  await v.again.click();
  await expect(v.field).toBeVisible();
  // Another paired browser cancels it: this page's sign-in is gone and nothing new was saved.
  expect((await request.post(`${service}/api/sign-in/claude`, { data: { action: "cancel" } })).ok()).toBe(true);
  await walk.state("06b-interrupted", {
    action: () => page.evaluate(() => window.dispatchEvent(new Event("focus"))),
    visible: [v.alert("The sign-in was interrupted." + EARLIER), v.tryAgain],
    hidden: [v.field],
  });

  // Another browser starts a sign-in of its own while this page holds a code: the code is not sent to it.
  await v.tryAgain.click();
  await v.field.fill(CODE);
  expect((await request.post(`${service}/api/sign-in/claude`, { data: { action: "start" } })).ok()).toBe(true);
  await walk.state("06d-replaced-elsewhere", {
    action: () => page.evaluate(() => window.dispatchEvent(new Event("focus"))),
    visible: [v.alert("The sign-in was interrupted." + EARLIER), v.tryAgain],
    hidden: [v.field, v.finish],
  });

  await v.tryAgain.click();
  await expect(v.field).toBeVisible();
  await expect(v.field).toHaveValue("");
  await expect(v.alert("The sign-in expired after ten minutes.")).toBeVisible({ timeout: 30_000 });
  await walk.state("06-expired", {
    visible: [v.alert("The sign-in expired after ten minutes." + EARLIER), v.tryAgain],
    hidden: [v.field, v.link],
  });
  await noToken();
});
