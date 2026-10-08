import { useEffect, useId, useState } from "react";
import type { EngineReadout, TaskTokenUsage, TokenContext, TokenSession } from "../data/api";
import { agoText, exactTime, when } from "../data/observed";

const count = (value: number | null | undefined) => value == null ? "Unknown" : value.toLocaleString("en-US");
const coverage = (status: string | undefined) => status === "observed" ? "Observed coverage" : status === "partial" ? "Partial coverage" : "Unknown coverage";
const roles: Record<string, string> = { owner: "L2 owner", delegated: "Delegated", provider: "Provider total · helpers unsplit" };

function Counters({ usage }: { usage: Partial<TaskTokenUsage> }) {
  const rows = [
    ["Input processed", usage.input_tokens],
    ["Cache read · in input", usage.cache_read_tokens],
    ["Cache write · in input", usage.cache_write_tokens],
    ["Output generated", usage.output_tokens],
    ["Reasoning · in output", usage.reasoning_tokens],
    ["Model requests", usage.requests],
  ] as const;
  return <dl className="token-counters">{rows.map(([label, value]) => (
    <div key={label}><dt>{label}</dt><dd>{count(value)}</dd></div>
  ))}</dl>;
}

function Helpers({ helpers, label }: {
  helpers: TaskTokenUsage["helpers"];
  label: (engine: string) => string;
}) {
  return <section className="token-helpers" aria-label="L1 helpers observed">
    <h3>L1 helpers observed</h3>
    <dl className="token-counters">
      <div><dt>Unique helpers</dt><dd>{count(helpers?.observed_count)}</dd></div>
      <div><dt>Direct helpers</dt><dd>{count(helpers?.direct_count)}</dd></div>
      <div><dt>Descendants</dt><dd>{count(helpers?.descendant_count)}</dd></div>
      <div><dt>Attributable helper tokens</dt><dd>{count(helpers?.total_tokens)}</dd></div>
    </dl>
    {(helpers?.unclassified_count ?? 0) > 0 ? <p>{count(helpers?.unclassified_count)} owner-linked · depth unknown.</p> : null}
    {helpers?.observed_count === 0 ? <p>No helpers observed.</p> : null}
    {!helpers || helpers.status === "unknown" ? <p>Helper evidence unavailable.</p> : <p>Partial discovery: observed identities across attempts; resumed helpers count once. Helpers with unavailable counters still count.</p>}
    <p>Attributable helper tokens are a lower bound included in task totals.</p>
    {helpers?.sessions.map((session) => <section className="token-session" key={`${session.engine}:${session.session_id}`} aria-label={`Helper session ${session.session_id}`}>
      <h4 className="token-session-label">{label(session.engine)} · {session.depth === 1 ? "Direct helper" : session.depth == null ? "Owner-linked helper · depth unknown" : `Descendant helper · depth ${session.depth}`} · {session.total_tokens == null ? "tokens unknown" : `${count(session.total_tokens)} tokens`} · {coverage(session.status)}</h4>
      <Counters usage={session} />
      {session.provider_total_tokens != null ? <p>Unsplit provider total: {count(session.provider_total_tokens)} tokens · may include descendants; not attributable to this helper alone.</p> : null}
      <p className="token-session-id">Session {session.session_id}</p>
      <p className="token-session-id">{session.parentage === "owner" ? "Owner linkage" : "Parent"} {session.parent_session_id ?? "unknown"} · L2 owner {session.owner_session_id ?? "unknown"}</p>
      <p>Owner attempt context: {session.attempts.length ? session.attempts.join(", ") : session.attempt ?? "unknown"}.</p>
      {session.notes.map((note, index) => <p key={index}>{note}</p>)}
    </section>)}
  </section>;
}

/** The current owner session's newest request input; never derived from cumulative totals. */
export function TaskContext({ context, running }: { context: TokenContext | null | undefined; running: boolean }) {
  const label = running ? "Current context" : "Context at last request";
  if (!context) return <p className="task-context"><span>{label}</span> unavailable · no reliable reading for the current session</p>;
  const size = context.window == null ? "window not reported"
    : `${context.percent == null || context.percent >= 1 ? Math.round(context.percent ?? 0) : "<1"}% of ${count(context.window)}`;
  return <p className="task-context" title={exactTime(context.observed_at)}>
    <span>{label}</span> {count(context.tokens)} tokens · {size}{context.observed_at ? ` · ${agoText(context.observed_at)}` : ""}
  </p>;
}

/** Reads the server's observed sum; never substitutes report spend or guesses missing helpers. */
export function TokenUsage({ usage, running = false, engines = [], disclosureLabel }: {
  usage: TaskTokenUsage | null | undefined;
  running?: boolean;
  engines?: Pick<EngineReadout, "engine" | "label">[];
  /** Monitor keeps even task totals behind its L2 disclosure. */
  disclosureLabel?: string;
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
  const engineLabel = (engine: string) => engines.find((e) => e.engine === engine)?.label ?? engine;
  const helperIds = new Set(usage?.helpers?.sessions.map((s) => `${s.engine}:${s.session_id}`));
  const summary = <>
    <span className="token-summary-value">{usage?.total_tokens == null ? "Token usage unknown" : `${count(usage.total_tokens)} tokens processed`}</span>
    {usage?.requests != null ? <span className="token-coverage">{count(usage.requests)} model requests</span> : null}
    <span className="token-coverage">{coverage(usage?.status)}</span>
    <span className="token-freshness" title={exactTime(usage?.finalized_at || usage?.checked_at)}>{freshness}</span>
  </>;

  return (
    <section className="token-usage" aria-label="Task token usage">
      <button type="button" className="token-summary" aria-label={disclosureLabel} aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen(!open)}>
        {disclosureLabel ? <span className="token-summary-value">{disclosureLabel}</span> : summary}
        <span className="token-toggle">{open ? "Hide details" : "Details"}</span>
      </button>
      {open ? <div className="token-details" id={detailsId}>
        {disclosureLabel ? <div className="token-readout">{summary}</div> : null}
        <p>Each model request sends the conversation so far again, so processed input grows with every step and is mostly cached. Output is what the model generated, including reasoning. This is an activity count, not a bill, quota use or the current context.</p>
        {usage?.status === "partial" ? <p>Partial totals sum available counters and are a lower bound.</p> : null}
        <Counters usage={usage ?? {}} />
        <p title={exactTime(usage?.observed_at)}>{when(usage?.observed_at) == null ? "No usage observation recorded." : `Usage observed ${agoText(usage?.observed_at)}.`}</p>
        {usage?.finalized_at ? <p title={exactTime(usage.checked_at)}>Last checked {agoText(usage.checked_at) || "at an unknown time"}.</p> : null}
        {usage?.notes.map((note, index) => <p key={index}>{note}</p>)}
        <Helpers helpers={usage?.helpers} label={engineLabel} />
        {!usage || usage.sessions.length === 0 ? <p>No attributable session counters available.</p> : null}
        {Array.from(byEngine, ([engine, sessions]) => {
          const totals = sessions.map((s) => s.total_tokens).filter((n): n is number => n != null);
          return <section className="token-engine" key={engine} aria-label={`${engineLabel(engine)} usage`}>
            <h3>{engineLabel(engine)} <span>{totals.length ? `${count(totals.reduce((a, b) => a + b, 0))} tokens processed` : "Token usage unknown"}</span></h3>
            {sessions.filter((s) => s.role === "provider" || !helperIds.has(`${s.engine}:${s.session_id}`)).map((session) => <div className="token-session" key={`${session.attempt}:${session.session_id}`}>
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
