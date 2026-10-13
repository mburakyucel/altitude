import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "task-model-service.py" });

const pill = (page: Page) => page.getByRole("button", { name: /^Task model and effort:/ });
const details = (page: Page) => page.getByRole("dialog", { name: "Task details", exact: true });

// Every owner launched through real routing with nothing configured, so each runs on Altitude's resolved default.
const owners = [
  { slug: "codex-launched", chip: "6.1 Sol on Codex · High", button: "6.1 Sol · High" },
  { slug: "codex-confirmed", chip: "6.1 Sol on Codex · High", button: "6.1 Sol · High" },
  { slug: "claude-confirmed", chip: "Opus 5.5 on Claude · Default", button: "Opus · Default" },
  { slug: "codex-finished", chip: "6.1 Sol on Codex · High", button: null },
];

test("the task page names the engine, model and effort each owner runs with", async ({ page }, info) => {
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  for (const owner of owners) {
    await walk.open(`/projects/atlas/tasks/${owner.slug}`);
    // Desktop shows the chip in the header; the phone header keeps only the state, and its details sheet holds the chip.
    if (phone) await page.getByRole("button", { name: /Task details$/ }).click();
    const scope = phone ? details(page) : page.locator("header .task-chips");
    const chip = scope.getByText(owner.chip, { exact: true });
    await walk.state(`01-${owner.slug}`, {
      visible: [chip, ...(owner.button && !phone ? [pill(page)] : [])],
      hidden: [scope.getByText(/engine default|Settings unknown/)],
    });
    if (phone) await page.keyboard.press("Escape");
    if (owner.button) await expect(pill(page)).toHaveAttribute("aria-label", `Task model and effort: ${owner.button}`);
    else await expect(pill(page)).toHaveCount(0);
  }
  // Details name the launch and the engine's report in the chooser's words; before a report, that row says so.
  for (const [slug, reported] of [["codex-confirmed", "6.1 Sol · High"], ["codex-launched", "Not reported yet"]]) {
    await walk.open(`/projects/atlas/tasks/${slug}`);
    await page.getByRole("button", { name: /Task details$/ }).click();
    const facts = details(page).getByRole("region", { name: "Model and effort", exact: true });
    await facts.scrollIntoViewIfNeeded();
    await walk.state(`02-${slug}-details`, {
      visible: [facts.getByText("Messages and resumes ask for 6.1 Sol · High.", { exact: true })],
      hidden: [facts.getByText(/engine default/)],
    });
    await expect(facts.getByRole("definition").first()).toHaveText("Project choice or defaults");
    await expect(facts.getByRole("definition").nth(1)).toHaveText("6.1 Sol · High");
    await expect(facts.getByRole("definition").nth(2)).toHaveText(reported);
  }
});
