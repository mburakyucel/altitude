import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TerminalScreen, { inputPiece } from "./TerminalScreen";
import { terminalSend, terminalStatus, terminalStream, type TerminalStatus } from "../data/api";

// A stand-in xterm without bracketed paste: a paste reaches onData as the raw text, as a keystroke would.
const term: { data?: (data: string) => void } = {};
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 80;
    rows = 24;
    loadAddon() {}
    open() {}
    write() {}
    focus() {}
    dispose() {}
    hasSelection() { return false; }
    attachCustomKeyEventHandler() {}
    buffer = { active: { viewportY: 0, baseY: 0 } };
    onData(listener: (data: string) => void) { term.data = listener; return { dispose() {} }; }
    paste(text: string) { term.data?.(text); }
  },
}));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));
vi.mock("../data/Toast", () => ({ useToast: () => ({ show: vi.fn() }) }));
vi.mock("../data/api", async (original) => ({
  ...await original<typeof import("../data/api")>(),
  terminalStatus: vi.fn(),
  terminalStream: vi.fn(),
  terminalSend: vi.fn(async () => ({ ok: true })),
}));

const running: TerminalStatus = { state: "running", id: "t1", enabled: true, folder: "/home/fixture/demo", offset: 0, exit_code: null, reason: null, busy: null };
let emit: () => void;

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.mocked(terminalStatus).mockResolvedValue(running);
  vi.mocked(terminalStream).mockImplementation(() => {
    const listeners: Record<string, (event: MessageEvent<string>) => void> = {};
    emit = () => listeners.output?.(new MessageEvent("output", { data: JSON.stringify({ offset: 1, data: btoa("$ ") }) }));
    return { addEventListener: (name: string, listener: never) => { listeners[name] = listener; }, close() {} } as unknown as EventSource;
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

const typed = () => vi.mocked(terminalSend).mock.calls.filter(([, action]) => action === "input").map(([, , body]) => body.data);

function show(text: string, keys = false) {
  const onCommand = vi.fn();
  const props = { project: "demo", id: "t1", keys, intro: "Runs as you", reconnecting: false, onEnd: vi.fn(), onReconnecting: vi.fn(), onCommand };
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
  it("is typed without Enter once the prompt has stayed quiet, never before", async () => {
    const { request, onCommand } = show("echo hi");
    request();
    act(() => emit());
    await act(() => vi.advanceTimersByTimeAsync(200));
    expect(typed()).toEqual([]);
    await act(() => vi.advanceTimersByTimeAsync(200));
    expect(typed()).toEqual(["echo hi"]);
    expect(onCommand).toHaveBeenCalledTimes(1);
  });

  it("is never turned into a control character by a latched Ctrl", async () => {
    const { request } = show("m", true);
    act(() => emit());
    fireEvent.click(screen.getByRole("button", { name: "Control" }));
    expect(screen.getByRole("button", { name: "Control" })).toHaveAttribute("aria-pressed", "true");
    request();
    await act(() => vi.advanceTimersByTimeAsync(400));
    expect(typed()).toEqual(["m"]);
    expect(screen.getByRole("button", { name: "Control" })).toHaveAttribute("aria-pressed", "false");
  });

  it("never turns a browser paste into a control character after Ctrl is latched", () => {
    const { container } = render(<TerminalScreen project="demo" id="t1" keys intro="Runs as you" reconnecting={false} onEnd={vi.fn()} onReconnecting={vi.fn()} />);
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
    expect(typed()).toEqual([]);
  });

  it("is refused while output keeps coming, and output settling afterwards types nothing", async () => {
    show("echo hi").request();
    for (let at = 0; at < 5_200; at += 100) {
      act(() => emit());
      await act(() => vi.advanceTimersByTimeAsync(100));
    }
    expect(screen.getByRole("status")).toHaveTextContent("The terminal kept printing, so the command wasn't typed.");
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    expect(typed()).toEqual([]);
  });

  it("is refused when the check answers after five seconds, and the late answer types nothing", async () => {
    let answer!: (status: TerminalStatus) => void;
    vi.mocked(terminalStatus).mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
    show("echo hi").request();
    act(() => emit());
    await act(() => vi.advanceTimersByTimeAsync(5_000));
    expect(screen.getByRole("status")).toHaveTextContent("Altitude couldn't check the terminal in time, so the command wasn't typed.");
    await act(async () => answer(running));
    expect(typed()).toEqual([]);
  });

  it("is refused when the check answers after the deadline but before a throttled timer fires", async () => {
    let answer!: (status: TerminalStatus) => void;
    vi.mocked(terminalStatus).mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
    show("echo hi").request();
    act(() => emit());
    await act(() => vi.advanceTimersByTimeAsync(400));
    vi.setSystemTime(Date.now() + 5_000);
    await act(async () => answer(running));
    expect(screen.getByRole("status")).toHaveTextContent("Altitude couldn't check the terminal in time, so the command wasn't typed.");
    expect(typed()).toEqual([]);
  });

  it("is refused while a program holds the foreground", async () => {
    vi.mocked(terminalStatus).mockResolvedValueOnce({ ...running, busy: "vim" });
    show("echo hi").request();
    act(() => emit());
    await act(() => vi.advanceTimersByTimeAsync(400));
    expect(screen.getByRole("status")).toHaveTextContent("vim is running, so the command wasn't typed.");
    expect(screen.getByRole("button", { name: "Copy command" })).toBeVisible();
    expect(typed()).toEqual([]);
  });

  it("names the command for the project terminal so the coordinator hears, and says when it won't", async () => {
    vi.mocked(terminalSend).mockImplementation(async (_project, action) => {
      if (action === "command") throw new Error("refused");
      return { ok: true };
    });
    show("gh api repos/fixture/demo").request();
    act(() => emit());
    await act(() => vi.advanceTimersByTimeAsync(400));
    expect(vi.mocked(terminalSend)).toHaveBeenCalledWith("demo", "command", { task: undefined, id: "t1", text: "gh api repos/fixture/demo" });
    expect(screen.getByRole("status")).toHaveTextContent("Altitude couldn't tell the coordinator to watch this command, so reply in chat once it has run.");
    expect(typed()).toEqual(["gh api repos/fixture/demo"]);
  });
});
