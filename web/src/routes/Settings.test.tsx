import { act, screen, waitFor, within } from "@testing-library/react";
import { defaultScheduler, notifyManager } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

const saved = { backend: "endpoint", selection: "endpoint-one", url: "https://speech.example.test/transcribe", model: "", key_set: true, host: { state: "absent", download_bytes: 698435338 } };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
function fixture(initial: Record<string, unknown> = saved) {
  let setting = { ...initial };
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
    if (String(input) === "/api/machine") return json({ operator: null, incident_repository: null, altitude_repository: "fixture/altitude", terminal: false });
    return json({}, 404);
  }));
  return calls;
}

/** The host's voice setup: each action moves `host` along as the server would. */
function hostFixture(host: Record<string, unknown>, backend = "browser") {
  const actions: string[] = [];
  let setting = { backend, selection: `${backend}-selection`, url: "", model: "", key_set: false, host };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/voice/host") {
      const { action } = JSON.parse(String(init?.body));
      actions.push(action);
      const next = action === "setup" ? { state: "setting-up", download_bytes: 698435338, done_bytes: 0 }
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

describe("Host voice settings", () => {
  it.each([390, 1440])("choosing this computer offers the one-time setup, shows its progress and can cancel it (%i)", async (width) => {
    setViewport(width);
    const host = hostFixture({ state: "absent", download_bytes: 698435338 });
    const { user } = renderApp({ route: "/settings/voice" });
    expect(screen.queryByRole("button", { name: "Set up voice" })).toBeNull();
    await user.click(await screen.findByRole("radio", { name: "This computer — live text" }));
    expect(await screen.findByText("Needs a one-time download of about 698 MB, checked against this release.")).toBeVisible();
    expect(screen.getByText("Speech model: NVIDIA Parakeet TDT 0.6B v2, licensed CC-BY-4.0.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Set up voice" }));
    expect(await screen.findByText("Setting up… 0 MB of 698 MB")).toBeVisible();
    expect(screen.getByRole("progressbar", { name: "Voice setup" })).toBeVisible();
    host.advance({ state: "setting-up", download_bytes: 698435338, done_bytes: 349000000 });
    expect(await screen.findByText("Setting up… 349 MB of 698 MB", {}, { timeout: 3000 })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Cancel setup" }));
    expect(await screen.findByRole("button", { name: "Set up voice" })).toBeVisible();
    expect(host.actions).toEqual(["setup", "cancel"]);
  });

  it("a finished setup can be removed, and a failed one retried", async () => {
    const host = hostFixture({ state: "ready", download_bytes: 698435338 }, "host");
    const { user } = renderApp({ route: "/settings/voice" });
    expect(await screen.findByText(/Ready on this computer/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Remove voice (698 MB)" }));
    expect(await screen.findByRole("button", { name: "Set up voice" })).toBeVisible();
    expect(host.actions).toEqual(["remove"]);
  });

  it("a failed setup says so and offers Retry", async () => {
    hostFixture({ state: "failed", download_bytes: 698435338, reason: "Setup stopped: encoder-model.int8.onnx did not match its checksum." }, "host");
    renderApp({ route: "/settings/voice" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Setup did not finish. Setup stopped: encoder-model.int8.onnx did not match its checksum.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  it("a computer that cannot run voice says why and cannot choose it", async () => {
    hostFixture({ state: "unavailable", reason: "voice runs on Linux x86_64 only for now" });
    renderApp({ route: "/settings/voice" });
    expect(await screen.findByRole("radio", { name: "This computer — live text" })).toBeDisabled();
    expect(screen.getByText("Not available on this computer: voice runs on Linux x86_64 only for now.")).toBeVisible();
  });
});

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
      await user.click(screen.getByRole("button", { name: "Save service" }));
      expect(screen.getByLabelText("API key (optional)")).toBeDisabled();
      expect(screen.getByText("Saving…")).toBeVisible();
      release();
      await screen.findByText("Saved.");
      expect(screen.getByLabelText("Service URL")).toBeEnabled();
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
      await user.click(screen.getByRole("button", { name: "Save service" }));
      await screen.findByText("Saved.");
      await user.click(screen.getByRole("button", { name: "Hosted provider? Add a key or model" }));
      await user.type(screen.getByLabelText("API key (optional)"), "next-fixture-key");
      await act(async () => flush());
      expect(screen.getByLabelText("API key (optional)")).toHaveValue("next-fixture-key");
      await user.click(screen.getByRole("button", { name: "Save service" }));
      await waitFor(() => expect(calls[1]).toMatchObject({ key: "next-fixture-key" }));
      await screen.findByText("Key set · never shown");
      await user.click(screen.getByRole("radio", { name: "Browser recognition" }));
      await waitFor(() => expect(screen.getByRole("radio", { name: "Browser recognition" })).toBeChecked());
      await user.click(screen.getByRole("radio", { name: "Your speech service" }));
      await user.type(screen.getByLabelText("Service URL"), "http://127.0.0.1:8080/v1/audio/transcriptions");
      await user.click(screen.getByRole("button", { name: "Save service" }));
      await waitFor(() => expect(calls.at(-1)).toMatchObject({ backend: "endpoint", selection: "browser-selection" }));
    } finally {
      notifyManager.setScheduler(defaultScheduler);
      await act(async () => flush());
    }
  });

  it.each([390, 1440])("keeps overview compact, discards unsaved edits and never carries a key to a changed URL (%s)", async (width) => {
    setViewport(width);
    const calls = fixture();
    const { user } = renderApp({ route: "/settings" });
    await user.click(await screen.findByRole("link", { name: "Voice input Your speech service · speech.example.test" }));
    await screen.findByText("Key set · never shown");
    const url = screen.getByLabelText("Service URL");
    await user.clear(url);
    await user.type(url, "https://new.example.test/transcribe");
    expect(screen.queryByText("Key set · never shown")).toBeNull();
    expect(screen.getByLabelText("API key (optional)")).toHaveValue("");
    await user.click(screen.getByRole("link", { name: "‹ Settings" }));
    expect(screen.queryByRole("radio")).toBeNull();
    expect(calls).toHaveLength(0);
    await user.click(screen.getByRole("link", { name: "Voice input Your speech service · speech.example.test" }));
    await screen.findByText("Key set · never shown");
    expect(screen.getByLabelText("Service URL")).toHaveValue(saved.url);
    await user.click(screen.getByRole("button", { name: "Replace" }));
    await user.click(screen.getByRole("button", { name: "Save service" }));
    await screen.findByText("Saved.");
    expect(calls[0]).toMatchObject({ key: "", url: saved.url, selection: saved.selection });
    expect(calls[0]).not.toHaveProperty("keep_key");
  });

  it.each([390, 1440])("asks only for a URL, keeps the hosted key and model one click away, and names the host (%s)", async (width) => {
    setViewport(width);
    const calls = fixture({ backend: "browser", selection: "browser-selection", url: "", model: "", key_set: false, host: { state: "absent", download_bytes: 698435338 } });
    const { user } = renderApp({ route: "/settings" });
    await user.click(await screen.findByRole("link", { name: "Voice input Browser recognition" }));
    await user.click(await screen.findByRole("radio", { name: "Your speech service" }));
    expect(calls).toHaveLength(0);
    expect(screen.getByText(/using the standard OpenAI transcription API/)).toBeVisible();
    expect(screen.getByRole("link", { name: "How to run one" })).toHaveAttribute("href", "https://github.com/fixture/altitude/blob/main/docs/OPERATIONS.md#your-speech-service");
    expect(screen.queryByLabelText("Model (optional)")).toBeNull();
    expect(screen.queryByLabelText("API key (optional)")).toBeNull();
    await user.type(screen.getByLabelText("Service URL"), "http://127.0.0.1:8080/v1/audio/transcriptions");
    await user.click(screen.getByRole("button", { name: "Hosted provider? Add a key or model" }));
    expect(screen.getByLabelText("Model (optional)")).toHaveAttribute("placeholder", "Default: whisper-1");
    await user.type(screen.getByLabelText("API key (optional)"), "hosted-fixture-key");
    await user.click(screen.getByRole("button", { name: "Save service" }));
    await screen.findByText("Saved.");
    expect(calls).toEqual([{ backend: "endpoint", selection: "browser-selection", url: "http://127.0.0.1:8080/v1/audio/transcriptions", model: "", key: "hosted-fixture-key" }]);
    await user.click(screen.getByRole("link", { name: "‹ Settings" }));
    await screen.findByRole("link", { name: "Voice input Your speech service · 127.0.0.1:8080" });
  });

  it("closes the hosted fields once a save leaves no key and the default model", async () => {
    const calls = fixture();
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("button", { name: "Replace" }));
    await user.click(screen.getByRole("button", { name: "Save service" }));
    await screen.findByText("Saved.");
    expect(calls[0]).toMatchObject({ key: "", model: "" });
    expect(screen.queryByLabelText("API key (optional)")).toBeNull();
    expect(screen.getByRole("button", { name: "Hosted provider? Add a key or model" })).toBeVisible();
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
    expect(screen.getByRole("radio", { name: "Your speech service" })).toBeChecked();
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
    await screen.findByRole("link", { name: "Voice input Your speech service · speech.example.test" });
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
    expect(screen.getByRole("radio", { name: "Your speech service" })).toBeChecked();
    stale = false;
    await user.click(screen.getByRole("radio", { name: "Browser recognition" }));
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
    await user.click(await screen.findByRole("link", { name: /Projects folder ~\/Projects/ }));
    const browser = await screen.findByRole("region", { name: "Choose a folder" });
    await user.click(await within(browser).findByRole("button", { name: /code/ }));
    await user.click(await within(browser).findByRole("button", { name: "Use “code”" }));
    await screen.findByText("Saved. First run now lists the folders in ~/code.");
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
