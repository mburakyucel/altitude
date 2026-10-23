import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { StatusMark } from "./StatusMark";
import { ApiError, terminalSend, terminalStatus, terminalStream } from "../data/api";
import type { TerminalStatus } from "../data/api";
import { useToast } from "../data/Toast";
import { CopyButton } from "./CodeBlock";

const RECONNECT_MS = 2_000;
const RESIZE_MS = 100;
/** The most one input request carries (altd refuses more than 64 KiB): a long paste goes in pieces, in order. */
const INPUT_CHUNK = 16_384;
/** A released swipe keeps scrolling, slowing by this share each frame, like the page's own scrolling. */
const FLING_DECAY = 0.95;
/** A chat command waits until the screen has drawn output and then stayed quiet this long: the prompt. */
const SETTLE_MS = 300;
/** A chat command not typed this long after its tap (no prompt yet, output that never settles, a slow check) is refused. */
const PROMPT_WAIT_MS = 5_000;

const KEYS: { label: string; name: string; data: string }[] = [
  { label: "Esc", name: "Escape", data: "\x1b" },
  { label: "Tab", name: "Tab", data: "\t" },
  { label: "↑", name: "Up", data: "\x1b[A" },
  { label: "↓", name: "Down", data: "\x1b[B" },
  { label: "←", name: "Left", data: "\x1b[D" },
  { label: "→", name: "Right", data: "\x1b[C" },
];

/** A letter typed after the sticky Ctrl key becomes its control character (Ctrl+C is 0x03). */
function control(data: string): string {
  const code = data.toUpperCase().charCodeAt(0);
  return data.length === 1 && code >= 64 && code <= 95 ? String.fromCharCode(code - 64) : data;
}

function bytes(base64: string): Uint8Array {
  return Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
}

/** The next input request's text: at most `INPUT_CHUNK` code units, never splitting a surrogate pair. */
export function inputPiece(text: string): string {
  const cut = /[\uD800-\uDBFF]/.test(text.charAt(INPUT_CHUNK - 1)) ? INPUT_CHUNK - 1 : INPUT_CHUNK;
  return text.slice(0, cut);
}

/**
 * One running terminal on screen. It writes `intro` dimmed, replays the server's buffer from the start without
 * answering the queries in it, follows the output stream, and after a lost connection resumes from the last offset it drew; the
 * shell's end, a replaced terminal or an altd restart ends it through `onEnd`. Typed input is sent in
 * order, one request at a time over a kept connection, and every request names terminal `id`, so nothing
 * reaches a terminal that replaced it. Input that fails may have arrived in part, so typing stops, with an
 * alert, until the operator has checked the screen.
 *
 * A chat `command` (SPEC.md §3.3) is handed to Altitude once the screen has settled on the shell's prompt. Altitude
 * types it, as a paste when the shell asked for one and without Enter, and follows it, so the terminal's reader (the
 * task's owner, or the coordinator) hears once the operator has run it. When Altitude does not type it (a program holds
 * the foreground, an earlier command waits for Enter on the line) or no prompt appears, a notice offers Copy instead.
 */
export default function TerminalScreen({ project, task, id, keys, font, intro, reconnecting, command, onEnd, onReconnecting, onCommand }: {
  project: string;
  task?: string;
  id: string;
  keys: boolean;
  /** Already loaded: the screen measures its cells in it once, as it opens. */
  font: { family: string; size: number };
  intro: string;
  reconnecting: boolean;
  command?: { text: string; at: number; seq: number } | null;
  onEnd: (status: TerminalStatus) => void;
  onReconnecting: (lost: boolean) => void;
  /** The command was typed or refused: the view no longer holds it. */
  onCommand?: () => void;
}) {
  const toast = useToast();
  const host = useRef<HTMLDivElement>(null);
  const send = useRef<(data: string) => void>(() => undefined);
  const focus = useRef<() => void>(() => undefined);
  // On phone only the operator's tap on the screen, or Ctrl, opens the soft keyboard. Focus given without a tap opens
  // none on iOS and could leave the screen focused with the keyboard closed, where a tap may not open it.
  const phone = useRef(keys);
  phone.current = keys;
  const settle = () => { if (!phone.current) focus.current(); };
  // Whether the screen had focus as a key row key was pressed: the key leaves the soft keyboard as it found it.
  const typing = useRef<boolean | null>(null);
  const pasteText = useRef<(text: string) => void>(() => undefined);
  const xterm = useRef<XTerm | null>(null);
  const ctrlRef = useRef(false);
  const [ctrl, setCtrl] = useState(false);
  const stopped = useRef(false);
  const [inputError, setInputError] = useState<string | null>(null);
  const report = useRef({ onEnd, onReconnecting, onCommand });
  report.current = { onEnd, onReconnecting, onCommand };
  const output = useRef({ seen: false, at: 0 });
  const [held, setHeld] = useState<{ text: string; reason: string } | null>(null);

  useEffect(() => {
    const term = new XTerm({
      cursorBlink: true,
      fontFamily: font.family,
      fontSize: font.size,
      scrollback: 5000,
      theme: { background: "#111827", foreground: "#e5e7eb", cursor: "#e5e7eb" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host.current!);
    // xterm answers a query in the output it parses (a colour, the cursor's place, what terminal it is) through onData,
    // as it does keys. Each write's callback runs right after its output is parsed, in the same task, so what xterm
    // emits while output waits to be parsed is held: the callback takes the answers to that output and sends them only
    // when it is live, never for output only replayed, whose programs have moved on. The operator's keys, pastes,
    // mouse and focus arrive in tasks of their own, so what is held outside a parse goes on as that task ends.
    let parsing = 0;
    let held = "";
    const draw = (data: string | Uint8Array, live = true) => {
      parsing += 1;
      term.write(data, () => {
        parsing -= 1;
        const answers = held;
        held = "";
        if (live && answers) deliver(answers);
      });
    };
    const release = () => {
      const data = held;
      held = "";
      if (data) send.current(data);
    };
    const introduce = () => draw(`\x1b[2m${intro}\x1b[0m\r\n`);
    introduce();
    // The browser keeps Ctrl+V (paste) and, with text selected, Ctrl+C (copy); without a selection Ctrl+C interrupts.
    term.attachCustomKeyEventHandler((event) => {
      if (event.type !== "keydown" || !event.ctrlKey || event.altKey || event.metaKey) return true;
      const key = event.key.toLowerCase();
      return !(key === "v" || (key === "c" && term.hasSelection()));
    });
    let offset = 0;
    let source: EventSource | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let resize: ReturnType<typeof setTimeout> | undefined;
    let sized = "";
    let done = false;

    const finish = (status: TerminalStatus) => {
      done = true;
      source?.close();
      report.current.onReconnecting(false);
      report.current.onEnd(status);
    };
    const connect = () => {
      source?.close();
      const stream = terminalStream(project, task, id, offset);
      stream.addEventListener("output", (event) => {
        const chunk = JSON.parse((event as MessageEvent<string>).data) as { offset: number; data: string; missed?: boolean; replay?: number };
        // Output from before the replay buffer's start is gone: the screen starts over, as a fresh attach draws it.
        if (chunk.missed) {
          term.reset();
          introduce();
        }
        // Output already there as the stream opened is replayed, apart from its trailing sequences (altd's `replayed`).
        const data = bytes(chunk.data);
        const replay = chunk.replay ?? 0;
        if (replay) draw(data.subarray(0, replay), false);
        if (replay < data.length) draw(data.subarray(replay));
        offset = chunk.offset;
        output.current.seen = true;
        output.current.at = Date.now();
      });
      stream.addEventListener("end", (event) => finish(JSON.parse((event as MessageEvent<string>).data) as TerminalStatus));
      stream.onopen = () => report.current.onReconnecting(false);
      stream.onerror = () => {
        stream.close();
        if (done) return;
        report.current.onReconnecting(true);
        retry = setTimeout(check, RECONNECT_MS);
      };
      source = stream;
      // Another page may have sized the shell meanwhile: it takes this screen's size again.
      sized = "";
      size();
    };
    // After a lost connection: the same terminal resumes from its offset; anything else has ended it. One check runs
    // at a time; a retry or a page shown again while one waits leaves the answer to it.
    let checking = false;
    const check = async () => {
      if (checking) return;
      checking = true;
      try {
        const status = await terminalStatus(project, task);
        if (done) return;
        if (status.id !== id || status.state === "none") finish(status);
        else connect();
      } catch {
        if (done) return;
        report.current.onReconnecting(true);
        clearTimeout(retry);
        retry = setTimeout(check, RECONNECT_MS);
      } finally {
        checking = false;
      }
    };
    // iOS suspends a page in the background and can drop its connection without a word to the stream, which would
    // then wait on nothing: a page shown again, also from the back-forward cache, resumes from its offset.
    const resume = (event: Event) => {
      if (done || document.visibilityState !== "visible" || (event instanceof PageTransitionEvent && !event.persisted)) return;
      clearTimeout(retry);
      void check();
    };
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("pageshow", resume);

    let pending = "";
    let sending = false;
    const flush = async () => {
      if (done || sending || !pending) return;
      sending = true;
      const data = inputPiece(pending);
      pending = pending.slice(data.length);
      try {
        await terminalSend(project, "input", { task, id, data });
      } catch (error) {
        // An ended (404) or replaced (410) terminal reports its end on the stream; the rest has no shell to reach.
        pending = "";
        if (!done && !(error instanceof ApiError && (error.status === 404 || error.status === 410))) {
          stopped.current = true;
          setInputError(error instanceof ApiError ? error.message : "Altitude could not be reached.");
        }
      }
      sending = false;
      void flush();
    };
    function deliver(data: string) {
      if (stopped.current) return;
      pending += data;
      void flush();
    }
    send.current = (data: string) => {
      if (stopped.current) return;
      if (ctrlRef.current) {
        data = control(data);
        ctrlRef.current = false;
        setCtrl(false);
      }
      // Input returns the screen to the prompt: xterm does so for typing, not for the key row. Only when scrolled
      // back: at the bottom, xterm's viewport can still be a frame behind new output and would scroll back to it.
      const { viewportY, baseY } = term.buffer.active;
      if (viewportY !== baseY) term.scrollToBottom();
      deliver(data);
    };
    focus.current = () => term.focus();
    // xterm frames a paste as the shell asked (bracketed paste), so pasted lines wait for Enter.
    // A latched Ctrl belongs to the next keystroke, never to pasted text, whether pasted here or by the browser.
    const unlatch = () => {
      ctrlRef.current = false;
      setCtrl(false);
    };
    pasteText.current = (text) => {
      unlatch();
      term.paste(text);
    };
    xterm.current = term;
    host.current!.addEventListener("paste", unlatch, true);
    const input = term.onData((data) => {
      if (!parsing && !held) return send.current(data);
      held += data;
      queueMicrotask(release);
    });

    // xterm's viewport scrolls only by wheel, so a finger drag over the screen scrolls the scrollback here, as
    // content scrolls anywhere else, and a released swipe flings on. It reaches only the display: no key or
    // focus, so the soft keyboard stays as it is. Scrolled back, new output keeps the view; at the bottom it follows.
    let drag: { y: number; at: number; speed: number } | null = null;
    let rest = 0;
    let fling = 0;
    const scroll = (pixels: number): boolean => {
      const cell = term.element!.querySelector(".xterm-screen")!.clientHeight / term.rows;
      const lines = Math.trunc((rest + pixels) / cell);
      rest += pixels - lines * cell;
      const before = term.buffer.active.viewportY;
      if (lines) term.scrollLines(-lines);
      return !lines || term.buffer.active.viewportY !== before;
    };
    const touchStart = (event: TouchEvent) => {
      cancelAnimationFrame(fling);
      rest = 0;
      drag = event.touches.length === 1 ? { y: event.touches[0]!.clientY, at: event.timeStamp, speed: 0 } : null;
    };
    const touchMove = (event: TouchEvent) => {
      if (!drag || event.touches.length !== 1) return;
      event.preventDefault();
      const y = event.touches[0]!.clientY;
      const elapsed = Math.max(event.timeStamp - drag.at, 1);
      drag.speed = 0.8 * ((y - drag.y) / elapsed) + 0.2 * drag.speed;
      scroll(y - drag.y);
      drag.y = y;
      drag.at = event.timeStamp;
    };
    const touchEnd = (event: TouchEvent) => {
      if (!drag || event.touches.length) return;
      // A finger that paused before lifting, or a touch the browser took over, stops where it is.
      let speed = event.type === "touchend" && event.timeStamp - drag.at < 100 ? drag.speed : 0;
      drag = null;
      let last = performance.now();
      const step = (now: number) => {
        const elapsed = Math.max(now - last, 0);
        speed *= FLING_DECAY ** (elapsed / 16);
        if (Math.abs(speed) < 0.05 || !scroll(speed * elapsed)) return;
        last = now;
        fling = requestAnimationFrame(step);
      };
      fling = requestAnimationFrame(step);
    };
    const screen = host.current!;
    screen.addEventListener("touchstart", touchStart, { passive: true });
    screen.addEventListener("touchmove", touchMove, { passive: false });
    screen.addEventListener("touchend", touchEnd);
    screen.addEventListener("touchcancel", touchEnd);

    // A settled size reaches the shell once, and only when its columns or rows changed.
    function size() {
      clearTimeout(resize);
      resize = setTimeout(() => {
        fit.fit();
        const now = `${term.cols}x${term.rows}`;
        if (done || now === sized) return;
        sized = now;
        void terminalSend(project, "resize", { task, id, cols: term.cols, rows: term.rows }).catch(() => {
          if (sized === now) sized = "";
        });
      }, RESIZE_MS);
    }
    const observer = new ResizeObserver(size);
    observer.observe(host.current!);
    fit.fit();
    connect();
    settle();
    return () => {
      done = true;
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("pageshow", resume);
      clearTimeout(retry);
      clearTimeout(resize);
      observer.disconnect();
      cancelAnimationFrame(fling);
      screen.removeEventListener("paste", unlatch, true);
      screen.removeEventListener("touchstart", touchStart);
      screen.removeEventListener("touchmove", touchMove);
      screen.removeEventListener("touchend", touchEnd);
      screen.removeEventListener("touchcancel", touchEnd);
      source?.close();
      input.dispose();
      term.dispose();
    };
  }, [project, task, id]); // eslint-disable-line react-hooks/exhaustive-deps -- the intro is drawn once

  // Hand the chat command over once the prompt has settled, or refuse it: nothing is handed after PROMPT_WAIT_MS
  // from the tap.
  useEffect(() => {
    if (!command) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refuse = (reason: string) => {
      clearTimeout(timer);
      clearTimeout(expiry);
      setHeld({ text: command.text, reason });
      report.current.onCommand?.();
    };
    const late = () => refuse(output.current.seen ? "The terminal kept printing, so the command wasn't typed."
      : "The terminal hasn't shown a prompt, so the command wasn't typed.");
    const expiry = setTimeout(late, command.at + PROMPT_WAIT_MS - Date.now());
    const attempt = async () => {
      const seen = output.current;
      const now = Date.now();
      if (!seen.seen || now - seen.at < SETTLE_MS) {
        timer = setTimeout(() => void attempt(), seen.seen ? SETTLE_MS - (now - seen.at) : SETTLE_MS);
        return;
      }
      // A throttled timer can fire late; the deadline itself is what counts.
      if (now >= command.at + PROMPT_WAIT_MS) return refuse("Too much time passed since the tap, so the command wasn't typed.");
      if (stopped.current) return refuse("Typing is stopped, so the command wasn't typed.");
      clearTimeout(expiry);
      setHeld(null);
      try {
        await terminalSend(project, "command", { task, id, text: command.text, bracketed: xterm.current!.modes.bracketedPasteMode });
      } catch (error) {
        if (gone) return;
        // An ended (404) or replaced (410) terminal reports its end on the stream.
        if (error instanceof ApiError && (error.status === 404 || error.status === 410)) return report.current.onCommand?.();
        // altd's own refusals say why; anything else may have typed part of it.
        return refuse(error instanceof ApiError && error.status === 409 ? error.message
          : "Altitude couldn't type the command. Check the screen before typing it yourself.");
      }
      if (gone) return;
      xterm.current!.scrollToBottom();
      settle();
      report.current.onCommand?.();
    };
    let gone = false;
    void attempt();
    return () => {
      gone = true;
      clearTimeout(timer);
      clearTimeout(expiry);
    };
  }, [command?.seq, command?.text]); // eslint-disable-line react-hooks/exhaustive-deps -- one attempt per request

  // A key row key keeps focus where it is as it is pressed, and afterwards returns it to the screen only if the
  // operator was typing: an open soft keyboard stays open, a closed one stays closed. Ctrl, latching, opens it for its
  // letter.
  const hold = (event: ReactPointerEvent) => {
    event.preventDefault();
    typing.current = Boolean(host.current?.contains(document.activeElement));
  };
  const wasTyping = () => {
    const was = typing.current ?? Boolean(host.current?.contains(document.activeElement));
    typing.current = null;
    return was;
  };
  const press = (data: string) => {
    const was = wasTyping();
    send.current(data);
    if (was) focus.current();
  };
  const paste = async () => {
    const was = wasTyping();
    try {
      const text = await navigator.clipboard.readText();
      if (text) pasteText.current(text);
    } catch {
      toast.show({ message: "This browser didn't allow reading the clipboard.", severity: "failure" });
    }
    if (was) focus.current();
  };

  return <>
    {inputError ? <div className="terminal-stopped" role="alert">
      <p>{`Typing stopped: ${inputError} Part of what you typed may not have arrived; check the screen.`}</p>
      <button type="button" className="btn" onClick={() => { stopped.current = false; setInputError(null); settle(); }}>Resume typing</button>
    </div> : null}
    {held ? <div className="terminal-held" role="status">
      <p>{held.reason}</p>
      <CopyButton text={held.text} label="Copy command" />
      <button type="button" className="icon-btn" aria-label="Dismiss" onClick={() => { setHeld(null); settle(); }}>
        <svg aria-hidden viewBox="0 0 20 20" width="16" height="16"><path d="M5 5l10 10M15 5L5 15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
      </button>
    </div> : null}
    <div className="terminal-frame">
      <div className="terminal-screen" ref={host} />
      {reconnecting ? <p className="terminal-reconnecting"><StatusMark label="Reconnecting…" /></p> : null}
    </div>
    {keys ? <div className="terminal-keys" role="toolbar" aria-label="Terminal keys">
      {[KEYS[0]!, KEYS[1]!].map((key) => <button key={key.name} type="button" aria-label={key.name}
        onPointerDown={hold} onClick={() => press(key.data)}>{key.label}</button>)}
      <button type="button" aria-label="Control" aria-pressed={ctrl} onPointerDown={hold}
        onClick={() => { const was = wasTyping(); ctrlRef.current = !ctrlRef.current; setCtrl(ctrlRef.current); if (ctrlRef.current || was) focus.current(); }}>Ctrl</button>
      {KEYS.slice(2).map((key) => <button key={key.name} type="button" aria-label={key.name}
        onPointerDown={hold} onClick={() => press(key.data)}>{key.label}</button>)}
      <button type="button" aria-label="Paste" onPointerDown={hold} onClick={() => void paste()}>
        <svg aria-hidden viewBox="0 0 20 20" width="18" height="18"><path d="M7 4h6v2H7zM6 5H5a1 1 0 00-1 1v10a1 1 0 001 1h10a1 1 0 001-1V6a1 1 0 00-1-1h-1" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></svg>
      </button>
      {/* The keyboard's Return, also with the soft keyboard closed: it stays closed for a typed line, a `read` or a password. */}
      <button type="button" className="terminal-enter" aria-label="Enter" onPointerDown={hold} onClick={() => press("\r")}>
        <svg aria-hidden viewBox="0 0 20 20" width="18" height="18"><path d="M15 5v5a2 2 0 01-2 2H5M8.5 8.5L5 12l3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
    </div> : null}
  </>;
}
