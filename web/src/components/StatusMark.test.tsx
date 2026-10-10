import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BusyLabel, StatusMark } from "./StatusMark";

describe("accessible status cues", () => {
  it("keeps a live status available through progress and completion without deferring it", () => {
    const { rerender } = render(<StatusMark label="Saving…" />);
    const status = screen.getByRole("status", { name: "Saving…" });
    expect(status).not.toHaveAttribute("aria-busy", "true");
    expect(status.querySelector(".spinner")).toBeInTheDocument();
    rerender(<StatusMark label="Saved." busy={false} />);
    expect(screen.getByRole("status", { name: "Saved." })).toBe(status);
    expect(status.querySelector(".spinner")).toBeNull();
  });

  it("names a busy action without nesting a live region inside its busy container", () => {
    const { rerender } = render(<button disabled><BusyLabel busy label="Save" working="Saving…" /></button>);
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(screen.queryByRole("status")).toBeNull();
    rerender(<button><BusyLabel busy={false} label="Save" working="Saving…" /></button>);
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("uses its parent's live region when one already owns the announcement", () => {
    render(<div role="status"><StatusMark label="Restarting" announce={false} /></div>);
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("img", { name: "Restarting" })).toBeInTheDocument();
  });
});
