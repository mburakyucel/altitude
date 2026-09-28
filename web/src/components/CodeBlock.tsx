import { createContext, useContext, useEffect, useRef, useState } from "react";
import "./code-block.css";

/**
 * Where a conversation's `run` blocks open (SPEC.md §3.3): its terminal, or the reason it has none now.
 * Surfaces without a provider (Live session, Needs you, reports) show commands with Copy only.
 */
export const ProseTerminal = createContext<{ open: (command: string) => void } | { unavailable: string } | null>(null);

/**
 * A `run` block's command: exactly one line, kept byte for byte, with no control, invisible formatting
 * or line-separator character anywhere, so what the message shows is exactly what reaches the terminal.
 * Anything else is refused with the reason shown under it.
 */
export function runCommand(text: string): { command: string } | { refused: string } {
  if (!text.trim()) return { refused: "Not offered for the terminal: the command is empty." };
  if (/[\r\n\u2028\u2029]/.test(text)) return { refused: "Not offered for the terminal: more than one line." };
  if (/[\p{Cc}\p{Cf}]/u.test(text)) return { refused: "Not offered for the terminal: it contains a control or invisible character." };
  return { command: text };
}

const COPY_ICON = <svg aria-hidden viewBox="0 0 20 20" width="16" height="16"><path d="M7.5 7.5h8v8h-8zM4.5 12.5v-8h8" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></svg>;
const TERMINAL_ICON = <svg aria-hidden viewBox="0 0 20 20" width="16" height="16"><rect x="2.5" y="3.5" width="15" height="13" rx="2" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M6 8l2.5 2.5L6 13M10.5 13h4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>;

/** Copies `text` verbatim; reads "Copied" for two seconds, or "Couldn't copy" when the browser refuses. */
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "refused">("idle");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const copy = async () => {
    let next: "copied" | "refused" = "copied";
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      next = "refused";
    }
    setState(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 2_000);
  };
  return <button type="button" className="btn code-copy" onClick={() => void copy()}>
    {state === "copied" ? <svg aria-hidden viewBox="0 0 20 20" width="16" height="16"><path d="M4.5 10.5l3.5 3.5 7.5-8" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg> : COPY_ICON}
    <span role={state === "idle" ? undefined : "status"}>{state === "copied" ? "Copied" : state === "refused" ? "Couldn't copy" : label}</span>
  </button>;
}

/**
 * A fenced code block in prose. A `run` block holding one safe line is a command: shown in full, with
 * Copy and, where its conversation has a terminal, Open in terminal, which types it at that terminal's
 * prompt without pressing Enter. Every other block is code with Copy; it is never an action.
 */
export function CodeBlock({ text, info }: { text: string; info: string }) {
  const terminal = useContext(ProseTerminal);
  if (info.trim() !== "run") {
    return <div className="code-block"><pre className="session-code">{text}</pre><CopyButton text={text} /></div>;
  }
  const run = runCommand(text);
  const note = "refused" in run ? run.refused : terminal && "unavailable" in terminal ? terminal.unavailable : null;
  const open = "command" in run && terminal && "open" in terminal ? terminal.open : null;
  return <div className="command-block" role="group" aria-label="Command">
    <pre className="session-code">{text}</pre>
    <div className="command-actions">
      {note ? <span className="command-note">{note}</span> : null}
      <CopyButton text={text} />
      {open ? <button type="button" className="btn btn-primary" onClick={() => open(text)}>{TERMINAL_ICON}Open in terminal</button> : null}
    </div>
  </div>;
}
