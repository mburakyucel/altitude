import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, taskAction } from "../data/api";
import type { TaskView } from "../data/api";
import { TransferCard, TransferDialog, useTaskTransfer } from "./TaskTransfer";
import { TaskModelButton } from "./ModelMenu";

vi.mock("../data/api", async (original) => ({ ...await original<typeof import("../data/api")>(), taskAction: vi.fn() }));
const initial: TaskView = { slug: "sample", state: "running", transfer: {
  current_engine: "first", current_label: "Engine A", eligible: true, preparation: true, quota_limited: false,
  current_model: "model-a", current_model_label: "Model A", current_effort: "high", current_effort_label: "High",
  targets: [{ engine: "first", label: "Engine A", available: true, default_effort: "high", efforts: [{value:"high",label:"High"}, {value:"medium",label:"Medium"}], models: [{value:"model-a",model:"model-a",label:"Model A",available:true}] },
    { engine: "second", label: "Engine B", available: true, default_effort: "high", efforts: [{value:"high",label:"High"}, {value:"medium",label:"Medium"}], models: [{value:"model-b",model:"model-b",label:"Model B",available:true}] }],
  observed: { attempt: 1, agent_id: "worker", session_id: "session", block_id: null }, request: null,
} };
const preparing: TaskView = { ...initial, transfer: { ...initial.transfer!, eligible: false,
  request: { id: "switch-1", status: "executing", phase: "preparing", engine: "second", target_label: "Engine B" } } };
function Harness({ task = initial, next = preparing, refreshError = false }: { task?: TaskView; next?: TaskView; refreshError?: boolean }) {
  const [current, update] = useState(task);
  const [denied, setDenied] = useState(false);
  const t = useTaskTransfer("sample", current, async () => { if (refreshError) throw new Error("offline"); update(next); }, denied, () => setDenied(true), () => undefined);
  return <><TaskModelButton transfer={t} /><TransferCard transfer={t} /><TransferDialog transfer={t} /><input aria-label="Draft" defaultValue="Keep my draft" /></>;
}
// Model and effort share one panel: the second helper opens it only when it is closed.
const openModels = async (u: ReturnType<typeof userEvent.setup>) => { if (!screen.queryByRole("menu")) await u.click(screen.getByRole("button", { name: /^Task model and effort:/ })); };
const openEfforts = openModels;
const confirmPanel = () => screen.queryByRole("group", { name: "Confirm the switch" });
async function pickModelB(u: ReturnType<typeof userEvent.setup>) {
  await openModels(u);
  await u.click(within(screen.getByRole("group", { name: "Engine B" })).getByRole("menuitemradio", { name: "Model B" }));
}
afterEach(() => vi.resetAllMocks());
it("confirms the warning and fences the observed attempt, then exposes patient controls", async () => {
  vi.mocked(taskAction).mockResolvedValue({});
  const u = userEvent.setup(); render(<Harness />);
  await pickModelB(u);
  expect(within(confirmPanel()!).getByText("Switch to Model B on Engine B · High?")).toBeVisible();
  expect(within(confirmPanel()!).getByText(/writes a handoff note.*Some context may be lost/)).toBeVisible();
  expect(taskAction).not.toHaveBeenCalled();
  await u.click(within(confirmPanel()!).getByRole("button", { name: "Switch" }));
  expect(taskAction).toHaveBeenCalledWith({ project: "sample", slug: "sample", action: "handoff", engine: "second", selection: {model:"model-b",effort:"high"}, default_model: undefined, observed: initial.transfer!.observed });
  expect(await screen.findByRole("button", { name: "Switch now" })).toBeVisible();
  expect(within(screen.getByRole("region", { name: "Model switch" })).getByText("Switching to Engine B")).toBeVisible();
  expect(confirmPanel()).not.toBeInTheDocument();
  expect(screen.getByLabelText("Draft")).toHaveValue("Keep my draft");
});
it("reconciles a lost response by reading instead of resubmitting", async () => {
  vi.mocked(taskAction).mockRejectedValue(new Error("response lost"));
  const u = userEvent.setup(); render(<Harness />);
  await pickModelB(u);
  await u.click(within(confirmPanel()!).getByRole("button", { name: "Switch" }));
  expect(await screen.findByText(/^Could not confirm the switch/)).toBeVisible();
  expect(confirmPanel()).not.toBeInTheDocument();
  // The button offers no second switch: it leads to the card, which settles this one.
  await u.click(screen.getByRole("button", { name: /Switch unconfirmed; show the switch$/ }));
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Model switch" })).toHaveFocus();
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
  await pickModelB(u);
  expect(within(confirmPanel()!).getByText(/^A fresh session continues from the task’s saved work\./)).toBeVisible();
  expect(within(confirmPanel()!).queryByText(/handoff note/)).not.toBeInTheDocument();
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
  expect(screen.queryByRole("region", {name:"Model switch"})).not.toBeInTheDocument();
  expect(screen.getByRole("button", {name:/Task model and effort:/})).toBeEnabled();
});
it("shows queued progress without offering a conflicting new switch", () => {
  render(<Harness task={{ ...initial, state: "queued", transfer: { ...initial.transfer!, request: { ...preparing.transfer!.request!, status: "done", phase: "done" } } }} />);
  expect(screen.getByText("Waiting for Engine B")).toBeVisible();
  expect(screen.getByRole("button", { name: /Task model and effort:/ })).toBeDisabled();
});
it("stops a refused task with the engine's reason and offers the model panel", async () => {
  const u = userEvent.setup();
  render(<Harness task={{ ...initial, state: "blocked", transfer: { ...initial.transfer!, preparation: false, refused: "model access rejected" } }} />);
  const card = screen.getByRole("region", { name: "Engine refused the task" });
  expect(within(card).getByText("Engine A: model access rejected.")).toBeVisible();
  await u.click(within(card).getByRole("button", { name: "Change model…" }));
  expect(screen.getByRole("menu", { name: "This task’s model and effort" })).toBeVisible();
  expect(taskAction).not.toHaveBeenCalled();
});
it("does not promote historical quota evidence after the current hold clears", () => {
  render(<Harness task={{ ...initial, state: "blocked", transfer: { ...initial.transfer!, quota_limited: true, resume_hold: null } }} />);
  expect(screen.queryByRole("region", { name: "Engine usage limit" })).not.toBeInTheDocument();
});

it("lists the current model once and confirms an effort-only change on the same engine", async () => {
  const u=userEvent.setup();
  const task={...initial,transfer:{...initial.transfer!,current_engine:"second",current_model:"model-b",current_model_label:"Model B",targets:[{...initial.transfer!.targets[1]!,models:[{value:"default",model:"model-b",label:"Default · Model B",available:true},...initial.transfer!.targets[1]!.models]}]}};
  render(<Harness task={task}/>);
  await openModels(u);
  const rows=within(screen.getByRole("group",{name:"Engine B"})).getAllByRole("menuitemradio");
  expect(rows.map((row)=>row.textContent)).toEqual(["Model BCurrent"]);
  await u.click(rows[0]!);
  expect(confirmPanel()).not.toBeInTheDocument();
  await openEfforts(u);
  await u.click(screen.getByRole("menuitemradio",{name:"Medium"}));
  expect(within(confirmPanel()!).getByText("Switch to Medium effort?")).toBeVisible();
  // The pick awaiting confirmation is marked apart from the running level, which says it is current.
  expect(screen.getByRole("menuitemradio",{name:"Medium"})).toHaveAttribute("data-pending");
  expect(screen.getByRole("menuitemradio",{name:/^High/})).toHaveTextContent("HighCurrent");
  expect(screen.getByRole("menuitemradio",{name:"Medium"})).toHaveAttribute("aria-checked","true");
});
it("opening and browsing the lists creates no request", async () => {
  const u=userEvent.setup();render(<Harness/>);
  await openModels(u);
  await u.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  await openEfforts(u);
  await u.click(screen.getByRole("menuitemradio",{name:"Medium"}));
  await u.click(within(confirmPanel()!).getByRole("button",{name:"Cancel"}));
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(screen.getByRole("button",{name:/^Task model and effort: Model A · High/})).toHaveFocus();
  await openModels(u);
  await u.keyboard("{Escape}");
  expect(confirmPanel()).not.toBeInTheDocument();
  expect(screen.queryByRole("region",{name:"Model switch"})).not.toBeInTheDocument();
  expect(taskAction).not.toHaveBeenCalled();
});
it("opens on the checked model; left and right move between the model and effort columns", async () => {
  const u=userEvent.setup();render(<Harness/>);
  await openModels(u);
  expect(within(screen.getByRole("group",{name:"Model"})).getByRole("menuitemradio",{checked:true})).toHaveFocus();
  await u.keyboard("{ArrowRight}");
  expect(within(screen.getByRole("group",{name:"Effort"})).getByRole("menuitemradio",{name:/^High/})).toHaveFocus();
  await u.keyboard("{ArrowDown}{ArrowLeft}");
  expect(within(screen.getByRole("group",{name:"Model"})).getByRole("menuitemradio",{checked:true})).toHaveFocus();
  expect(taskAction).not.toHaveBeenCalled();
});
it("model and effort picks build one pending choice that a press elsewhere drops", async () => {
  const u=userEvent.setup();render(<Harness/>);
  await pickModelB(u);
  await openEfforts(u);
  expect(within(confirmPanel()!).getByText("Switch to Model B on Engine B · High?")).toBeVisible();
  await u.click(screen.getByRole("menuitemradio",{name:"Medium"}));
  expect(within(confirmPanel()!).getByText("Switch to Model B on Engine B · Medium?")).toBeVisible();
  await u.click(screen.getByLabelText("Draft"));
  expect(confirmPanel()).not.toBeInTheDocument();
  expect(screen.getByRole("button",{name:/^Task model and effort: Model A/})).toBeVisible();
  expect(taskAction).not.toHaveBeenCalled();
});

const byCoordinator: TaskView = { ...preparing, transfer: { ...preparing.transfer!, request: { ...preparing.transfer!.request!,
  by: "l3", reason: "The operator asked for existing tasks to run on Engine B." } } };
it("names who started a preparing switch, and the pill shows that switch instead of opening the chooser", async () => {
  const u=userEvent.setup();render(<Harness task={byCoordinator}/>);
  const card=screen.getByRole("region",{name:"Model switch"});
  expect(within(card).getByText("Started by the coordinator · The operator asked for existing tasks to run on Engine B.")).toBeVisible();
  await u.click(screen.getByRole("button",{name:"Task model: switching to Engine B. Show the switch"}));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(card).toHaveFocus();
  expect(taskAction).not.toHaveBeenCalled();
});
it("says the operator started their own switch", () => {
  render(<Harness task={{...preparing,transfer:{...preparing.transfer!,request:{...preparing.transfer!.request!,by:"operator",reason:null}}}}/>);
  expect(screen.getByText("Started by you")).toBeVisible();
});
it("a switch is fenced to the session the panel showed, so a resume meanwhile refuses it", async () => {
  function Live({ task }: { task: TaskView }) {
    const t=useTaskTransfer("sample",task,async()=>undefined,false,()=>undefined,()=>undefined);
    return <><TaskModelButton transfer={t}/><TransferCard transfer={t}/></>;
  }
  vi.mocked(taskAction).mockRejectedValue(new ApiError(409, "changed"));
  const u=userEvent.setup();
  const {rerender}=render(<Live task={initial}/>);
  await pickModelB(u);
  const resumed = { attempt: 2, agent_id: "worker-2", session_id: "session-2", block_id: null };
  rerender(<Live task={{...initial,transfer:{...initial.transfer!,observed:resumed}}}/>);
  await u.click(within(confirmPanel()!).getByRole("button", { name: "Switch" }));
  expect(vi.mocked(taskAction).mock.calls[0]![0]).toMatchObject({ action: "handoff", observed: initial.transfer!.observed });
  expect(await screen.findByText("The task changed. Check status before switching.")).toBeVisible();
});
it("a switch someone else starts closes an open chooser without submitting it", async () => {
  function Live({ task }: { task: TaskView }) {
    const t=useTaskTransfer("sample",task,async()=>undefined,false,()=>undefined,()=>undefined);
    return <><TaskModelButton transfer={t}/><TransferCard transfer={t}/><TransferDialog transfer={t}/></>;
  }
  const u=userEvent.setup();
  const {rerender}=render(<Live task={initial}/>);
  await u.click(screen.getByRole("button",{name:/Task model and effort:/}));
  expect(screen.getByRole("menu")).toBeVisible();
  rerender(<Live task={byCoordinator}/>);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(screen.getByText(/Started by the coordinator/)).toBeVisible();
  expect(taskAction).not.toHaveBeenCalled();
});
it("a switch that finishes between reads also closes the chooser", async () => {
  function Live({ task }: { task: TaskView }) {
    const t=useTaskTransfer("sample",task,async()=>undefined,false,()=>undefined,()=>undefined);
    return <><TaskModelButton transfer={t}/><TransferCard transfer={t}/><TransferDialog transfer={t}/></>;
  }
  const u=userEvent.setup();
  const blocked={...initial,state:"blocked"} as TaskView;
  const {rerender}=render(<Live task={blocked}/>);
  await u.click(screen.getByRole("button",{name:/Task model and effort:/}));
  expect(screen.getByRole("menu")).toBeVisible();
  rerender(<Live task={{...byCoordinator,state:"queued",transfer:{...byCoordinator.transfer!,request:{...byCoordinator.transfer!.request!,status:"done"}}}}/>);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(screen.getByText("Waiting for Engine B")).toBeVisible();
  expect(screen.getByText(/Started by the coordinator/)).toBeVisible();
  expect(taskAction).not.toHaveBeenCalled();
});
it("an unconfirmed switch of your own never borrows an earlier request's author", async () => {
  vi.mocked(taskAction).mockRejectedValue(new Error("response lost"));
  const cancelled={...byCoordinator,transfer:{...byCoordinator.transfer!,eligible:true,request:{...byCoordinator.transfer!.request!,status:"cancelled"}}};
  const u=userEvent.setup();render(<Harness task={cancelled} next={cancelled}/>);
  await pickModelB(u);
  await u.click(within(confirmPanel()!).getByRole("button",{name:"Switch"}));
  expect(await screen.findByText(/^Could not confirm the switch/)).toBeVisible();
  expect(screen.queryByText(/Started by/)).not.toBeInTheDocument();
});
