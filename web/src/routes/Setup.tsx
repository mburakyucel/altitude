import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { StatusMark } from "../components/StatusMark";
import { ApiError, useSetup, useSetupAction } from "../data/api";
import type { Setup } from "../data/api";
import { Overlay } from "../shell/Overlay";

const statuses: Record<string, string> = {
  pending: "Pending", running: "In progress", complete: "Complete", reused: "Already configured",
  not_applicable: "Not applicable", input_needed: "Needs attention", failed: "Failed", unknown: "Recheck needed",
};
const attention = (status: string) => ["failed", "input_needed", "unknown"].includes(status);

function summary(setup?: Setup) {
  switch (setup?.status) {
    case "ready": return "Ready";
    case "conversation_ready": return "Conversation ready";
    case "attention": return "Needs attention";
    case "checking": return "Checking";
    default: return "Recheck needed";
  }
}

/**
 * The current reading of project setup. It needs attention while setup runs, a current requirement is
 * unmet or the reading failed; a healthy or still-loading reading stays quiet.
 */
export function useSetupReading(name: string) {
  const setup = useSetup(name);
  const [, setSearch] = useSearchParams();
  const label = setup.isPending ? "Checking" : setup.isError ? "Unavailable" : summary(setup.data);
  const healthy = setup.data?.status === "ready" || setup.data?.status === "conversation_ready";
  const open = () => {
    void setup.refetch();
    setSearch((current) => { const next = new URLSearchParams(current); next.set("setup", "1"); return next; });
  };
  return { setup, label, attention: !setup.isPending && (setup.isError || !healthy), open };
}

/** Setup is an observation of project state. Only accepted server work changes a row's progress. */
export function ProjectSetup({ name }: { name: string }) {
  const { setup, label, attention: noticed, open: show } = useSetupReading(name);
  const action = useSetupAction(name);
  const [search, setSearch] = useSearchParams();
  const [integration, setIntegration] = useState<string | null>(null);
  // Opened from the header, the status stays there until focus leaves it, so closing can return focus.
  const [kept, setKept] = useState(false);
  const client = useQueryClient();
  const open = search.get("setup") === "1";
  const close = (conversation = false) => {
    // The conversation polls slowly while idle; a first reply saved meanwhile appears on arrival.
    if (conversation) void client.invalidateQueries({ queryKey: ["chat", name] });
    setSearch((current) => {
      const next = new URLSearchParams(current);
      next.delete("setup");
      if (conversation) next.delete("tab");
      return next;
    }, { replace: true });
    setIntegration(null);
  };
  const data = setup.data;
  const refused = action.error instanceof ApiError && action.error.status >= 400 && action.error.status < 500;
  const awaitingObservation = action.isError && !refused && (setup.isFetching || setup.dataUpdatedAt < action.submittedAt);
  const busy = action.isPending || awaitingObservation || data?.status === "checking";
  const stale = setup.isError;
  const steps = [...(data?.steps ?? [])].sort((a, b) => Number(attention(b.status)) - Number(attention(a.status)));
  const failed = data?.steps.some((step) => step.status === "failed" || step.status === "unknown");
  const run = (kind: "check" | "repair" | "combine", expected?: string) => {
    setIntegration(null);
    action.mutate({ action: kind, ...(expected ? { expected } : {}) });
  };

  return <>
    {noticed || kept ? <button type="button" className="setup-trigger" data-status={stale ? "unknown" : data?.status}
      aria-label={`Setup: ${label}`} aria-haspopup="dialog" aria-expanded={open}
      onClick={(event) => {
        // WebKit does not focus a clicked button; the checklist returns focus to its opener.
        event.currentTarget.focus();
        setKept(true);
        show();
      }} onBlur={() => { if (!open) setKept(false); }}>
      <span className="setup-dot" aria-hidden />
      <span>Setup<span className="setup-trigger-status"><span className="setup-separator"> · </span>{label}</span></span>
    </button> : null}
    {open ? <Overlay label="Project setup" side="right" onClose={() => close()}>
      <section className="setup-panel">
        <header className="setup-heading">
          <div><p className="label text-muted">{name}</p><h2>Project setup</h2></div>
          <button type="button" className="icon-btn" aria-label="Close setup" onClick={() => close()}>✕</button>
        </header>
        <div className="setup-intro">
          <p className="setup-summary" data-status={stale ? "unknown" : data?.status} role="status">{label}</p>
          <p className="text-muted">Setup runs automatically. If a step fails, L3 can help.</p>
        </div>
        {stale ? <div className="setup-notice" role="alert">
          <p>{data ? "Showing saved results. Current setup could not be checked." : "Could not read project setup."}</p>
          <p className="text-meta">{setup.error.message}</p>
          <button type="button" className="btn" onClick={() => void setup.refetch()}>Retry connection</button>
        </div> : null}
        {action.isError ? <div className="setup-notice" role="alert">
          <p>{refused ? "Request refused. No action was accepted." : stale ? "Confirmation lost. Reconnect to check whether the action completed." : awaitingObservation ? "Confirmation lost. Checking the existing operation before retrying." : "Confirmation was lost. The current setup results are shown below."}</p>
          <p className="text-meta">{action.error.message}</p>
        </div> : null}
        {action.isPending ? <p className="text-meta text-muted"><StatusMark label="Sending request…" /></p> : null}
        {setup.isPending ? <div aria-label="Loading setup" className="setup-loading"><div className="skeleton h-10" /><div className="skeleton h-10" /><div className="skeleton h-10" /></div> : null}
        {data?.error ? <p className="setup-notice" role="alert">{data.error}</p> : null}
        {data && steps.length === 0 ? <p className="text-muted">No setup results yet. Check again to inspect this project.</p> : null}
        <ol className="setup-steps" aria-live="polite" aria-relevant="text">
          {steps.map((step) => <li key={step.id} className="setup-step" data-status={step.status}>
            <span className={`setup-mark${step.status === "running" ? " spinner" : ""}`} aria-hidden>
              {step.status === "complete" || step.status === "reused" ? "✓" : attention(step.status) ? "!" : step.status === "running" ? "" : "−"}
            </span>
            <div className="setup-step-body">
              <div className="setup-step-heading"><h3>{step.label}</h3><span className="setup-step-status">{statuses[step.status] ?? "Recheck needed"}</span></div>
              <p className="text-meta text-muted">{step.detail}</p>
              {step.action === "repair" ? <button type="button" className="btn" disabled={busy || stale} onClick={() => run("repair")}>
                {step.status === "failed" || step.status === "unknown" ? "Retry" : "Repair"}
              </button> : null}
              {step.action === "discuss" ? <button type="button" className="btn" onClick={() => close(true)}>Discuss with L3</button> : null}
              {step.action === "combine" ? <>
                {integration !== step.id ? <button type="button" className="btn" disabled={busy || stale} onClick={() => setIntegration(step.id)}>Review integration</button> : <div className="setup-integration">
                  <p>Use your existing hooks together with Altitude’s guards? Original hook files stay intact. Either set can reject a Git operation.</p>
                  {step.custom_hooks ? <p className="text-meta">{step.custom_hooks.path}<br />{step.custom_hooks.events.join(", ") || "No executable hooks found"}</p> : null}
                  <p>Only combine hooks you trust. They and programs they call run with Altitude’s Git permissions, outside the task agent’s sandbox.</p>
                  <div className="setup-actions">
                    <button type="button" className="btn btn-primary" disabled={busy || stale || !step.fingerprint} onClick={() => run("combine", step.fingerprint ?? undefined)}>Use both hook sets</button>
                    <button type="button" className="btn btn-ghost" onClick={() => setIntegration(null)}>Keep current setup</button>
                  </div>
                </div>}
              </> : null}
            </div>
          </li>)}
        </ol>
        {failed ? <div className="setup-help"><p className="text-meta text-muted">Retry runs setup again. Discuss with L3 opens your project conversation.</p><button type="button" className="btn" onClick={() => close(true)}>Discuss with L3</button></div> : null}
        <footer className="setup-footer">
          {data?.checked_at ? <p className="text-meta text-muted">{stale ? "Last observed" : "Checked"} {new Date(data.checked_at).toLocaleString()}</p> : null}
          <div className="setup-actions">
            <button type="button" className="btn" disabled={busy || setup.isPending} onClick={() => run("check")}>Check again</button>
            <button type="button" className="btn btn-primary" onClick={() => close(true)}>Open conversation</button>
          </div>
          <p className="text-meta text-muted">Closing this panel does not cancel setup.</p>
        </footer>
      </section>
    </Overlay> : null}
  </>;
}
