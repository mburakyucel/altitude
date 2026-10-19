import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Overview, Update } from "../data/api";
import { renderApp } from "../test/render";

const notes = "https://github.com/example/altitude/releases/tag/v0.2.0";
const available: Update = { current: "v0.1.0", check: true, automatic: true, automatic_pending: true, installed: null,
  command: "alt update", available: { version: "v0.2.0", notes }, waiting_for: ["the validation run of atlas/check-the-lane"] };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

function fixture(initial: Update | null = available, repair: Overview["repair"] = null) {
  let update = initial;
  let automatic = initial?.automatic ?? true;
  let refuse = false;
  const posts: { path: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const machine = { altitude_repository: "example/altitude", update_automatic: automatic };
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posts.push({ path, body });
      if (refuse) return json({ error: "Change refused." }, 403);
      if (path === "/api/update-automatic") automatic = body.enabled;
      if (path === "/api/update-check") update = { ...update!, check: body.enabled, available: body.enabled ? available.available : null };
      update = { ...update!, automatic: !!update?.check && automatic, automatic_pending: !!update?.check && automatic };
      if (path === "/api/update") update = { ...update!, attempt: { version: body.version, state: "running" } };
      return json({ ...machine, update_automatic: automatic, update });
    }
    if (path === "/api/machine") return json(machine);
    if (path === "/api/project-model-settings") return json({ rows: [] });
    if (path === "/api/repairs") return json({ running: null, engine: "Claude", repairs: [] });
    if (path === "/api/voice") return json({ backend: "host", selection: "fixture", host: { state: "ready", download_bytes: 698435338 } });
    if (path === "/api/overview") return json({ projects: [], queue: [], engines: [], update, repair,
      wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } });
    return json({});
  }));
  return { posts, refuse: (value: boolean) => { refuse = value; } };
}

describe("Installed release updates", () => {
  it("names what a pending automatic update waits for, joining the last with and", async () => {
    fixture({ ...available, waiting_for: ["the browser terminal open in atlas", "the adversarial review of atlas/a", "the validation run of atlas/b"] });
    renderApp({ route: "/" });
    expect(await screen.findByText("Altitude v0.2.0 installs automatically at the next quiet moment. Waiting for the browser terminal open in atlas, the adversarial review of atlas/a and the validation run of atlas/b.")).toBeVisible();
  });
  it("says only that it installs at the next quiet moment when nothing is in the way", async () => {
    fixture({ ...available, waiting_for: [] });
    renderApp({ route: "/" });
    expect(await screen.findByText("Altitude v0.2.0 installs automatically at the next quiet moment.")).toBeVisible();
  });
  it("offers manual installation when a previously attempted version is offered again", async () => {
    fixture({ ...available, automatic_pending: false,
      attempt: { version: "v0.3.0", state: "failed", error: "the download failed its certificate check." } });
    renderApp({ route: "/" });
    expect(await screen.findByRole("button", { name: "Update" })).toBeVisible();
    expect(screen.queryByText(/installs automatically/)).toBeNull();
  });
  it("waits automatically, switches to a prompt, and preserves the preference with checking off", async () => {
    const state = fixture();
    const { user } = renderApp({ route: "/settings" });
    expect(await screen.findByText("Altitude v0.2.0 installs automatically at the next quiet moment. Waiting for the validation run of atlas/check-the-lane.")).toBeVisible();
    expect(screen.getByText(/v0\.2\.0 waits for the validation run of atlas\/check-the-lane/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Update" })).toBeNull();
    const automatic = screen.getByRole("switch", { name: /Automatic updates/ });
    expect(automatic).toBeChecked();
    await user.click(automatic);
    await waitFor(() => expect(automatic).not.toBeChecked());
    expect(await screen.findByRole("button", { name: "Update" })).toBeVisible();
    await user.click(automatic);
    await waitFor(() => expect(automatic).toBeChecked());
    await user.click(screen.getByRole("switch", { name: /Check for new versions/ }));
    await waitFor(() => expect(automatic).toBeDisabled());
    expect(automatic).not.toBeChecked();
    expect(screen.queryByRole("status", { name: "New version" })).toBeNull();
    expect(state.posts).toEqual([
      { path: "/api/update-automatic", body: { enabled: false } },
      { path: "/api/update-automatic", body: { enabled: true } },
      { path: "/api/update-check", body: { enabled: false } },
    ]);
  });

  it("keeps a refused preference unchanged and permits another attempt", async () => {
    const state = fixture();
    state.refuse(true);
    const { user } = renderApp({ route: "/settings" });
    const automatic = await screen.findByRole("switch", { name: /Automatic updates/ });
    await user.click(automatic);
    expect(await screen.findByRole("alert")).toHaveTextContent("Change refused.");
    expect(automatic).toBeChecked();
    state.refuse(false);
    await user.click(automatic);
    await waitFor(() => expect(automatic).not.toBeChecked());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("disables both preferences until an in-flight save finishes", async () => {
    fixture();
    const original = globalThis.fetch;
    let release = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/update-automatic") await gate;
      return original(input, init);
    }));
    const { user } = renderApp({ route: "/settings" });
    const automatic = await screen.findByRole("switch", { name: /Automatic updates/ });
    try {
      await user.click(automatic);
      expect(automatic).toBeDisabled();
      expect(screen.getByRole("switch", { name: /Check for new versions/ })).toBeDisabled();
      expect(screen.getByText("Saving…")).toBeVisible();
    } finally {
      release();
    }
    await waitFor(() => expect(automatic).toBeEnabled());
    expect(automatic).not.toBeChecked();
  });

  it("offers explicit retry for automatic failures and shows a later success even after dismissal", async () => {
    const state = fixture({ ...available, attempt: { version: "v0.2.0", state: "failed", error: "the download failed its certificate check." } });
    const { user, queryClient } = renderApp({ route: "/" });
    expect(await screen.findByText(/The update to v0.2.0 did not finish/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/Installing Altitude v0.2.0/)).toBeVisible();
    expect(state.posts).toEqual([{ path: "/api/update", body: { version: "v0.2.0" } }]);
    const set = (update: Update) => act(() => {
      queryClient.setQueryData<Overview>(["overview"], (value) => value && { ...value, update });
    });
    set(available);
    await user.click(await screen.findByRole("button", { name: "Dismiss new version notice" }));
    set({ ...available, current: "v0.2.0", available: null, installed: { version: "v0.2.0", notes } });
    expect(await screen.findByText(/Updated to v0.2.0/)).toBeVisible();
    expect(screen.getByRole("link", { name: "What’s new" })).toHaveAttribute("href", notes);
    await user.click(screen.getByRole("button", { name: "Dismiss new version notice" }));
    expect(localStorage.getItem("altitude.update.dismissed")).toBe("v0.2.0:installed");
    set({ ...available, available: { version: "v0.3.0", notes } });
    expect(await screen.findByText(/Altitude v0.3.0 installs automatically at the next quiet moment/)).toBeVisible();
  });

  it("leads with Repair for a lasting cause and opens the sheet filled with it", async () => {
    fixture({ ...available, automatic_pending: false, attempt: { version: "v0.2.0", state: "failed", error: "the download failed its certificate check." } });
    const { user } = renderApp({ route: "/" });
    expect(await screen.findByText("The update to v0.2.0 did not finish: the download failed its certificate check. Altitude v0.1.0 keeps running.")).toBeVisible();
    const buttons = within(screen.getByRole("status", { name: "New version" })).getAllByRole("button").map((button) => button.textContent);
    expect(buttons.slice(0, 2)).toEqual(["Repair", "Try again"]);
    expect(screen.getByRole("button", { name: "Repair" })).toHaveClass("btn-primary");
    expect(screen.getByRole("button", { name: "Try again" })).not.toHaveClass("btn-primary");
    await user.click(screen.getByRole("button", { name: "Repair" }));
    expect(await screen.findByRole("heading", { name: "Start a repair?" })).toBeVisible();
    expect(screen.getByRole("textbox", { name: "What’s wrong?" })).toHaveValue("The update to v0.2.0 failed: the download failed its certificate check.");
    expect(await screen.findByText("Claude · uses your plan like a small task")).toBeVisible();
  });

  it.each([[{ passing: true }, "Try again"], [{ passing: true, retry: true }, "Repair"], [{}, "Repair"]] as const)(
    "fills the button a cause %j calls for", async (flags, filled) => {
      fixture({ ...available, automatic_pending: false, attempt: { version: "v0.2.0", state: "failed", error: "the connection was reset.", ...flags } });
      renderApp({ route: "/" });
      await screen.findByText(/did not finish: the connection was reset\./);
      const notice = screen.getByRole("status", { name: "New version" });
      expect(within(notice).getAllByRole("button")[0]).toHaveTextContent(filled);
      expect(within(notice).getByRole("button", { name: filled })).toHaveClass("btn-primary");
    });

  it("says the stopped updater's own words without a cause", async () => {
    fixture({ ...available, automatic_pending: false, attempt: { version: "v0.2.0", state: "failed" } });
    renderApp({ route: "/" });
    expect(await screen.findByText("The update to v0.2.0 did not finish: the updater stopped without saying why. Altitude v0.1.0 keeps running.")).toBeVisible();
  });

  it.each([[{ id: "r1", route: "app", title: "Failed update" }, "Repair running · Open"], [{ id: null, route: "terminal" }, "A repair is running in a terminal on this computer"]] as const)(
    "gives way to the running repair %j", async (running, text) => {
      fixture({ ...available, automatic_pending: false, attempt: { version: "v0.2.0", state: "failed", error: "the download failed its certificate check." } },
        { running, questions: [] });
      renderApp({ route: "/" });
      const notice = await screen.findByRole("status", { name: "New version" });
      await waitFor(() => expect(notice).toHaveTextContent(text));
      expect(within(notice).queryByRole("button", { name: "Repair" })).toBeNull();
      expect(within(notice).queryByRole("button", { name: "Try again" })).toBeNull();
      if (running.id) expect(within(notice).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/repairs/r1");
    });

  it.each([null, { ...available, available: null, automatic: false, managed: "image" as const, reason: "Managed by its container image." }])(
    "does not offer native update switches for source or image-managed copies", async (update) => {
      fixture(update);
      renderApp({ route: "/settings" });
      await screen.findByRole("switch", { name: /Terminal/ });
      expect(screen.queryByRole("switch", { name: /Automatic updates/ })).toBeNull();
      expect(screen.queryByRole("switch", { name: /Check for new versions/ })).toBeNull();
    },
  );

  it("keeps Settings failure and retry available after the notice is dismissed", async () => {
    fixture({ ...available, attempt: { version: "v0.2.0", state: "failed", error: "the download failed its certificate check." } });
    localStorage.setItem("altitude.update.dismissed", "v0.2.0:failed");
    const { user } = renderApp({ route: "/settings" });
    expect(await screen.findByText(/The update to v0.2.0 did not finish/)).toBeVisible();
    expect(screen.queryByRole("status", { name: "New version" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/Installing Altitude v0.2.0/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});
