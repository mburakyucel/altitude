import { useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { useViewport } from "../shell/breakpoints";
import { Prose } from "./Prose";
import "../routes/settings.css";
import "../routes/agents.css";

/*
 * Pieces every agent surface shares (design/wireframes/project-agents/PROPOSAL.md): the page frame with its back
 * link, the row chevron, the pause mark, a run's dot and a brief clamped to three lines with Show all.
 */

/** The agents pages' frame: the back link and title in the phone header, or above the page on desktop. */
export function AgentFrame({ back, title, children }: { back: { to: string; label: string }; title: ReactNode; children: ReactNode }) {
  const { phone } = useViewport();
  const { state } = useLocation();
  const link = <Link to={back.to} state={state} className="btn settings-back agents-back">
    <svg aria-hidden viewBox="0 0 20 20" width="16" height="16"><path d="M12 5l-5 5 5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
    <span className="truncate">{back.label}</span>
  </Link>;
  return <>
    {phone ? <header className="phone-header settings-header">{link}<h1 className="truncate">{title}</h1></header> : null}
    <div className="page settings-page agents-page">
      {phone ? null : <>{link}<h1>{title}</h1></>}
      {children}
    </div>
  </>;
}

export function Chevron() {
  return <svg className="agents-chevron" aria-hidden viewBox="0 0 20 20" width="16" height="16">
    <path d="M8 5l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}

export function PauseMark() {
  return <svg className="agents-pause" aria-hidden viewBox="0 0 12 12" width="12" height="12">
    <path d="M4 2.5v7M8 2.5v7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </svg>;
}

/** A run's dot: accent for found, danger for couldn't finish and its limit, muted otherwise. */
export function RunDot({ tone }: { tone: "accent" | "danger" | "muted" }) {
  return <span className="dot" data-state={tone === "accent" ? "running" : tone === "danger" ? "danger" : "idle"} aria-hidden />;
}

/** A brief clamped to three lines; Show all opens the rest in place, offered only when there is more. */
export function ClampedBrief({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const [more, setMore] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = box.current;
    if (node && !open) setMore(node.scrollHeight > node.clientHeight + 1);
  }, [text, open]);
  return <div className="agent-brief-wrap">
    <div ref={box} className="agent-brief" data-clamped={open ? undefined : ""}><Prose text={text} /></div>
    {more && !open ? <button type="button" className="link agents-link" onClick={() => setOpen(true)}>Show all</button> : null}
  </div>;
}
