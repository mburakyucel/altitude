import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, taskAction } from "../data/api";
import type { TaskView } from "../data/api";
import { TransferCard, TransferDialog, TransferModelButton, useTaskTransfer } from "./TaskTransfer";

vi.mock("../data/api", async (original) => ({ ...await original<typeof import("../data/api")>(), taskAction: vi.fn() }));
const initial: TaskView = { slug: "sample", state: "running", transfer: {
  current_engine: "first", current_label: "Engine A", eligible: true, preparation: true, quota_limited: false,
  current_model: "model-a", current_model_label: "Model A", current_effort: "high", current_effort_label: "High",
  targets: [{ engine: "second", label: "Engine B", available: true, default_effort: "high", efforts: [{value:"high",label:"High"}, {value:"medium",label:"Medium"}], models: [{value:"model-b",model:"model-b",label:"Model B",available:true}] }],
  observed: { attempt: 1, agent_id: "worker", session_id: "session", block_id: null }, request: null,
} };
const preparing: TaskView = { ...initial, transfer: { ...initial.transfer!, eligible: false,
  request: { id: "switch-1", status: "executing", phase: "preparing", engine: "second", target_label: "Engine B" } } };
function Harness({ task = initial, next = preparing, refreshError = false }: { task?: TaskView; next?: TaskView; refreshError?: boolean }) {
  const [current, update] = useState(task);
  const [denied, setDenied] = useState(false);
  const t = useTaskTransfer("sample", current, async () => { if (refreshError) throw new Error("offline"); update(next); }, denied, () => setDenied(true), () => undefined);
  return <><TransferModelButton transfer={t} /><TransferCard transfer={t} /><TransferDialog transfer={t} /><input aria-label="Draft" defaultValue="Keep my draft" /></>;
}
afterEach(() => vi.resetAllMocks());
it("confirms the warning and fences the observed attempt, then exposes patient controls", async () => {
  vi.mocked(taskAction).mockResolvedValue({});
  const u = userEvent.setup(); render(<Harness />);
  await u.click(screen.getByRole("button", { name: /Task model:/ }));
  expect(screen.getByText(/Some context may be lost/)).toBeVisible();
  await u.click(screen.getByRole("button", { name: "Prepare change" }));
  expect(taskAction).toHaveBeenCalledWith({ project: "sample", slug: "sample", action: "handoff", engine: "second", selection: {model:"model-b",effort:"high"}, default_model: undefined, observed: initial.transfer!.observed });
  expect(await screen.findByRole("button", { name: "Switch now" })).toBeVisible();
  expect(screen.getByText("Engine B")).toBeVisible();
  expect(screen.getByRole("button", {name:/Task model:/})).toHaveFocus();
  expect(screen.getByLabelText("Draft")).toHaveValue("Keep my draft");
});
it("reconciles a lost response by reading instead of resubmitting", async () => {
  vi.mocked(taskAction).mockRejectedValue(new Error("response lost"));
  const u = userEvent.setup(); render(<Harness />);
  await u.click(screen.getByRole("button", { name: /Task model:/ }));
  await u.click(screen.getByRole("button", { name: "Prepare change" }));
  expect(await screen.findByText("Switch unconfirmed")).toBeVisible();
  expect(screen.getByRole("button", { name: /Task model:/ })).toBeDisabled();
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
  await u.click(screen.getByRole("button", { name: /Task model:/ }));
  expect(screen.getByText(/Starts a fresh session from saved context/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Prepare change" })).not.toBeInTheDocument();
});
it("removes mutation controls after access is denied", async () => {
  vi.mocked(taskAction).mockRejectedValue(new ApiError(403, "denied"));
  const u = userEvent.setup(); render(<Harness task={preparing} />);
  await u.click(screen.getByRole("button", { name: "Cancel switch" }));
  expect(await screen.findByText("You do not have permission to switch this task.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Cancel switch" })).not.toBeInTheDocument();
});
it("returns to the composer chooser after the completed outcome enters history", () => {
  render(<Harness task={{ ...initial, transfer: { ...initial.transfer!, request: { ...preparing.transfer!.request!, status: "done", phase: "done" } } }} />);
  expect(screen.queryByRole("region", {name:"Task change"})).not.toBeInTheDocument();
  expect(screen.getByRole("button", {name:/Task model:/})).toBeEnabled();
});
it("shows queued progress without offering a conflicting new switch", () => {
  render(<Harness task={{ ...initial, state: "queued", transfer: { ...initial.transfer!, request: { ...preparing.transfer!.request!, status: "done", phase: "done" } } }} />);
  expect(screen.getByText("Waiting for Engine B")).toBeVisible();
  expect(screen.getByRole("button", { name: /Task model:/ })).toBeEnabled();
});
it("does not promote historical quota evidence after the current hold clears", () => {
  render(<Harness task={{ ...initial, state: "blocked", transfer: { ...initial.transfer!, quota_limited: true, resume_hold: null } }} />);
  expect(screen.queryByRole("region", { name: "Engine usage limit" })).not.toBeInTheDocument();
});

it("disables unchanged default aliases but allows an explicit same-engine effort change", async () => {
  const u=userEvent.setup();
  const task={...initial,transfer:{...initial.transfer!,current_engine:"second",current_model:"model-b",current_model_label:"Model B",targets:[{...initial.transfer!.targets[0]!,models:[{value:"default",model:"model-b",label:"Default · Model B",available:true},...initial.transfer!.targets[0]!.models]}]}};
  render(<Harness task={task}/>);
  await u.click(screen.getByRole("button",{name:/Task model:/}));
  expect(screen.getByRole("button",{name:"Prepare change"})).toBeDisabled();
  await u.selectOptions(screen.getByLabelText("Model"),"model-b");
  expect(screen.getByRole("button",{name:"Prepare change"})).toBeDisabled();
  await u.selectOptions(screen.getByLabelText("Effort"),"medium");
  expect(screen.getByRole("button",{name:"Prepare change"})).toBeEnabled();
});
it("Escape closes a chooser opened while preparation controls are visible", async () => {
  const u=userEvent.setup();render(<Harness task={preparing}/>);
  await u.click(screen.getByRole("button",{name:/Task model:/}));
  const modal=screen.getByRole("dialog");
  within(modal).getByRole("button",{name:"Cancel switch"}).focus();
  await u.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(taskAction).not.toHaveBeenCalled();
});
