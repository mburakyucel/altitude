import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { expect, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// A fresh computer in a disposable real service: only engine installation and execution are controlled. Creating a
// marker file installs the agent or the sandbox; the service runs its setup check as the daemon's tick does.
test.use({ serviceScript: "project-first-timer-service.py" });
test.setTimeout(60_000);
const setupPanel = (page: Page) => page.getByRole("dialog", { name: "Project setup" });
type FirstTimer = { agent: string; sandbox: string; faults: string };
const faults = (service: FirstTimer) => existsSync(service.faults) ? JSON.parse(readFileSync(service.faults, "utf8")) : {};
const step = (panel: Locator, label: string) => panel.getByRole("listitem").filter({ has: panel.page().getByRole("heading", { name: label, exact: true }) });

test("a fresh computer names one coding agent and the sandbox, and setup waits until a tool is ready, then continues by itself", async ({ page, request, service, altitude }, info) => {
  const fresh = altitude as unknown as FirstTimer;
  const walk = walkthrough(page, info);
  const firstRun = page.getByRole("region", { name: "First run" });
  await walk.open(`${service}/projects`);
  await firstRun.getByRole("button", { name: "Continue" }).click();
  const agents = firstRun.locator(".onboarding-check").filter({ hasText: "Install at least one coding agent" });
  const sandbox = firstRun.locator(".onboarding-check").filter({ hasText: "Agent sandbox" });
  const rail = page.getByRole("complementary");
  await walk.state("01-coding-tools-fresh", {
    visible: [agents.getByText(/^Install at least one coding agent: \w+ or \w+$/), agents.getByText("curl -fsSL https://claude.ai/install.sh | bash"),
      sandbox.getByText("sudo apt install bubblewrap"), firstRun.getByRole("button", { name: "Choose a project anyway" }),
      ...(info.project.name === "desktop" ? [rail.getByRole("button", { name: "1 folder found — add it" }), rail.getByText("not installed").first()] : [])],
    hidden: [firstRun.getByText(/not installed$/), firstRun.getByText("Needs attention").nth(2), rail.getByText("no reading")],
  });
  await firstRun.getByRole("button", { name: "Choose a project anyway" }).click();
  await firstRun.getByRole("listitem").filter({ hasText: "atlas" }).getByRole("button", { name: "Add project" }).click();
  const panel = setupPanel(page);
  const coordinator = step(panel, "Coordinator");
  const repository = step(panel, "Git repository");
  await walk.state("02-setup-waits-for-a-tool", {
    visible: [panel.getByRole("status").filter({ hasText: /^Needs attention$/ }), step(panel, "Git guards").getByText(/Installed and verified/),
      coordinator.getByText("Waiting", { exact: true }),
      coordinator.getByText("Waiting for a coding tool. Install Claude (Coding tools), and setup continues by itself."),
      coordinator.getByRole("link", { name: "Coding tools" }),
      repository.getByText("This repository isn't on GitHub yet. Agents deliver through pull requests:"),
      repository.getByText("gh repo create --source . --push"), page.getByText("L3 starts by itself once a coding tool is ready.")],
    hidden: [coordinator.getByText("Failed", { exact: true }), panel.getByText(/engine hold/), panel.getByText(/Discuss with L3/),
      page.getByRole("button", { name: "Start L3" })],
  });
  expect(faults(fresh), "a missing tool and an unpublished repository file no incident").toEqual({});
  expect((await (await request.get("/api/chat/atlas")).json()).history.filter((row: { role: string }) => row.role !== "user")).toEqual([]);
  writeFileSync(fresh.agent, "");
  await walk.state("03-setup-waits-for-the-sandbox", {
    action: () => page.reload(),
    visible: [coordinator.getByText("Waiting", { exact: true }),
      coordinator.getByText("Waiting for a coding tool. Install the agent sandbox (Coding tools), and setup continues by itself."),
      coordinator.getByRole("link", { name: "Coding tools" })],
    hidden: [coordinator.getByText(/Install Claude/), coordinator.getByText("Failed", { exact: true })],
  });
  writeFileSync(fresh.sandbox, "");
  const replies = async () => (await (await request.get("/api/chat/atlas")).json()).history.map((row: { text?: string }) => row.text);
  await expect.poll(replies, { message: "the next setup check starts the first conversation", timeout: 15_000 }).toContain("The project conversation is ready.");
  await walk.state("04-setup-continued-by-itself", {
    action: () => page.reload(),
    visible: [coordinator.getByText("First conversation ready."), repository.getByText("gh repo create --source . --push"),
      panel.getByRole("status").filter({ hasText: /^Needs attention$/ }),
      ...(info.project.name === "desktop" ? [rail.getByText("not installed")] : [])],
    hidden: [coordinator.getByText(/Waiting for a coding tool/), coordinator.getByRole("link", { name: "Coding tools" })],
  });
  expect(faults(fresh)).toEqual({});
});

test("Settings hides validation runs on a computer that cannot run them", async ({ page, service }, info) => {
  const walk = walkthrough(page, info);
  await walk.open(`${service}/settings`);
  await walk.state("05-settings-without-validation", {
    visible: [page.getByRole("link", { name: /Prerequisites/ })],
    hidden: [page.getByRole("switch", { name: /Validation runs/ }), page.getByText(/Not available here/)],
  });
});
