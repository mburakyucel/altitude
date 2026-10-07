import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

test("project actions appear and disappear after cancel and Escape (issue #195)", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const more = page.getByRole("button", { name: "More actions" });
  const menu = page.getByRole("menu", { name: "Project actions" });
  const settings = menu.getByRole("menuitem", { name: "Project settings…", exact: true });
  const setup = menu.getByRole("menuitem", { name: /^Setup: / });
  const reset = menu.getByRole("menuitem", { name: "Reset L3 conversation…", exact: true });
  const all = menu.getByRole("menuitem", { name: "All settings…", exact: true });
  const confirmation = page.getByRole("group", { name: "Reset the L3 conversation?", exact: true });
  await walk.open(project.path);
  await walk.state("01-closed", { visible: [more], hidden: [menu, confirmation] });
  await walk.state("02-open", {
    action: () => more.click(), visible: [menu, settings, setup, reset, all], hidden: [confirmation],
  });
  await expect(settings).toBeFocused();
  // Removal lives on the project's settings page, behind its own dialog (project-lifecycle.pw.ts).
  await expect(menu.getByRole("menuitem")).toHaveText([/^Project settings…$/, /^Setup…/, /^Reset L3 conversation…$/, /^All settings…$/]);
  await expect(menu.getByText(/Remove/)).toHaveCount(0);
  await walk.state("03-confirmation", {
    action: () => reset.click(), visible: [confirmation, confirmation.getByRole("button", { name: "Reset", exact: true })], hidden: [reset],
  });
  await walk.state("04-cancelled", {
    action: () => page.getByRole("button", { name: "Cancel", exact: true }).click(),
    visible: [menu, reset], hidden: [confirmation],
  });
  await expect(reset).toBeFocused();
  await walk.state("05-dismissed", {
    action: () => page.keyboard.press("Escape"), visible: [more], hidden: [menu, confirmation],
  });
  await expect(more).toBeFocused();
  await walk.state("06-project-settings", {
    action: async () => { await more.click(); await settings.click(); },
    visible: [page.getByRole("main").getByRole("heading", { name: project.name, exact: true })], hidden: [menu],
  });
  await expect(page).toHaveURL(new RegExp(`/settings/projects/${project.name}$`));
});
