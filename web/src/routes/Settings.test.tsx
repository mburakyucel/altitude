import { act, screen, waitFor, within } from "@testing-library/react";
import { defaultScheduler, notifyManager } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
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
    await user.click(await screen.findByRole("radio", { name: "This computer" }));
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
    expect(await screen.findByRole("radio", { name: "This computer" })).toBeDisabled();
    expect(screen.getByText("Not available on this computer: voice runs on Linux x86_64 only for now.")).toBeVisible();
  });
});

describe("Voice settings", () => {
  it.each([390, 1440])("offers exactly two choices, This computer first, and keeps the overview compact (%s)", async (width) => {
    setViewport(width);
    const calls = fixture();
    const { user } = renderApp({ route: "/settings" });
    await user.click(await screen.findByRole("link", { name: "Voice input This computer" }));
    const radios = await screen.findAllByRole("radio");
    expect(radios.map((radio) => radio.closest("label")?.textContent)).toEqual(["This computer", "Browser recognition"]);
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
      expect(screen.getByRole("radio", { name: "This computer" })).toBeDisabled();
      expect(screen.getByText("Saving…")).toBeVisible();
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
      await screen.findByText("Saved.");
      await act(async () => flush());
      expect(screen.getByRole("radio", { name: "Browser recognition" })).toBeChecked();
      await user.click(screen.getByRole("radio", { name: "This computer" }));
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
    expect(await screen.findByRole("alert")).toHaveTextContent("Settings unavailable Retry");
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
    expect(await screen.findByRole("alert")).toHaveTextContent("Voice settings changed. Reload settings and try again.");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Reload settings" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.getByRole("radio", { name: "This computer" })).toBeChecked();
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

describe("Add a phone", () => {
  const FINGERPRINT = Array.from({ length: 32 }, (_, index) => (index + 16).toString(16).toUpperCase()).join(":");
  function shareFixture(options: { seconds?: number; refuse?: string } = {}) {
    const posts: { path: string; body: unknown }[] = [];
    let opened = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method === "POST") posts.push({ path, body: JSON.parse(String(init.body)) });
      if (path === "/api/devices") return json({ devices: [], current: null,
        certificate: { name: "Altitude local CA", expires: "Sep 25 04:00:00 2036 GMT", sha256: FINGERPRINT, scope: "Names under local." } });
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
    await user.click(within(card).getByRole("button", { name: "Add a phone" }));
    expect(await within(card).findByRole("img", { name: "QR code for http://192.168.1.20:40001/" })).toBeInTheDocument();
    expect(within(card).getByRole("timer")).toHaveTextContent(/^Closes in (10:00|9:5\d)$/);
    expect(within(card).getByText("10 11 12 13 14 15 16 17", { exact: false })).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Close" }));
    expect(await within(card).findByRole("status")).toHaveTextContent("The link is closed.");
    await waitFor(() => expect(posts).toContainEqual({ path: "/api/devices/share-close", body: { link: "http://192.168.1.20:40001/" } }));
    await user.click(within(card).getByRole("button", { name: "Add a phone" }));
    await within(card).findByRole("img", { name: "QR code for http://192.168.1.20:40002/" });
    await user.click(screen.getByRole("link", { name: "‹ Settings" }));
    await waitFor(() => expect(posts).toContainEqual({ path: "/api/devices/share-close", body: { link: "http://192.168.1.20:40002/" } }));
  });

  it("shows the window closed when its time runs out, as the service closes it itself", async () => {
    const posts = shareFixture({ seconds: 1 });
    const { user } = renderApp({ route: "/settings/devices" });
    await user.click(await screen.findByRole("button", { name: "Add a phone" }));
    await screen.findByRole("img", { name: /^QR code/ });
    expect(await screen.findByText("The link is closed.", {}, { timeout: 4000 })).toBeInTheDocument();
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
    await user.click(await screen.findByRole("button", { name: "Add a phone" }));
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
    await user.click(await screen.findByRole("button", { name: "Add a phone" }));
    await screen.findByRole("button", { name: "Opening…" });
    await user.click(screen.getByRole("link", { name: "‹ Settings" }));
    answer();
    await waitFor(() => expect(posts).toContainEqual({ path: "/api/devices/share-close", body: { link: "http://192.168.1.20:40001/" } }));
  });

  it("shows why the service cannot offer the certificate and keeps the button", async () => {
    shareFixture({ refuse: "The Altitude service is configured for 127.0.0.1, which only this computer can open." });
    const { user } = renderApp({ route: "/settings/devices" });
    await user.click(await screen.findByRole("button", { name: "Add a phone" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("configured for 127.0.0.1");
    expect(screen.getByRole("button", { name: "Add a phone" })).toBeEnabled();
  });
});
