import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TokenUsageSchema, TaskViewSchema } from "../data/api";
import { TaskContext, TokenUsage } from "./TokenUsage";

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
    expect(screen.getAllByText("Unknown")).toHaveLength(10);
    expect(screen.getByText("Helper evidence unavailable.")).toBeVisible();
    expect(screen.queryByText("No helpers observed.")).not.toBeInTheDocument();
    view.rerender(<TokenUsage usage={snapshot({ status: "observed", total_tokens: 0, input_tokens: 0, output_tokens: 0 })} />);
    expect(screen.getByText("0 tokens processed")).toBeVisible();
    expect(screen.getByText("Observed coverage")).toBeVisible();
  });

  it("separates processed input, cache and output from the request count and explains repeated input", async () => {
    render(<TokenUsage usage={snapshot({ requests: 229 })} />);
    expect(screen.getByText("229 model requests")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /1,200 tokens processed/ }));
    expect(screen.getByText("Input processed").nextSibling).toHaveTextContent("1,000");
    expect(screen.getByText("Output generated").nextSibling).toHaveTextContent("200");
    expect(screen.getByText("Model requests").nextSibling).toHaveTextContent("229");
    expect(screen.getByText(/sends the conversation so far again.*not a bill, quota use or the current context/)).toBeVisible();
  });

  it("shows current context only from a reliable reading and never from cumulative totals", () => {
    const view = render(<TaskContext context={null} running />);
    expect(screen.getByText("Current context")).toBeVisible();
    expect(screen.getByText(/unavailable · no reliable reading for the current session/)).toBeVisible();
    view.rerender(<TaskContext context={{ tokens: 188_131, window: 1_000_000, percent: 18.8, observed_at: new Date().toISOString() }} running />);
    expect(screen.getByText(/188,131 tokens · 19% of 1,000,000 · just now/)).toBeVisible();
    view.rerender(<TaskContext context={{ tokens: 4_000, window: 1_000_000, percent: 0.4 }} running={false} />);
    expect(screen.getByText("Context at last request")).toBeVisible();
    expect(screen.getByText(/4,000 tokens · <1% of 1,000,000/)).toBeVisible();
    view.rerender(<TaskContext context={{ tokens: 50_000, window: null, percent: null }} running />);
    expect(screen.getByText(/50,000 tokens · window not reported/)).toBeVisible();
  });

  it("shows live increases without closing details, cache subsets, and distinct collection and evidence ages", async () => {
    const view = render(<TokenUsage usage={snapshot()} running />);
    const toggle = screen.getByRole("button", { name: /1,200 tokens processed/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Input processed")).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(screen.getByText("Checked just now")).toBeVisible();
    expect(screen.getByText("Usage observed 5 min ago.")).toBeVisible();
    expect(screen.getByText("Cache read · in input").nextSibling).toHaveTextContent("800");
    expect(screen.getByText("Cache write · in input").nextSibling).toHaveTextContent("Unknown");
    expect(screen.getByText("Reasoning · in output").nextSibling).toHaveTextContent("50");
    view.rerender(<TokenUsage usage={snapshot({ total_tokens: 1_500, output_tokens: 500 })} running />);
    expect(screen.getByRole("button", { name: /1,500 tokens processed/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Output generated").nextSibling).toHaveTextContent("500");
    expect(screen.getByText("Helper coverage is unknown.")).toBeVisible();
    expect(screen.getByText("Partial totals sum available counters and are a lower bound.")).toBeVisible();
    await userEvent.click(toggle);
    expect(screen.queryByText("Input processed")).not.toBeInTheDocument();
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
    expect(first.getByRole("heading", { name: "Engine A 1,200 tokens processed" })).toBeVisible();
    expect(first.getByText(/L2 owner · attempt 1 · 1,000 tokens/)).toBeVisible();
    expect(first.getByText(/Delegated · attempt 1 · 200 tokens/)).toBeVisible();
    expect(first.getByText("Session helper · parent owner-first")).toBeVisible();
    const second = within(screen.getByRole("region", { name: "beta usage" }));
    expect(second.getByText(/Provider total · helpers unsplit · attempt 2 · 600 tokens/)).toBeVisible();
    expect(second.getByText(/L2 owner · attempt 3 · tokens unknown/)).toBeVisible();
    expect(second.getByRole("heading", { name: "beta 600 tokens processed" })).toBeVisible();
  });

  it("marks a stalled collector stale while retaining its total, then retains the final observation without aging it stale", () => {
    vi.useFakeTimers();
    const checked = new Date().toISOString();
    const view = render(<TokenUsage usage={snapshot({ checked_at: checked })} running />);
    act(() => vi.advanceTimersByTime(80_000));
    expect(screen.getByText("Stale · checked 1 min ago")).toBeVisible();
    expect(screen.getByText("1,200 tokens processed")).toBeVisible();
    view.rerender(<TokenUsage usage={snapshot({ checked_at: checked, finalized_at: checked, notes: ["Source removed; retained observations are partial."] })} running />);
    act(() => vi.advanceTimersByTime(86_400_000));
    expect(screen.queryByText(/Stale/)).not.toBeInTheDocument();
    expect(screen.getByText(/Finalized/)).toBeVisible();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Source removed; retained observations are partial.")).toBeVisible();
    expect(screen.getByText(/Last checked/)).toBeVisible();
  });

  it("audits unique helper identities and owner attempts without adding their counters again or inventing missing depth", async () => {
    const helper = { engine: "alpha", session_id: "direct-helper", role: "delegated", status: "observed", total_tokens: 200,
      input_tokens: 150, output_tokens: 50, parent_session_id: "owner-first", owner_session_id: "owner-first", parentage: "thread", depth: 1, attempts: [1, 2] };
    render(<TokenUsage usage={snapshot({ total_tokens: 1_800, helpers: {
      status: "partial", observed_count: 3, direct_count: 1, descendant_count: 1, unclassified_count: 1, total_tokens: 200,
      sessions: [helper,
        { ...helper, session_id: "descendant", parent_session_id: "direct-helper", depth: 2, total_tokens: null, input_tokens: null, output_tokens: null, provider_total_tokens: 600, status: "partial" },
        { ...helper, session_id: "owner-linked", parentage: "owner", depth: null, total_tokens: null, input_tokens: null, output_tokens: null, status: "unknown" },
      ],
    }, sessions: [
      { engine: "alpha", session_id: "owner-first", attempt: 1, role: "owner", total_tokens: 1_000 },
      helper, { ...helper, session_id: "descendant", role: "provider", total_tokens: 600, input_tokens: 500, output_tokens: 100, cache_read_tokens: 300 },
    ] })} engines={[{ engine: "alpha", label: "Engine A" }]} />);
    expect(screen.queryByRole("region", { name: "L1 helpers observed" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button"));
    const helpers = within(screen.getByRole("region", { name: "L1 helpers observed" }));
    expect(helpers.getByText("Unique helpers").nextSibling).toHaveTextContent("3");
    expect(helpers.getByText("Attributable helper tokens").nextSibling).toHaveTextContent("200");
    expect(helpers.getByText("1 owner-linked · depth unknown.")).toBeVisible();
    expect(helpers.getAllByText("Owner attempt context: 1, 2.")).toHaveLength(3);
    expect(helpers.getByText("Parent direct-helper · L2 owner owner-first")).toBeVisible();
    expect(helpers.getByText("Owner linkage owner-first · L2 owner owner-first")).toBeVisible();
    expect(helpers.getByRole("heading", { name: /Engine A · Owner-linked helper · depth unknown · tokens unknown/ })).toBeVisible();
    expect(helpers.getByText("Unsplit provider total: 600 tokens · may include descendants; not attributable to this helper alone.")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Engine A 1,800 tokens processed" })).toBeVisible();
    expect(screen.getByText(/Provider total · helpers unsplit/)).toBeVisible();
    const provider = within(screen.getByText(/Provider total · helpers unsplit/).parentElement!);
    expect(provider.getByText("Input processed").nextSibling).toHaveTextContent("500");
    expect(provider.getByText("Cache read · in input").nextSibling).toHaveTextContent("300");
    expect(screen.queryByText(/Delegated · attempt/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("Unique helpers")).not.toBeInTheDocument();
    expect(screen.queryByText(/Unsplit provider total:/)).not.toBeInTheDocument();
  });

  it("distinguishes observed empty helper discovery from unavailable evidence", async () => {
    const view = render(<TokenUsage usage={snapshot({ helpers: { status: "partial", observed_count: 0, direct_count: 0, descendant_count: 0, total_tokens: null, sessions: [] } })} />);
    await userEvent.click(screen.getByRole("button"));
    expect(screen.getByText("No helpers observed.")).toBeVisible();
    expect(screen.getByText(/Partial discovery:/)).toBeVisible();
    view.rerender(<TokenUsage usage={snapshot({ helpers: { status: "unknown", observed_count: null, direct_count: null, descendant_count: null, total_tokens: null, sessions: [] } })} />);
    expect(screen.getByText("Helper evidence unavailable.")).toBeVisible();
    expect(screen.queryByText("No helpers observed.")).not.toBeInTheDocument();
    expect(screen.queryByText(/0 spawned/)).not.toBeInTheDocument();
  });
});
