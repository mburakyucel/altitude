import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { defaultScheduler, notifyManager } from "@tanstack/react-query";
import { describe, expect, it, onTestFinished, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

const saved = { backend: "host", selection: "host-one", host: { state: "ready", download_bytes: 698435338 } };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
function fixture(initial: Record<string, unknown> = saved) {
  let setting = { ...initial };
  const calls: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) === "/api/voice") {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)); calls.push(body);
        setting = { ...setting, ...body, selection: `${body.backend}-selection` };
      }
      return json(setting);
    }
    if (String(input) === "/api/overview") return json({ projects: [], queue: [], engines: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } });
    if (String(input) === "/api/machine") return json({ operator: null, incident_repository: null, altitude_repository: "fixture/altitude", terminal: false });
    return json({}, 404);
  }));
  return calls;
}

/** The host's voice setup: each action moves `host` along as the server would. */
function hostFixture(host: Record<string, unknown>, backend = "browser") {
  const actions: string[] = [];
  let setting = { backend, selection: `${backend}-selection`, host };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/voice/host") {
      const { action } = JSON.parse(String(init?.body));
      actions.push(action);
      const installed = setting.host.state === "ready" || setting.host.state === "outdated";
      const next = installed && action !== "remove" ? { ...setting.host, update: { state: action === "setup" ? "preparing" : "cancelled" } }
        : action === "setup" ? { state: "setting-up", download_bytes: 698435338, done_bytes: 0 }
        : action === "cancel" ? { state: "absent", download_bytes: 698435338 } : { state: "absent", download_bytes: 698435338 };
      setting = { ...setting, host: next };
      return json(setting);
    }
    if (path === "/api/voice") {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        setting = { ...setting, backend: body.backend, selection: `${body.backend}-selection` };
      }
      return json(setting);
    }
    if (path === "/api/overview") return json({ projects: [], queue: [], engines: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } });
    if (path === "/api/machine") return json({ operator: null, incident_repository: null, altitude_repository: "fixture/altitude", terminal: false });
    return json({}, 404);
  }));
  return { actions, advance: (next: Record<string, unknown>) => { setting = { ...setting, host: next }; } };
}

/** A transcription card, found by its title. */
const card = (title: string) => screen.getByRole("heading", { name: title, level: 3 }).closest(".voice-option") as HTMLElement;
const inUse = (title: string) => within(card(title)).queryByText("In use") !== null;

describe("Host voice settings", () => {
  it.each([390, 1440])("offers the one-time setup without switching to this computer, shows its progress and can cancel it (%i)", async (width) => {
    setViewport(width);
    const host = hostFixture({ state: "absent", download_bytes: 698435338 });
    const { user } = renderApp({ route: "/settings/voice" });
    expect(await screen.findByText("Needs a one-time download of about 698 MB, checked against this release.")).toBeVisible();
    expect(screen.getByText("Speech model: NVIDIA Parakeet TDT 0.6B v2, licensed CC-BY-4.0.")).toBeVisible();
    // Voice that does not run yet cannot be chosen; setting it up leaves the working choice in use.
    expect(screen.queryByRole("button", { name: "Use this computer" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Set up voice" }));
    expect(await screen.findByRole("progressbar", { name: "Voice setup" })).toBeVisible();
    host.advance({ state: "setting-up", download_bytes: 698435338, done_bytes: 349000000 });
    await waitFor(() => expect(screen.getByRole("progressbar", { name: "Voice setup" })).toHaveAttribute("value", "349000000"));
    await user.click(screen.getByRole("button", { name: "Cancel setup" }));
    expect(await screen.findByRole("button", { name: "Set up voice" })).toBeVisible();
    expect(inUse("Browser recognition")).toBe(true);
    expect(host.actions).toEqual(["setup", "cancel"]);
  });

  it("a finished setup can be chosen and removed", async () => {
    const host = hostFixture({ state: "ready", download_bytes: 698435338 });
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Use this computer" }));
    await waitFor(() => expect(inUse("This computer")).toBe(true));
    await user.click(screen.getByRole("button", { name: "Remove voice (698 MB)" }));
    expect(await screen.findByRole("button", { name: "Set up voice" })).toBeVisible();
    expect(host.actions).toEqual(["remove"]);
  });

  /** Host voice ready while browser recognition is in use, with every save held until `release`. */
  function heldHost() {
    const host = hostFixture({ state: "ready", download_bytes: 698435338 });
    const fetchOriginal = globalThis.fetch;
    let release = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") await gate;
      return fetchOriginal(input, init);
    }));
    return { host, release: () => release() };
  }

  it("a running host action locks Use this computer, so a removed voice cannot be chosen", async () => {
    const { host, release } = heldHost();
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Remove voice (698 MB)" }));
    const useHost = screen.getByRole("button", { name: "Use this computer" });
    expect(useHost).toHaveAttribute("aria-disabled", "true");
    await user.click(useHost);
    release();
    expect(await screen.findByRole("button", { name: "Set up voice" })).toBeVisible();
    expect(inUse("Browser recognition")).toBe(true);
    expect(host.actions).toEqual(["remove"]);
  });

  it("a saving choice locks the host actions", async () => {
    const { host, release } = heldHost();
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Use this computer" }));
    expect(screen.getByRole("button", { name: "Remove voice (698 MB)" })).toBeDisabled();
    release();
    await waitFor(() => expect(inUse("This computer")).toBe(true));
    expect(host.actions).toEqual([]);
  });

  it("a failed setup says so and offers Retry", async () => {
    hostFixture({ state: "failed", download_bytes: 698435338, reason: "Setup stopped: encoder-model.int8.onnx did not match its checksum." }, "host");
    renderApp({ route: "/settings/voice" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Setup did not finish. Setup stopped: encoder-model.int8.onnx did not match its checksum.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  it("shows an update as one line with Cancel update, and removal only once it ends", async () => {
    const host = hostFixture({ state: "ready", download_bytes: 701000000,
      update: { state: "downloading", done_bytes: 12000000, download_bytes: 40000000 } }, "host");
    const { user } = renderApp({ route: "/settings/voice" });
    const progress = await screen.findByRole("progressbar", { name: "Updating voice" });
    expect(progress).toHaveAttribute("value", "12000000");
    expect(progress).toHaveAttribute("max", "40000000");
    expect(screen.getByText("Updating · 12 MB of 40 MB")).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Remove voice/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Cancel update" }));
    // A cancelled update leaves voice as it was: no text, Update voice beside Remove voice.
    expect(await screen.findByRole("button", { name: "Update voice" })).not.toHaveClass("btn-primary");
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByText(/cancelled/i)).toBeNull();
    expect(screen.getByRole("button", { name: "Remove voice (701 MB)" })).not.toHaveClass("btn-outline-danger");
    await user.click(screen.getByRole("button", { name: "Update voice" }));
    expect(await screen.findByText("Updating…")).toBeVisible();
    expect(screen.getByRole("img", { name: "Updating voice" })).toBeVisible();
    expect(screen.queryByRole("progressbar")).toBeNull();
    host.advance({ state: "ready", download_bytes: 701000000, update: { state: "waiting" } });
    expect(await screen.findByText("Updating · finishes after dictation", {}, { timeout: 2000 })).toBeVisible();
    host.advance({ state: "ready", download_bytes: 701000000, update: { state: "checking" } });
    expect(await screen.findByText("Updating…", {}, { timeout: 2000 })).toBeVisible();
    host.advance({ state: "ready", download_bytes: 701000000 });
    expect(await screen.findByText("While you dictate, the speech process uses about 2 GB of memory.", {}, { timeout: 2000 })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancel update" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Remove voice (701 MB)" }));
    expect(await screen.findByRole("button", { name: "Set up voice" })).toBeVisible();
    expect(host.actions).toEqual(["cancel", "setup", "remove"]);
  });

  it("keeps using this computer while it updates", async () => {
    hostFixture({ state: "ready", download_bytes: 701000000, update: { state: "preparing" } });
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Use this computer" }));
    await waitFor(() => expect(inUse("This computer")).toBe(true));
    expect(screen.getByRole("button", { name: "Cancel update" })).toBeVisible();
  });

  it.each(["ready", "outdated"])("update failure keeps the reason and only alerts when voice is unusable (%s)", async (state) => {
    hostFixture({ state, download_bytes: 701000000, update: { state: "failed", reason: "Download failed. Try again." } }, "host");
    renderApp({ route: "/settings/voice" });
    if (state === "ready") {
      expect(await screen.findByText("Update did not finish. Download failed.")).toHaveAttribute("role", "status");
      expect(screen.getByRole("button", { name: "Retry update" })).not.toHaveClass("btn-primary");
    } else {
      expect(await screen.findByRole("alert")).toHaveTextContent("Voice needs an update. Download failed.");
      expect(screen.getByRole("button", { name: "Update voice" })).toHaveClass("btn-primary");
    }
    expect(screen.getByRole("button", { name: "Remove voice (701 MB)" })).toBeEnabled();
  });

  it.each(["cancel", "setup", "remove"])("locks update actions through authoritative response when %s is pending", async (action) => {
    // Remove voice is offered once no update runs.
    hostFixture({ state: "ready", download_bytes: 701000000, ...(action === "remove" ? {} : { update: { state: action === "setup" ? "cancelled" : "preparing" } }) }, "host");
    const original = globalThis.fetch;
    let release = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/voice/host") await gate;
      return original(input, init);
    }));
    const { user } = renderApp({ route: "/settings/voice" });
    const label = action === "cancel" ? "Cancel update" : action === "setup" ? "Update voice" : "Remove voice (701 MB)";
    await user.click(await screen.findByRole("button", { name: label }));
    expect(screen.getByRole("button", { name: action === "cancel" ? "Cancelling update" : action === "setup" ? "Starting update" : "Removing voice" })).toBeDisabled();
    for (const control of document.querySelectorAll('.voice-host button')) expect(control).toBeDisabled();
    release();
    expect(await screen.findByRole("button", { name: action === "cancel" ? "Update voice" : action === "setup" ? "Cancel update" : "Set up voice" })).toBeEnabled();
  });

  it("a denied update action keeps the authoritative voice state and allows another action", async () => {
    hostFixture({ state: "ready", download_bytes: 701000000, update: { state: "preparing" } }, "host");
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input) === "/api/voice/host" ? json({ error: "Action denied." }, 403) : original(input, init)));
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Cancel update" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Action denied.");
    expect(screen.getByRole("button", { name: "Cancel update" })).toBeEnabled();
    expect(inUse("This computer")).toBe(true);
  });

  it.each(["ready", "outdated"])("overview separates installed availability from update status (%s)", async (state) => {
    hostFixture({ state, download_bytes: 701000000, update: { state: "downloading", done_bytes: 12, download_bytes: 40 } }, "host");
    renderApp({ route: "/settings" });
    expect(await screen.findByRole("link", { name: state === "ready" ? "Voice input This computer" : "Voice input This computer · update required" })).toBeVisible();
  });

  it("a computer that cannot run voice says why and cannot be chosen", async () => {
    hostFixture({ state: "unavailable", reason: "voice runs on Linux x86_64 and on Macs with Apple silicon" });
    renderApp({ route: "/settings/voice" });
    expect(await screen.findByText("Not available on this computer: voice runs on Linux x86_64 and on Macs with Apple silicon.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Use this computer" })).toBeNull();
  });
});

describe("Voice settings", () => {
  it.each(["transport", "http"])("keeps %s save failures generic when there is no server explanation", async (mode) => {
    fixture();
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/voice" && init?.method === "POST") {
        if (mode === "transport") throw new TypeError("internal connection detail");
        return new Response("upstream details", { status: 502 });
      }
      return original(input, init);
    }));
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Use browser recognition" }));
    expect(await within(card("Browser recognition")).findByRole("alert")).toHaveTextContent("Could not save voice settings. Retry");
    expect(screen.queryByText(/internal connection detail|upstream details/)).toBeNull();
  });
  it.each([
    ["API key", "x".repeat(4097), "fictional-key", "Enter a valid speech service key."],
    ["Transcription URL", "https://speech.example.test:0/transcribe", "https://speech.example.test/transcribe", "Enter a complete http:// or https:// transcription URL."],
  ])("shows server validation for %s and saves the corrected draft", async (label, rejected, corrected, message) => {
    const calls = fixture();
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/voice" && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        if (body[label === "API key" ? "key" : "url"] === rejected) return json({ error: message }, 400);
      }
      return original(input, init);
    }));
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Use your speech service" }));
    fireEvent.change(screen.getByLabelText("Transcription URL"), { target: { value: "https://speech.example.test/transcribe" } });
    await user.click(screen.getByText("Key or model (optional)"));
    fireEvent.change(screen.getByLabelText(label), { target: { value: rejected } });
    await user.click(screen.getByRole("button", { name: "Use this service" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByLabelText(label)).toHaveValue(rejected);
    fireEvent.change(screen.getByLabelText(label), { target: { value: corrected } });
    expect(screen.queryByRole("alert")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Use this service" }));
    await screen.findByText("Saved.");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.[label === "API key" ? "key" : "url"]).toBe(corrected);
  });

  it("warns for mixed-case HTTP and associates URL validation with its field", async () => {
    const calls = fixture();
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Use your speech service" }));
    const url = screen.getByLabelText("Transcription URL");
    expect(url).toHaveFocus();
    fireEvent.change(url, { target: { value: "hTtP://speech.example.test/transcribe" } });
    expect(screen.getByText("This connection is unencrypted. Audio and any key travel over HTTP.")).toBeVisible();
    fireEvent.change(url, { target: { value: "not a URL" } });
    await user.click(screen.getByRole("button", { name: "Use this service" }));
    expect(url).toHaveAttribute("aria-invalid", "true");
    expect(url).toHaveAccessibleDescription(/Enter a complete http:\/\/ or https:\/\/ transcription URL\./);
    expect(calls).toHaveLength(0);
    fireEvent.change(url, { target: { value: "https://speech.example.test/transcribe" } });
    await user.click(screen.getByRole("button", { name: "Use this service" }));
    await screen.findByText("Saved.");
    await waitFor(() => expect(inUse("Your speech service")).toBe(true));
    expect(url).not.toHaveAttribute("aria-invalid");
    expect(url).not.toHaveAccessibleDescription(/Enter a complete/);
  });

  it("Cancel closes an unsaved service without saving it", async () => {
    const calls = fixture();
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Use your speech service" }));
    await user.type(screen.getByLabelText("Transcription URL"), "https://speech.example.test/transcribe");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Transcription URL")).toBeNull();
    expect(screen.getByRole("button", { name: "Use your speech service" })).toBeVisible();
    expect(inUse("This computer")).toBe(true);
    expect(calls).toHaveLength(0);
  });

  it("an untouched form reflects a fresh key-only change without relying on another render", async () => {
    const service = { url: "https://speech.example.test/transcribe", model: "small", key_present: true };
    fixture({ ...saved, backend: "service", service });
    const { user, queryClient } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByText("Key or model (optional)"));
    expect(screen.getByLabelText("API key")).toHaveAttribute("placeholder", "Key saved — leave blank to keep");
    act(() => queryClient.setQueryData(["voice-settings"], { ...saved, backend: "service", selection: "key-removed", service: { ...service, key_present: false } }));
    await waitFor(() => expect(screen.getByLabelText("API key")).not.toHaveAttribute("placeholder"));
    expect(screen.queryByLabelText("Remove saved key")).toBeNull();
  });
  it("host action refreshes runtime status without granting a dirty service edit a changed selection or key", async () => {
    const originalUrl = "https://speech.example.test/v1/audio/transcriptions";
    const calls = fixture({ ...saved, backend: "service", service: { url: originalUrl, model: "small", key_present: true } });
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input) === "/api/voice/host" ? json({ ...saved, backend: "service", selection: "other-selection",
        service: { url: "https://other.example.test/transcribe", model: "other", key_present: false },
        host: { state: "absent", download_bytes: 698435338 } }) : original(input, init)));
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByText("Key or model (optional)"));
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    await user.clear(screen.getByLabelText("Model"));
    await user.type(screen.getByLabelText("Model"), "edited");
    await user.click(screen.getByRole("button", { name: "Remove voice (698 MB)" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /^Remove voice/ })).toBeNull());
    expect(screen.getByLabelText("Transcription URL")).toHaveValue(originalUrl);
    expect(screen.getByLabelText("API key")).toHaveAttribute("placeholder", "Key saved — leave blank to keep");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved.");
    expect(calls).toEqual([{ backend: "service", selection: "host-one", url: originalUrl, model: "edited", key: "", remove_key: false }]);
  });

  it("a denied service save preserves fields and saves subsequent edits", async () => {
    const calls = fixture();
    const original = globalThis.fetch;
    let denied = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      denied && String(input) === "/api/voice" && init?.method === "POST" ? json({ error: "Action denied." }, 403) : original(input, init)));
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Use your speech service" }));
    await user.type(screen.getByLabelText("Transcription URL"), "https://speech.example.test/transcribe");
    await user.click(screen.getByText("Key or model (optional)"));
    await user.type(screen.getByLabelText("API key"), "fictional-key");
    await user.type(screen.getByLabelText("Model"), "small");
    await user.click(screen.getByRole("button", { name: "Use this service" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Action denied. Retry");
    expect(inUse("This computer")).toBe(true);
    expect(screen.getByLabelText("Transcription URL")).toHaveValue("https://speech.example.test/transcribe");
    expect(screen.getByLabelText("API key")).toHaveValue("fictional-key");
    expect(screen.getByLabelText("Model")).toHaveValue("small");
    await user.clear(screen.getByLabelText("API key"));
    await user.type(screen.getByLabelText("API key"), "corrected-key");
    await user.clear(screen.getByLabelText("Model"));
    await user.type(screen.getByLabelText("Model"), "corrected-model");
    denied = false;
    await user.click(screen.getByRole("button", { name: "Use this service" }));
    await screen.findByText("Saved.");
    expect(calls).toEqual([{ backend: "service", selection: "host-one", url: "https://speech.example.test/transcribe", key: "corrected-key", model: "corrected-model", remove_key: false }]);
  });
  it("fresh settings replace an untouched page, and a choice saves against them", async () => {
    const calls = fixture();
    const { user, queryClient } = renderApp({ route: "/settings/voice" });
    await screen.findByRole("button", { name: "Use browser recognition" });
    act(() => queryClient.setQueryData(["voice-settings"], { ...saved, backend: "browser", selection: "browser-fresh" }));
    await waitFor(() => expect(inUse("Browser recognition")).toBe(true));
    await user.click(screen.getByRole("button", { name: "Use this computer" }));
    await screen.findByText("Saved.");
    expect(calls).toEqual([{ backend: "host", selection: "browser-fresh" }]);
  });
  it("leaving a saved service asks first in the pressed card, and Keep service changes nothing", async () => {
    const calls = fixture({ ...saved, backend: "service", service: { url: "https://speech.example.test/v1/audio/transcriptions", model: "small", key_present: true } });
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByText("Key or model (optional)"));
    expect(screen.getByLabelText("API key")).toHaveValue("");
    expect(screen.getByLabelText("API key")).toHaveAttribute("placeholder", "Key saved — leave blank to keep");
    await user.click(screen.getByRole("button", { name: "Use browser recognition" }));
    const browser = within(card("Browser recognition"));
    expect(browser.getByText("Switching removes your speech service’s address and key.")).toBeVisible();
    expect(calls).toHaveLength(0);
    await user.click(browser.getByRole("button", { name: "Keep service" }));
    expect(screen.queryByRole("button", { name: "Remove service and switch" })).toBeNull();
    expect(inUse("Your speech service")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Use browser recognition" }));
    await user.click(screen.getByRole("button", { name: "Remove service and switch" }));
    await screen.findByText("Saved.");
    await waitFor(() => expect(inUse("Browser recognition")).toBe(true));
    expect(screen.queryByLabelText("Transcription URL")).toBeNull();
    expect(calls).toEqual([{ backend: "browser", selection: "host-one" }]);
  });

  it("Keep service after a failed switch withdraws its Retry, so the service is only removed after asking again", async () => {
    const calls = fixture({ ...saved, backend: "service", service: { url: "https://speech.example.test/v1/audio/transcriptions", model: "", key_present: false } });
    const fetchOriginal = globalThis.fetch;
    let release = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") { await gate; return json({ error: "Settings unavailable" }, 503); }
      return fetchOriginal(input, init);
    }));
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Use browser recognition" }));
    await user.click(screen.getByRole("button", { name: "Remove service and switch" }));
    // The switch shows from the press, so the question leaves with it.
    expect(screen.queryByRole("button", { name: "Keep service" })).toBeNull();
    expect(within(card("Browser recognition")).getByRole("status", { name: "Saving…" })).toBeVisible();
    expect(inUse("Browser recognition")).toBe(true);
    release();
    expect(await within(card("Browser recognition")).findByRole("alert")).toHaveTextContent("Settings unavailable Retry");
    await user.click(screen.getByRole("button", { name: "Keep service" }));
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(inUse("Your speech service")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Use browser recognition" }));
    expect(screen.getByText("Switching removes your speech service’s address and key.")).toBeVisible();
    expect(calls).toHaveLength(0);
  });

  it("focus follows each press to the control that replaces it, and a save is announced", async () => {
    fixture({ ...saved, backend: "service", service: { url: "https://speech.example.test/v1/audio/transcriptions", model: "", key_present: false } });
    const { user } = renderApp({ route: "/settings/voice" });
    await screen.findByRole("button", { name: "Use browser recognition" });
    const status = screen.getAllByRole("status").find((element) => element.classList.contains("sr-only"))!;
    expect(status).toBeEmptyDOMElement();
    await user.click(screen.getByRole("button", { name: "Use browser recognition" }));
    const remove = screen.getByRole("button", { name: "Remove service and switch" });
    expect(remove).toHaveFocus();
    expect(remove).toHaveAccessibleDescription("Switching removes your speech service’s address and key.");
    await user.click(screen.getByRole("button", { name: "Keep service" }));
    expect(screen.getByRole("button", { name: "Use browser recognition" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Use browser recognition" }));
    await user.click(screen.getByRole("button", { name: "Remove service and switch" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Browser recognition", level: 3 })).toHaveFocus());
    expect(status).toHaveTextContent("Saved.");
    await user.click(screen.getByRole("button", { name: "Use your speech service" }));
    expect(screen.getByLabelText("Transcription URL")).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Use your speech service" })).toHaveFocus();
  });

  it("a service edit that a refresh overtakes saves against the setting it began from and offers Reload settings", async () => {
    const calls = fixture({ ...saved, backend: "service", service: { url: "https://speech.example.test/v1/audio/transcriptions", model: "", key_present: false } });
    const fetchOriginal = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") { calls.push(JSON.parse(String(init.body))); return json({ error: "Voice settings changed" }, 409); }
      return fetchOriginal(input, init);
    }));
    const { user, queryClient } = renderApp({ route: "/settings/voice" });
    const url = await screen.findByLabelText("Transcription URL");
    await user.clear(url);
    await user.type(url, "https://other.example.test/v1/audio/transcriptions");
    act(() => queryClient.setQueryData(["voice-settings"], { ...saved, backend: "browser", selection: "browser-elsewhere" }));
    expect(inUse("Your speech service")).toBe(true);
    expect(url).toHaveValue("https://other.example.test/v1/audio/transcriptions");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await within(card("Your speech service")).findByRole("alert")).toHaveTextContent("Voice settings changed. Reload settings");
    expect(calls).toEqual([{ backend: "service", selection: "host-one", url: "https://other.example.test/v1/audio/transcriptions", model: "", key: "", remove_key: false }]);
  });

  it("changing a service URL clears any typed key", async () => {
    const calls = fixture({ ...saved, backend: "service", service: { url: "https://speech.example.test/v1/audio/transcriptions", key_present: true } });
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByText("Key or model (optional)"));
    await user.type(screen.getByLabelText("API key"), "fictional-key");
    await user.clear(screen.getByLabelText("Transcription URL"));
    await user.type(screen.getByLabelText("Transcription URL"), "http://new.example.test/v1/audio/transcriptions");
    expect(screen.getByLabelText("API key")).toHaveValue("");
    expect(screen.getByLabelText("API key")).not.toHaveAttribute("placeholder");
    expect(screen.getByText("This connection is unencrypted. Audio and any key travel over HTTP.")).toBeVisible();
    expect(calls).toHaveLength(0);
  });
  it.each([390, 1440])("shows three cards, This computer first and in use, and keeps the overview compact (%s)", async (width) => {
    setViewport(width);
    const calls = fixture();
    const { user } = renderApp({ route: "/settings" });
    await user.click(await screen.findByRole("link", { name: "Voice input This computer" }));
    const titles = await screen.findAllByRole("heading", { level: 3 });
    expect(titles.map((title) => title.textContent)).toEqual(["This computer", "Browser recognition", "Your speech service"]);
    expect(inUse("This computer")).toBe(true);
    expect(screen.queryByRole("button", { name: "Use this computer" })).toBeNull();
    expect(screen.getByRole("button", { name: "Use browser recognition" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Use your speech service" })).toBeVisible();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("radio")).toBeNull();
    await user.click(screen.getByRole("link", { name: "‹ Settings" }));
    expect(screen.queryByRole("button", { name: "Use browser recognition" })).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("locks the choices until the response is applied even when query notifications wait", async () => {
    fixture();
    const fetchOriginal = globalThis.fetch;
    let release = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") await gate;
      return fetchOriginal(input, init);
    }));
    const { user } = renderApp({ route: "/settings/voice" });
    await screen.findByRole("button", { name: "Use browser recognition" });
    const pending: (() => void)[] = [];
    notifyManager.setScheduler((callback) => pending.push(callback));
    try {
      await user.click(screen.getByRole("button", { name: "Use browser recognition" }));
      // The choice is In use from the press, its card saying Saving… beside the mark.
      expect(within(card("Browser recognition")).getByRole("status", { name: "Saving…" })).toBeVisible();
      expect(inUse("Browser recognition")).toBe(true);
      const service = screen.getByRole("button", { name: "Use your speech service" });
      expect(service).toHaveAttribute("aria-disabled", "true");
      // Locked buttons stay focusable and ignore presses.
      await user.click(service);
      expect(screen.queryByLabelText("Transcription URL")).toBeNull();
      release();
      await screen.findByText("Saved.");
      expect(screen.getByRole("button", { name: "Use your speech service" })).not.toHaveAttribute("aria-disabled");
      expect(inUse("Browser recognition")).toBe(true);
    } finally {
      release();
      notifyManager.setScheduler(defaultScheduler);
      await act(async () => { while (pending.length) pending.shift()!(); });
    }
  });

  it("saves each choice with the selection it replaces and shows it while cache notifications wait", async () => {
    const calls = fixture();
    const { user } = renderApp({ route: "/settings/voice" });
    await screen.findByRole("button", { name: "Use browser recognition" });
    const pending: (() => void)[] = [];
    const flush = () => { while (pending.length) pending.shift()!(); };
    notifyManager.setScheduler((callback) => pending.push(callback));
    try {
      await user.click(screen.getByRole("button", { name: "Use browser recognition" }));
      await screen.findByText("Saved.");
      expect(inUse("Browser recognition")).toBe(true);
      await act(async () => flush());
      expect(inUse("Browser recognition")).toBe(true);
      await user.click(screen.getByRole("button", { name: "Use this computer" }));
      await waitFor(() => expect(calls).toHaveLength(2));
      await waitFor(() => expect(inUse("This computer")).toBe(true));
      await act(async () => flush());
      expect(inUse("This computer")).toBe(true);
      expect(calls).toEqual([{ backend: "browser", selection: "host-one" }, { backend: "host", selection: "browser-selection" }]);
    } finally {
      notifyManager.setScheduler(defaultScheduler);
      await act(async () => flush());
    }
  });

  it("a failed choice keeps the saved one in use and Retry saves the same choice", async () => {
    const calls = fixture();
    const fetchOriginal = globalThis.fetch;
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST" && fail) return json({ error: "Settings unavailable" }, 503);
      return fetchOriginal(input, init);
    }));
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Use browser recognition" }));
    expect(await within(card("Browser recognition")).findByRole("alert")).toHaveTextContent("Settings unavailable Retry");
    expect(inUse("This computer")).toBe(true);
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Saved.");
    await waitFor(() => expect(inUse("Browser recognition")).toBe(true));
    expect(calls).toEqual([{ backend: "browser", selection: saved.selection }]);
  });

  it("rail entry within Settings retains the original Back destination across viewports", async () => {
    setViewport(1440);
    fixture();
    const { user, router } = renderApp({ route: "/settings/voice" });
    await screen.findByRole("button", { name: "Use browser recognition" });
    await user.click(screen.getByRole("link", { name: "Settings" }));
    await screen.findByRole("link", { name: "Voice input This computer" });
    setViewport(390);
    await user.click(await screen.findByRole("button", { name: "‹ Back" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects"));
  });

  it("reloads a conflicting save before accepting another choice", async () => {
    const calls = fixture();
    const fetchOriginal = globalThis.fetch;
    let stale = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST" && stale) return json({ error: "Voice settings changed. Reload settings and try again." }, 409);
      return fetchOriginal(input, init);
    }));
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Use browser recognition" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Voice settings changed. Reload settings");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Reload settings" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(inUse("This computer")).toBe(true);
    stale = false;
    await user.click(screen.getByRole("button", { name: "Use browser recognition" }));
    await screen.findByText("Saved.");
    expect(calls).toHaveLength(1);
  });
});

describe("Projects folder setting", () => {
  function folderFixture(options: { fail?: () => boolean } = {}) {
    let roots = ["~/Projects"];
    const saves: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/voice") return json(saved);
      if (url === "/api/overview") return json({ projects: [], queue: [], engines: [], roots, wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } });
      if (url === "/api/folders") return json({ path: "/home/ada", parts: [], readable: true, folders: [{ name: "code", path: "/home/ada/code", project: null, git: false }] });
      if (url === "/api/folders?path=%2Fhome%2Fada%2Fcode") return json({ path: "/home/ada/code", parts: ["code"], readable: true, folders: [] });
      if (url === "/api/projects-folder" && init?.method === "POST") {
        if (options.fail?.()) return json({ error: "Settings unavailable" }, 503);
        saves.push(JSON.parse(String(init.body)));
        roots = ["~/code"];
        return json({ roots });
      }
      return json({}, 404);
    }));
    return saves;
  }

  it("shows the folder on the overview and chooses another by browsing", async () => {
    const saves = folderFixture();
    const { user } = renderApp({ route: "/settings" });
    await user.click(await screen.findByRole("link", { name: /All projects .*folder ~\/Projects/ }));
    await user.click(await screen.findByRole("link", { name: /Projects folder ~\/Projects/ }));
    const browser = await screen.findByRole("region", { name: "Choose a folder" });
    await user.click(await within(browser).findByRole("button", { name: /code/ }));
    await user.click(await within(browser).findByRole("button", { name: "Use “code”" }));
    await screen.findByRole("status", { name: "Saved." });
    expect(saves).toEqual([{ path: "/home/ada/code" }]);
    expect(screen.getByText("~/code")).toBeInTheDocument();
  });

  it("keeps a failed save actionable and retries the same folder", async () => {
    let fail = true;
    const saves = folderFixture({ fail: () => fail });
    const { user } = renderApp({ route: "/settings/projects-folder" });
    const browser = await screen.findByRole("region", { name: "Choose a folder" });
    await user.click(await within(browser).findByRole("button", { name: "Use “Home”" }));
    await screen.findByText("Settings unavailable");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText(/^Saved\./);
    expect(saves).toEqual([{ path: "/home/ada" }]);
  });
});

describe("Set up a device", () => {
  const FINGERPRINT = Array.from({ length: 32 }, (_, index) => (index + 16).toString(16).toUpperCase()).join(":");
  function shareFixture(options: { seconds?: number; refuse?: string } = {}) {
    const posts: { path: string; body: unknown }[] = [];
    let opened = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method === "POST") posts.push({ path, body: JSON.parse(String(init.body)) });
      if (path === "/api/devices") return json({ devices: [], current: null,
        certificate: { name: "Altitude local CA", expires: "2036-09-25T04:00:00+00:00", sha256: FINGERPRINT, scope: "Names under local." } });
      if (path === "/api/devices/share") {
        if (options.refuse) return json({ error: options.refuse }, 409);
        opened += 1;
        return json({ link: `http://192.168.1.20:4000${opened}/`, seconds: options.seconds ?? 600, name: "Altitude local CA",
          sha256: FINGERPRINT, qr: ["1010101", "0101010", "1111111", "0000000", "1010101", "0101010", "1111111"] });
      }
      if (path === "/api/devices/share-close") return json({ closed: true });
      if (path === "/api/overview") return json({ projects: [], queue: [], engines: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } });
      if (path === "/api/machine") return json({ operator: null, incident_repository: null, altitude_repository: "fixture/altitude", terminal: false });
      if (path === "/api/voice") return json(saved);
      return json({}, 404);
    }));
    return posts;
  }

  it("shows the QR code with its time left, and Close or leaving the page closes the window", async () => {
    const posts = shareFixture();
    const { user } = renderApp({ route: "/settings/devices" });
    const card = await screen.findByRole("region", { name: "Certificate" });
    await user.click(within(card).getByRole("button", { name: "Set up a device" }));
    expect(await within(card).findByRole("img", { name: "QR code for http://192.168.1.20:40001/" })).toBeInTheDocument();
    expect(within(card).getByRole("timer")).toHaveTextContent(/^Closes in (10:00|9:5\d)$/);
    expect(within(card).getByText("10 11 12 13 14 15 16 17", { exact: false })).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Close" }));
    expect(await within(card).findByRole("status")).toHaveTextContent("The link is closed.");
    await waitFor(() => expect(posts).toContainEqual({ path: "/api/devices/share-close", body: { link: "http://192.168.1.20:40001/" } }));
    await user.click(within(card).getByRole("button", { name: "Set up a device" }));
    await within(card).findByRole("img", { name: "QR code for http://192.168.1.20:40002/" });
    await user.click(screen.getByRole("link", { name: "‹ Settings" }));
    await waitFor(() => expect(posts).toContainEqual({ path: "/api/devices/share-close", body: { link: "http://192.168.1.20:40002/" } }));
  });

  it("starts at the full ten minutes even when the clock ticks while the window opens", async () => {
    shareFixture();
    let clock = 1_800_000_000_000;
    const now = vi.spyOn(Date, "now").mockImplementation(() => clock++);
    onTestFinished(() => now.mockRestore());
    const { user } = renderApp({ route: "/settings/devices" });
    await user.click(await screen.findByRole("button", { name: "Set up a device" }));
    await screen.findByRole("img", { name: /^QR code/ });
    expect(screen.getByRole("timer")).toHaveTextContent("Closes in 10:00");
  });

  it("shows the window closed when its time runs out, as the service closes it itself", async () => {
    const posts = shareFixture({ seconds: 1 });
    const { user } = renderApp({ route: "/settings/devices" });
    await user.click(await screen.findByRole("button", { name: "Set up a device" }));
    await screen.findByRole("img", { name: /^QR code/ });
    expect(await screen.findByText("The link is closed.")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /^QR code/ })).toBeNull();
    expect(posts.filter((post) => post.path === "/api/devices/share-close")).toHaveLength(0);
  });

  it("keeps the QR code and a retryable Close when the service could not close the link", async () => {
    const posts = shareFixture();
    const original = globalThis.fetch;
    let refuse = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/devices/share-close" && refuse) return json({ error: "Could not reach Altitude." }, 503);
      return original(input, init);
    }));
    const { user } = renderApp({ route: "/settings/devices" });
    await user.click(await screen.findByRole("button", { name: "Set up a device" }));
    await user.click(await screen.findByRole("button", { name: "Close" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The link is still open: Could not reach Altitude.");
    expect(screen.getByRole("img", { name: /^QR code/ })).toBeInTheDocument();
    refuse = false;
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(await screen.findByText("The link is closed.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(posts.filter((post) => post.path === "/api/devices/share-close")).toHaveLength(1);
  });

  it("closes a window that finishes opening after the page was left", async () => {
    const posts = shareFixture();
    const original = globalThis.fetch;
    let answer: () => void = () => undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/devices/share") await new Promise<void>((resolve) => { answer = resolve; });
      return original(input, init);
    }));
    const { user } = renderApp({ route: "/settings/devices" });
    await user.click(await screen.findByRole("button", { name: "Set up a device" }));
    await screen.findByRole("button", { name: "Opening…" });
    await user.click(screen.getByRole("link", { name: "‹ Settings" }));
    answer();
    await waitFor(() => expect(posts).toContainEqual({ path: "/api/devices/share-close", body: { link: "http://192.168.1.20:40001/" } }));
  });

  it("shows why the service cannot offer the certificate and keeps the button", async () => {
    shareFixture({ refuse: "The Altitude service is configured for 127.0.0.1, which only this computer can open." });
    const { user } = renderApp({ route: "/settings/devices" });
    await user.click(await screen.findByRole("button", { name: "Set up a device" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("configured for 127.0.0.1");
    expect(screen.getByRole("button", { name: "Set up a device" })).toBeEnabled();
  });
});

describe("Pair another device", () => {
  function codeFixture(code: Record<string, unknown>) {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/devices") return json({ devices: [], current: null, certificate: null });
      if (path === "/api/devices/code") return json(code);
      if (path === "/api/overview") return json({ projects: [], queue: [], engines: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } });
      if (path === "/api/machine") return json({ operator: null, incident_repository: null, altitude_repository: "fixture/altitude", terminal: false });
      if (path === "/api/voice") return json(saved);
      return json({}, 404);
    }));
  }

  it("shows the code with the address to open, its QR and the certificate's full SHA-256, and never a code link", async () => {
    codeFixture({ code: "ABCD-2345", minutes: 10, address: "https://192.168.1.20:8890", qr: ["1010101", "0101010", "1111111", "0000000", "1010101", "0101010", "1111111"],
      certificate: { name: "Altitude CA 4F7K", sha256: ["AA BB CC DD EE FF 00 11", "22 33 44 55 66 77 88 99", "AA BB CC DD EE FF 00 12", "22 33 44 55 66 77 88 9A"] } });
    const { user } = renderApp({ route: "/settings/devices" });
    const card = await screen.findByRole("region", { name: "Pair another device" });
    await user.click(within(card).getByRole("button", { name: "Make a pairing code" }));
    expect(await within(card).findByLabelText("Pairing code")).toHaveTextContent("ABCD-2345");
    expect(within(card).getByText(/Works once, for the next 10 minutes\. On the other device, open/)).toHaveTextContent("open https://192.168.1.20:8890 and type it.");
    expect(within(card).getByRole("img", { name: "QR code for https://192.168.1.20:8890" })).toBeInTheDocument();
    expect(within(card).getByText("Scan with a phone to open Altitude.")).toBeVisible();
    expect(within(card).getByText(/check it is named “Altitude CA 4F7K” with this SHA-256/)).toBeVisible();
    expect(within(card).getByLabelText("Certificate SHA-256")).toHaveTextContent("AA BB CC DD EE FF 00 1122 33 44 55 66 77 88 99AA BB CC DD EE FF 00 1222 33 44 55 66 77 88 9A");
    expect(within(card).queryByText(/pair\?code=/)).toBeNull();
    expect(within(card).getByRole("button", { name: "Make a new code" })).toBeVisible();
  });

  it("without HTTPS, says to type the code on the Pair this device screen", async () => {
    codeFixture({ code: "ABCD-2345", minutes: 10, address: null, qr: null, certificate: null });
    const { user } = renderApp({ route: "/settings/devices" });
    const card = await screen.findByRole("region", { name: "Pair another device" });
    await user.click(within(card).getByRole("button", { name: "Make a pairing code" }));
    expect(await within(card).findByText("Works once, for the next 10 minutes. On the other device, type it on the Pair this device screen.")).toBeVisible();
    expect(within(card).queryByRole("img")).toBeNull();
    expect(within(card).queryByText(/SHA-256/)).toBeNull();
  });
});

describe("Remove a device", () => {
  const device = (id: string, name: string) => ({ id, name, paired: "2026-10-01T08:00:00Z", used: "2026-10-18T08:00:00Z" });
  /** Three paired browsers; each removal waits until the test answers it. */
  function removalFixture() {
    let devices = [device("mine", "Safari on iPhone"), device("mac", "Chrome on Mac"), device("pad", "Safari on iPad")];
    const answers: Record<string, (refusal?: string) => void> = {};
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/devices/revoke") {
        const { id } = JSON.parse(String(init?.body));
        const refusal = await new Promise<string | undefined>((resolve) => { answers[id] = resolve; });
        if (refusal) return json({ error: refusal }, 503);
        devices = devices.filter((each) => each.id !== id);
      }
      if (path === "/api/devices" || path === "/api/devices/revoke") return json({ devices, current: "mine", certificate: null });
      if (path === "/api/overview") return json({ projects: [], queue: [], engines: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } });
      if (path === "/api/machine") return json({ operator: null, incident_repository: null, altitude_repository: "fixture/altitude", terminal: false });
      return json({}, 404);
    }));
    return answers;
  }
  const row = (name: string) => screen.getByText(name, { exact: true }).closest("li") as HTMLElement;
  async function remove(user: { click: (element: Element) => Promise<void> }, name: string) {
    await user.click(within(row(name)).getByRole("button", { name: "Remove" }));
    await user.click(within(row(name)).getByRole("button", { name: "Remove device" }));
  }

  it("a refused removal says why with Retry even while another removal is on its way", async () => {
    const answers = removalFixture();
    const { user } = renderApp({ route: "/settings/devices" });
    await screen.findByText("Chrome on Mac", { exact: true });
    await remove(user, "Chrome on Mac");
    await waitFor(() => expect(screen.queryByText("Chrome on Mac", { exact: true })).toBeNull());
    await remove(user, "Safari on iPad");
    await waitFor(() => expect(screen.queryByText("Safari on iPad", { exact: true })).toBeNull());
    await waitFor(() => expect(Object.keys(answers)).toEqual(["mac", "pad"]));
    await act(async () => answers.mac!("Altitude is busy."));
    expect(await within(await screen.findByRole("alert")).findByText(/^Not removed: Altitude is busy\./)).toBeVisible();
    expect(within(row("Chrome on Mac")).getByRole("button", { name: "Retry" })).toBeVisible();
    await act(async () => answers.pad!());
    await waitFor(() => expect(screen.queryByText("Safari on iPad", { exact: true })).toBeNull());
    expect(within(row("Chrome on Mac")).getByRole("alert")).toHaveTextContent("Not removed: Altitude is busy.");
  });

  it("this browser's row stays, saying Removing…, while its own removal is on its way", async () => {
    removalFixture();
    const { user } = renderApp({ route: "/settings/devices" });
    await screen.findByText("Safari on iPhone", { exact: true });
    await remove(user, "Safari on iPhone");
    expect(await within(row("Safari on iPhone")).findByText("Removing…")).toBeVisible();
    expect(within(row("Safari on iPhone")).getByRole("button")).toBeDisabled();
    expect(screen.queryByText(/No browser is paired/)).toBeNull();
  });
});
