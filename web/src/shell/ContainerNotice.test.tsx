import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ContainerNotice } from "./ContainerNotice";

describe("container continuation", () => {
  it("explains host continuation, keeps browser mutation absent, and disappears when the next read admits work", () => {
    const lifecycle = { ready: false, instance: "fixture", reason: "Recovery paused",
      continue_command: "python3 scripts/container.py continue --name fixture" };
    const view = render(<ContainerNotice lifecycle={lifecycle} />);
    expect(screen.getByRole("status", { name: "Container work paused" })).toHaveTextContent("Stop remains available");
    expect(screen.getByText(lifecycle.continue_command)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    view.rerender(<ContainerNotice lifecycle={{ ...lifecycle, ready: true }} />);
    expect(screen.queryByRole("status")).toBeNull();
  });
  it("shows failed recovery evidence and has no optimistic native or loading notice", () => {
    const view = render(<ContainerNotice lifecycle={{ ready: false, instance: null, reason: "Container identity unavailable" }} />);
    expect(screen.getByText("Container identity unavailable")).toBeInTheDocument();
    view.rerender(<ContainerNotice lifecycle={undefined} />);
    expect(screen.queryByRole("status")).toBeNull();
    view.rerender(<ContainerNotice lifecycle={null} />);
    expect(screen.queryByRole("status")).toBeNull();
  });
});
