import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// Stored records and agent text carry UTC; a reader in Los Angeles sees Los Angeles time (SPEC.md §4.5).
test.use({ serviceScript: "local-time-service.py", timezoneId: "America/Los_Angeles", locale: "en-US" });

test("stored UTC stamps and written UTC times read in the browser's own zone", async ({ page, service }, info) => {
  const walk = walkthrough(page, info);
  const conversation = page.getByRole("region", { name: "Conversation", exact: true });
  const reply = conversation.locator(".reply").filter({ hasText: "Restart pending" });
  const written = reply.locator("p").first();
  const fyi = conversation.locator(".sys-line").filter({ hasText: "This L2 resumes after" });
  await walk.open(`${service}/projects/alpha`);
  // 23:58Z on January 14 is 3:58 PM PST whatever today's offset is; a clock stays a clock, and a dated time
  // off the reply's own day keeps its date.
  await expect(written).toHaveText(
    "Restart pending since 03:58 PM. Checks finished at Jan 13, 03:12 PM; the quiet window is 02:20 PM–02:45 PM.");
  await expect(written.locator("time").first()).toHaveAttribute("title", "3:58:00 PM PST");
  await expect(written.locator("time").nth(1)).toHaveAttribute("title", "Jan 13, 2026, 3:12:04 PM PST");
  await expect(reply.locator("code")).toHaveText("2026-01-14T23:58:42+00:00");
  await expect(reply).toContainText("a bare 23:58 has no zone");
  await expect(fyi).toContainText("This L2 resumes after Jan 14, 06:00 PM.");
  await expect(conversation.getByText("Wed, Jan 14", { exact: true })).toBeVisible();
  await walk.state("01-chat-local-times", {
    visible: [written, fyi],
    hidden: [conversation.getByText(/23:58Z|23:12:04|UTC|02:00:00/)],
  });

  await walk.open(`${service}/projects/alpha/tasks/time-task`);
  const task = page.getByRole("region", { name: "Task conversation", exact: true });
  const answer = task.locator('.msg-row[data-role="l2"]');
  await expect(answer.locator(".reply")).toHaveText("Checks pass as of 03:40 PM.");
  await expect(answer.locator(".msg-time")).toHaveText("04:30 PM");
  await walk.state("02-task-local-times", { visible: [answer], hidden: [task.getByText(/23:40Z/)] });
});
