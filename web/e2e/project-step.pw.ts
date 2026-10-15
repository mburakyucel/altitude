import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * A command only the operator can run for the project's own recovery waits in Needs you, opening the project
 * terminal, until that terminal's notice clears it; the task waiting on it shows the operator's turn. The one
 * recovery command of an incident once lived only in project chat, so Needs you was empty and the two tasks
 * waiting on it read "waiting on L3" for a day.
 */
test.use({ serviceScript: "project-step-service.py" });
test.setTimeout(90_000);

test("a recovery command from L3 waits in Needs you until the project terminal runs it", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const headline = "Restore the service checkout so merged changes can activate again.";
  const card = page.getByRole("article", { name: "Command for atlas", exact: true });
  const command = card.getByRole("group", { name: "Command", exact: true });
  const rows = page.getByRole("region", { name: "Work", exact: true });
  const row = (title: string) => rows.getByRole("link", { name: new RegExp(`^${title} ·`) });

  await walk.open("/");
  await walk.state("01-needs-you-shows-the-command", {
    visible: [
      page.getByText("1 step in atlas", { exact: true }),
      card.getByText("L3 brought this to you", { exact: true }),
      card.getByText(headline, { exact: true }),
      command.getByText("git -C ~/Projects/atlas restore README.md", { exact: true }),
      command.getByRole("button", { name: "Copy", exact: true }),
      command.getByRole("button", { name: "Open in terminal", exact: true }),
      card.getByRole("link", { name: "VM harness", exact: true }),
      card.getByText("This leaves Needs you once the command finishes in the project terminal.", { exact: true }),
      card.getByRole("link", { name: "Open project chat", exact: true }),
    ],
    hidden: [card.getByRole("button", { name: /^Send/ }), card.getByRole("link", { name: "Export access", exact: true })],
  });

  await walk.open("/projects/atlas?tab=work");
  await walk.state("02-work-rows-name-the-step-for-the-waiting-task", {
    visible: [
      row("VM harness").getByText(`Your turn · ${headline}`, { exact: true }),
      row("Export access").getByText("Waiting for the coordinator: CI export access is missing.", { exact: true }),
    ],
    hidden: [row("VM harness").getByText(/Waiting for the coordinator/), row("Export access").getByText(/Your turn/)],
  });

  await walk.open("/projects/atlas/tasks/vm-harness");
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  await walk.state("03-task-page-shows-the-step", {
    visible: [
      conversation.getByText("Your turn · a command from L3", { exact: true }),
      conversation.getByText(headline, { exact: true }),
      conversation.getByRole("group", { name: "Command", exact: true }).getByRole("button", { name: "Open in terminal", exact: true }),
      conversation.getByRole("link", { name: "Open project chat", exact: true }),
    ],
    // The owner's note to L3 waits out of sight while the step is the operator's turn.
    hidden: [conversation.getByText(/Waiting for the coordinator/), conversation.getByText(/Waiting on this/),
      conversation.getByText("L3 is answering", { exact: true }), conversation.getByText("L3 is handling this", { exact: true }),
      conversation.getByText("The service checkout must be restored before this can activate.", { exact: true })],
  });

  await walk.open("/");
  await card.getByRole("group", { name: "Command", exact: true }).getByRole("button", { name: "Open in terminal", exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/atlas\/terminal$/);

  expect(await (await request.put("/fixture/notice")).json()).toEqual({ steps: 0, told: ["terminal"] });
  await walk.open("/");
  await walk.state("04-needs-you-after-the-terminal-notice", {
    visible: [page.getByText("Nothing needs you.", { exact: true })],
    hidden: [card, page.getByText(headline, { exact: true })],
  });
  await walk.open("/projects/atlas?tab=work");
  await walk.state("05-work-row-after-the-terminal-notice", {
    visible: [row("VM harness").getByText("Waiting for the coordinator: The service checkout must be restored before this can activate.", { exact: true })],
    hidden: [row("VM harness").getByText(/Your turn/)],
  });
});
