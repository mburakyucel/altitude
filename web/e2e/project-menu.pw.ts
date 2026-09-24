import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

test("project actions appear and disappear after cancel and Escape (issue #195)", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const walk = walkthrough(page, info);
  const more = page.getByRole("button", { name: "More actions" });
  const menu = page.getByRole("menu", { name: "Project actions" });
  const reset = page.getByRole("menuitem", { name: "Reset L3 conversation", exact: true });
  const confirmation = page.getByRole("group", { name: "Reset the L3 conversation?", exact: true });
  await walk.open(project.path);
  await walk.state("01-closed", { visible: [more], hidden: [menu, confirmation] });
  await walk.state("02-open", {
    action: () => more.click(), visible: [menu, reset], hidden: [confirmation],
  });
  await expect(menu.getByRole("menuitem", { name: "Settings…", exact: true })).toBeFocused();
  await walk.state("03-confirmation", {
    action: () => reset.click(), visible: [confirmation], hidden: [reset],
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
});
