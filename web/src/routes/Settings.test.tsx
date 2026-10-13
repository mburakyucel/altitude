import { act, screen, waitFor, within } from "@testing-library/react";
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

describe("Host voice settings", () => {
  it.each([390, 1440])("choosing this computer offers the one-time setup, shows its progress and can cancel it (%i)", async (width) => {
    setViewport(width);
    const host = hostFixture({ state: "absent", download_bytes: 698435338 });
    const { user } = renderApp({ route: "/settings/voice" });
    expect(screen.queryByRole("button", { name: "Set up voice" })).toBeNull();
    await user.click(await screen.findByRole("radio", { name: "This computer" }));
    expect(await screen.findByText("Needs a one-time download of about 698 MB, checked against this release.")).toBeVisible();
    expect(screen.getByText("Speech model: NVIDIA Parakeet TDT 0.6B v2, licensed CC-BY-4.0.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Set up voice" }));
    expect(await screen.findByRole("progressbar", { name: "Voice setup" })).toBeVisible();
    expect(screen.getByRole("progressbar", { name: "Voice setup" })).toBeVisible();
    host.advance({ state: "setting-up", download_bytes: 698435338, done_bytes: 349000000 });
    await waitFor(() => expect(screen.getByRole("progressbar", { name: "Voice setup" })).toHaveAttribute("value", "349000000"));
    await user.click(screen.getByRole("button", { name: "Cancel setup" }));
    expect(await screen.findByRole("button", { name: "Set up voice" })).toBeVisible();
    expect(host.actions).toEqual(["setup", "cancel"]);
  });

  it("a finished setup can be removed, and a failed one retried", async () => {
    const host = hostFixture({ state: "ready", download_bytes: 698435338 }, "host");
    const { user } = renderApp({ route: "/settings/voice" });
    expect(await screen.findByRole("button", { name: /Remove voice/ })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Remove voice" }));
    expect(await screen.findByRole("button", { name: "Set up voice" })).toBeVisible();
    expect(host.actions).toEqual(["remove"]);
  });

  it("a failed setup says so and offers Retry", async () => {
    hostFixture({ state: "failed", download_bytes: 698435338, reason: "Setup stopped: encoder-model.int8.onnx did not match its checksum." }, "host");
    renderApp({ route: "/settings/voice" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Setup did not finish. Setup stopped: encoder-model.int8.onnx did not match its checksum.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  it("keeps supported voice distinct from update progress, cancellation, retry and removal", async () => {
    const host = hostFixture({ state: "ready", download_bytes: 701000000,
      update: { state: "downloading", done_bytes: 12000000, download_bytes: 40000000 } }, "host");
    const { user } = renderApp({ route: "/settings/voice" });
    const progress = await screen.findByRole("progressbar", { name: "Updating voice" });
    expect(progress).toHaveAttribute("value", "12000000");
    expect(progress).toHaveAttribute("max", "40000000");
    expect(screen.getByText("Updating voice · 12 MB of 40 MB")).toBeVisible();
    expect(screen.getByRole("button", { name: "Remove voice" })).toHaveClass("btn-outline-danger");
    await user.click(screen.getByRole("button", { name: "Cancel update" }));
    expect(await screen.findByText("Update cancelled.")).toBeVisible();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.getByRole("button", { name: "Retry update" })).not.toHaveClass("btn-primary");
    expect(screen.queryByRole("alert")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Retry update" }));
    expect(await screen.findByRole("img", { name: "Preparing update…" })).toBeVisible();
    expect(screen.queryByRole("progressbar")).toBeNull();
    host.advance({ state: "ready", download_bytes: 701000000, update: { state: "waiting" } });
    expect(await screen.findByText("The update will finish after dictation.")).toBeVisible();
    host.advance({ state: "ready", download_bytes: 701000000, update: { state: "checking" } });
    expect(await screen.findByRole("img", { name: "Checking update…" }, { timeout: 2000 })).toBeVisible();
    host.advance({ state: "ready", download_bytes: 701000000 });
    expect(await screen.findByText("While you dictate, the speech process uses about 2 GB of memory.", {}, { timeout: 2000 })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancel update" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Remove voice" }));
    expect(await screen.findByRole("button", { name: "Set up voice" })).toBeVisible();
    expect(host.actions).toEqual(["cancel", "setup", "remove"]);
  });

  it.each(["ready", "outdated"])("update failure keeps the reason and only alerts when voice is unusable (%s)", async (state) => {
    hostFixture({ state, download_bytes: 701000000, update: { state: "failed", reason: "Download failed." } }, "host");
    renderApp({ route: "/settings/voice" });
    const failure = await screen.findByText("Update did not finish. Download failed.");
    expect(failure.closest('[role]')).toHaveAttribute("role", state === "ready" ? "status" : "alert");
    expect(screen.getByRole("button", { name: "Retry update" })).toHaveClass("btn-primary");
    expect(screen.getByRole("button", { name: "Remove voice" })).toBeEnabled();
    expect(screen.queryByText("Voice needs an update.") !== null).toBe(state === "outdated");
  });

  it.each(["cancel", "setup", "remove"])("locks update actions through authoritative response when %s is pending", async (action) => {
    hostFixture({ state: "ready", download_bytes: 701000000, update: { state: action === "setup" ? "cancelled" : "preparing" } }, "host");
    const original = globalThis.fetch;
    let release = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/voice/host") await gate;
      return original(input, init);
    }));
    const { user } = renderApp({ route: "/settings/voice" });
    const label = action === "cancel" ? "Cancel update" : action === "setup" ? "Retry update" : "Remove voice";
    await user.click(await screen.findByRole("button", { name: label }));
    expect(screen.getByRole("button", { name: action === "cancel" ? "Cancelling update" : action === "setup" ? "Starting update" : "Removing voice" })).toBeDisabled();
    for (const control of document.querySelectorAll('.voice-host button')) expect(control).toBeDisabled();
    release();
    expect(await screen.findByRole("button", { name: action === "cancel" ? "Retry update" : action === "setup" ? "Cancel update" : "Set up voice" })).toBeEnabled();
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
    expect(screen.getByRole("radio", { name: "This computer" })).toBeChecked();
  });

  it.each(["ready", "outdated"])("overview separates installed availability from update status (%s)", async (state) => {
    hostFixture({ state, download_bytes: 701000000, update: { state: "downloading", done_bytes: 12, download_bytes: 40 } }, "host");
    renderApp({ route: "/settings" });
    expect(await screen.findByRole("link", { name: state === "ready" ? "Voice input This computer" : "Voice input This computer · update required" })).toBeVisible();
  });

  it("a computer that cannot run voice says why and cannot choose it", async () => {
    hostFixture({ state: "unavailable", reason: "voice runs on Linux x86_64 and on Macs with Apple silicon" });
    renderApp({ route: "/settings/voice" });
    expect(await screen.findByRole("radio", { name: "This computer" })).toBeDisabled();
    expect(screen.getByText("Not available on this computer: voice runs on Linux x86_64 and on Macs with Apple silicon.")).toBeVisible();
  });
});

describe("Voice settings", () => {
  it("fresh settings replace an untouched cached form but preserve an edit's original selection", async () => {
    const calls = fixture();
    const { user, queryClient } = renderApp({ route: "/settings/voice" });
    await screen.findByRole("radio", { name: "This computer" });
    act(() => queryClient.setQueryData(["voice-settings"], { ...saved, backend: "browser", selection: "browser-fresh" }));
    await waitFor(() => expect(screen.getByRole("radio", { name: "Browser recognition" })).toBeChecked());
    await user.click(screen.getByRole("radio", { name: "This computer" }));
    act(() => queryClient.setQueryData(["voice-settings"], { ...saved, backend: "browser", selection: "browser-later" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Saved.");
    expect(calls).toEqual([{ backend: "host", selection: "browser-fresh" }]);
  });
  it("service edits are drafts, preserve an empty saved key and explicitly remove details on switching", async () => {
    const calls = fixture({ ...saved, backend: "service", owner: "device", service: { url: "https://speech.example.test/v1/audio/transcriptions", model: "small", key_present: true } });
    const { user } = renderApp({ route: "/settings/voice" });
    await screen.findByRole("radio", { name: "Your speech service" });
    await user.click(screen.getByText("Key or model (optional)"));
    expect(screen.getByLabelText("API key")).toHaveValue("");
    expect(screen.getByLabelText("API key")).toHaveAttribute("placeholder", "Key saved — leave blank to keep");
    await user.click(screen.getByRole("radio", { name: "Browser recognition" }));
    expect(calls).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("radio", { name: "Your speech service" })).toBeChecked();
    await user.click(screen.getByRole("radio", { name: "Browser recognition" }));
    await user.click(screen.getByRole("button", { name: "Save and remove service" }));
    await screen.findByText("Saved.");
    expect(calls).toEqual([{ backend: "browser", selection: "host-one" }]);
  });

  it("changing a service URL clears any typed key and explains the new destination", async () => {
    const calls = fixture({ ...saved, backend: "service", owner: "device", service: { url: "https://speech.example.test/v1/audio/transcriptions", key_present: true } });
    const { user } = renderApp({ route: "/settings/voice" });
    await screen.findByRole("radio", { name: "Your speech service" });
    await user.click(screen.getByText("Key or model (optional)"));
    await user.type(screen.getByLabelText("API key"), "fictional-key");
    await user.clear(screen.getByLabelText("Transcription URL"));
    await user.type(screen.getByLabelText("Transcription URL"), "http://new.example.test/v1/audio/transcriptions");
    expect(screen.getByLabelText("API key")).toHaveValue("");
    expect(screen.getByText("Enter a key for the new address, if needed.")).toBeVisible();
    expect(screen.getByText("This connection is unencrypted. Audio and any key travel over HTTP.")).toBeVisible();
    expect(calls).toHaveLength(0);
  });
  it.each([390, 1440])("offers three deliberate choices, This computer first, and keeps the overview compact (%s)", async (width) => {
    setViewport(width);
    const calls = fixture();
    const { user } = renderApp({ route: "/settings" });
    await user.click(await screen.findByRole("link", { name: "Voice input This computer" }));
    const radios = await screen.findAllByRole("radio");
    expect(radios.map((radio) => radio.closest("label")?.textContent)).toEqual(["This computer", "Browser recognition", "Your speech service"]);
    expect(screen.getByRole("radio", { name: "This computer" })).toBeChecked();
    expect(screen.queryByRole("textbox")).toBeNull();
    await user.click(screen.getByRole("link", { name: "‹ Settings" }));
    expect(screen.queryByRole("radio")).toBeNull();
    expect(calls).toHaveLength(0);
  });

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
    await screen.findByRole("radio", { name: "This computer" });
    const pending: (() => void)[] = [];
    notifyManager.setScheduler((callback) => pending.push(callback));
    try {
      await user.click(screen.getByRole("radio", { name: "Browser recognition" }));
      await user.click(screen.getByRole("button", { name: "Save changes" }));
      expect(screen.getByRole("radio", { name: "This computer" })).toBeDisabled();
      expect(screen.getByText("Saving…")).toBeVisible();
      expect(screen.getByRole("status", { name: "Saving…" })).toBeVisible();
      release();
      await screen.findByText("Saved.");
      expect(screen.getByRole("radio", { name: "This computer" })).toBeEnabled();
      expect(screen.getByRole("radio", { name: "Browser recognition" })).toBeChecked();
    } finally {
      release();
      notifyManager.setScheduler(defaultScheduler);
      await act(async () => { while (pending.length) pending.shift()!(); });
    }
  });

  it("saves each choice with the selection it replaces and keeps it while cache notifications wait", async () => {
    const calls = fixture();
    const { user } = renderApp({ route: "/settings/voice" });
    await screen.findByRole("radio", { name: "This computer" });
    const pending: (() => void)[] = [];
    const flush = () => { while (pending.length) pending.shift()!(); };
    notifyManager.setScheduler((callback) => pending.push(callback));
    try {
      await user.click(screen.getByRole("radio", { name: "Browser recognition" }));
      await user.click(screen.getByRole("button", { name: "Save changes" }));
      await screen.findByText("Saved.");
      await act(async () => flush());
      expect(screen.getByRole("radio", { name: "Browser recognition" })).toBeChecked();
      await user.click(screen.getByRole("radio", { name: "This computer" }));
      await user.click(screen.getByRole("button", { name: "Save changes" }));
      await waitFor(() => expect(calls).toHaveLength(2));
      await act(async () => flush());
      await waitFor(() => expect(screen.getByRole("radio", { name: "This computer" })).toBeChecked());
      expect(calls).toEqual([{ backend: "browser", selection: "host-one" }, { backend: "host", selection: "browser-selection" }]);
    } finally {
      notifyManager.setScheduler(defaultScheduler);
      await act(async () => flush());
    }
  });

  it("shows save failure with the unsaved choice and retries the same choice", async () => {
    const calls = fixture();
    const fetchOriginal = globalThis.fetch;
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST" && fail) return json({ error: "Settings unavailable" }, 503);
      return fetchOriginal(input, init);
    }));
    const { user } = renderApp({ route: "/settings/voice" });
    await user.click(await screen.findByRole("radio", { name: "Browser recognition" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save voice settings. Retry");
    // The choice being saved stays shown beside its failure; Retry saves that same choice.
    expect(screen.getByRole("radio", { name: "Browser recognition" })).toBeChecked();
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
    await screen.findByRole("radio", { name: "This computer" });
    await user.click(screen.getByRole("link", { name: "Settings" }));
    await screen.findByRole("link", { name: "Voice input This computer" });
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
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Voice settings changed. Reload settings and try again.");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Reload settings" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.getByRole("radio", { name: "This computer" })).toBeChecked();
    stale = false;
    await user.click(screen.getByRole("radio", { name: "Browser recognition" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
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

  it("shows the code with the address to open, its QR and the certificate's check, and never a code link", async () => {
    codeFixture({ code: "ABCD-2345", minutes: 10, address: "https://192.168.1.20:8890", qr: ["1010101", "0101010", "1111111", "0000000", "1010101", "0101010", "1111111"],
      certificate: { name: "Altitude CA 4F7K", check: "AA BB CC DD EE FF 00 11" } });
    const { user } = renderApp({ route: "/settings/devices" });
    const card = await screen.findByRole("region", { name: "Pair another device" });
    await user.click(within(card).getByRole("button", { name: "Make a pairing code" }));
    expect(await within(card).findByLabelText("Pairing code")).toHaveTextContent("ABCD-2345");
    expect(within(card).getByText(/Works once, for the next 10 minutes\. On the other device, open/)).toHaveTextContent("open https://192.168.1.20:8890 and type it.");
    expect(within(card).getByRole("img", { name: "QR code for https://192.168.1.20:8890" })).toBeInTheDocument();
    expect(within(card).getByText("Scan with a phone to open Altitude.")).toBeVisible();
    expect(within(card).getByText(/Certificate “Altitude CA 4F7K” — SHA-256 ends with/)).toHaveTextContent("AA BB CC DD EE FF 00 11");
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
