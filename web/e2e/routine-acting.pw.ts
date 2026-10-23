import { expect, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Routines that change worktrees and branches (design/wireframes/project-agents/ACTING.md, "Increment 1 in detail" and
 * "States") through the real handlers, records and Git of routine-acting-service.py: the permission on the list, the
 * routine page and the form, the built-in's Turn on sheet, a run's What it changed with Undo and Undo all, a refused
 * request of a routine that only reports, and Altitude's sentence in chat.
 */
test.use({ serviceScript: "routine-acting-service.py" });

const project = "/projects/harbor";
const PUT_BACK = /^Put back \w+ \d+\. This routine leaves it alone from now on\.$/;

function setup(page: Page, phone: boolean) {
  const press = (target: Locator) => (phone ? target.tap() : target.click());
  const fits = async () => {
    if (phone) expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no horizontal overflow").toBe(true);
  };
  return { press, fits };
}

async function openAgent(page: Page, press: (target: Locator) => Promise<unknown>, name: string) {
  await page.goto(`${project}/agents`);
  await press(page.getByRole("link", { name: new RegExp(`^${name}`) }));
  await expect(page.getByRole("heading", { level: 1, name, exact: true })).toBeVisible();
}

test("the permission on the list, the routine page and the form, and the built-in's Turn on sheet", async ({ page }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const { press, fits } = setup(page, info.project.name === "phone");

  await walk.open(`${project}/agents`);
  const builtin = page.locator(".agents-builtin-row").filter({ hasText: "Tidy worktrees and branches" });
  await walk.state("a01-list", {
    visible: [page.getByRole("link", { name: /^Clear old worktrees Mondays at 09:00 · Changes worktrees and branches · Found something/ }),
      page.getByRole("link", { name: /^Dependency check Mondays at 09:00 · Nothing found/ }),
      builtin.getByText("Mondays at 09:00 · Clears worktrees and branches nothing needs, with Undo", { exact: true })],
    hidden: [page.getByRole("link", { name: /^Dependency check.*Changes worktrees/ })],
  });
  await fits();

  // Turn on asks first: Cancel is focused and changes nothing.
  await press(builtin.getByRole("button", { name: "Turn on" }));
  const sheet = page.getByRole("dialog", { name: "Turn on Tidy worktrees and branches for harbor?" });
  await walk.state("a02-builtin-sheet", {
    visible: [sheet.getByText(/^Mondays at 09:00 it sets aside worktrees and deletes local branches of harbor that nothing needs: no uncommitted change, nothing changed in seven days, every commit kept on a branch or merged, and nothing open in them\. Each is listed on its run's page with Undo\. It never touches main, open tasks or GitHub\.$/),
      sheet.getByRole("button", { name: "Turn on" })],
    hidden: [],
  });
  await expect(sheet.getByRole("button", { name: "Cancel" })).toBeFocused();
  await press(sheet.getByRole("button", { name: "Cancel" }));
  await expect(sheet).toBeHidden();
  await expect(builtin).toBeVisible();

  await press(builtin.getByRole("button", { name: "Turn on" }));
  await press(sheet.getByRole("button", { name: "Turn on" }));
  await walk.state("a03-builtin-on", {
    visible: [page.getByRole("link", { name: /^Tidy worktrees and branches Mondays at 09:00 · Changes worktrees and branches/ })],
    hidden: [builtin, sheet],
  });

  // The routine page says what it may change, or that it only reports.
  await openAgent(page, press, "Dependency check");
  await walk.state("a04-only-reports", { visible: [page.getByText("Only reports", { exact: true })], hidden: [page.getByText(/^May change/)] });
  await openAgent(page, press, "Clear old worktrees");
  await walk.state("a05-may-change", {
    visible: [page.getByText("May change worktrees and branches, with Undo", { exact: true })],
    hidden: [page.getByText("Only reports", { exact: true })],
  });

  // The form: ticked, its line alone; unticked, it says it only reports; the save shows on the routine page.
  await press(page.getByRole("link", { name: "Edit" }));
  const may = page.getByRole("checkbox", { name: "Worktrees and branches" });
  await walk.state("a06-form-ticked", {
    visible: [may, page.getByText(/^Sets aside worktrees and deletes branches nothing needs/)],
    hidden: [page.getByText("Unticked, it only reports.", { exact: true })],
  });
  await expect(may).toBeChecked();
  await fits();
  await press(may);
  await walk.state("a07-form-unticked", { visible: [page.getByText("Unticked, it only reports.", { exact: true })], hidden: [] });
  await expect(may).not.toBeChecked();
  await press(page.getByRole("button", { name: "Save" }));
  await expect(page.getByRole("heading", { level: 1, name: "Clear old worktrees", exact: true })).toBeVisible();
  await walk.state("a08-saved-only-reports", {
    visible: [page.getByText("Only reports", { exact: true })],
    hidden: [page.getByText("May change worktrees and branches, with Undo", { exact: true })],
  });
});

test("a run's What it changed: each request in order, Undo, Undo all, a refused request, and the chat sentence", async ({ page }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const { press, fits } = setup(page, info.project.name === "phone");

  // In chat, Altitude's own sentence follows the run's summary.
  await walk.open(project);
  await walk.state("b01-chat", {
    visible: [page.locator(".agent-line").filter({ hasText: "Clear old worktrees found something: 2 worktrees and 1 branch nothing needs. Set aside 2 worktrees and deleted 1 branch." })],
    hidden: [],
  });

  await openAgent(page, press, "Clear old worktrees");
  await press(page.getByRole("link", { name: /^Found something.*2 worktrees and 1 branch nothing needs\. Set aside 2 worktrees and deleted 1 branch\.$/ }));
  const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "What it changed" }) });
  const row = (text: string) => card.locator(".agent-change").filter({ hasText: text });
  const undoAll = card.getByRole("button", { name: "Undo all" });
  await walk.state("b02-run-changes", {
    visible: [undoAll,
      row("Set aside worktree agent-7c41e2d0").getByText(/^Removed for good .+ unless you undo it\.$/),
      row("Set aside worktree agent-91d0c3f2").getByRole("button", { name: "Undo: Set aside worktree agent-91d0c3f2" }),
      row("Deleted branch upload-retry-fix").getByText("Merged into main.", { exact: true }),
      row("Kept worktree add-offline-sync-for…ile-app-and-tablets").getByText("It has uncommitted changes.", { exact: true }),
      row("Kept branch retry-limit-tests").getByText("It has 1 commit found nowhere else.", { exact: true })],
    hidden: [row("Kept worktree").getByRole("button"), row("Kept branch").getByRole("button")],
  });
  await expect(card.locator(".agent-change")).toHaveCount(5);
  await expect(card.getByTitle("add-offline-sync-for-the-mobile-app-and-tablets")).toHaveCount(1);
  await fits();

  // Undo puts one back; the row says so and loses its button, and Undo all stays for the two left.
  const first = row("Set aside worktree agent-7c41e2d0");
  await press(first.getByRole("button", { name: "Undo: Set aside worktree agent-7c41e2d0" }));
  await walk.state("b03-undone", {
    visible: [first.getByText(PUT_BACK), first.getByText(/ · Undone$/), undoAll],
    hidden: [first.getByRole("button")],
  });

  // Undo all puts back every row that can be; then no Undo is left.
  await press(undoAll);
  await walk.state("b04-undo-all", {
    visible: [row("agent-91d0c3f2").getByText(PUT_BACK), row("upload-retry-fix").getByText(PUT_BACK)],
    hidden: [undoAll, card.getByRole("button")],
  });
  await fits();

  // A routine that only reports was refused with a plain reason, and nothing it asked for changed.
  await openAgent(page, press, "Dependency check");
  await press(page.getByRole("link", { name: /^Nothing found/ }));
  const refused = page.locator(".agent-change").filter({ hasText: "Not allowed: delete branch retry-limit-tests" });
  await walk.state("b05-not-allowed", {
    visible: [refused.getByText("This routine only reports.", { exact: true })],
    hidden: [refused.getByRole("button"), page.getByRole("button", { name: "Undo all" })],
  });
});
