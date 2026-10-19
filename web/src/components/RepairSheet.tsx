import { useLayoutEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { BusyLabel } from "./StatusMark";
import { Overlay } from "../shell/Overlay";
import { useViewport } from "../shell/breakpoints";
import { RepairRefused, newRequest, repairRequest, useRepairAction, useRepairs } from "../data/api";
import "./repair.css";

/** Text with `code` spans, as Altitude's refusals name a command. */
export function Coded({ text }: { text: string }) {
  return <>{text.split(/(`[^`]+`)/).map((part, i) => part.startsWith("`") && part.endsWith("`") && part.length > 2
    ? <code key={i}>{part.slice(1, -1)}</code> : part)}</>;
}

/**
 * Start a repair (SPEC.md §3.15, design/wireframes/repair/PROPOSAL.md): what full access means, what's wrong, and
 * Start repair. A bottom sheet on the phone, a 480px dialog on desktop. `problem` fills the field, as the failed-update
 * notice does; `continues` resumes an ended repair's session after its report. One request identity per sheet makes a
 * double tap, or a retry after a lost reply, start one repair.
 */
export function RepairSheet({ problem = "", continues, onClose }: { problem?: string; continues?: string; onClose: () => void }) {
  const { phone } = useViewport();
  const navigate = useNavigate();
  const repairs = useRepairs();
  const [text, setText] = useState(problem);
  const [request] = useState(newRequest);
  const field = useRef<HTMLTextAreaElement>(null);
  const start = useRepairAction((what: string) => repairRequest("/api/repairs", { problem: what, ...(continues ? { continues } : {}) }, request));
  useLayoutEffect(() => {
    const node = field.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight}px`;
  }, [text]);
  const engine = repairs.data?.engine ?? "its coding agent";
  const refused = start.error;
  const running = refused instanceof RepairRefused ? refused.running : null;
  const submit = () => start.mutate(text.trim(), {
    onSuccess: (repair) => { onClose(); navigate(`/repairs/${repair.id}`); },
  });
  return <Overlay label="Start a repair" side={phone ? "bottom" : "center"} onClose={() => { if (!start.isPending) onClose(); }}>
    <form className="repair-sheet" onSubmit={(event) => { event.preventDefault(); if (text.trim() && !start.isPending) submit(); }}>
      {phone ? <span className="repair-sheet-grab" aria-hidden /> : null}
      <h2>Start a repair?</h2>
      <p>A repair agent gets <b>full access to this computer as you</b> to find out what’s wrong and fix it. It works until
        it reports, you stop it, or 2 hours pass.</p>
      <p>Its rules are instructions it follows, not limits Altitude enforces. It can read any of your files, and what it
        reads goes to {engine}. Each step it takes is shown here and kept.</p>
      <label className="repair-sheet-label" htmlFor="repair-problem">What’s wrong?</label>
      <textarea id="repair-problem" ref={field} rows={2} value={text} data-autofocus maxLength={2000}
        placeholder="For example: the page doesn’t load on my phone" onChange={(event) => setText(event.target.value)} />
      {repairs.data?.engine ? <p className="text-meta text-muted">{repairs.data.engine} · uses your plan like a small task</p> : null}
      <div className="repair-sheet-buttons">
        <button type="submit" className="btn btn-primary" disabled={!text.trim() || start.isPending}
          aria-label={start.isPending ? "Start repair, starting" : undefined}>
          <BusyLabel busy={start.isPending} label="Start repair" working="Starting…" />
        </button>
        <button type="button" className="btn btn-ghost" disabled={start.isPending} onClick={onClose}>Cancel</button>
      </div>
      {refused ? <p className="text-danger repair-sheet-error" role="alert">
        <Coded text={refused.message} />
        {running ? <>{" "}<Link className="link" to={`/repairs/${running}`} onClick={onClose}>Open it</Link></> : null}
      </p> : null}
    </form>
  </Overlay>;
}

/** A button that opens the sheet. */
export function RepairButton({ label, problem, continues, className = "btn" }: { label: string; problem?: string; continues?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className={className} onClick={() => setOpen(true)}>{label}</button>
    {open ? <RepairSheet problem={problem} continues={continues} onClose={() => setOpen(false)} /> : null}
  </>;
}
