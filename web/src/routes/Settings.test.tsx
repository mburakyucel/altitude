import { act, screen, waitFor } from "@testing-library/react";
import { defaultScheduler, notifyManager } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

const saved = { backend: "endpoint", selection: "endpoint-one", url: "https://speech.example.test/transcribe", model: "", key_set: true };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
function fixture() {
  let setting = { ...saved };
  const calls: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) === "/api/voice") {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)); calls.push(body);
        setting = { ...setting, ...body, key_set: body.keep_key || Boolean(body.key),
          selection: body.backend === "endpoint" ? saved.selection : `${body.backend}-selection` };
      }
      return json(setting);
    }
    if (String(input) === "/api/overview") return json({ projects: [], queue: [], engines: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } });
    return json({}, 404);
  }));
  return calls;
}

describe("Voice settings", () => {
  it("locks the form until the response is applied even when query notifications wait", async () => {
    fixture();
    const fetchOriginal = globalThis.fetch;
    let release = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") await gate;
      return fetchOriginal(input, init);
    }));
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Replace" }));
    const pending: (() => void)[] = [];
    notifyManager.setScheduler((callback) => pending.push(callback));
    try {
      await user.click(screen.getByRole("button", { name: "Save endpoint" }));
      expect(screen.getByLabelText("API key (optional)")).toBeDisabled();
      expect(screen.getByText("Saving…")).toBeVisible();
      release();
      await screen.findByText("Saved.");
      expect(screen.getByLabelText("API key (optional)")).toBeEnabled();
    } finally {
      release();
      notifyManager.setScheduler(defaultScheduler);
      await act(async () => { while (pending.length) pending.shift()!(); });
    }
  });

  it("keeps the next key edit and acknowledged selection while cache notifications wait", async () => {
    const calls = fixture();
    const { user } = renderApp({ route: "/settings/voice" });
    await screen.findByText("Key set · never shown");
    const pending: (() => void)[] = [];
    const flush = () => { while (pending.length) pending.shift()!(); };
    notifyManager.setScheduler((callback) => pending.push(callback));
    try {
      await user.click(screen.getByRole("button", { name: "Replace" }));
      await user.click(screen.getByRole("button", { name: "Save endpoint" }));
      await screen.findByText("Saved.");
      await user.type(screen.getByLabelText("API key (optional)"), "next-fixture-key");
      await act(async () => flush());
      expect(screen.getByLabelText("API key (optional)")).toHaveValue("next-fixture-key");
      await user.click(screen.getByRole("button", { name: "Save endpoint" }));
      await waitFor(() => expect(calls[1]).toMatchObject({ key: "next-fixture-key" }));
      await screen.findByText("Key set · never shown");
      await user.click(screen.getByRole("radio", { name: "Browser recognition" }));
      await waitFor(() => expect(screen.getByRole("radio", { name: "Browser recognition" })).toBeChecked());
      await user.click(screen.getByRole("radio", { name: "Local speech service" }));
      await waitFor(() => expect(calls.at(-1)).toMatchObject({ backend: "local", selection: "browser-selection" }));
    } finally {
      notifyManager.setScheduler(defaultScheduler);
      await act(async () => flush());
    }
  });

  it.each([390, 1440])("keeps overview compact, discards unsaved edits and never carries a key to a changed URL (%s)", async (width) => {
    setViewport(width);
    const calls = fixture();
    const { user } = renderApp({ route: "/settings" });
    await user.click(await screen.findByRole("link", { name: "Voice input Custom endpoint" }));
    await screen.findByText("Key set · never shown");
    const url = screen.getByLabelText("Endpoint URL");
    await user.clear(url);
    await user.type(url, "https://new.example.test/transcribe");
    expect(screen.queryByText("Key set · never shown")).toBeNull();
    expect(screen.getByLabelText("API key (optional)")).toHaveValue("");
    await user.click(screen.getByRole("link", { name: "‹ Settings" }));
    expect(screen.queryByRole("radio")).toBeNull();
    expect(calls).toHaveLength(0);
    await user.click(screen.getByRole("link", { name: "Voice input Custom endpoint" }));
    await screen.findByText("Key set · never shown");
    expect(screen.getByLabelText("Endpoint URL")).toHaveValue(saved.url);
    await user.click(screen.getByRole("button", { name: "Replace" }));
    await user.click(screen.getByRole("button", { name: "Save endpoint" }));
    await screen.findByText("Saved.");
    expect(calls[0]).toMatchObject({ key: "", url: saved.url, selection: saved.selection });
    expect(calls[0]).not.toHaveProperty("keep_key");
  });

  it("shows save failure with the draft and retries the same choice", async () => {
    const calls = fixture();
    const fetchOriginal = globalThis.fetch;
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST" && fail) return json({ error: "Settings unavailable" }, 503);
      return fetchOriginal(input, init);
    }));
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("radio", { name: "Browser recognition" }));
    await screen.findByText("Settings unavailable");
    expect(screen.getByRole("radio", { name: "Custom endpoint" })).toBeChecked();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Saved.");
    await waitFor(() => expect(screen.getByRole("radio", { name: "Browser recognition" })).toBeChecked());
    expect(calls).toEqual([{ backend: "browser", selection: saved.selection }]);
  });

  it("rail entry within Settings retains the original Back destination across viewports", async () => {
    setViewport(1440);
    fixture();
    const { user, router } = renderApp({ route: "/settings/voice" });
    await screen.findByText("Key set · never shown");
    await user.click(screen.getByRole("link", { name: "Settings" }));
    await screen.findByRole("link", { name: "Voice input Custom endpoint" });
    setViewport(390);
    await user.click(await screen.findByRole("button", { name: "‹ Back" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects"));
  });

  it("reloads a conflicting save before accepting another edit", async () => {
    const calls = fixture();
    const fetchOriginal = globalThis.fetch;
    let stale = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST" && stale) return json({ error: "Voice settings changed. Reload settings and try again." }, 409);
      return fetchOriginal(input, init);
    }));
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("radio", { name: "Browser recognition" }));
    await user.click(await screen.findByRole("button", { name: "Reload settings" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.getByRole("radio", { name: "Custom endpoint" })).toBeChecked();
    stale = false;
    await user.click(screen.getByRole("radio", { name: "Browser recognition" }));
    await screen.findByText("Saved.");
    expect(calls).toHaveLength(1);
  });
});
