import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { sendImprovementSetting, useImprovementSettings } from "../data/api";
import type { ImprovementSettings } from "../data/api";
import { Overlay } from "../shell/Overlay";
import { useViewport } from "../shell/breakpoints";
import { ImprovementRunning } from "./Improvement";
import { BusyLabel, StatusMark } from "./StatusMark";
import { UnavailableAction } from "./UnavailableAction";

/*
 * Improvement suggestions in a project's settings (design/wireframes/improvement-review/PROPOSAL.md, "Turning it
 * on"): the Weekly switch, Get suggestions now, what is sent and Past suggestions. Without the operator's agreement to the current
 * terms either control opens the sheet first; nothing saves or runs until Turn on, which agrees and then completes
 * the press. Every line is read from the record.
 */

type Action = "weekly" | "run";
type Terms = NonNullable<ImprovementSettings["terms"]>;

/** The sheet: what is sent and how much of the plan it uses. Cancel, Escape or the scrim change nothing. */
function Sheet({ terms, onAgree, onClose }: {
  terms: Terms; onAgree: () => void; onClose: () => void;
}) {
  const { phone } = useViewport();
  return <Overlay label={terms.title} side={phone ? "bottom" : "center"} onClose={onClose}>
    <div className="remove-dialog improve-sheet">
      <h2>{terms.title}</h2>
      <p>{terms.body}</p>
      <div className="remove-actions">
        <button type="button" className="btn" data-autofocus onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" onClick={onAgree}>Turn on</button>
      </div>
    </div>
  </Overlay>;
}

export function ImprovementSection({ name }: { name: string }) {
  const settings = useImprovementSettings(name);
  const client = useQueryClient();
  // The open sheet keeps the terms it showed: a refetch behind it never changes its text or the version Turn on sends.
  const [sheet, setSheet] = useState<{ action: Action; terms: Terms } | null>(null);
  const [saving, setSaving] = useState<Action | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = settings.data;
  const { hash } = useLocation();
  const shown = Boolean(data?.available);
  // The chat's terms line links here; the section loads after the page's other settings.
  useEffect(() => {
    if (shown && hash === "#improvement") document.getElementById("improvement")?.scrollIntoView();
  }, [shown, hash]);
  if (settings.isError) return <section className="settings-section" aria-labelledby="settings-improvement">
    <h2 id="settings-improvement">Improvement suggestions</h2>
    <p role="alert" className="text-meta text-danger">Could not load. <button type="button" className="link" onClick={() => void settings.refetch()}>Retry</button></p>
  </section>;
  if (!data?.available || !data.terms) return null;
  const send = async (action: Action, terms?: string) => {
    setSheet(null);
    setError(null);
    setSaving(action);
    try {
      await sendImprovementSetting(name, action === "weekly" ? { action, on: !data.weekly } : { action }, terms);
    } catch (failure) {
      setError((failure as Error).message);
    }
    // A refusal shows what is stored now: the switch returns to its saved value.
    await client.refetchQueries({ queryKey: ["project", name] }).catch(() => undefined);
    setSaving(null);
  };
  // Turning Weekly off needs no agreement; turning it on or running does, so the sheet comes first.
  const press = (action: Action) => (action === "weekly" && data.weekly) || data.agreed ? void send(action) : setSheet({ action, terms: data.terms! });
  const weekly = saving === "weekly" ? !data.weekly : Boolean(data.weekly);
  return <section className="settings-section" id="improvement" aria-labelledby="settings-improvement">
    <div><h2 id="settings-improvement">Improvement suggestions</h2>
      <p className="text-meta text-muted">Finds friction that keeps coming back in this project's tasks and suggests fixes.</p></div>
    <div className="settings-group">
      <div className="settings-row settings-switch-row">
        <label htmlFor="improvement-weekly"><strong>Weekly</strong>{" "}<small id="improvement-weekly-line">{data.line}</small></label>
        <input id="improvement-weekly" type="checkbox" role="switch" className="settings-switch" checked={weekly}
          aria-describedby="improvement-weekly-line" disabled={saving !== null} onChange={() => press("weekly")} />
      </div>
      {data.running ? <div className="settings-row improve-running"><ImprovementRunning project={name} run={data.running} /></div>
        : data.now ? <div className="settings-row improve-now">
          {data.now.unavailable ? <span className="improve-unavailable"><UnavailableAction label="Get suggestions now" reason={data.now.unavailable} /></span>
            : <>
              <button type="button" className="btn" disabled={saving !== null} onClick={() => press("run")}>
                <BusyLabel busy={saving === "run"} label="Get suggestions now" working="Starting…" />
              </button>
              <small>{data.now.line}</small>
            </>}
        </div> : null}
      <p className="settings-row improve-sent"><small>{data.terms.sent}</small></p>
      {data.past ? <p className="settings-row improve-past"><small>{data.past.line}{data.past.latest ? <>
        {" · "}<Link to={`/projects/${encodeURIComponent(name)}#improvement-${data.past.latest}`}>Latest</Link></> : null}</small></p> : null}
    </div>
    {saving === "weekly" ? <StatusMark label="Saving…" /> : null}
    {error ? <p role="alert" className="text-meta text-danger">{error}</p> : null}
    {sheet ? <Sheet terms={sheet.terms} onAgree={() => void send(sheet.action, sheet.terms.version)} onClose={() => setSheet(null)} /> : null}
  </section>;
}
