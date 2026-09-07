import { useEffect, useId, useState } from "react";
import type { EngineReadout, TaskTokenUsage, TokenSession } from "../data/api";
import { agoText, exactTime, when } from "../data/observed";

const count = (value: number | null | undefined) => value == null ? "Unknown" : value.toLocaleString("en-US");
const coverage = (status: string | undefined) => status === "observed" ? "Observed coverage" : status === "partial" ? "Partial coverage" : "Unknown coverage";
const roles: Record<string, string> = { owner: "L2 owner", delegated: "Delegated", provider: "Provider total · helpers unsplit" };

function Counters({ usage }: { usage: Partial<TaskTokenUsage> }) {
  const rows = [
    ["Input", usage.input_tokens],
    ["Output", usage.output_tokens],
    ["Cache read · in input", usage.cache_read_tokens],
    ["Cache write · in input", usage.cache_write_tokens],
    ["Reasoning · in output", usage.reasoning_tokens],
  ] as const;
  return <dl className="token-counters">{rows.map(([label, value]) => (
    <div key={label}><dt>{label}</dt><dd>{count(value)}</dd></div>
  ))}</dl>;
}

/** Reads the server's observed sum; never substitutes report spend or guesses missing helpers. */
export function TokenUsage({ usage, running = false, engines = [] }: {
  usage: TaskTokenUsage | null | undefined;
  running?: boolean;
  engines?: Pick<EngineReadout, "engine" | "label">[];
}) {
  const [open, setOpen] = useState(false);
  const [, tick] = useState(0);
  const detailsId = useId();
  useEffect(() => {
    if (!running || usage?.finalized_at) return;
    const timer = window.setInterval(() => tick((value) => value + 1), 20_000);
    return () => window.clearInterval(timer);
  }, [running, usage?.finalized_at]);

  const checked = when(usage?.checked_at);
  const stale = running && !usage?.finalized_at && checked != null && Date.now() - checked > 60_000;
  const freshness = usage?.finalized_at ? `Finalized ${agoText(usage.finalized_at)}`
    : checked == null ? "Freshness unknown" : `${stale ? "Stale · checked" : "Checked"} ${agoText(usage?.checked_at)}`;
  const byEngine = new Map<string, TokenSession[]>();
  for (const session of usage?.sessions ?? []) {
    byEngine.set(session.engine, [...(byEngine.get(session.engine) ?? []), session]);
  }

  return (
    <section className="token-usage" aria-label="Task token usage">
      <button type="button" className="token-summary" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen(!open)}>
        <span className="token-summary-value">{usage?.total_tokens == null ? "Token usage unknown" : `${count(usage.total_tokens)} observed tokens`}</span>
        <span className="token-coverage">{coverage(usage?.status)}</span>
        <span className="token-freshness" title={exactTime(usage?.finalized_at || usage?.checked_at)}>{freshness}</span>
        <span className="token-toggle">{open ? "Hide details" : "Details"}</span>
      </button>
      {open ? <div className="token-details" id={detailsId}>
        <p>Observed input + output. Cache is counted once in input; reasoning is included in output.</p>
        {usage?.status === "partial" ? <p>Partial totals sum available counters and are a lower bound.</p> : null}
        <Counters usage={usage ?? {}} />
        <p title={exactTime(usage?.observed_at)}>{when(usage?.observed_at) == null ? "No usage observation recorded." : `Usage observed ${agoText(usage?.observed_at)}.`}</p>
        {usage?.finalized_at ? <p title={exactTime(usage.checked_at)}>Last checked {agoText(usage.checked_at) || "at an unknown time"}.</p> : null}
        {usage?.notes.map((note, index) => <p key={index}>{note}</p>)}
        {!usage || usage.sessions.length === 0 ? <p>No attributable session counters available.</p> : null}
        {Array.from(byEngine, ([engine, sessions]) => {
          const totals = sessions.map((s) => s.total_tokens).filter((n): n is number => n != null);
          return <section className="token-engine" key={engine} aria-label={`${engines.find((e) => e.engine === engine)?.label ?? engine} usage`}>
            <h3>{engines.find((e) => e.engine === engine)?.label ?? engine} <span>{totals.length ? `${count(totals.reduce((a, b) => a + b, 0))} observed tokens` : "Token usage unknown"}</span></h3>
            {sessions.map((session) => <div className="token-session" key={`${session.attempt}:${session.session_id}`}>
              <p className="token-session-label">{roles[session.role] ?? session.role} · attempt {session.attempt ?? "unknown"} · {session.total_tokens == null ? "tokens unknown" : `${count(session.total_tokens)} tokens`} · {coverage(session.status)}</p>
              <Counters usage={session} />
              <p className="token-session-id">Session {session.session_id}{session.parent_session_id ? ` · parent ${session.parent_session_id}` : ""}</p>
              {session.notes.map((note, index) => <p key={index}>{note}</p>)}
            </div>)}
          </section>;
        })}
      </div> : null}
    </section>
  );
}
