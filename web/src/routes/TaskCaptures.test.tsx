import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

const route = "/projects/atlas/tasks/send-flow/captures/m-7";
const url = (name: string) => `/api/captures/atlas/send-flow/m-7/${name}.gif`;
const view = {
  run: 7, at: new Date(Date.now() - 5 * 60_000).toISOString(), conversation_url: "/projects/atlas/tasks/send-flow",
  captures: [
    { title: "Phone send", url: url("a".repeat(64)), bytes: 306_000, width: 390, height: 600, frames: 11, seconds: 9.8 },
    { title: "Desktop send", url: url("b".repeat(64)), bytes: 900, width: 800, height: 500, frames: 1, seconds: 2 },
  ],
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function mockCaptures(read: () => Response | Promise<Response> = () => json(view)) {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.startsWith("/api/captures/")) return read();
    if (path.startsWith("/api/overview")) return json({ projects: [], queue: [], wip: { per_project: {}, machine: 0 }, quota: {} });
    return json({ name: "atlas" });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("TaskCaptures", () => {
  it("loads one reply's captures, each looping at its recorded size with its meta", async () => {
    let release!: (response: Response) => void;
    const fetch = mockCaptures(() => new Promise((resolve) => { release = resolve; }));
    renderApp({ route });
    expect(await screen.findByText("Loading captures…")).toBeVisible();
    expect(screen.getByRole("link", { name: "← Back to conversation" })).toHaveAttribute("href", "/projects/atlas/tasks/send-flow");
    release(json(view));
    expect(await screen.findByRole("heading", { name: "Captures from validation run 7" })).toBeVisible();
    expect(screen.getByText("Attached 5 min ago")).toBeVisible();
    expect(screen.queryByText("Loading captures…")).toBeNull();
    expect(fetch.mock.calls.map(([path]) => String(path)).filter((path) => path.startsWith("/api/captures/"))).toEqual(["/api/captures/atlas/send-flow/m-7"]);
    const phone = screen.getByRole("figure", { name: "Phone send" });
    expect(within(phone).getByText("299 KiB · 9.8 s · 11 frames")).toBeVisible();
    expect(within(phone).getByText("Loading capture…")).toBeVisible();
    const image = phone.querySelector("img")!;
    expect(image).toHaveAttribute("src", view.captures[0]!.url);
    expect(image).toHaveAttribute("width", "390");
    expect(image).toHaveAttribute("height", "600");
    fireEvent.load(image);
    expect(within(phone).getByRole("img", { name: "Phone send" })).toBeVisible();
    expect(within(phone).queryByText("Loading capture…")).toBeNull();
    expect(within(screen.getByRole("figure", { name: "Desktop send" })).getByText("1 KiB · 2 s · 1 frame")).toBeVisible();
  });

  it("shows an unavailable capture beside the others and retries it with a fresh request", async () => {
    mockCaptures();
    const { user } = renderApp({ route });
    const desktop = await screen.findByRole("figure", { name: "Desktop send" });
    fireEvent.load(screen.getByRole("figure", { name: "Phone send" }).querySelector("img")!);
    fireEvent.error(desktop.querySelector("img")!);
    expect(within(desktop).getByRole("alert")).toHaveTextContent("Capture unavailable.");
    expect(desktop.querySelector("img")).toBeNull();
    expect(within(desktop).queryByText(/KiB/)).toBeNull();
    expect(within(screen.getByRole("figure", { name: "Phone send" })).getByRole("img", { name: "Phone send" })).toBeVisible();
    await user.click(within(desktop).getByRole("button", { name: "Retry capture" }));
    expect(within(desktop).getByText("Loading capture…")).toBeVisible();
    expect(within(desktop).queryByRole("alert")).toBeNull();
    const retried = desktop.querySelector("img")!;
    expect(retried).toHaveAttribute("src", `${view.captures[1]!.url}?retry=1`);
    fireEvent.load(retried);
    expect(within(desktop).getByRole("img", { name: "Desktop send" })).toBeVisible();
    expect(within(desktop).queryByText("Loading capture…")).toBeNull();
  });

  it.each([[404, "These captures could not be loaded."], [403, "Access to these captures is unavailable."]])("explains an unavailable read (%s) and retries to the captures", async (status, explanation) => {
    let read = 0;
    mockCaptures(() => ++read === 1 ? json({ error: "Capture unavailable" }, status) : json(view));
    const { user } = renderApp({ route });
    expect(await screen.findByRole("heading", { name: "Capture unavailable" })).toBeVisible();
    expect(screen.getByText(new RegExp(explanation))).toBeVisible();
    expect(screen.getByRole("link", { name: "← Back to conversation" })).toHaveAttribute("href", "/projects/atlas/tasks/send-flow");
    expect(screen.queryByRole("figure")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { name: "Captures from validation run 7" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Capture unavailable" })).toBeNull();
    expect(screen.getAllByRole("figure")).toHaveLength(2);
  });
});
