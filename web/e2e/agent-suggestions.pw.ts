import { expect, type Locator, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * Suggested tasks from agent runs (design/wireframes/project-agents/PROPOSAL.md, "Suggestions (step 5)" and "States")
 * through the real handlers and records of agent-suggestions-service.py: the card in Needs you, the run page's
 * Suggested tasks while L3 checks, after it kept some, when it kept none and when its check missed the deadline, the
 * presses, a task's origin in Work and the form's Tell me line.
 */
test.use({ serviceScript: "agent-suggestions-service.py" });

const project = "/projects/harbor";

function setup(page: Page, phone: boolean) {
  const press = (target: Locator) => (phone ? target.tap() : target.click());
  const fits = async () => {
    if (phone) expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no horizontal overflow").toBe(true);
  };
  return { press, fits };
}

async function openRun(page: Page, press: (target: Locator) => Promise<unknown>, agent: string, run: RegExp) {
  await page.goto(`${project}/agents`);
  await press(page.getByRole("link", { name: new RegExp(`^${agent}`) }));
  await expect(page.getByRole("heading", { level: 1, name: agent, exact: true })).toBeVisible();
  await press(page.getByRole("link", { name: run }).first());
}

test("a run's suggested tasks in Needs you and on its page, decided in either place", async ({ page }, info) => {
  test.setTimeout(90_000);
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { press, fits } = setup(page, phone);
  const card = page.getByRole("article", { name: "Suggested tasks from Review each merge" });
  const first = card.getByRole("region", { name: "1. Add a test for the uploader's retry limit" });

  // Needs you: under Suggestions, uncounted, the agent's name and what fired the run; the made task's card in place.
  await walk.open("/");
  await walk.state("s01-needs-you", {
    visible: [page.getByRole("region", { name: "Suggestions", exact: true }),
      card.getByRole("heading", { name: "Review each merge" }), card.getByText("Requested by you", { exact: true }),
      first.getByText(/^A case beside the other retry tests in tests\/test_uploader\.py that checks the limit of 5/),
      first.getByRole("button", { name: "Make a task" }), first.getByRole("button", { name: "Dismiss" }),
      card.locator("a.task-card"), card.getByRole("link", { name: "Open the run" })],
    hidden: [page.getByText("Nothing needs you.", { exact: true }), card.getByText("Raise the CLI's retry limit to match"), card.locator(".chip")],
  });
  await fits();

  // The run page: the report, then Suggested tasks with what L3 kept and what it set aside, folded.
  await press(card.getByRole("link", { name: "Open the run" }));
  const section = page.getByRole("region", { name: "Suggested tasks" });
  const aside = section.getByText("Not offered · 1", { exact: true });
  const kept = section.getByRole("region", { name: "1. Add a test for the uploader's retry limit" });
  await walk.state("s02-run-kept", {
    visible: [page.getByText(/^#811 raised the retry limit in uploader\.py/), kept.getByRole("button", { name: "Make a task" }),
      section.getByRole("link", { name: /^Say the new retry limit in the CLI's help/ }), aside],
    hidden: [section.getByText("2. Raise the CLI's retry limit to match"), page.getByText(/^Suggest:/)],
  });
  await press(aside);
  await walk.state("s03-set-aside-open", {
    visible: [section.getByText("3. Raise the CLI's retry limit to match", { exact: true }),
      section.getByText("the CLI reads the uploader's limit, so there is nothing to change", { exact: true })],
    hidden: [],
  });

  // Dismiss folds at the press with Undo; Undo opens it again.
  await press(kept.getByRole("button", { name: "Dismiss" }));
  await walk.state("s04-dismissed", {
    visible: [section.getByText(/^Dismissed/), section.getByRole("button", { name: "Undo" })],
    hidden: [section.getByRole("button", { name: "Make a task" })],
  });
  await press(section.getByRole("button", { name: "Undo" }));
  await walk.state("s05-undone", { visible: [kept.getByRole("button", { name: "Make a task" })], hidden: [section.getByRole("button", { name: "Undo" })] });

  // Make a task: L3 is opening it, and the card still waits in Needs you until the task exists.
  await press(kept.getByRole("button", { name: "Make a task" }));
  await walk.state("s06-accepted", {
    visible: [kept.getByText("Accepted · L3 is opening a task", { exact: true })],
    hidden: [kept.getByRole("button", { name: "Dismiss" })],
  });
  // The fixture's L3 ends that turn without a task, so Needs you keeps the card with Make a task.
  await page.goto("/");
  await expect(first.getByText(/^Accepted · /)).toBeVisible();
  await fits();
});

test("the run page while L3 checks, when it kept none and when its check missed the deadline", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { press, fits } = setup(page, phone);
  const section = page.getByRole("region", { name: "Suggested tasks" });

  await openRun(page, press, "Dependency check", /^Found something/);
  await walk.state("s07-checking", {
    visible: [section.getByText("L3 is checking 2 suggestions", { exact: true })],
    hidden: [section.getByRole("button"), page.getByText("Bump left-pad to 1.3.1")],
  });
  await page.goto("/");
  await expect(page.getByRole("article", { name: "Suggested tasks from Dependency check" })).toHaveCount(0);

  // Docs drift's first run: L3 kept none, so only the fold shows, and Needs you never listed it.
  await openRun(page, press, "Docs drift", /^Found something.*two pages describe removed flags/);
  await walk.state("s08-none-kept", {
    visible: [section.getByText("Not offered · 2", { exact: true })],
    hidden: [section.getByRole("button"), section.getByText("1. Remove --fast from the upload page")],
  });
  await press(section.getByText("Not offered · 2", { exact: true }));
  await expect(section.getByText("#809 already removes it")).toHaveCount(2);

  // Its second run: the check missed its deadline, said once on the fold, which opens to the titles.
  await openRun(page, press, "Docs drift", /^Found something.*install line/);
  const unfinished = section.getByText("Not offered · 1 · not checked in time", { exact: true });
  await press(unfinished);
  await walk.state("s09-unfinished", {
    visible: [unfinished, section.getByText("1. Fix the README's install line", { exact: true })],
    hidden: [section.getByRole("button")],
  });
  await fits();
});

test("a task made from a suggestion says where it came from, and the form's Tell me says where suggestions wait", async ({ page }, info) => {
  const walk = walkthrough(page, info);
  const phone = info.project.name === "phone";
  const { fits } = setup(page, phone);

  await walk.open(`${project}?tab=work`);
  await walk.state("s10-origin", {
    visible: [page.getByRole("link", { name: /^Say the new retry limit in the CLI's help.*· from Review each merge$/ })],
    hidden: [],
  });
  await walk.open(`${project}/agents/new`);
  await walk.state("s11-tell-me", {
    visible: [page.getByText("Suggested tasks wait in Needs you whatever you choose.", { exact: true })],
    hidden: [],
  });
  await fits();
});
