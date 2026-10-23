import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, onTestFinished, vi } from "vitest";
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
    if (path.endsWith(".gif")) return new Response("GIF89a", { headers: { "Content-Type": "image/gif" } });
    if (path.startsWith("/api/captures/")) return read();
    if (path.startsWith("/api/overview")) return json({ projects: [], queue: [], wip: { per_project: {}, machine: 0 }, quota: {} });
    return json({ name: "atlas" });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("TaskCaptures", () => {
  it("loads one reply's captures, each at its recorded size with its meta, and opens them in the viewer as one run", async () => {
    let release!: (response: Response) => void;
    const fetch = mockCaptures(() => new Promise((resolve) => { release = resolve; }));
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
    onTestFinished(() => { Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal"); });
    const { user } = renderApp({ route });
    expect(await screen.findByText("Loading captures…")).toBeVisible();
    expect(screen.getByRole("link", { name: "← Back to conversation" })).toHaveAttribute("href", "/projects/atlas/tasks/send-flow");
    release(json(view));
    expect(await screen.findByRole("heading", { name: "Captures from validation run 7" })).toBeVisible();
    expect(screen.getByText("Attached 5 min ago")).toBeVisible();
    expect(screen.queryByText("Loading captures…")).toBeNull();
    expect(fetch.mock.calls.map(([path]) => String(path)).filter((path) => path.startsWith("/api/captures/"))).toEqual(["/api/captures/atlas/send-flow/m-7"]);
    const phone = screen.getByRole("figure", { name: "Phone send" });
    expect(within(phone).getByText("299 KiB · 9.8 s · 11 frames")).toBeVisible();
    // A grey block at the capture's saved size holds its place while it loads.
    const image = within(phone).getByRole("img", { name: "Phone send" });
    expect(image).toHaveAttribute("src", view.captures[0]!.url);
    expect(image).toHaveAttribute("width", "390");
    expect(image).toHaveAttribute("height", "600");
    expect(image).toHaveAttribute("data-loading");
    fireEvent.load(image);
    expect(image).not.toHaveAttribute("data-loading");
    expect(within(screen.getByRole("figure", { name: "Desktop send" })).getByText("1 KiB · 2 s · 1 frame")).toBeVisible();
    vi.stubGlobal("URL", class extends URL { static override createObjectURL() { return "blob:capture"; } static override revokeObjectURL() {} });
    await user.click(within(phone).getByRole("button", { name: "Open capture Phone send" }));
    const dialog = screen.getByRole("dialog", { name: "Image Phone send" });
    expect(dialog).toHaveTextContent("Validation run 7");
    expect(dialog).toHaveTextContent("1 / 2");
    // A GIF copied would keep only its first frame, so captures offer no Copy.
    expect(within(dialog).queryByRole("button", { name: "Copy image" })).toBeNull();
    // A capture's title has no extension; its download takes the GIF's.
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { saved.push(this.download); });
    const download = within(dialog).getByRole("button", { name: "Download image" });
    await waitFor(() => expect(download).toBeEnabled());
    await user.click(download);
    expect(saved).toEqual(["Phone send.gif"]);
  });

  it("shows an unavailable capture beside the others and retries it with a fresh request", async () => {
    mockCaptures();
    const { user } = renderApp({ route });
    const desktop = await screen.findByRole("figure", { name: "Desktop send" });
    fireEvent.load(screen.getByRole("figure", { name: "Phone send" }).querySelector("img")!);
    fireEvent.error(desktop.querySelector("img")!);
    expect(within(desktop).getByRole("group", { name: "Desktop send unavailable" })).toHaveTextContent("Capture unavailable");
    expect(desktop.querySelector("img")).toBeNull();
    expect(within(screen.getByRole("figure", { name: "Phone send" })).getByRole("img", { name: "Phone send" })).toBeVisible();
    await user.click(within(desktop).getByRole("button", { name: "Retry" }));
    const retried = within(desktop).getByRole("img", { name: "Desktop send" });
    expect(retried).toHaveAttribute("src", `${view.captures[1]!.url}?retry=1`);
    expect(retried).toHaveAttribute("data-loading");
    fireEvent.load(retried);
    expect(retried).not.toHaveAttribute("data-loading");
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
