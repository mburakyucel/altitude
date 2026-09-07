import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TokenUsageSchema, TaskViewSchema } from "../data/api";
import { TokenUsage } from "./TokenUsage";

const snapshot = (over: Record<string, unknown> = {}) => TokenUsageSchema.parse({
  status: "partial", total_tokens: 1_200, input_tokens: 1_000, output_tokens: 200,
  cache_read_tokens: 800, cache_write_tokens: null, reasoning_tokens: 50,
  checked_at: new Date().toISOString(), observed_at: new Date(Date.now() - 300_000).toISOString(),
  finalized_at: null, notes: ["Helper coverage is unknown."], sessions: [], ...over,
});

afterEach(() => vi.useRealTimers());

describe("passive task token usage", () => {
  it("keeps absent historical counters unknown and real observed zero distinct", async () => {
    const task = TaskViewSchema.parse({ slug: "old-task" });
    const view = render(<TokenUsage usage={task.token_usage} />);
    expect(screen.getByText("Token usage unknown")).toBeVisible();
    expect(screen.getByText("Unknown coverage")).toBeVisible();
    expect(screen.getByText("Freshness unknown")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /Token usage unknown/ }));
    expect(screen.getByText("No attributable session counters available.")).toBeVisible();
    expect(screen.getAllByText("Unknown")).toHaveLength(5);
    view.rerender(<TokenUsage usage={snapshot({ status: "observed", total_tokens: 0, input_tokens: 0, output_tokens: 0 })} />);
    expect(screen.getByText("0 observed tokens")).toBeVisible();
    expect(screen.getByText("Observed coverage")).toBeVisible();
  });

  it("shows live increases without closing details, cache subsets, and distinct collection and evidence ages", async () => {
    const view = render(<TokenUsage usage={snapshot()} running />);
    const toggle = screen.getByRole("button", { name: /1,200 observed tokens/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Input")).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(screen.getByText("Checked just now")).toBeVisible();
    expect(screen.getByText("Usage observed 5 min ago.")).toBeVisible();
    expect(screen.getByText("Cache read · in input").nextSibling).toHaveTextContent("800");
    expect(screen.getByText("Cache write · in input").nextSibling).toHaveTextContent("Unknown");
    expect(screen.getByText("Reasoning · in output").nextSibling).toHaveTextContent("50");
    view.rerender(<TokenUsage usage={snapshot({ total_tokens: 1_500, output_tokens: 500 })} running />);
    expect(screen.getByRole("button", { name: /1,500 observed tokens/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Output").nextSibling).toHaveTextContent("500");
    expect(screen.getByText("Helper coverage is unknown.")).toBeVisible();
    expect(screen.getByText("Partial totals sum available counters and are a lower bound.")).toBeVisible();
    await userEvent.click(toggle);
    expect(screen.queryByText("Input")).not.toBeInTheDocument();
  });

  it("groups attempts by the recorded engine, separating owner, attributable helpers and an unsplit provider total", async () => {
    const usage = snapshot({ total_tokens: 1_800, sessions: [
      { engine: "alpha", session_id: "owner-first", attempt: 1, role: "owner", status: "observed", total_tokens: 1_000 },
      { engine: "alpha", session_id: "helper", parent_session_id: "owner-first", attempt: 1, role: "delegated", status: "observed", total_tokens: 200 },
      { engine: "beta", session_id: "owner-second", attempt: 2, role: "provider", status: "partial", total_tokens: 600, notes: ["Owner/helper split unavailable."] },
      { engine: "beta", session_id: "owner-third", attempt: 3, role: "owner", status: "unknown", notes: ["Record missing."] },
    ] });
    render(<TokenUsage usage={usage} engines={[{ engine: "alpha", label: "Engine A" }]} />);
    await userEvent.click(screen.getByRole("button"));
    const first = within(screen.getByRole("region", { name: "Engine A usage" }));
    expect(first.getByRole("heading", { name: "Engine A 1,200 observed tokens" })).toBeVisible();
    expect(first.getByText(/L2 owner · attempt 1 · 1,000 tokens/)).toBeVisible();
    expect(first.getByText(/Delegated · attempt 1 · 200 tokens/)).toBeVisible();
    expect(first.getByText("Session helper · parent owner-first")).toBeVisible();
    const second = within(screen.getByRole("region", { name: "beta usage" }));
    expect(second.getByText(/Provider total · helpers unsplit · attempt 2 · 600 tokens/)).toBeVisible();
    expect(second.getByText(/L2 owner · attempt 3 · tokens unknown/)).toBeVisible();
    expect(second.getByRole("heading", { name: "beta 600 observed tokens" })).toBeVisible();
  });

  it("marks a stalled collector stale while retaining its total, then retains the final observation without aging it stale", () => {
    vi.useFakeTimers();
    const checked = new Date().toISOString();
    const view = render(<TokenUsage usage={snapshot({ checked_at: checked })} running />);
    act(() => vi.advanceTimersByTime(80_000));
    expect(screen.getByText("Stale · checked 1 min ago")).toBeVisible();
    expect(screen.getByText("1,200 observed tokens")).toBeVisible();
    view.rerender(<TokenUsage usage={snapshot({ checked_at: checked, finalized_at: checked, notes: ["Source removed; retained observations are partial."] })} running />);
    act(() => vi.advanceTimersByTime(86_400_000));
    expect(screen.queryByText(/Stale/)).not.toBeInTheDocument();
    expect(screen.getByText(/Finalized/)).toBeVisible();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Source removed; retained observations are partial.")).toBeVisible();
    expect(screen.getByText(/Last checked/)).toBeVisible();
  });
});
