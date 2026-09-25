import { useEffect, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { terminalSend, terminalStatus, terminalStream } from "../data/api";
import type { TerminalStatus } from "../data/api";

const RECONNECT_MS = 2_000;
const RESIZE_MS = 100;

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

/**
 * One running or ended terminal on screen. It replays the server's buffer from the start, follows the
 * output stream, and after a lost connection resumes from the last offset it drew; a replaced terminal
 * or an altd restart ends it through `onEnd`. Typed input is sent in order, one request at a time, and
 * every request names terminal `id`, so nothing reaches a terminal that replaced it.
 */
export default function TerminalScreen({ project, task, id, keys, onEnd, onReconnecting }: {
  project: string;
  task?: string;
  id: string;
  keys: boolean;
  onEnd: (status: TerminalStatus) => void;
  onReconnecting: (lost: boolean) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const send = useRef<(data: string) => void>(() => undefined);
  const focus = useRef<() => void>(() => undefined);
  const ctrlRef = useRef(false);
  const [ctrl, setCtrl] = useState(false);
  const report = useRef({ onEnd, onReconnecting });
  report.current = { onEnd, onReconnecting };

  useEffect(() => {
    const term = new XTerm({
      cursorBlink: true,
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      fontSize: 13,
      scrollback: 5000,
      theme: { background: "#111827", foreground: "#e5e7eb", cursor: "#e5e7eb" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host.current!);
    let offset = 0;
    let source: EventSource | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let resize: ReturnType<typeof setTimeout> | undefined;
    let done = false;

    const finish = (status: TerminalStatus) => {
      done = true;
      source?.close();
      report.current.onReconnecting(false);
      report.current.onEnd(status);
    };
    const connect = () => {
      const stream = terminalStream(project, task, id, offset);
      stream.addEventListener("output", (event) => {
        const chunk = JSON.parse((event as MessageEvent<string>).data) as { offset: number; data: string };
        term.write(bytes(chunk.data));
        offset = chunk.offset;
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
    };
    // After a lost connection: the same terminal resumes from its offset; anything else has ended it.
    const check = async () => {
      try {
        const status = await terminalStatus(project, task);
        if (done) return;
        if (status.id !== id || status.state === "none") finish(status);
        else connect();
      } catch {
        if (!done) retry = setTimeout(check, RECONNECT_MS);
      }
    };

    let pending = "";
    let sending = false;
    const flush = async () => {
      if (done || sending || !pending) return;
      sending = true;
      const data = pending;
      pending = "";
      try {
        await terminalSend(project, "input", { task, id, data });
      } catch {
        // A closed terminal reports its end on the stream; a lost connection shows Reconnecting.
      }
      sending = false;
      void flush();
    };
    send.current = (data: string) => {
      if (ctrlRef.current) {
        data = control(data);
        ctrlRef.current = false;
        setCtrl(false);
      }
      pending += data;
      void flush();
    };
    focus.current = () => term.focus();
    const input = term.onData((data) => send.current(data));

    const size = () => {
      clearTimeout(resize);
      resize = setTimeout(() => {
        fit.fit();
        if (!done) void terminalSend(project, "resize", { task, id, cols: term.cols, rows: term.rows }).catch(() => undefined);
      }, RESIZE_MS);
    };
    const observer = new ResizeObserver(size);
    observer.observe(host.current!);
    fit.fit();
    connect();
    term.focus();
    return () => {
      done = true;
      clearTimeout(retry);
      clearTimeout(resize);
      observer.disconnect();
      source?.close();
      input.dispose();
      term.dispose();
    };
  }, [project, task, id]);

  return <>
    <div className="terminal-screen" ref={host} />
    {keys ? <div className="terminal-keys" role="toolbar" aria-label="Terminal keys">
      {[KEYS[0]!, KEYS[1]!].map((key) => <button key={key.name} type="button" aria-label={key.name}
        onPointerDown={(event) => event.preventDefault()} onClick={() => { send.current(key.data); focus.current(); }}>{key.label}</button>)}
      <button type="button" aria-label="Control" aria-pressed={ctrl} onPointerDown={(event) => event.preventDefault()}
        onClick={() => { ctrlRef.current = !ctrlRef.current; setCtrl(ctrlRef.current); focus.current(); }}>Ctrl</button>
      {KEYS.slice(2).map((key) => <button key={key.name} type="button" aria-label={key.name}
        onPointerDown={(event) => event.preventDefault()} onClick={() => { send.current(key.data); focus.current(); }}>{key.label}</button>)}
    </div> : null}
  </>;
}
