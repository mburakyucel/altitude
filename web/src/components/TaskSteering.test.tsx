import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { SteeringControls } from "./TaskSteering";

it("a status-check gesture cannot become Continue when polling confirms termination", () => {
  const actions = { recheck: vi.fn(), resume: vi.fn(), stop: vi.fn(), error: null };
  const { rerender } = render(<SteeringControls steering={{ ...actions, state: "stop_unconfirmed" }} />);
  const checking = screen.getByRole("button", { name: "Check status" });
  fireEvent.pointerDown(checking);
  rerender(<SteeringControls steering={{ ...actions, state: "stopped" }} />);
  fireEvent.pointerUp(checking);
  fireEvent.click(checking);
  expect(actions.resume).not.toHaveBeenCalled();
  expect(actions.stop).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(actions.resume).toHaveBeenCalledOnce();
});
