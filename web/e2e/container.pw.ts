import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "container-service.py" });

test("container replacement explains queued work and clears only after host continuation", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const notice = page.getByRole("status", { name: "Container work paused" });
  await walk.open("/settings");
  await walk.state("01-admitted", { visible: [page.getByText("Terminal unavailable", { exact: true })], hidden: [notice] });
  await request.post("/fixture/replace");
  await page.reload();
  await walk.state("02-replacement-paused", { visible: [notice, page.getByText(/Messages and task requests stay queued/),
    page.getByText(/After checking recovery, run on the host/)], hidden: [notice.getByRole("button")] });
  await request.post("/fixture/continue");
  await page.reload();
  await walk.state("03-host-continued", { visible: [page.getByText("Terminal unavailable", { exact: true })], hidden: [notice] });
  await request.post("/fixture/identity-unavailable");
  await page.reload();
  await walk.state("04-identity-unavailable", { visible: [notice, page.getByText(/Repair image startup before continuing/)],
    hidden: [page.getByText(/After checking recovery, run on the host/)] });
});

test("container settings explain unavailable actions and container-only setup", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  await walk.open("/settings");
  await walk.state("01-image-managed", {
    visible: [page.getByText("Terminal unavailable", { exact: true }), page.getByText(/This container is image-managed/)],
    hidden: [page.getByRole("switch", { name: /^Terminal/ }), page.getByRole("switch", { name: /Check for new versions/ })],
  });
  await page.getByText(/This container is image-managed/).scrollIntoViewIfNeeded();
  await walk.state("01b-version-guidance", { visible: [page.getByText(/This container is image-managed/)],
    hidden: [page.getByRole("switch", { name: /Check for new versions/ })] });
  for (const route of ["/api/update", "/api/update-check", "/api/terminal-access", "/api/voice/host"]) {
    const response = await request.post(route, { data: { enabled: true, action: "setup" } });
    expect(response.status()).toBe(403);
    expect((await response.json()).error).toMatch(/container|image-managed/);
  }
  await walk.open("/settings/voice");
  const host = page.getByRole("radio", { name: "This computer", exact: true });
  await expect(host).toBeDisabled();
  await walk.state("02-host-voice-unavailable", {
    visible: [host, page.getByText(/Host voice is unavailable in this container/)],
    hidden: [page.getByRole("button", { name: "Set up voice" }), page.getByRole("button", { name: /Remove voice/ })],
  });
  await walk.open("/settings/prerequisites");
  await walk.state("03-tools-inside-container", {
    visible: [page.getByText(/Host tools and sign-ins do not count/), page.getByText(/Run on the host to open the container shell/),
      page.getByRole("button", { name: "Check again", exact: true })],
    hidden: [page.getByRole("button", { name: "Set up voice" })],
  });
  await page.getByRole("button", { name: "Check again", exact: true }).click();
  await expect(page.getByRole("button", { name: "Check again", exact: true })).toBeEnabled();
  await walk.open("/settings/projects-folder");
  await walk.state("04-empty-project-volume", { visible: [page.getByText("Folders in the container projects volume", { exact: true })], hidden: [page.getByRole("alert")] });
  await page.getByRole("button", { name: "Type a path instead" }).click();
  await page.getByLabel("A folder in the container projects volume").fill("/outside-container-projects");
  await page.getByRole("button", { name: "Use", exact: true }).click();
  await walk.state("05-outside-path-denied", { visible: [page.getByRole("alert").filter({ hasText: /container.*Projects volume/ }), page.getByRole("button", { name: "Retry", exact: true })], hidden: [page.getByRole("status")] });
  await page.reload();
  await walk.state("06-reloaded-volume", { visible: [page.getByText("Folders in the container projects volume", { exact: true })],
    hidden: [page.getByRole("alert")] });
});
