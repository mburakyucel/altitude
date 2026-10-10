/** Routine progress and completion stay visual; the full state remains accessible. */
export function StatusMark({ label, busy = true, announce = true }: { label: string; busy?: boolean; announce?: boolean }) {
  return <span className="status-mark" role={announce ? "status" : "img"} aria-label={label}>
    <span className="sr-only">{label}</span>
    {busy ? <span className="spinner" aria-hidden="true" /> : <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4.5 10.5 8 14l7.5-8" /></svg>}
  </span>;
}

/** Keep an action's size while its label gives way to progress. */
export function BusyLabel({ busy, label, working }: { busy: boolean; label: string; working: string }) {
  return <span className="busy-label" aria-busy={busy || undefined}>
    <span style={{ visibility: busy ? "hidden" : undefined }} aria-hidden={busy || undefined}>{label}</span>
    {busy ? <span className="busy-label-progress"><StatusMark label={working} announce={false} /></span> : null}
  </span>;
}
