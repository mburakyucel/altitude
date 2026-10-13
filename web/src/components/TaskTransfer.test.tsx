import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, taskAction } from "../data/api";
import type { TaskView } from "../data/api";
import { TransferCard, TransferDialog, useTaskTransfer } from "./TaskTransfer";

vi.mock("../data/api", async (original) => ({ ...await original<typeof import("../data/api")>(), taskAction: vi.fn() }));
const initial: TaskView = { slug: "sample", state: "running", transfer: {
  current_engine: "first", current_label: "Engine A", eligible: true, preparation: true, quota_limited: false,
  targets: [{ engine: "second", label: "Engine B", available: true }],
  observed: { attempt: 1, agent_id: "worker", session_id: "session", block_id: null }, request: null,
} };
const preparing: TaskView = { ...initial, transfer: { ...initial.transfer!, eligible: false,
  request: { id: "switch-1", status: "executing", phase: "preparing", engine: "second", target_label: "Engine B" } } };
function Harness({ task = initial, next = preparing, refreshError = false, details = true }: { task?: TaskView; next?: TaskView; refreshError?: boolean; details?: boolean }) {
  const [current, update] = useState(task);
  const [denied, setDenied] = useState(false);
  const t = useTaskTransfer("sample", current, async () => { if (refreshError) throw new Error("offline"); update(next); }, denied, () => setDenied(true), () => undefined);
  return <><TransferCard transfer={t} details={details} /><TransferDialog transfer={t} /><input aria-label="Draft" defaultValue="Keep my draft" /></>;
}
afterEach(() => vi.resetAllMocks());
it("confirms the warning and fences the observed attempt, then exposes patient controls", async () => {
  vi.mocked(taskAction).mockResolvedValue({});
  const u = userEvent.setup(); render(<Harness />);
  await u.click(screen.getByRole("button", { name: "Switch engine…" }));
  expect(screen.getByText(/Some session context/)).toBeVisible();
  expect(screen.getByText(/There is no time limit/)).toBeVisible();
  await u.click(screen.getByRole("button", { name: "Prepare switch" }));
  expect(taskAction).toHaveBeenCalledWith({ project: "sample", slug: "sample", action: "handoff", engine: "second", observed: initial.transfer!.observed });
  expect(await screen.findByRole("button", { name: "Switch now" })).toBeVisible();
  expect(screen.getByText("Preparing switch to Engine B")).toBeVisible();
  expect(screen.getByRole("region", { name: "Engine transfer" })).toHaveFocus();
  expect(screen.getByLabelText("Draft")).toHaveValue("Keep my draft");
});
it("reconciles a lost response by reading instead of resubmitting", async () => {
  vi.mocked(taskAction).mockRejectedValue(new Error("response lost"));
  const u = userEvent.setup(); render(<Harness />);
  await u.click(screen.getByRole("button", { name: "Switch engine…" }));
  await u.click(screen.getByRole("button", { name: "Prepare switch" }));
  expect(await screen.findByText("Switch unconfirmed")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Switch engine…" })).not.toBeInTheDocument();
  await u.click(screen.getByRole("button", { name: "Check status" }));
  expect(await screen.findByRole("button", { name: "Cancel switch" })).toBeVisible();
  expect(taskAction).toHaveBeenCalledTimes(1);
});
it("requires explicit switch-now confirmation and uses the request identity", async () => {
  vi.mocked(taskAction).mockResolvedValue({});
  const u = userEvent.setup(); render(<Harness task={preparing} />);
  await u.click(screen.getByRole("button", { name: "Switch now" }));
  const modal = screen.getByRole("dialog");
  expect(within(modal).getByText(/pending commands will stop/)).toBeVisible();
  await u.click(within(modal).getByRole("button", { name: "Switch now" }));
  expect(taskAction).toHaveBeenCalledWith({ project: "sample", slug: "sample", action: "handoff-now", request_id: "switch-1" });
});
it("uses saved-context confirmation for a quota-stopped owner", async () => {
  const u = userEvent.setup(); render(<Harness task={{ ...initial, state: "blocked", transfer: { ...initial.transfer!, preparation: false, quota_limited: true } }} />);
  await u.click(screen.getByRole("button", { name: "Switch engine…" }));
  expect(screen.getByText("Engine B continues from the saved task record.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Prepare switch" })).not.toBeInTheDocument();
});
it("removes mutation controls after access is denied", async () => {
  vi.mocked(taskAction).mockRejectedValue(new ApiError(403, "denied"));
  const u = userEvent.setup(); render(<Harness task={preparing} />);
  await u.click(screen.getByRole("button", { name: "Cancel switch" }));
  expect(await screen.findByText("You do not have permission to switch this task.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Cancel switch" })).not.toBeInTheDocument();
});
it("keeps a completed result and the next action in one details card", () => {
  render(<Harness task={{ ...initial, transfer: { ...initial.transfer!, request: { ...preparing.transfer!.request!, status: "done", phase: "done" } } }} />);
  const card = screen.getByRole("region", { name: "Engine transfer" });
  expect(within(card).getByText("Switched to Engine B.")).toBeVisible();
  expect(within(card).getByRole("button", { name: "Switch engine…" })).toBeVisible();
  expect(card.querySelector(".review-icon-run")).toBeNull();
});
it("shows queued progress without offering a conflicting new switch", () => {
  render(<Harness task={{ ...initial, state: "queued", transfer: { ...initial.transfer!, request: { ...preparing.transfer!.request!, status: "done", phase: "done" } } }} />);
  expect(screen.getByText("Waiting for Engine B")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Switch engine…" })).not.toBeInTheDocument();
});
it("does not promote historical quota evidence after the current hold clears", () => {
  render(<Harness details={false} task={{ ...initial, state: "blocked", transfer: { ...initial.transfer!, quota_limited: true, resume_hold: null } }} />);
  expect(screen.queryByRole("region", { name: "Engine usage limit" })).not.toBeInTheDocument();
});
