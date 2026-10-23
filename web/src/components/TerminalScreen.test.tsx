import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TerminalScreen, { inputPiece } from "./TerminalScreen";
import { ApiError, terminalSend, terminalStatus, terminalStream, type TerminalStatus } from "../data/api";

// A stand-in xterm, without bracketed paste unless a test turns it on: a paste reaches onData as the raw text, as a
// keystroke would. Its screen is a focusable box, and the fit gives it `size`. It parses what is written at once, or
// once a test calls `parse` while `later` holds the writes, answering a background colour query and a cursor position
// report through its own onData as it parses them, before the write's callback runs, as xterm does. `data` types at the
// screen opened last.
const term: { data?: (data: string) => void; bracketed: boolean; written: string[]; resets: number; size: { cols: number; rows: number }; later: (() => void)[] | null } =
  { bracketed: false, written: [], resets: 0, size: { cols: 80, rows: 24 }, later: null };
const QUERIES = /\x1b\]11;\?(?:\x07|\x1b\\)|\x1b\[6n/g;
const answer = (query: string) => query.startsWith("\x1b]") ? "\x1b]11;rgb:1111/1818/2727\x1b\\" : "\x1b[1;1R";
const parse = () => { for (const write of term.later!.splice(0)) write(); };
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 80;
    rows = 24;
    get modes() { return { bracketedPasteMode: term.bracketed }; }
    textarea?: HTMLTextAreaElement;
    listener?: (data: string) => void;
    loadAddon(addon: { activate(terminal: unknown): void }) { addon.activate(this); }
    open(parent: HTMLElement) { this.textarea = parent.appendChild(document.createElement("textarea")); }
    write(data: string | Uint8Array, callback?: () => void) {
      const text = typeof data === "string" ? data : new TextDecoder().decode(data);
      term.written.push(text);
      const parsed = () => {
        for (const [query] of text.matchAll(QUERIES)) this.listener?.(answer(query));
        callback?.();
      };
      if (term.later) term.later.push(parsed);
      else parsed();
    }
    reset() { term.resets += 1; }
    focus() { this.textarea?.focus(); }
    resize(cols: number, rows: number) { this.cols = cols; this.rows = rows; }
    dispose() {}
    hasSelection() { return false; }
    attachCustomKeyEventHandler() {}
    buffer = { active: { viewportY: 0, baseY: 0 } };
    scrollToBottom() {}
    onData(listener: (data: string) => void) { this.listener = term.data = listener; return { dispose() {} }; }
    paste(text: string) { term.data?.(text); }
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    terminal?: { resize(cols: number, rows: number): void };
    activate(terminal: { resize(cols: number, rows: number): void }) { this.terminal = terminal; }
    fit() { this.terminal?.resize(term.size.cols, term.size.rows); }
  },
}));
vi.mock("../data/Toast", () => ({ useToast: () => ({ show: vi.fn() }) }));
vi.mock("../data/api", async (original) => ({
  ...await original<typeof import("../data/api")>(),
  terminalStatus: vi.fn(),
  terminalStream: vi.fn(),
  terminalSend: vi.fn(async () => ({ ok: true })),
}));

const running: TerminalStatus = { state: "running", id: "t1", enabled: true, folder: "/home/fixture/demo", offset: 0, exit_code: null, reason: null, busy: null };
const font = { family: "monospace", size: 13 };
type Stream = { offset: number; closed: boolean; listeners: Record<string, (event: MessageEvent<string>) => void>; addEventListener(name: string, listener: (event: MessageEvent<string>) => void): void; close(): void };
let streams: Stream[];
let observed: () => void;
const output = (offset: number, data: string, missed = false, replay = 0, stream = streams.at(-1)!) => stream.listeners.output?.(new MessageEvent("output", { data: JSON.stringify({ offset, data: btoa(data), missed, replay }) }));
const emit = () => output(1, "$ ");

beforeEach(() => {
  vi.useFakeTimers();
  streams = [];
  Object.assign(term, { written: [], resets: 0, size: { cols: 80, rows: 24 }, later: null });
  vi.stubGlobal("ResizeObserver", class { constructor(callback: () => void) { observed = callback; } observe() {} disconnect() {} });
  vi.mocked(terminalStatus).mockResolvedValue(running);
  vi.mocked(terminalSend).mockImplementation(async () => ({ ok: true }));
  vi.mocked(terminalStream).mockImplementation((_project, _task, _id, offset) => {
    const stream: Stream = { offset, closed: false, listeners: {}, addEventListener(name, listener) { this.listeners[name] = listener; }, close() { this.closed = true; } };
    streams.push(stream);
    return stream as unknown as EventSource;
  });
});
afterEach(() => {
  term.bracketed = false;
  vi.useRealTimers();
  vi.clearAllMocks();
});

const typed = () => vi.mocked(terminalSend).mock.calls.filter(([, action]) => action === "input").map(([, , body]) => body.data);
const tagged = () => vi.mocked(terminalSend).mock.calls.filter(([, action]) => action === "input").map(([, , body]) => body.answer);
const handed = () => vi.mocked(terminalSend).mock.calls.filter(([, action]) => action === "command").map(([, , body]) => body);
// Altitude refuses the chat command; input and resizes still succeed.
const refuseCommand = (error: unknown) => vi.mocked(terminalSend).mockImplementation(async (_project, action) => {
  if (action === "command") throw error;
  return { ok: true };
});
const sizes = () => vi.mocked(terminalSend).mock.calls.filter(([, action]) => action === "resize").map(([, , body]) => `${body.cols}x${body.rows}`);

function show(text: string, keys = false) {
  const onCommand = vi.fn();
  const props = { project: "demo", id: "t1", keys, font, intro: "Runs as you", reconnecting: false, onEnd: vi.fn(), onReconnecting: vi.fn(), onCommand };
  const view = render(<TerminalScreen {...props} />);
  const request = () => view.rerender(<TerminalScreen {...props} command={{ text, at: Date.now(), seq: 1 }} />);
  return { request, onCommand };
}

describe("terminal input pieces", () => {
  it("sends a long paste in 16 KiB pieces without splitting a character", () => {
    expect(inputPiece("ls\r")).toBe("ls\r");
    expect(inputPiece("a".repeat(20_000))).toHaveLength(16_384);
    const across = `${"a".repeat(16_383)}😀b`;
    const first = inputPiece(across);
    expect(first).toBe("a".repeat(16_383));
    expect(inputPiece(across.slice(first.length))).toBe("😀b");
  });
});

describe("a chat command at the terminal", () => {
  it("is handed to Altitude to type, without Enter, once the prompt has stayed quiet, never before", async () => {
    const { request, onCommand } = show("echo hi");
    request();
    act(() => emit());
    await act(() => vi.advanceTimersByTimeAsync(200));
    expect(handed()).toEqual([]);
    await act(() => vi.advanceTimersByTimeAsync(200));
    expect(handed()).toEqual([{ task: undefined, id: "t1", text: "echo hi", bracketed: false }]);
    expect(typed()).toEqual([]);
    expect(onCommand).toHaveBeenCalledTimes(1);
  });

  it("is typed as a paste when the shell asked for bracketed paste", async () => {
    term.bracketed = true;
    show("echo hi").request();
    act(() => emit());
    await act(() => vi.advanceTimersByTimeAsync(400));
    expect(handed()).toEqual([{ task: undefined, id: "t1", text: "echo hi", bracketed: true }]);
  });

  it("is never turned into a control character by a latched Ctrl, which stays for the next key", async () => {
    const { request } = show("m", true);
    act(() => emit());
    fireEvent.click(screen.getByRole("button", { name: "Control" }));
    request();
    await act(() => vi.advanceTimersByTimeAsync(400));
    expect(handed()).toEqual([expect.objectContaining({ text: "m" })]);
    expect(typed()).toEqual([]);
    expect(screen.getByRole("button", { name: "Control" })).toHaveAttribute("aria-pressed", "true");
  });

  it("never turns a browser paste into a control character after Ctrl is latched", () => {
    const { container } = render(<TerminalScreen project="demo" id="t1" keys font={font} intro="Runs as you" reconnecting={false} onEnd={vi.fn()} onReconnecting={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Control" }));
    fireEvent.paste(container.querySelector(".terminal-screen")!);
    act(() => term.data?.("m"));
    expect(typed()).toEqual(["m"]);
    expect(screen.getByRole("button", { name: "Control" })).toHaveAttribute("aria-pressed", "false");
  });

  it("is refused when the shell shows nothing for five seconds", async () => {
    show("echo hi").request();
    await act(() => vi.advanceTimersByTimeAsync(5_000));
    expect(screen.getByRole("status")).toHaveTextContent("The terminal hasn't shown a prompt, so the command wasn't typed.");
    act(() => emit());
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    expect(handed()).toEqual([]);
  });

  it("is refused while output keeps coming, and output settling afterwards types nothing", async () => {
    show("echo hi").request();
    for (let at = 0; at < 5_200; at += 100) {
      act(() => emit());
      await act(() => vi.advanceTimersByTimeAsync(100));
    }
    expect(screen.getByRole("status")).toHaveTextContent("The terminal kept printing, so the command wasn't typed.");
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    expect(handed()).toEqual([]);
  });

  it("is refused when a throttled timer reaches the settled prompt after the deadline", async () => {
    show("echo hi").request();
    act(() => emit());
    vi.setSystemTime(Date.now() + 5_000);
    await act(() => vi.advanceTimersByTimeAsync(400));
    expect(screen.getByRole("status")).toHaveTextContent("Too much time passed since the tap, so the command wasn't typed.");
    expect(handed()).toEqual([]);
  });

  it.each([
    "vim is running, so the command wasn't typed.",
    "This command is already typed at the prompt. Press Enter to run it, or Ctrl+C to clear the line.",
  ])("says why Altitude didn't type it, with Copy: %s", async (reason) => {
    refuseCommand(new ApiError(409, reason));
    const { request, onCommand } = show("echo hi");
    request();
    act(() => emit());
    await act(() => vi.advanceTimersByTimeAsync(400));
    expect(screen.getByRole("status")).toHaveTextContent(reason);
    expect(screen.getByRole("button", { name: "Copy command" })).toBeVisible();
    expect(onCommand).toHaveBeenCalledTimes(1);
    expect(typed()).toEqual([]);
  });

  it.each([new TypeError("Failed to fetch"), new ApiError(503, "Service unavailable")])(
    "asks the operator to check the screen when Altitude couldn't type it: %s", async (error) => {
      refuseCommand(error);
      show("gh api repos/fixture/demo").request();
      act(() => emit());
      await act(() => vi.advanceTimersByTimeAsync(400));
      expect(screen.getByRole("status")).toHaveTextContent("Altitude couldn't type the command. Check the screen before typing it yourself.");
    });

  it("drops the command quietly for a terminal that was replaced, whose end the stream reports", async () => {
    refuseCommand(new ApiError(410, "This terminal was replaced."));
    const { request, onCommand } = show("echo hi");
    request();
    act(() => emit());
    await act(() => vi.advanceTimersByTimeAsync(400));
    expect(onCommand).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("the terminal screen", () => {
  const open = (keys = false) => {
    const onEnd = vi.fn();
    const onReconnecting = vi.fn();
    const view = render(<TerminalScreen project="demo" id="t1" keys={keys} font={font} intro="Runs as you" reconnecting={false} onEnd={onEnd} onReconnecting={onReconnecting} />);
    const textarea = view.container.querySelector("textarea")!;
    return { onEnd, onReconnecting, textarea };
  };
  const key = (name: string) => {
    const button = screen.getByRole("button", { name });
    fireEvent.pointerDown(button);
    fireEvent.click(button);
  };
  const show = async () => {
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });
  };

  it("gives the shell a settled size once, and again only when its columns or rows change", async () => {
    open();
    await act(() => vi.advanceTimersByTimeAsync(100));
    expect(sizes()).toEqual(["80x24"]);
    act(() => observed());
    await act(() => vi.advanceTimersByTimeAsync(100));
    expect(sizes()).toEqual(["80x24"]);
    // The soft keyboard opening resizes the screen in steps: the shell hears the size it settles on.
    term.size = { cols: 80, rows: 18 };
    act(() => observed());
    await act(() => vi.advanceTimersByTimeAsync(50));
    term.size = { cols: 80, rows: 13 };
    act(() => observed());
    await act(() => vi.advanceTimersByTimeAsync(100));
    expect(sizes()).toEqual(["80x24", "80x13"]);
  });

  it("resumes from its offset when the page is shown again, with the shell's size", async () => {
    open();
    await act(() => vi.advanceTimersByTimeAsync(100));
    act(() => output(120, "$ "));
    await show();
    expect(streams.map((stream) => [stream.offset, stream.closed])).toEqual([[0, true], [120, false]]);
    await act(() => vi.advanceTimersByTimeAsync(100));
    expect(sizes()).toEqual(["80x24", "80x24"]);
  });

  it("leaves when its shell ended while the page was hidden, and shows Reconnecting while Altitude is out of reach", async () => {
    const { onEnd, onReconnecting } = open();
    vi.mocked(terminalStatus).mockRejectedValueOnce(new Error("unreachable"));
    await show();
    expect(onReconnecting).toHaveBeenLastCalledWith(true);
    const ended: TerminalStatus = { state: "none", enabled: true };
    vi.mocked(terminalStatus).mockResolvedValue(ended);
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    expect(onEnd).toHaveBeenCalledWith(ended);
  });

  it("starts the screen over when output from before the replay buffer is gone", () => {
    open();
    act(() => output(10, "$ ls"));
    expect(term.resets).toBe(0);
    act(() => output(400_000, "later", true));
    expect(term.resets).toBe(1);
    expect(term.written.slice(-2)).toEqual(["\x1b[2mRuns as you\x1b[0m\r\n", "later"]);
  });

  it("drops what waits to be typed once its terminal has ended", async () => {
    let refuse!: (error: Error) => void;
    vi.mocked(terminalSend).mockImplementation(async (_project, action) => {
      if (action === "input") await new Promise((_resolve, reject) => { refuse = reject; });
      return { ok: true };
    });
    open();
    act(() => term.data?.("a"));
    act(() => term.data?.("b"));
    await act(async () => refuse(new ApiError(404, "No terminal is running here.")));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(typed()).toEqual(["a"]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  // A program's queries in replayed output were answered as typed input: `gh` asks the background colour and the cursor's
  // place, and every opening of the terminal typed the answers at the shell's prompt.
  const queries = "\x1b]11;?\x1b\\\x1b[6n";
  const answers = "\x1b]11;rgb:1111/1818/2727\x1b\\\x1b[1;1R";

  it("answers no query in output it replays as it opens or starts over, and answers a live one", () => {
    open();
    const history = `$ gh repo edit\r\n${queries}Edited\r\n$ `;
    act(() => output(history.length, history, false, history.length));
    act(() => output(400_000, history, true, history.length));
    expect(term.written.at(-1)).toBe(history);
    expect(typed()).toEqual([]);
    act(() => output(400_100, queries));
    expect(typed()).toEqual([answers]);
  });

  it("answers the queries replayed output ends with, which their program may still wait on", () => {
    open();
    const history = `${queries}$ read -p query\r\n`;
    act(() => output(history.length + queries.length, history + queries, false, history.length));
    expect(typed()).toEqual([answers]);
  });

  it("never holds back or drops the operator's keys while output waits to be drawn", async () => {
    term.later = [];
    open(true);
    const history = `${queries}$ `;
    act(() => output(history.length + queries.length, history + queries, false, history.length));
    // A latched Ctrl belongs to the operator's next key, not to an answer.
    key("Control");
    await act(async () => term.data?.("c"));
    expect(typed()).toEqual(["\x03"]);
    act(() => parse());
    await act(async () => term.data?.("x"));
    expect(typed()).toEqual(["\x03", answers, "x"]);
  });

  // Two pages showing one terminal both answered a live query: the program read one answer and the other reached the
  // shell's prompt as text. Each answer names its page and the piece of output it answers, and altd types the first only.
  it("names this page and the piece of live output each answer is to, and never names the operator's typing as an answer", async () => {
    open();
    const phone = { stream: streams[0]!, type: term.data! };
    open();
    const desktop = streams[1]!;
    for (const stream of [phone.stream, desktop]) act(() => output(2, "$ ", false, 0, stream));
    for (const stream of [phone.stream, desktop]) act(() => output(2 + queries.length, queries, false, 0, stream));
    expect(typed()).toEqual([answers, answers]);
    const [first, second] = tagged() as { page: string; end: number }[];
    expect([first!.end, second!.end]).toEqual([2 + queries.length, 2 + queries.length]);
    expect(first!.page).not.toBe(second!.page);
    // A page catching up answers the live piece its replay ends with.
    await act(async () => output(400_000, `$ ${queries}`, true, 2, desktop));
    expect(tagged().at(-1)).toEqual({ page: second!.page, end: 400_000 });
    await act(async () => phone.type("x"));
    expect(typed().at(-1)).toBe("x");
    expect(tagged().at(-1)).toBeUndefined();
  });

  it("sends typing and answers in order and never joins the operator's keys to an answer", async () => {
    let release!: () => void;
    vi.mocked(terminalSend).mockImplementationOnce(async () => {
      await new Promise<void>((resolve) => { release = resolve; });
      return { ok: true };
    });
    open();
    await act(async () => term.data?.("a"));
    await act(async () => term.data?.("b"));
    act(() => output(queries.length, queries));
    await act(async () => term.data?.("c"));
    await act(async () => term.data?.("d"));
    await act(async () => release());
    expect(typed()).toEqual(["a", "b", answers, "cd"]);
    expect(tagged().map(Boolean)).toEqual([false, false, true, false]);
  });

  it("takes focus as it opens on desktop", () => {
    const { textarea } = open();
    expect(document.activeElement).toBe(textarea);
  });

  it("on phone leaves the soft keyboard closed until the operator taps, and the key row leaves it as it was", () => {
    const { textarea } = open(true);
    expect(document.activeElement).not.toBe(textarea);
    for (const name of ["Up", "Tab", "Paste", "Enter"]) {
      key(name);
      expect(document.activeElement, name).not.toBe(textarea);
    }
    act(() => textarea.focus());
    for (const name of ["Up", "Escape", "Enter"]) {
      key(name);
      expect(document.activeElement, name).toBe(textarea);
    }
    // Ctrl opens the keyboard for the letter it modifies; releasing it leaves the keyboard as it was.
    act(() => textarea.blur());
    key("Control");
    expect(document.activeElement).toBe(textarea);
    act(() => textarea.blur());
    key("Control");
    expect(document.activeElement).not.toBe(textarea);
  });

  it("runs one status check at a time, however often the page is shown while it waits", async () => {
    let answer!: (status: TerminalStatus) => void;
    vi.mocked(terminalStatus).mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
    open();
    await show();
    await show();
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    expect(vi.mocked(terminalStatus)).toHaveBeenCalledTimes(1);
    await act(async () => answer(running));
    expect(streams.map((stream) => stream.closed)).toEqual([true, false]);
  });
});
