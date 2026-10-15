import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

/**
 * A wait on the operator's step is their turn and names the step on the task card; a coordinator wait keeps
 * saying so, and a question with choices keeps its count. Two owners once recorded such a wait for L3, so
 * their tasks read "waiting on L3" while each waited for the operator to run a command.
 */
test.use({ serviceScript: "operator-step-service.py" });
test.setTimeout(90_000);

test("a wait on the operator's step names the step until the owner is woken, and its card says the command ran", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const rows = page.getByRole("region", { name: "Work", exact: true });
  const row = (title: string) => rows.getByRole("link", { name: new RegExp(`^${title} ·`) });
  const step = "Your turn · Run the sign-in command in the task terminal.";
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const command = conversation.getByRole("group", { name: "Command", exact: true });

  await walk.open("/projects/atlas?tab=work");
  await walk.state("01-work-rows-step-coordinator-choice", {
    visible: [
      row("Sign in").getByText(step, { exact: true }),
      row("Export access").getByText("Waiting for the coordinator: CI export access is missing.", { exact: true }),
      row("Installer region").getByText("Your turn · 1 question", { exact: true }),
    ],
    hidden: [row("Export access").getByText(/Your turn/), row("Sign in").getByText("Your turn · 1 question", { exact: true })],
  });

  await walk.open("/projects/atlas/tasks/sign-in");
  await walk.state("02-task-page-counts-and-shows-the-command", {
    visible: [
      conversation.locator(".conversation-turn").getByText("Your turn · 1 question", { exact: true }),
      conversation.getByText("Run the sign-in command in the task terminal.", { exact: true }),
      command.getByText("example-tool sign-in", { exact: true }),
      command.getByRole("button", { name: "Open in terminal", exact: true }),
    ],
    hidden: [page.getByText(step, { exact: true })],
  });

  expect(await (await request.put("/fixture/notice")).json()).toEqual({ resume_after: true });
  await walk.open("/projects/atlas?tab=work");
  await walk.state("03-work-row-after-the-terminal-notice", {
    visible: [row("Sign in").getByText("Your turn · 1 question", { exact: true })],
    hidden: [row("Sign in").getByText(step, { exact: true })],
  });

  // The question that handed the command says it ran while the owner checks, wherever it shows, and stays open.
  const card = page.getByRole("article", { name: "Sign in", exact: true });
  await walk.open("/");
  await walk.state("04-needs-you-card-says-the-command-ran", {
    visible: [
      card.getByText("Run the sign-in command in the task terminal.", { exact: true }),
      card.getByRole("status").getByText("Ran in the terminal", { exact: true }),
      card.getByRole("status").getByText("The L2 is checking the result.", { exact: true }),
      card.getByRole("button", { name: "Open in terminal", exact: true }),
      card.getByRole("textbox", { name: /^Your answer to:/ }),
    ],
    hidden: [card.getByText("The terminal closed before this command finished", { exact: true })],
  });
  await walk.open("/projects/atlas/tasks/sign-in");
  await walk.state("05-task-conversation-says-the-command-ran", {
    visible: [
      conversation.getByRole("status").getByText("Ran in the terminal", { exact: true }),
      conversation.getByText("The L2 is checking the result.", { exact: true }),
      command.getByRole("button", { name: "Open in terminal", exact: true }),
    ],
    hidden: [conversation.getByText("The terminal closed before this command finished", { exact: true })],
  });

  expect(await (await request.put("/fixture/notice")).json()).toEqual({ resume_after: true });
  await walk.open("/");
  await walk.state("06-needs-you-card-says-the-terminal-closed-first", {
    visible: [
      card.getByRole("status").getByText("The terminal closed before this command finished", { exact: true }),
      card.getByText("Open in terminal to run it again.", { exact: true }),
      card.getByRole("button", { name: "Open in terminal", exact: true }),
    ],
    hidden: [card.getByText("Ran in the terminal", { exact: true })],
  });
});
