import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
// @ts-expect-error Node types are not part of the browser app's TypeScript surface.
import { execFileSync } from "node:child_process";
// @ts-expect-error Node types are not part of the browser app's TypeScript surface.
import { rmSync } from "node:fs";
import { renderApp } from "../test/render";
import { installVoiceBrowser } from "../components/voiceTest";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** NDJSON over a ReadableStream, one enqueue per chunk — lines may straddle chunks. */
function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

const overview = {
  projects: [{ name: "altitude", managed: true }],
  queue: [],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
  now: new Date().toISOString(),
};

const chatView = {
  history: [
    { at: new Date(Date.now() - 5 * 60_000).toISOString(), role: "user", text: "how is it going?" },
    {
      at: new Date(Date.now() - 4 * 60_000).toISOString(),
      role: "assistant",
      text: "two tasks running.",
      trigger: "chat",
      context_percent: 12,
    },
  ],
  busy: false,
  l3: { session_id: "abcdef1234567890", context_percent: 12, turns: 3 },
};

function postsToChat(mock: ReturnType<typeof vi.fn>) {
  return mock.mock.calls.filter(
    ([u, init]) =>
      String(u).includes("/api/chat") && (init as RequestInit | undefined)?.method === "POST",
  );
}

const route = "/chat/altitude";

function productionStyles(): string {
  const script = String.raw`
    import { build } from "vite";
    const result = await build({ logLevel: "silent", build: { write: false,
      rollupOptions: { input: "src/styles.css" } } });
    const output = Array.isArray(result) ? result.flatMap((bundle) => bundle.output) : result.output;
    const css = output.find((file) => file.type === "asset" && file.fileName.endsWith(".css"));
    if (!css || typeof css.source !== "string") {
      throw new Error("Vite did not build the app stylesheet");
    }
    process.stdout.write(css.source);
  `;
  return execFileSync("node", ["--input-type=module", "-e", script], { encoding: "utf8" });
}

function mobileLayout(body: string, styles: string, width: number, height: number) {
  const profile = `/tmp/altitude-chat-layout-${Date.now()}-${Math.random()}`;
  const html = `<!doctype html>
    <html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>${styles}</style></head><body>${body}</body></html>`;
  const chromeScript = String.raw`
    import { spawn } from "node:child_process";
    const [html, width, height, profile] = process.argv.slice(1);
    const chrome = spawn("google-chrome", ["--headless=new", "--no-sandbox", "--disable-gpu",
      "--disable-dev-shm-usage", "--disable-extensions", "--no-first-run",
      "--remote-debugging-pipe", "--user-data-dir=" + profile, "about:blank"],
      { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"] });
    const stopped = new Promise((resolve) => chrome.once("exit", resolve));
    let serial = 0;
    let buffer = "";
    const pending = new Map();
    chrome.stdio[4].setEncoding("utf8");
    chrome.stdio[4].on("data", (chunk) => {
      buffer += chunk;
      let end;
      while ((end = buffer.indexOf("\0")) !== -1) {
        const raw = buffer.slice(0, end);
        buffer = buffer.slice(end + 1);
        if (!raw) continue;
        const message = JSON.parse(raw);
        const waiting = pending.get(message.id);
        if (waiting) {
          clearTimeout(waiting.timer);
          pending.delete(message.id);
          waiting.resolve(message);
        }
      }
    });
    function cdp(method, params = {}, sessionId) {
      const id = ++serial;
      chrome.stdio[3].write(JSON.stringify({ id, method, params,
        ...(sessionId ? { sessionId } : {}) }) + "\0");
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("Chrome timeout: " + method)), 10000);
        pending.set(id, { resolve, timer });
      });
    }
    try {
      let page;
      for (let attempt = 0; attempt < 50 && !page; attempt += 1) {
        const targets = await cdp("Target.getTargets");
        page = targets.result.targetInfos.find((target) => target.type === "page");
        if (!page) await new Promise((resolve) => setTimeout(resolve, 50));
      }
      const attached = await cdp("Target.attachToTarget", { targetId: page.targetId, flatten: true });
      const session = attached.result.sessionId;
      await cdp("Emulation.setDeviceMetricsOverride", { width: Number(width), height: Number(height),
        deviceScaleFactor: 3, mobile: false, screenWidth: Number(width), screenHeight: Number(height) }, session);
      await cdp("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 }, session);
      await cdp("Page.navigate", { url: "data:text/html;charset=utf-8," + encodeURIComponent(html) }, session);
      for (let attempt = 0; attempt < 100; attempt += 1) {
        const ready = await cdp("Runtime.evaluate", { expression:
          "document.readyState === 'complete' && Boolean(document.querySelector('.chat-route'))",
          returnByValue: true }, session);
        if (ready.result.result.value) break;
        if (attempt === 99) throw new Error("Chrome did not render Chat");
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      const measured = await cdp("Runtime.evaluate", { expression: "(() => {" +
        "const route = document.querySelector('.chat-route');" +
        "const transcript = document.querySelector('[aria-label=Transcript]');" +
        "const nodes = [document.documentElement, document.body, document.querySelector('main')," +
        "route, transcript, route.querySelector('header'), route.querySelector('nav')," +
        "route.querySelector('form'), route.querySelector('textarea')," +
        "...route.querySelectorAll('article, article p, article span, .pill, .voice-review, .voice-transcript, .voice-review-actions, button')];" +
        "const bounds = route.getBoundingClientRect();" +
        "return { viewport: innerWidth, touch: matchMedia('(hover: none) and (pointer: coarse)').matches," +
        "nodes: nodes.map((node) => {" +
        "const rect = node.getBoundingClientRect(); return { tag: node.tagName, classes: node.className," +
        "clientWidth: node.clientWidth, scrollWidth: node.scrollWidth, clientHeight: node.clientHeight," +
        "scrollHeight: node.scrollHeight, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom," +
        "routeLeft: bounds.left, routeRight: bounds.right, routeTop: bounds.top, routeBottom: bounds.bottom }; }) }; })()", returnByValue: true }, session);
      console.log(JSON.stringify(measured.result.result.value));
    } finally {
      chrome.kill("SIGTERM");
      await stopped;
    }
  `;

  try {
    const output = execFileSync("node", ["--input-type=module", "-e", chromeScript,
      html, String(width), String(height), profile], { encoding: "utf8" });
    return JSON.parse(output) as {
      viewport: number;
      touch: boolean;
      nodes: Array<{
        tag: string;
        classes: string;
        clientWidth: number;
        scrollWidth: number;
        clientHeight: number;
        scrollHeight: number;
        left: number;
        right: number;
        top: number;
        bottom: number;
        routeLeft: number;
        routeRight: number;
        routeTop: number;
        routeBottom: number;
      }>;
    };
  } finally {
    rmSync(profile, { recursive: true, force: true });
  }
}

describe("Chat", () => {
  it("renders the L3 history", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) return jsonResponse(overview);
        if (url.includes("/api/chat")) return jsonResponse(chatView);
        return jsonResponse({ error: "not found" }, 404);
      }),
    );
    renderApp({ route });

    await screen.findByText("how is it going?");
    expect(screen.getByText("two tasks running.")).toBeInTheDocument();
    expect(screen.getByText("L3 · chat · 4m · ctx 12%")).toBeInTheDocument();
  });

  it("keeps only the transcript in the bounded scroll region", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) return jsonResponse(overview);
        if (url.includes("/api/chat")) return jsonResponse(chatView);
        return jsonResponse({ error: "not found" }, 404);
      }),
    );
    renderApp({ route });

    const transcript = await screen.findByRole("region", { name: "Transcript" });
    const chatRoute = transcript.closest(".chat-route");

    expect(chatRoute).toHaveClass("min-h-0", "flex-1", "overflow-hidden");
    expect(chatRoute?.parentElement).toBe(screen.getByRole("main"));
    expect(transcript).toHaveClass(
      "min-h-0",
      "min-w-0",
      "flex-1",
      "overflow-x-hidden",
      "overflow-y-auto",
    );
    expect(transcript).not.toContainElement(screen.getByLabelText("Message L3"));
    expect(transcript).not.toContainElement(screen.getByRole("navigation", { name: "Projects" }));
    expect(screen.getByLabelText("Message L3")).toHaveClass("voice-composer-textarea");
  });

  it("contains hostile chat content at iPhone portrait and landscape widths", async () => {
    const unbroken = "https://example.test/" + "x".repeat(500);
    const streaming = "stream-" + "s".repeat(500);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) {
        return jsonResponse({
          ...overview,
          projects: [
            ...overview.projects,
            { name: `project-${"p".repeat(100)}`, managed: true },
          ],
        });
      }
      if (url.includes("/api/chat") && init?.method === "POST") {
        return streamResponse([`{"t":"${streaming}"}\n`, '{"done":{"error":null}}']);
      }
      if (url.includes("/api/chat")) {
        return jsonResponse({
          ...chatView,
          busy: true,
          history: [
            { at: "2026-09-03T00:00:00Z", role: "user", text: unbroken },
            {
              at: "2026-09-03T00:00:01Z",
              role: "assistant",
              text: unbroken,
              trigger: `trigger-${"t".repeat(100)}`,
            },
          ],
          queued: [
            {
              id: "q1",
              at: "2026-09-03T00:00:02Z",
              trigger: "chat",
              role: "burak",
              text: `queued-${"q".repeat(500)}`,
            },
          ],
        });
      }
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route });
    await screen.findAllByText(unbroken);
    fireEvent.change(screen.getByLabelText("Message L3"), { target: { value: unbroken } });
    await user.click(screen.getByRole("button", { name: "Queue" }));
    await waitFor(() => expect(postsToChat(fetchMock)).toHaveLength(1));
    await screen.findByText(streaming);
    const css = productionStyles();

    for (const [width, height] of [[390, 844], [844, 390]] as const) {
      const layout = mobileLayout(document.body.innerHTML, css, width, height);
      expect(layout.viewport).toBe(width);
      expect(layout.touch).toBe(true);
      const overflow: string[] = [];
      for (const node of layout.nodes) {
        const shell = ["HTML", "BODY", "MAIN"].includes(node.tag);
        const label = `${node.tag}.${node.classes}`;
        if (node.scrollWidth > node.clientWidth) overflow.push(`${label} scrolls sideways`);
        if (node.left < (shell ? 0 : node.routeLeft) - 0.5) overflow.push(`${label} crosses left`);
        if (node.right > (shell ? width : node.routeRight) + 0.5) overflow.push(`${label} crosses right`);
      }
      expect(overflow, `${width}x${height} horizontal overflow`).toEqual([]);
    }
  });

  it("streams the reply into the transcript across chunk boundaries", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/chat") && init?.method === "POST") {
        return streamResponse([
          '{"t":"Two tasks are ',
          'run',
          'ning."}\n{"t":" One is blo',
          'cked."}\n',
          // the terminal line arrives with no trailing newline
          '{"done":{"session_id":"s1","context_percent":13,"error":null}}',
        ]);
      }
      if (url.includes("/api/chat")) return jsonResponse(chatView);
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route });
    await screen.findByText("how is it going?");

    await user.type(screen.getByLabelText("Message L3"), "status?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Two tasks are running. One is blocked.");
    expect(screen.getByText("status?")).toBeInTheDocument();
    expect(postsToChat(fetchMock)).toHaveLength(1);
    expect(JSON.parse(String(postsToChat(fetchMock)[0]?.[1]?.body))).toEqual({
      project: "altitude",
      text: "status?",
    });
  });

  it("pins the project's L3 to the chosen engine and clears the pin on Auto", async () => {
    // The server keeps the pin; the refetched chat view carries it back after each change.
    let pinned: string | null = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/l3/engine")) {
        pinned = (JSON.parse(String(init?.body)) as { engine: string | null }).engine;
        return jsonResponse({ ok: true, engine: pinned });
      }
      if (url.includes("/api/chat")) return jsonResponse({ ...chatView, engine: pinned });
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const pins = () =>
      fetchMock.mock.calls
        .filter(([u]) => String(u).includes("/api/l3/engine"))
        .map(([, init]) => JSON.parse(String((init as RequestInit | undefined)?.body)) as unknown);

    const { user } = renderApp({ route });
    await screen.findByText("how is it going?");
    const select = screen.getByLabelText("L3 engine") as HTMLSelectElement;
    expect(select.value).toBe("auto");

    await user.selectOptions(select, "codex");
    await waitFor(() => expect(pins()).toEqual([{ project: "altitude", engine: "codex" }]));
    await waitFor(() => expect(select.value).toBe("codex"));

    await user.selectOptions(select, "auto");
    await waitFor(() => expect(pins()).toHaveLength(2));
    expect(pins()[1]).toEqual({ project: "altitude", engine: null });
    await waitFor(() => expect(select.value).toBe("auto"));
  });

  it("follows streamed text inside the transcript until the reader scrolls away", async () => {
    const encoder = new TextEncoder();
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/chat") && init?.method === "POST") {
        return new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              streamController = controller;
            },
          }),
          { status: 200 },
        );
      }
      if (url.includes("/api/chat")) return jsonResponse(chatView);
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route });
    const transcript = await screen.findByRole("region", { name: "Transcript" });
    let scrollTop = 0;
    const scrollWrites: number[] = [];
    Object.defineProperties(transcript, {
      clientHeight: { configurable: true, value: 300 },
      scrollHeight: { configurable: true, value: 900 },
      scrollTop: {
        configurable: true,
        get: () => scrollTop,
        set: (value: number) => {
          scrollTop = value;
          scrollWrites.push(value);
        },
      },
    });

    await user.type(screen.getByLabelText("Message L3"), "status?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("you · sending");
    await waitFor(() => expect(scrollWrites).toContain(900));

    scrollWrites.length = 0;
    await act(async () => {
      streamController?.enqueue(encoder.encode('{"t":"first"}\n'));
    });
    await screen.findByText("first");
    await waitFor(() => expect(scrollWrites).toContain(900));

    scrollTop = 100;
    fireEvent.scroll(transcript);
    scrollWrites.length = 0;
    await act(async () => {
      streamController?.enqueue(encoder.encode('{"t":" second"}\n{"done":{"error":null}}'));
      streamController?.close();
    });
    await screen.findByText("first second");
    expect(scrollWrites).toHaveLength(0);
  });

  it("queues a message sent while L3 is busy and shows it waiting", async () => {
    // The server takes the message instead of refusing it; the queue comes back with the chat view.
    let queued: unknown[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/chat") && init?.method === "POST") {
        const row = { id: "q1", at: new Date().toISOString(), trigger: "chat", role: "burak",
          text: "status?", position: 1 };
        queued = [row];
        return jsonResponse({ queued: row });
      }
      if (url.includes("/api/chat")) return jsonResponse({ ...chatView, busy: true, queued });
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route });
    await screen.findByText("how is it going?");

    const composer = screen.getByLabelText("Message L3");
    expect(composer).not.toBeDisabled();
    await user.type(composer, "status?");
    await user.click(await screen.findByRole("button", { name: "Queue" }));

    await screen.findByText("you · queued · 0m");
    expect(screen.getByText("status?")).toBeInTheDocument();
    expect(postsToChat(fetchMock)).toHaveLength(1);
    expect(JSON.parse(String(postsToChat(fetchMock)[0]?.[1]?.body))).toEqual({
      project: "altitude",
      text: "status?",
    });
    expect(composer).toHaveValue("");
  });

  it("uses the normal Queue path when voice Send is chosen while L3 is busy", async () => {
    installVoiceBrowser();
    let queued: unknown[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/transcribe")) return jsonResponse({ text: "spoken ending" });
      if (url.includes("/api/chat") && init?.method === "POST") {
        const text = (JSON.parse(String(init.body)) as { text: string }).text;
        const row = { id: "voice-q", at: new Date().toISOString(), trigger: "chat", role: "burak",
          text, position: 1 };
        queued = [row];
        return jsonResponse({ queued: row });
      }
      if (url.includes("/api/chat")) return jsonResponse({ ...chatView, busy: true, queued });
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route });
    await screen.findByText("how is it going?");
    await user.type(screen.getByLabelText("Message L3"), "Typed beginning");
    await user.click(screen.getByRole("button", { name: "Start voice recording" }));
    await user.click(await screen.findByRole("button", { name: "Stop voice recording" }));

    await screen.findByRole("region", { name: "Voice transcript review" });
    expect(postsToChat(fetchMock)).toHaveLength(0);
    expect(screen.getByLabelText("Message L3")).toHaveValue("Typed beginning");
    await user.click(screen.getByRole("button", { name: "Queue" }));

    await screen.findByText("you · queued · 0m");
    expect(postsToChat(fetchMock)).toHaveLength(1);
    expect(JSON.parse(String(postsToChat(fetchMock)[0]?.[1]?.body))).toEqual({
      project: "altitude",
      text: "Typed beginning spoken ending",
    });
  });

  it("keeps the original draft and transcript review when Chat transport fails", async () => {
    installVoiceBrowser();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/transcribe")) return jsonResponse({ text: "recoverable words" });
      if (url.includes("/api/chat") && init?.method === "POST") throw new Error("offline");
      if (url.includes("/api/chat")) return jsonResponse(chatView);
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route });
    await screen.findByText("how is it going?");
    await user.type(screen.getByLabelText("Message L3"), "Original draft");
    await user.click(screen.getByRole("button", { name: "Start voice recording" }));
    await user.click(await screen.findByRole("button", { name: "Stop voice recording" }));
    await screen.findByRole("region", { name: "Voice transcript review" });
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("transcript is still here");
    expect(screen.getByLabelText("Message L3")).toHaveValue("Original draft");
    expect(screen.getByRole("region", { name: "Voice transcript review" })).toHaveTextContent(
      "recoverable words",
    );
  });

  it("keeps long voice-review actions reachable at iPhone portrait and landscape sizes", async () => {
    installVoiceBrowser();
    const longTranscript = Array.from({ length: 500 }, (_, index) => `word${index}`).join(" ");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) return jsonResponse(overview);
        if (url.includes("/api/transcribe")) return jsonResponse({ text: longTranscript });
        if (url.includes("/api/chat")) return jsonResponse(chatView);
        return jsonResponse({ error: "not found" }, 404);
      }),
    );

    const { user } = renderApp({ route });
    await screen.findByText("how is it going?");
    await user.click(screen.getByRole("button", { name: "Start voice recording" }));
    await user.click(await screen.findByRole("button", { name: "Stop voice recording" }));
    await screen.findByRole("region", { name: "Voice transcript review" });
    const css = productionStyles();

    for (const [width, height] of [[390, 844], [844, 390]] as const) {
      const layout = mobileLayout(document.body.innerHTML, css, width, height);
      const actions = layout.nodes.find((node) => node.classes === "voice-review-actions");
      const text = layout.nodes.find((node) => node.classes === "voice-transcript");
      expect(actions, `${width}x${height} review actions`).toBeDefined();
      expect(actions!.bottom).toBeLessThanOrEqual(actions!.routeBottom + 0.5);
      expect(actions!.top).toBeGreaterThanOrEqual(actions!.routeTop - 0.5);
      expect(text!.scrollHeight).toBeGreaterThan(text!.clientHeight);
    }
  });

  it("keeps drafts per project but cancels voice review when a cached Chat destination changes", async () => {
    installVoiceBrowser();
    const twoProjects = {
      ...overview,
      projects: [
        { name: "altitude", managed: true },
        { name: "sibling", managed: true },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) return jsonResponse(twoProjects);
        if (url.includes("/api/transcribe")) return jsonResponse({ text: "only for altitude" });
        if (url.includes("/api/chat/sibling")) {
          return jsonResponse({ ...chatView, history: [{ role: "assistant", text: "Sibling chat" }] });
        }
        if (url.includes("/api/chat/altitude")) return jsonResponse(chatView);
        return jsonResponse({ error: "not found" }, 404);
      }),
    );

    const { user } = renderApp({ route });
    await screen.findByText("how is it going?");
    await user.type(screen.getByLabelText("Message L3"), "Altitude draft");
    await user.click(screen.getByRole("button", { name: "Start voice recording" }));
    await user.click(await screen.findByRole("button", { name: "Stop voice recording" }));
    await screen.findByRole("region", { name: "Voice transcript review" });

    await user.click(screen.getByRole("link", { name: "sibling" }));
    await screen.findByText("Sibling chat");
    expect(screen.queryByRole("region", { name: "Voice transcript review" })).toBeNull();
    expect(screen.getByLabelText("Message L3")).toHaveValue("");

    await user.click(screen.getByRole("link", { name: "altitude" }));
    await screen.findByText("how is it going?");
    expect(screen.queryByRole("region", { name: "Voice transcript review" })).toBeNull();
    expect(screen.getByLabelText("Message L3")).toHaveValue("Altitude draft");
  });

  it("numbers several queued messages and takes one back off the queue", async () => {
    const rows = [
      { id: "q1", at: new Date().toISOString(), trigger: "chat", role: "burak", text: "first" },
      { id: "q2", at: new Date().toISOString(), trigger: "chat", role: "burak", text: "second" },
    ];
    let queued = rows;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/chat/remove")) {
        const id = (JSON.parse(String(init?.body)) as { id: string }).id;
        queued = queued.filter((q) => q.id !== id);
        return jsonResponse({ ok: true, queued });
      }
      if (url.includes("/api/chat")) return jsonResponse({ ...chatView, busy: true, queued });
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp({ route });
    await screen.findByText("you · queued 1 · 0m");
    expect(screen.getByText("you · queued 2 · 0m")).toBeInTheDocument();
    expect(screen.getByText(/^busy · 2 queued ·/)).toBeInTheDocument();

    const first = screen.getByText("first").closest("article") as HTMLElement;
    fireEvent.click(within(first).getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(screen.queryByText("first")).toBeNull());
    expect(
      fetchMock.mock.calls
        .filter(([u]) => String(u).includes("/api/chat/remove"))
        .map(([, init]) => JSON.parse(String((init as RequestInit | undefined)?.body)) as unknown),
    ).toEqual([{ project: "altitude", id: "q1" }]);
    // the one left is no longer numbered: there is nothing to order it against
    await screen.findByText("you · queued · 0m");
  });

  it("shows server work in FIFO position without offering to remove it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) return jsonResponse(overview);
        if (url.includes("/api/chat")) {
          return jsonResponse({
            ...chatView,
            busy: true,
            queued: [
              { id: "s1", at: new Date().toISOString(), trigger: "incident", role: "server", text: "check fault" },
              { id: "q1", at: new Date().toISOString(), trigger: "chat", role: "burak", text: "then answer me" },
            ],
          });
        }
        return jsonResponse({ error: "not found" }, 404);
      }),
    );

    renderApp({ route });
    const serverWork = (await screen.findByText("check fault")).closest("article") as HTMLElement;
    expect(within(serverWork).getByText("server · queued 1 · incident · 0m")).toBeInTheDocument();
    expect(within(serverWork).queryByRole("button", { name: "Remove" })).toBeNull();
    expect(screen.getByText("you · queued 2 · 0m")).toBeInTheDocument();
  });

  // Matching is by turn identity, so repeating an earlier question keeps the new user bubble and reply.
  it("keeps the new turn when the same words are already in the history", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/chat") && init?.method === "POST") {
        return streamResponse(['{"t":"still two."}\n', '{"done":{"error":null}}']);
      }
      // The refetch after the stream returns the *same* transcript — the L3 write has not landed
      // yet — so the pending turn must survive it.
      if (url.includes("/api/chat")) return jsonResponse(chatView);
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route });
    await screen.findByText("how is it going?");

    // exactly the text of the first message already in the transcript
    await user.type(screen.getByLabelText("Message L3"), "how is it going?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("still two.");
    await waitFor(() => {
      expect(postsToChat(fetchMock)).toHaveLength(1);
    });
    // the history copy and the new optimistic bubble, both on screen
    expect(screen.getAllByText("how is it going?")).toHaveLength(2);
    expect(screen.getByText("still two.")).toBeInTheDocument();
  });

  it("offers a link to every managed project's chat and marks the current one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) {
          return jsonResponse({
            ...overview,
            projects: [
              { name: "altitude", managed: true },
              { name: "sibling", managed: true },
              { name: "unmanaged", managed: false },
            ],
          });
        }
        if (url.includes("/api/chat")) return jsonResponse(chatView);
        return jsonResponse({ error: "not found" }, 404);
      }),
    );
    renderApp({ route });

    await screen.findByText("how is it going?");
    const strip = await screen.findByRole("navigation", { name: "Projects" });

    expect(within(strip).getByRole("link", { name: "sibling" })).toHaveAttribute(
      "href",
      "/chat/sibling",
    );
    expect(within(strip).getByRole("link", { name: "altitude" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(strip).getByRole("link", { name: "sibling" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(within(strip).queryByRole("link", { name: "unmanaged" })).toBeNull();
  });
});
