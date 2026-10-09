import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

test.use({ serviceScript: "l2-progress-service.py" });

test("separate queued messages remove independently and the remaining batch reaches the session", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const status = async () => (await (await request.get("/fixture/status")).json());
  const slug = (await status()).tasks[0].slug;
  const control = async (mode: string) => expect((await request.post("/fixture/control", { data: { slug, mode } })).ok()).toBe(true);
  const convo = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = convo.getByRole("textbox", { name: "Message the L2" });
  const row = (text: string) => convo.locator(".msg-row").filter({ hasText: text });
  const send = async (text: string) => {
    await field.fill(text);
    await convo.getByRole("button", { name: "Send", exact: true }).click();
    await expect(row(text).getByRole("button", { name: "Remove", exact: true })).toBeVisible();
  };
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await walk.state("01-no-queued-controls", { visible: [field], hidden: [convo.getByRole("button", { name: "Remove", exact: true })] });
  await send("First instruction.");
  await send("Discard this instruction.");
  await send("Third instruction.");
  await row("First instruction.").locator(".bubble").scrollIntoViewIfNeeded();
  await expect(row("First instruction.").locator(".bubble")).toBeInViewport();
  await expect(row("Third instruction.").getByRole("button", { name: "Remove", exact: true })).toBeInViewport();
  await walk.state("02-three-separate-removable-messages", { visible: [row("First instruction."), row("Discard this instruction."), row("Third instruction.")], hidden: [] });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/l2/remove", async (route) => { await gate; await route.continue(); }, { times: 1 });
  try {
    await row("Discard this instruction.").getByRole("button", { name: "Remove", exact: true }).click();
    await walk.state("03-removing-keeps-text-until-confirmed", { visible: [row("Discard this instruction.").getByRole("button", { name: "Removing…" })], hidden: [] });
    await expect(row("First instruction.").getByRole("button", { name: "Remove", exact: true })).toBeDisabled();
  } finally { release(); }
  await walk.state("04-only-selected-message-removed", { visible: [row("First instruction."), row("Third instruction.")], hidden: [convo.getByText("Discard this instruction.", { exact: true })] });
  await page.reload();
  await expect(row("Third instruction.")).toBeVisible();
  await expect(convo.getByText("Discard this instruction.", { exact: true })).toBeHidden();
  expect((await status()).tasks[0].pending.map((message: { text: string }) => message.text)).toEqual(["First instruction.", "Third instruction."]);
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await walk.state("05-stop-keeps-waiting-messages-removable", { visible: [page.getByRole("button", { name: "Continue" }), row("First instruction.").getByRole("button", { name: "Remove", exact: true })], hidden: [] });
  await control("hold-resume");
  try {
    await page.getByRole("button", { name: "Continue" }).click();
    await walk.state("06-claimed-batch-cannot-be-removed", { visible: [row("First instruction.").getByText("Sending to session · cannot remove"), row("Third instruction.").getByText("Sending to session · cannot remove")], hidden: [convo.getByRole("button", { name: "Remove", exact: true })] });
    expect((await request.post("/api/l2/message", { data: { project: "atlas", slug, text: "Later arrival." } })).ok()).toBe(true);
    await walk.state("07-later-arrival-stays-removable", { visible: [row("Later arrival.").getByRole("button", { name: "Remove", exact: true })], hidden: [row("First instruction.").getByRole("button", { name: "Remove", exact: true })] });
  } finally { await control("release-resume"); }
  await walk.state("08-exact-batch-delivered-later-message-waits", { visible: [row("First instruction.").getByText("Delivered to session"), row("Third instruction.").getByText("Delivered to session"), row("Later arrival.").getByRole("button", { name: "Remove", exact: true })], hidden: [convo.getByText("Sending to session · cannot remove")] });
  const evidence = await status();
  const prompt = evidence.calls.at(-1).prompt;
  expect(prompt.indexOf("First instruction.")).toBeLessThan(prompt.indexOf("Third instruction."));
  expect(prompt).not.toContain("Discard this instruction.");
  expect(prompt).not.toContain("Later arrival.");
  await row("Later arrival.").getByRole("button", { name: "Remove", exact: true }).click();
  await walk.state("09-no-pending-message-controls", { visible: [row("First instruction.").getByText("Delivered to session")], hidden: [convo.getByRole("button", { name: "Remove", exact: true }), convo.getByText("Later arrival.", { exact: true })] });
  expect((await status()).tasks[0].pending).toEqual([]);
});

test("removal refuses a raced pickup and distinguishes denied, failed and uncertain recovery", async ({ page, request }, info) => {
  const walk = walkthrough(page, info);
  const slug = (await (await request.get("/fixture/status")).json()).tasks[0].slug;
  const control = async (mode: string) => expect((await request.post("/fixture/control", { data: { slug, mode } })).ok()).toBe(true);
  const convo = page.getByRole("region", { name: "Task conversation", exact: true });
  const field = convo.getByRole("textbox", { name: "Message the L2" });
  const remove = convo.getByRole("button", { name: "Remove", exact: true });
  await walk.open(`/projects/atlas/tasks/${slug}`);
  await field.fill("Keep the original evidence.");
  await convo.getByRole("button", { name: "Send", exact: true }).click();
  await page.route("**/api/l2/remove", (route) => route.fulfill({ status: 403, json: { error: "Permission denied" } }), { times: 1 });
  await remove.click();
  await walk.state("01-removal-denied-keeps-message", { visible: [convo.getByText("You do not have permission to remove this message."), convo.getByText("Keep the original evidence.", { exact: true })], hidden: [] });
  await page.route("**/api/l2/remove", (route) => route.abort(), { times: 1 });
  await remove.click();
  await walk.state("02-removal-transport-error-never-claims-success", { visible: [convo.getByText("Removal unconfirmed. Check this message’s status before trying again."), convo.getByText("Keep the original evidence.", { exact: true })], hidden: [] });
  await page.route("**/api/l2/remove", async (route) => { await control("unconfirmed-delivery"); await route.continue(); }, { times: 1 });
  await remove.click();
  await walk.state("03-pickup-wins-removal-race", { visible: [convo.getByText("This message can no longer be removed. Refresh its delivery status."), convo.getByText("Delivery unconfirmed · cannot remove")], hidden: [remove] });
  await page.reload();
  await field.fill("Next batch remains separate.");
  await convo.getByRole("button", { name: "Send", exact: true }).click();
  await expect(remove).toBeVisible();
  await control("prelaunch-recovery");
  await walk.state("04-prelaunch-recovery-restores-remove", { visible: [remove, convo.getByText("Next batch remains separate.", { exact: true })], hidden: [] });
  await control("uncertain-recovery");
  await walk.state("05-uncertain-restored-batch-cannot-remove", { visible: [convo.getByText("Delivery unconfirmed · cannot remove").last()], hidden: [remove] });
});
