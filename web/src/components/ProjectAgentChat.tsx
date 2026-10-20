import { useState } from "react";
import { Link } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { pressId, sendProjectAgent, useProjectAgents } from "../data/api";
import type { ChatMessage, ProjectAgentOffer } from "../data/api";
import { refusal, zoned } from "../data/projectAgents";
import { ClampedBrief } from "./ProjectAgentParts";
import { InlineProse, Prose } from "./Prose";
import { BusyLabel } from "./StatusMark";
import { Stamp } from "./Stamp";

/** Pages opened from chat keep the Chat tab lit. */
const fromChat = { from: "project", tab: "chat" };

/*
 * Agents in the project chat (design/wireframes/project-agents/PROPOSAL.md, "In chat"): a run's result is a quiet
 * system line led by the agent's name with Open, and L3's offer to create or change an agent is a card under its reply
 * that only the operator's press saves.
 */

/** A result line: its dot by outcome, the text Altitude wrote from the run record, and Open to the run or agent. */
export function ProjectAgentLine({ project, row }: { project: string; row: ChatMessage }) {
  const ref = row.project_agent;
  const to = ref ? `/projects/${project}/agents/${ref.agent}${ref.n != null ? `/runs/${ref.n}` : ""}` : null;
  return <div className="sys-line agent-line">
    <Stamp at={row.at} className="sys-time" />
    <span className="sys-text">
      <span className="sys-dot" data-tone={ref?.tone === "muted" ? undefined : ref?.tone} aria-hidden />
      <InlineProse text={row.text} />
      {to ? <>{" "}<Link className="link" to={to} state={fromChat}>Open</Link></> : null}
    </span>
  </div>;
}

/** The offer card: Create agent or Save change spins until the project reads the offer as saved, then says so in place. */
export function ProjectAgentOfferCard({ project, offer }: { project: string; offer: ProjectAgentOffer }) {
  const client = useQueryClient();
  const clock = useProjectAgents(project).data?.clock;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One press per card: pressing again after a failure repeats it, so a lost answer does not save twice.
  const [requestId] = useState(pressId);
  const press = async () => {
    setBusy(true);
    setError(null);
    try {
      await sendProjectAgent(project, "offer", { offer: offer.id }, requestId);
    } catch (failure) {
      setError((failure as Error).message);
    }
    // A refused or lapsed offer is saved as such, so the card reads its new state either way.
    await client.refetchQueries({ queryKey: ["project", project] }).catch(() => undefined);
    setBusy(false);
  };
  const isNew = offer.kind === "new";
  const edit = isNew ? `/projects/${project}/agents/new?offer=${offer.id}` : `/projects/${project}/agents/${offer.agent}/edit?offer=${offer.id}`;
  const result = offer.result_agent ?? offer.agent;
  return <div className="agent-offer" data-offer={offer.id}>
    <small className="text-muted">{isNew ? "New agent" : `Change to ${offer.agent_name ?? offer.definition.name}`}</small>
    <strong className="agent-offer-name">{offer.definition.name}</strong>
    <small className="text-muted">{zoned(offer.line, clock)}{isNew && offer.on ? " · On when created" : ""}</small>
    {isNew ? <ClampedBrief text={offer.definition.brief} /> : <>
      <ul className="agent-offer-changes">{offer.changes.map((change) => <li key={change}><InlineProse text={change} /></li>)}</ul>
      {offer.changes.includes("New brief") ? <div className="agent-brief"><Prose text={offer.definition.brief} /></div> : null}
    </>}
    {offer.state === "open" ? <div className="agent-offer-actions">
      <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void press()}>
        <BusyLabel busy={busy} label={isNew ? "Create agent" : "Save change"} working={isNew ? "Creating the agent…" : "Saving the change…"} />
      </button>
      <Link className="btn" to={edit} state={fromChat} aria-disabled={busy || undefined} onClick={(event) => { if (busy) event.preventDefault(); }}>Edit first</Link>
    </div> : null}
    {offer.state === "open" && error ? <p role="alert" className="text-meta text-danger">{refusal("Not saved", error)}</p> : null}
    {offer.state === "created" || offer.state === "saved" ? <p className="text-meta agent-offer-done">
      {offer.state === "created" ? "Created" : "Saved"}{result ? <>{" · "}<Link className="link" to={`/projects/${project}/agents/${result}`} state={fromChat}>Open</Link></> : null}
    </p> : null}
    {offer.state === "refused" ? <p className="text-meta text-danger">{offer.reason ? refusal("Not saved", offer.reason) : "Not saved."}</p> : null}
    {offer.state === "lapsed" ? <p className="text-meta text-muted">This offer lapsed.</p> : null}
  </div>;
}
