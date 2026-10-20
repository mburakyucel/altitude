import { useState } from "react";
import { Link, useLocation, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { sendProjectAgent, sendWeekly, useProjectAgents, useSaveAgentBudget } from "../data/api";
import type { AgentClock, ProjectAgentSummary, ProjectAgents, WeeklyTerms } from "../data/api";
import { durationText, newsAgo, refusal, zoned } from "../data/projectAgents";
import { AgentFrame, Chevron, PauseMark, WeeklyTermsSheet } from "../components/ProjectAgentParts";
import { BusyLabel, StatusMark } from "../components/StatusMark";

/*
 * All agents of a project (design/wireframes/project-agents/PROPOSAL.md, "All agents"): one card of whole-row links,
 * the weekly budget and New agent, the retired agents behind their count, then the built-in agents to turn on. In
 * Altitude's own project the Weekly improvement review leads the agents once it is the operator's, else Built in.
 */

function AgentRow({ project, agent, clock }: { project: string; agent: ProjectAgentSummary; clock: AgentClock }) {
  const line = agent.retired_line ?? `${zoned(agent.triggers, clock)} · ${agent.news}${agent.news_at ? ` ${newsAgo(agent.news_at)}` : ""}`;
  return <Link className="settings-row agents-row" to={`/projects/${project}/agents/${agent.id}`}>
    <span><strong>{agent.name}</strong>
      <small className="agents-row-line">
        {agent.cue === "running" ? <span className="dot" data-state="running" data-pulse aria-hidden /> : agent.cue === "paused" ? <PauseMark /> : null}
        {line}
      </small>
    </span>
    <Chevron />
  </Link>;
}

/** The weekly budget: a choice shows at once with Saving…, a refusal returns to the saved value with Retry. */
function Budget({ project, budget }: { project: string; budget: ProjectAgents["budget"] }) {
  const save = useSaveAgentBudget(project);
  const used = `${durationText(budget.used)} used this week`;
  return <>
    <div className="settings-row agents-budget">
      <label htmlFor="agents-budget"><strong>Agents may use up to</strong>{" "}
        {save.isPending ? <small className="agents-saving" role="status"><span className="spinner" aria-hidden />Saving…</small>
          : budget.spent ? <small className="agents-spent">{used} · agents wait until Monday</small>
            : <small>{used}</small>}
      </label>
      <select id="agents-budget" className="settings-select" value={budget.minutes} disabled={save.isPending}
        onChange={(event) => save.mutate({ minutes: Number(event.target.value) })}>
        {budget.choices.map((minutes) => <option key={minutes} value={minutes}>{durationText(minutes)} a week</option>)}
      </select>
    </div>
    {save.error ? <p role="alert" className="text-meta text-danger agents-note">{refusal("Not saved", save.error.message)}{" "}
      <button type="button" className="link" onClick={() => save.variables && save.mutate(save.variables)}>Retry</button></p> : null}
  </>;
}

/** Built in: Turn on spins until the agent reads as on, when its row leaves; a refusal returns the button with why.
 * The Weekly improvement review's Turn on shows its terms first and agrees to the version shown. */
function BuiltIn({ project, builtins, terms, clock }: { project: string; builtins: ProjectAgents["builtins"]; terms?: WeeklyTerms; clock: AgentClock }) {
  const client = useQueryClient();
  const [turning, setTurning] = useState<string | null>(null);
  const [refused, setRefused] = useState<{ key: string; message: string } | null>(null);
  // The open sheet keeps the terms it showed: a read behind it never changes its text or the version Turn on sends.
  const [sheet, setSheet] = useState<WeeklyTerms | null>(null);
  const turnOn = async (key: string, agreed?: string) => {
    setSheet(null);
    setTurning(key);
    setRefused(null);
    try {
      await (key === "weekly" ? sendWeekly(project, { action: "weekly", on: true }, agreed) : sendProjectAgent(project, "builtin", { key }));
      await client.refetchQueries({ queryKey: ["project", project, "agents"], exact: true });
    } catch (failure) {
      setRefused({ key, message: (failure as Error).message });
    }
    setTurning(null);
  };
  if (!builtins.length) return null;
  return <section className="settings-section" aria-labelledby="agents-builtin">
    <div><h2 id="agents-builtin">Built in</h2><p className="text-meta text-muted">Ready to turn on; each becomes yours to change.</p></div>
    <div className="settings-group">
      {builtins.map((item) => <div key={item.key} className="settings-row agents-builtin-row">
        <span><strong>{item.name}</strong><small>{zoned(item.line, clock)}</small>
          {refused?.key === item.key ? <small role="alert" className="text-danger">{refusal("Not turned on", refused.message)}</small> : null}
        </span>
        <button type="button" className="btn" disabled={turning !== null} aria-haspopup={item.key === "weekly" ? "dialog" : undefined}
          onClick={() => item.key === "weekly" && terms ? setSheet(terms) : void turnOn(item.key)}>
          <BusyLabel busy={turning === item.key} label="Turn on" working={`Turning on ${item.name}`} />
        </button>
      </div>)}
    </div>
    {sheet ? <WeeklyTermsSheet terms={sheet} onAgree={() => void turnOn("weekly", sheet.version)} onClose={() => setSheet(null)} /> : null}
  </section>;
}

export default function ProjectAgentsPage() {
  const { name = "" } = useParams();
  const agents = useProjectAgents(name);
  const [retired, setRetired] = useState(false);
  const data = agents.data;
  const weekly = data?.weekly;
  // Back returns to the tab that opened the agents: Work unless chat did.
  const tab = (useLocation().state as { tab?: string } | null)?.tab === "chat" ? "" : "?tab=work";
  return <AgentFrame back={{ to: `/projects/${name}${tab}`, label: name }} title="Agents">
    <p className="text-meta text-muted agents-intro">Each one runs a brief when its trigger fires, and tells you what it found.</p>
    {agents.isPending ? <div className="settings-card"><StatusMark label="Loading agents…" /></div>
      : !data ? <div className="settings-card" role="alert"><p>Could not load agents.</p>
        <button type="button" className="btn agents-retry" onClick={() => void agents.refetch()}>Retry</button></div>
        : <>
          <div className="settings-group">
            {[...weekly?.agent ? [weekly.agent] : [], ...data.agents].map((agent) => <AgentRow key={agent.id} project={name} agent={agent} clock={data.clock} />)}
            <Budget project={name} budget={data.budget} />
            <div className="settings-row agents-new"><Link className="btn" to={`/projects/${name}/agents/new`}>New agent</Link></div>
          </div>
          {data.retired.length ? <button type="button" className="link agents-link" aria-expanded={retired} onClick={() => setRetired(!retired)}>
            {data.retired.length} retired</button> : null}
          {retired && data.retired.length ? <section className="settings-section" aria-labelledby="agents-retired">
            <h2 id="agents-retired">Retired agents</h2>
            <div className="settings-group">{data.retired.map((agent) => <AgentRow key={agent.id} project={name} agent={agent} clock={data.clock} />)}</div>
          </section> : null}
          <BuiltIn project={name} builtins={[...weekly?.builtin ? [weekly.builtin] : [], ...data.builtins]} terms={weekly?.terms} clock={data.clock} />
        </>}
  </AgentFrame>;
}
