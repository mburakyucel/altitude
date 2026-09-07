import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const asks = {
  project: "altitude",
  slug: "add-badge",
  title: "Add the badge",
  question: "L3 asks: Which badge colour should the count use?",
  detail: "Accent matches the boards; amber matches the old build.",
  asked: ago(4),
  options: ["Resume", "Reject"],
  kind: "blocked",
};

const stopped = {
  project: "tutor",
  slug: "fix-audio",
  title: "Fix the audio",
  question: "Stopped mid-task: the recording upload fails at 10 minutes",
  detail: "the recording upload fails at 10 minutes",
  asked: ago(60 * 30),
  options: ["Resume", "Reject"],
  kind: "blocked",
};

function overview(queue: unknown[]) {
  return {
    projects: [
      { name: "altitude", managed: true },
      { name: "tutor", managed: true },
    ],
    queue,
    wip: { per_project: {}, machine: 0, waiting: [] },
    quota: { known: false },
    engines: [],
    roots: ["~/Projects"],
  };
}

function mockFetch(queue: unknown[], decideStatus = 200) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview(queue));
    if (url.includes("/api/decide")) {
      return decideStatus === 200
        ? jsonResponse({ ok: true })
        : jsonResponse({ error: "only blocked tasks need a user decision" }, decideStatus);
    }
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Needs you", () => {
  it("says so when nothing waits", async () => {
    mockFetch([]);
    renderApp({ route: "/" });
    expect(await screen.findByText("Nothing needs you.")).toHaveClass("text-muted");
  });

  it("shows one sentence and a Retry that repeats the read when the overview fails", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ error: "altd is restarting" }, 503));
    vi.stubGlobal("fetch", fetchMock);
    const { user } = renderApp({ route: "/" });

    await screen.findByText(/Could not read what needs you\./);
    const reads = fetchMock.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(reads));
  });

  it("groups the cards by project with a project chip and the kind row", async () => {
    mockFetch([asks, stopped]);
    renderApp({ route: "/" });

    const altitude = await screen.findByRole("region", { name: "altitude" });
    const card = within(altitude).getByRole("article", { name: "Add the badge" });
    expect(within(card).getByText("L3 asks")).toBeInTheDocument();
    expect(within(card).getByText("altitude")).toHaveClass("chip");
    expect(within(card).getByText("Which badge colour should the count use?")).toHaveClass("decision-question");
    expect(within(card).getByText("Accent matches the boards; amber matches the old build.")).toBeInTheDocument();
    expect(within(card).getByText("4 min ago")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Resume" })).toHaveClass("btn-primary");
    expect(within(card).getByRole("link", { name: "More context" })).toHaveAttribute(
      "href",
      "/projects/altitude/tasks/add-badge",
    );

    const tutor = screen.getByRole("region", { name: "tutor" });
    const stoppedCard = within(tutor).getByRole("article", { name: "Fix the audio" });
    expect(within(stoppedCard).getByText("Stopped mid-task").closest(".decision-kind")).toHaveAttribute(
      "data-tone",
      "danger",
    );
    expect(within(stoppedCard).getByText("yesterday")).toBeInTheDocument();
    // The why is the detail; when it only repeats the question it is not shown twice.
    expect(within(stoppedCard).getAllByText("the recording upload fails at 10 minutes")).toHaveLength(1);
  });

  it("records a decision and collapses the card", async () => {
    const fetchMock = mockFetch([asks]);
    const { user } = renderApp({ route: "/" });

    const card = await screen.findByRole("article", { name: "Add the badge" });
    await user.click(within(card).getByRole("button", { name: "Resume" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/decide"));
      expect(JSON.parse(String((call?.[1] as RequestInit | undefined)?.body))).toEqual({
        project: "altitude",
        slug: "add-badge",
        option: 0,
      });
    });
    await waitFor(() => expect(screen.queryByRole("article", { name: "Add the badge" })).toBeNull());
  });

  it("keeps the card with one line and a Retry when the decision fails", async () => {
    const fetchMock = mockFetch([asks], 409);
    const { user } = renderApp({ route: "/" });

    const card = await screen.findByRole("article", { name: "Add the badge" });
    await user.click(within(card).getByRole("button", { name: "Reject" }));

    await within(card).findByText(/Could not record the decision\./);
    expect(within(card).getByRole("button", { name: "Resume" })).toBeEnabled();
    const decides = () => fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/decide")).length;
    expect(decides()).toBe(1);
    await user.click(within(card).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(decides()).toBe(2));
  });

  it("selects the card's project when More context opens the task page", async () => {
    mockFetch([stopped]);
    const { router, user } = renderApp({ route: "/" });

    const card = await screen.findByRole("article", { name: "Fix the audio" });
    await user.click(within(card).getByRole("link", { name: "More context" }));
    expect(router.state.location.pathname).toBe("/projects/tutor/tasks/fix-audio");
    expect(localStorage.getItem("altitude.project")).toBe("tutor");
  });
});
