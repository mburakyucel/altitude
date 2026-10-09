import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Overview, Update } from "../data/api";
import { renderApp } from "../test/render";

const notes = "https://github.com/example/altitude/releases/tag/v0.2.0";
const available: Update = { current: "v0.1.0", check: true, automatic: true, automatic_pending: true, installed: null,
  command: "alt update", available: { version: "v0.2.0", notes } };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

function fixture(initial: Update | null = available) {
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
    if (path === "/api/voice") return json({ backend: "host", selection: "fixture", host: { state: "ready", download_bytes: 698435338 } });
    if (path === "/api/overview") return json({ projects: [], queue: [], engines: [], update,
      wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } });
    return json({});
  }));
  return { posts, refuse: (value: boolean) => { refuse = value; } };
}

describe("Installed release updates", () => {
  it("offers manual installation when a previously attempted version is offered again", async () => {
    fixture({ ...available, automatic_pending: false,
      attempt: { version: "v0.3.0", state: "failed", error: "Run alt update in a terminal to see why." } });
    renderApp({ route: "/" });
    expect(await screen.findByRole("button", { name: "Update" })).toBeVisible();
    expect(screen.queryByText(/will install automatically/)).toBeNull();
  });
  it("waits automatically, switches to a prompt, and preserves the preference with checking off", async () => {
    const state = fixture();
    const { user } = renderApp({ route: "/settings" });
    expect(await screen.findByText(/will install automatically at the next quiet point, when no browser terminal is open/)).toBeVisible();
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
    const state = fixture({ ...available, attempt: { version: "v0.2.0", state: "failed", error: "Run alt update in a terminal to see why." } });
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
    expect(await screen.findByText(/Altitude v0.3.0 will install automatically/)).toBeVisible();
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
    fixture({ ...available, attempt: { version: "v0.2.0", state: "failed", error: "Run alt update in a terminal to see why." } });
    localStorage.setItem("altitude.update.dismissed", "v0.2.0:failed");
    const { user } = renderApp({ route: "/settings" });
    expect(await screen.findByText(/The update to v0.2.0 did not finish/)).toBeVisible();
    expect(screen.queryByRole("status", { name: "New version" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/Installing Altitude v0.2.0/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});
