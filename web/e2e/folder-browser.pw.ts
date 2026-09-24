import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// Real listing, registration and settings in a disposable service whose home holds fictional folders.
test.use({ scenario: "folders" });

test("the folder browser lists the computer running Altitude and adds a folder, then the projects folder changes", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const firstRun = page.getByRole("region", { name: "First run" });
  const browser = page.getByRole("region", { name: "Choose a folder" });
  const row = (name: string) => browser.getByRole("button", { name: new RegExp(`^${name}\\b`) });
  let releaseListing!: () => void;
  const listingHeld = new Promise<void>((resolve) => { releaseListing = resolve; });
  await page.route("**/api/folders", async (route) => { await listingHeld; await route.continue(); }, { times: 1 });

  await walk.open("/projects");
  await walk.state("01-empty-projects-folder-loading", {
    visible: [firstRun.getByText("No folders in ~/Projects yet"), firstRun.getByRole("link", { name: "change the projects folder in Settings" }), browser],
    hidden: [row("code"), browser.getByRole("button", { name: "Cancel" })],
  });
  await walk.state("02-home-listing", {
    action: async () => releaseListing(),
    visible: [row("code"), row("locked"), row("Projects"), browser.getByRole("button", { name: "Add “Home”" })],
    hidden: [browser.getByText(".config"), browser.getByText("todo.md")],
  });
  await expect(browser.getByRole("button", { name: "Add “Home”" })).toBeDisabled();

  await walk.state("03-unreadable-folder", {
    action: () => row("locked").click(),
    visible: [browser.getByText(/your account can’t read it/), browser.getByRole("button", { name: "Add “locked”" })],
    hidden: [row("code")],
  });
  await expect(browser.getByRole("button", { name: "Add “locked”" })).toBeDisabled();

  const failOnce = (route: import("@playwright/test").Route) => route.fulfill({ status: 503, json: { error: "Could not list this folder." } });
  await page.route("**/api/folders?path=*code", failOnce, { times: 1 });
  await browser.getByRole("button", { name: "Home", exact: true }).click();
  await walk.state("04-listing-failed", {
    action: () => row("code").click(),
    visible: [browser.getByRole("alert").getByText("Could not list this folder."), browser.getByRole("button", { name: "Retry" })],
    hidden: [row("atlas")],
  });
  await walk.state("05-folder-with-git-tag", {
    action: () => browser.getByRole("button", { name: "Retry" }).click(),
    visible: [row("atlas").getByText("git"), row("new-idea"), row("notes")],
    hidden: [browser.getByRole("alert")],
  });
  await walk.state("06-empty-folder", {
    action: () => row("new-idea").click(),
    visible: [browser.getByText(/No folders inside/), browser.getByRole("button", { name: "Add “new-idea”" })],
    hidden: [row("atlas")],
  });
  await walk.state("07-typed-path-fallback", {
    action: () => browser.getByRole("button", { name: "Type a path instead" }).click(),
    visible: [page.getByLabel("A folder on the computer running Altitude"), page.getByRole("button", { name: "Browse folders instead" })],
    hidden: [browser],
  });
  await page.getByRole("button", { name: "Browse folders instead" }).click();
  await browser.getByRole("button", { name: "code", exact: true }).click();
  await row("atlas").click();

  let releaseAdd!: () => void;
  const addHeld = new Promise<void>((resolve) => { releaseAdd = resolve; });
  await page.route("**/api/project/add", async (route) => { await addHeld; await route.continue(); }, { times: 1 });
  await walk.state("08-adding", {
    action: () => browser.getByRole("button", { name: "Add “atlas”" }).click(),
    visible: [browser.getByText("Adding project…")],
    hidden: [browser.getByRole("button", { name: "Add “atlas”" })],
  });
  await walk.state("09-added-opens-setup", {
    action: async () => releaseAdd(),
    visible: [page.getByRole("dialog", { name: "Project setup" })],
    hidden: [browser],
  });
  await expect(page).toHaveURL(/\/projects\/atlas\?setup=1$/);

  await walk.open("/settings");
  const folderRow = page.getByRole("link", { name: /Projects folder ~\/Projects/ });
  await walk.state("10-settings-projects-folder-row", { visible: [folderRow], hidden: [browser] });
  await folderRow.click();
  await row("code").click();
  await walk.state("11-projects-folder-saved", {
    action: () => browser.getByRole("button", { name: "Use “code”" }).click(),
    visible: [page.getByText("Saved. First run now lists the folders in ~/code.")],
    hidden: [page.getByRole("alert")],
  });

  await walk.open("/projects/atlas");
  if (info.project.name === "phone") {
    await page.getByRole("button", { name: "atlas", exact: true }).click();
  } else await page.getByRole("button", { name: "Add a folder" }).click();
  await walk.state("12-first-run-reads-the-new-folder", {
    visible: [firstRun.getByText("Altitude found 2 folders in ~/code"), firstRun.getByText("new-idea", { exact: true }), firstRun.getByText("notes", { exact: true })],
    hidden: [browser],
  });
});
