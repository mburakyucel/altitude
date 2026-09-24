import { screen, waitFor } from "@testing-library/react";
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
        setting = { ...setting, ...body, key_set: body.keep_key || Boolean(body.key) };
      }
      return json(setting);
    }
    if (String(input) === "/api/overview") return json({ projects: [], queue: [], engines: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } });
    return json({}, 404);
  }));
  return calls;
}

describe("Voice settings", () => {
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
});
