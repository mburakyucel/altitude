import { expect, type Route } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";
import { fixtureHost } from "./hostVoice";

// First run in a disposable service with nothing managed and an empty ~/Projects; fictional folders sit in ~/code.
// The service's GitHub CLI is signed out and its one installed engine is signed in.
test.use({ scenario: "folders" });

test("first run walks the name, prerequisites, declined incident reports, voice setup and projects, then Settings", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const host = await fixtureHost(page, { state: "absent", download_bytes: 698435338 });
  const firstRun = page.getByRole("region", { name: "First run" });
  const heading = (name: string) => firstRun.getByRole("heading", { name });
  const name = firstRun.getByLabel("Your name");

  await walk.open("/projects");
  await walk.state("01-welcome-name", {
    visible: [heading("Welcome to Altitude"), name, firstRun.getByRole("button", { name: "Skip" })],
    hidden: [firstRun.getByRole("button", { name: "‹ Back" })],
  });
  await name.fill("Ada Fixture");
  await firstRun.getByRole("button", { name: "Continue" }).click();

  const github = firstRun.locator(".onboarding-check").filter({ hasText: "GitHub CLI" });
  await walk.state("02-prerequisites-unmet", {
    visible: [heading("What the agents need"), github.getByText("gh auth login"), firstRun.getByRole("button", { name: "Check again" }), firstRun.getByRole("button", { name: "Continue anyway" })],
    hidden: [firstRun.getByRole("textbox"), name],
  });
  const met = (route: Route) => route.fetch().then(async (response) => {
    const { items } = await response.json() as { items: { state: string }[] };
    await route.fulfill({ json: { items: items.map((item) => ({ ...item, state: item.state === "unmet" ? "met" : item.state, command: null })) } });
  });
  await page.route("**/api/prerequisites", met, { times: 1 });
  await walk.state("03-prerequisites-met", {
    action: () => firstRun.getByRole("button", { name: "Check again" }).click(),
    visible: [firstRun.getByRole("button", { name: "Continue", exact: true })],
    hidden: [github.getByText("gh auth login"), firstRun.getByRole("button", { name: "Continue anyway" })],
  });
  await firstRun.getByRole("button", { name: "Continue", exact: true }).click();

  const keep = firstRun.getByRole("radio", { name: "Keep incidents on this computer" });
  const publish = firstRun.getByRole("radio", { name: "Also publish them as GitHub issues" });
  const repository = firstRun.getByLabel("Repository");
  await walk.state("04-incidents-kept-by-default", {
    visible: [heading("Report Altitude’s own faults?"), keep, firstRun.getByText(/What leaves this computer/)],
    hidden: [repository],
  });
  await expect(keep).toBeChecked();
  await page.route("**/api/incident-reports", (route) => route.fulfill({ status: 400, json: { error: "The signed-in GitHub CLI cannot see fork-fixture/missing. Check the name, or sign in with gh auth login." } }), { times: 1 });
  await publish.check();
  await expect(repository).toHaveValue(/\/altitude$/);
  await repository.fill("fork-fixture/missing");
  await walk.state("05-repository-refused", {
    action: () => firstRun.getByRole("button", { name: "Save and continue" }).click(),
    visible: [firstRun.getByRole("alert").getByText(/cannot see fork-fixture\/missing/), repository],
    hidden: [heading("Add your projects")],
  });
  await keep.check();
  await walk.state("06-incidents-declined", {
    visible: [firstRun.getByRole("button", { name: "Continue", exact: true })],
    hidden: [repository, firstRun.getByRole("alert")],
  });
  await firstRun.getByRole("button", { name: "Continue", exact: true }).click();

  const setUpVoice = firstRun.getByRole("button", { name: "Set up voice", exact: true });
  await walk.state("06a-voice-download-offered", {
    visible: [heading("Voice to text"), firstRun.getByText("Voice to text runs on this computer. It needs a one-time download of about 698 MB.", { exact: true }),
      setUpVoice, firstRun.getByRole("button", { name: "Use browser recognition instead", exact: true }), firstRun.getByRole("button", { name: "Skip" })],
    hidden: [keep],
  });
  await setUpVoice.click();
  expect(host.requests).toEqual(["setup"]);

  const browser = page.getByRole("region", { name: "Choose a folder" });
  await walk.state("07-projects-folder-empty", {
    visible: [heading("Add your projects"), firstRun.getByText("No folders in ~/Projects yet"), firstRun.getByRole("button", { name: "Change…" }), firstRun.getByRole("button", { name: "Choose a folder elsewhere…" })],
    hidden: [browser, firstRun.getByRole("button", { name: "Skip" })],
  });
  await firstRun.getByRole("button", { name: "Change…" }).click();
  await browser.getByRole("button", { name: /^code\b/ }).click();
  const row = (folder: string) => firstRun.getByRole("listitem").filter({ hasText: folder });
  await walk.state("08-projects-listed", {
    action: () => browser.getByRole("button", { name: "Use “code”" }).click(),
    visible: [firstRun.getByText("~/code", { exact: true }), firstRun.getByRole("button", { name: "Add all 3" }), row("atlas").getByRole("button", { name: "Add project" }), row("new-idea"), row("notes")],
    hidden: [browser, firstRun.getByText("No folders in ~/Projects yet")],
  });

  await page.route("**/api/project/add", (route) => route.fulfill({ status: 503, json: { error: "Registration unavailable. Try again." } }), { times: 1 });
  await walk.state("09-add-all-failed", {
    action: () => firstRun.getByRole("button", { name: "Add all 3" }).click(),
    visible: [firstRun.getByRole("alert").getByText("Could not add atlas: Registration unavailable. Try again."), firstRun.getByRole("button", { name: "Retry Add all" })],
    hidden: [firstRun.getByRole("button", { name: "Add all 3" })],
  });
  await walk.state("10-added-opens-setup", {
    action: () => firstRun.getByRole("button", { name: "Retry Add all" }).click(),
    visible: [page.getByRole("dialog", { name: "Project setup" })],
    hidden: [firstRun],
  });
  await expect(page).toHaveURL(/\/projects\/atlas\?setup=1$/);
  const overview = await (await request.get("/api/overview")).json() as { projects: { name: string; managed: boolean }[]; operator: string };
  expect(overview.projects.filter((project) => project.managed).map((project) => project.name).sort()).toEqual(["atlas", "new-idea", "notes"]);
  expect(overview.operator).toBe("Ada Fixture");

  await walk.open("/settings");
  const nameRow = page.getByRole("link", { name: /Your name/ });
  const reportsRow = page.getByRole("link", { name: /Incident reports/ });
  await walk.state("11-settings-machine-rows", {
    visible: [nameRow.getByText("Ada Fixture"), page.getByRole("link", { name: /Prerequisites/ }), reportsRow.getByText("Kept on this computer")],
    hidden: [firstRun],
  });
  await reportsRow.click();
  await walk.state("12-settings-incident-reports", {
    visible: [page.getByRole("radio", { name: "Keep incidents on this computer" }), page.getByRole("button", { name: "Save" })],
    hidden: [page.getByRole("button", { name: "Skip" })],
  });
  await page.getByRole("link", { name: "‹ Settings" }).click();
  await page.getByRole("link", { name: /Prerequisites/ }).click();
  await walk.state("13-settings-prerequisites", {
    visible: [page.getByText("gh auth login"), page.getByRole("button", { name: "Check again" })],
    hidden: [page.getByRole("button", { name: "Continue anyway" })],
  });
});

test("first run's voice step: browser recognition instead, or why this computer cannot run voice", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const firstRun = page.getByRole("region", { name: "First run" });
  const host = await fixtureHost(page, { state: "absent", download_bytes: 698435338 });
  await walk.open("/projects?step=voice");
  await walk.state("voice-01-browser-instead", {
    action: () => firstRun.getByRole("button", { name: "Use browser recognition instead", exact: true }).click(),
    visible: [firstRun.getByRole("heading", { name: "Add your projects" })],
    hidden: [firstRun.getByRole("heading", { name: "Voice to text" })],
  });
  expect(host.backend).toBe("browser");
  expect(host.requests).toEqual([]);

  host.backend = "browser";
  host.state = { state: "unavailable", reason: "voice runs on Linux x86_64 only for now" };
  await walk.state("voice-02-unavailable", {
    action: () => firstRun.getByRole("button", { name: "‹ Back" }).click(),
    visible: [firstRun.getByText("Voice to text can’t run on this computer: voice runs on Linux x86_64 only for now.", { exact: true }),
      firstRun.getByRole("button", { name: "Continue", exact: true })],
    hidden: [firstRun.getByRole("button", { name: "Set up voice" }), firstRun.getByRole("button", { name: /Use browser recognition/ }), firstRun.getByRole("button", { name: "Skip" })],
  });
});
