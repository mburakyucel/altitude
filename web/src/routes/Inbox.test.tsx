import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const overview = {
  projects: [],
  queue: [
    {
      project: "altitude",
      slug: "fix-timer",
      kind: "proposed",
      class: "M",
      title: "Fix the timer",
      question: "The toast timer drifts; proposal attached.",
      asked: new Date(Date.now() - 5 * 60_000).toISOString(),
      options: ["Approve", "Revise", "Reject"],
    },
  ],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
  now: new Date().toISOString(),
};

// A card as altitude/tasks.py emits it since decision 46: a `context` line, a plain-words question,
// short option labels, and the reasoning (ids, file names, conditions) parked in `detail`.
const executive = {
  project: "altitude",
  slug: "quota-reader",
  kind: "decision",
  class: "M",
  title: "Quota reader",
  context:
    "Altitude only learns your window usage from an interactive session. Overnight it dispatches blind.",
  question: "When no fresh reading can be had, should Altitude stop dispatching or carry on?",
  asked: new Date(Date.now() - 12 * 60_000).toISOString(),
  options: ["Stop until a reading returns", "Carry on and warn"],
  detail: "Reserve line comes from decision 31; the headless reader is I-007 in altitude/quota.py.",
};

/** The <article> a card's own text sits in — everything else is scoped inside it. */
function cardFor(text: string | RegExp): HTMLElement {
  const card = screen.getByText(text).closest("article");
  if (!card) throw new Error(`no card around ${String(text)}`);
  return card;
}

/** True when `first` precedes `second` in document order — "above" as the reader sees it. */
function precedes(first: Element, second: Element): boolean {
  return Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);
}

describe("Inbox", () => {
  it("renders the decision queue and posts a decision with the option index", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/decide")) return jsonResponse({ ok: true, state: "approved" });
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route: "/" });
    await screen.findByText("Fix the timer");
    expect(screen.getByText("Decisions (1)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => {
      const decide = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/decide"));
      expect(decide).toBeDefined();
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/decide"));
    const init = call?.[1];
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      project: "altitude",
      slug: "fix-timer",
      option: 0,
    });
  });

  it("sends the typed note with the decision", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/decide")) return jsonResponse({ ok: true, state: "approved" });
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route: "/" });
    await screen.findByText("Fix the timer");

    await user.type(screen.getByLabelText("Note for Fix the timer"), "ship it");
    await user.click(screen.getByRole("button", { name: "Revise" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/decide"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/decide"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      project: "altitude",
      slug: "fix-timer",
      option: 1,
      note: "ship it",
    });
  });

  // A task Altitude is holding (blocked + resume_after) leaves the Decisions queue and joins the
  // waiting list; "(resume)" is what tells you nobody has to dispatch it by hand.
  it("marks a waiting entry Altitude will resume itself", async () => {
    const waiting = {
      ...overview,
      queue: [],
      wip: {
        per_project: { altitude: 1 },
        machine: 1,
        waiting: [
          { project: "altitude", slug: "held-task", why: "resume" },
          { project: "altitude", slug: "next-up", why: "dispatch" },
        ],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes("/api/overview")
          ? jsonResponse(waiting)
          : jsonResponse({ error: "not found" }, 404),
      ),
    );
    renderApp({ route: "/" });

    expect(
      await screen.findByText("Waiting: altitude/held-task (resume), altitude/next-up"),
    ).toBeInTheDocument();
  });

  it("shows the empty state when nothing is queued", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes("/api/overview")
          ? jsonResponse({ ...overview, queue: [] })
          : jsonResponse({ error: "not found" }, 404),
      ),
    );
    renderApp({ route: "/" });
    await screen.findByText("Nothing needs you.");
    expect(screen.getByText("No FYIs yet.")).toBeInTheDocument();
  });
});

// Decision 46 — the card is executive. What a reader must see: the situation above the one
// question, short options, and nothing else in front of the question.
describe("Inbox decision card (executive shape)", () => {
  function stubQueue(queue: unknown[]) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes("/api/overview")
          ? jsonResponse({ ...overview, queue })
          : jsonResponse({ error: "not found" }, 404),
      ),
    );
  }

  it("puts the context line above the question and the reasoning behind it", async () => {
    stubQueue([executive]);
    const { user } = renderApp({ route: "/" });

    const question = await screen.findByText(
      "When no fresh reading can be had, should Altitude stop dispatching or carry on?",
    );
    const card = cardFor("Quota reader");
    const context = within(card).getByText(/Altitude only learns your window usage/);
    expect(precedes(context, question)).toBe(true);

    // The ids and file names are in the card but not on show until you ask for them.
    const reasoning = within(card).getByText(/Reserve line comes from decision 31/);
    expect(reasoning).not.toBeVisible();
    expect(precedes(question, reasoning)).toBe(true);

    await user.click(within(card).getByText("Why"));
    expect(reasoning).toBeVisible();
  });

  it("renders a card with no context or reasoning exactly as before", async () => {
    stubQueue([executive, overview.queue[0]]);
    renderApp({ route: "/" });

    await screen.findByText("Fix the timer");
    const plain = cardFor("Fix the timer");
    const rich = cardFor("Quota reader");

    // The old, thin card gains nothing: no situation line, no disclosure, same question and buttons.
    expect(within(plain).queryByText("Why")).toBeNull();
    expect(within(plain).getByText("The toast timer drifts; proposal attached.")).toBeVisible();
    expect(
      within(plain)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["Approve", "Revise", "Reject"]);

    // ...while the card that carries them shows both, so the difference is the data, not the route.
    expect(within(rich).getByText("Why")).toBeVisible();
    expect(within(rich).getByText(/Altitude only learns your window usage/)).toBeVisible();
  });
});
