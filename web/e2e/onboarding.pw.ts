import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";
import { fixtureHost } from "./hostVoice";

// First run in a disposable real service; external sign-in and voice use bounded deterministic fixtures.
test.use({ scenario: "folders" });

test("first run establishes the host, checks real prerequisites and registers repositories before optional preferences", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  const firstRun = page.getByRole("region", { name: "First run" });
  const heading = (name: string) => firstRun.getByRole("heading", { name });
  await walk.open("/projects");
  await walk.state("01-welcome-host", {
    visible: [heading("Welcome to Altitude"), firstRun.getByText("Atlas host"), firstRun.getByText("Only the hosting computer"), firstRun.getByRole("button", { name: "Continue" })],
    hidden: [firstRun.getByLabel("Your name"), firstRun.getByRole("button", { name: "Skip" })],
  });
  await firstRun.getByText("Optional settings", { exact: true }).click();
  await walk.state("02-optional-preferences", {
    visible: [firstRun.getByRole("link", { name: "Your name" }), firstRun.getByRole("link", { name: "Incident reports" }), firstRun.getByRole("link", { name: "Voice input" })],
    hidden: [firstRun.getByRole("textbox")],
  });
  await firstRun.getByRole("button", { name: "Continue" }).click();
  const github = firstRun.locator(".onboarding-check").filter({ hasText: "GitHub CLI" });
  await walk.state("03-tools-unmet", {
    visible: [heading("What the agents need"), github.getByText("gh auth login"), github.getByText("Needs attention"), firstRun.getByRole("button", { name: "Choose a project anyway" })],
    hidden: [firstRun.getByRole("button", { name: "Continue", exact: true })],
  });
  // Control only the external sign-in seam. The application reads and reduces its actual prerequisite checks.
  expect((await request.post(`${service}/fixture/sign-in`)).ok()).toBe(true);
  await walk.state("04-tools-met", {
    action: () => firstRun.getByRole("button", { name: "Check again" }).click(),
    visible: [github.getByText("Ready"), firstRun.getByRole("button", { name: "Continue", exact: true })],
    hidden: [github.getByText("gh auth login"), firstRun.getByRole("button", { name: "Choose a project anyway" })],
  });
  await firstRun.getByRole("button", { name: "Continue", exact: true }).click();
  await walk.state("05-project-empty", {
    visible: [heading("Add a project"), firstRun.getByText("No folders in ~/Projects yet"), firstRun.getByRole("button", { name: "Change…" }), firstRun.getByText(/Setup may start the coordinator’s first introduction/)],
    hidden: [firstRun.getByRole("button", { name: "Skip" })],
  });
  await expect(firstRun.getByRole("list", { name: "Step 3 of 3" }).locator(".done")).toHaveCount(0);
  const browser = page.getByRole("region", { name: "Choose a folder" });
  await firstRun.getByRole("button", { name: "Change…" }).click();
  await browser.getByRole("button", { name: /^code\b/ }).click();
  const row = (folder: string) => firstRun.getByRole("listitem").filter({ hasText: folder });
  await walk.state("06-projects-listed", {
    action: () => browser.getByRole("button", { name: "Use “code”" }).click(),
    visible: [firstRun.getByText("~/code", { exact: true }), firstRun.getByRole("button", { name: "Add all 3" }), row("atlas").getByRole("button", { name: "Add project" })],
    hidden: [browser],
  });
  await page.route("**/api/project/add", (route) => route.fulfill({ status: 503, json: { error: "Registration unavailable. Try again." } }), { times: 1 });
  await walk.state("07-add-all-failed", {
    action: () => firstRun.getByRole("button", { name: "Add all 3" }).click(),
    visible: [firstRun.getByRole("alert").getByText("Could not add atlas: Registration unavailable. Try again."), firstRun.getByRole("button", { name: "Retry Add all" })],
    hidden: [page.getByRole("dialog", { name: "Project setup" })],
  });
  let release = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/project/add", async (route) => { await held; await route.continue(); }, { times: 1 });
  await firstRun.getByRole("button", { name: "Retry Add all" }).click();
  await walk.state("08-registration-pending", { visible: [firstRun.getByText("Adding 1 of 3…")], hidden: [page.getByRole("dialog", { name: "Project setup" })] });
  await expect(firstRun.getByRole("button", { name: "‹ Back" })).toBeDisabled();
  release();
  await walk.state("09-registration-opens-setup", { visible: [page.getByRole("dialog", { name: "Project setup" })], hidden: [firstRun] });
  const overview = await (await request.get("/api/overview")).json() as { projects: { name: string; managed: boolean }[] };
  expect(overview.projects.filter((project) => project.managed).map((project) => project.name).sort()).toEqual(["atlas", "new-idea", "notes"]);

  await walk.open("/settings/name");
  const name = page.getByLabel("Your name");
  await name.fill("Ada Fixture");
  await page.getByRole("button", { name: "Save" }).click();
  await walk.state("10-optional-name-saved-later", { visible: [page.getByRole("status", { name: "Saved." }), name], hidden: [firstRun] });
  await walk.open("/settings/incident-reports");
  const keep = page.getByRole("radio", { name: "Keep incidents on this computer" });
  const publish = page.getByRole("radio", { name: "Also publish them as GitHub issues" });
  await walk.state("11-reporting-default-privacy", { visible: [keep, page.getByText(/What leaves this computer/)], hidden: [page.getByLabel("Repository")] });
  await expect(keep).toBeChecked();
  await page.route("**/api/incident-reports", (route) => route.fulfill({ status: 400, json: { error: "The signed-in GitHub CLI cannot see fork-fixture/missing." } }), { times: 1 });
  await publish.check();
  await page.getByLabel("Repository").fill("fork-fixture/missing");
  await walk.state("12-reporting-refused", { action: () => page.getByRole("button", { name: "Save" }).click(), visible: [page.getByRole("alert")], hidden: [] });
  await keep.check();
  await walk.state("13-reporting-declined", { visible: [keep, page.getByRole("button", { name: "Save" })], hidden: [page.getByLabel("Repository"), page.getByRole("alert")] });
});

test("address reads show loading and errors, retry the actual host, and explain optional private networking", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  let release = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  const fail = async (route: import("@playwright/test").Route) => { await held; await route.fulfill({ status: 503, json: { error: "Address temporarily unavailable" } }); };
  await page.route("**/api/machine", fail);
  await walk.open("/settings/network");
  await walk.state("network-01-loading", { visible: [page.getByRole("status", { name: "Reading service address…" })], hidden: [page.getByText("Atlas host")] });
  release();
  await expect(page.getByText("Could not read service address.")).toBeVisible({ timeout: 15_000 });
  await walk.state("network-02-unreadable", { visible: [page.getByText("Could not read service address."), page.getByRole("button", { name: "Retry" })], hidden: [page.getByText("Atlas host")] });
  await page.unroute("**/api/machine", fail);
  await walk.state("network-03-host-restored", { action: () => page.getByRole("button", { name: "Retry" }).click(), visible: [page.getByText("Atlas host"), page.getByText("Only the hosting computer"), page.getByText(/Localhost on a phone means the phone/)], hidden: [page.getByText("Could not read service address.")] });
  await page.getByText("Use another device…", { exact: true }).click();
  await walk.state("network-04-source-guidance", { visible: [page.getByText(/For a source deployment, configure/), page.getByText(/A VPN changes neither Altitude’s address, certificate trust nor pairing/)], hidden: [] });
});

test("optional voice setup remains in Settings with its current privacy, download and unavailable explanations", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const host = await fixtureHost(page, { state: "absent", download_bytes: 698435338 });
  await walk.open("/settings/voice");
  const setUp = page.getByRole("button", { name: "Set up voice", exact: true });
  await walk.state("voice-01-download-choice", { visible: [setUp, page.getByText(/698 MB/)], hidden: [page.getByRole("button", { name: "Skip" })] });
  await setUp.click();
  await expect.poll(() => host.requests).toEqual(["setup"]);
  await walk.state("voice-02-setting-up", { visible: [page.getByText(/Setting up/, { exact: false }).first()], hidden: [setUp] });
  host.state = { state: "unavailable", reason: "Fictional capability unavailable" };
  await page.reload();
  await walk.state("voice-03-unavailable", { visible: [page.getByText(/Fictional capability unavailable/)], hidden: [setUp] });
});
