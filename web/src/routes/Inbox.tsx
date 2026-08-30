import { useState } from "react";
import { Link } from "react-router";
import { useDecide, useOverview } from "../data/api";
import type { Decision, Fyi, Wip } from "../data/api";

/** "5m", "3h", "2d" — empty string when the timestamp is missing or unparseable. */
function age(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const minutes = Math.floor((Date.now() - then) / 60_000);
  if (minutes < 0) return "";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

function DecisionCard({ d }: { d: Decision }) {
  const [note, setNote] = useState("");
  const decide = useDecide();
  const options =
    d.options ?? (d.kind === "blocked" ? ["Resume", "Park", "Reject"] : ["Approve", "Revise", "Reject"]);
  const blocked = d.kind === "blocked";
  return (
    <article className={`card space-y-3 ${blocked ? "border-danger/40" : ""}`}>
      <div className="flex items-center gap-2">
        {d.class ? <span className="pill">{d.class}</span> : null}
        <h3 className="text-card-title font-semibold">{d.title || d.slug}</h3>
        <span className="ml-auto text-meta text-muted">
          {d.project}
          {age(d.asked) ? ` · ${age(d.asked)}` : ""}
        </span>
      </div>
      {d.question ? <p className="text-body text-ink-2">{d.question}</p> : null}
      <input
        className="field w-full"
        placeholder="Note (optional)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        aria-label={`Note for ${d.title || d.slug}`}
      />
      <div className="flex flex-wrap items-center gap-2">
        {options.map((label, i) => (
          <button
            key={label}
            type="button"
            className={i === 0 ? "btn btn-primary" : "btn"}
            disabled={decide.isPending}
            onClick={() =>
              decide.mutate({ project: d.project, slug: d.slug, option: i, note: note || undefined })
            }
          >
            {label}
          </button>
        ))}
        <Link className="ml-auto text-meta" to={`/projects/${d.project}/tasks/${d.slug}`}>
          Details
        </Link>
      </div>
    </article>
  );
}

function Running({ wip }: { wip: Wip }) {
  const perProject = Object.entries(wip.per_project).filter(([, n]) => n > 0);
  return (
    <section className="space-y-2">
      <h2 className="label">Running</h2>
      <p className="text-body text-ink-2">
        {wip.machine} task{wip.machine === 1 ? "" : "s"} on the machine
        {wip.limit_machine != null ? ` (limit ${wip.limit_machine})` : ""}
      </p>
      {perProject.length > 0 ? (
        <ul className="text-meta text-muted">
          {perProject.map(([project, n]) => (
            <li key={project}>
              {project}: {n}
            </li>
          ))}
        </ul>
      ) : null}
      {wip.waiting.length > 0 ? (
        <p className="text-meta text-muted">
          Waiting:{" "}
          {wip.waiting
            .map((w) => `${w.project}/${w.slug}${w.why === "resume" ? " (resume)" : ""}`)
            .join(", ")}
        </p>
      ) : null}
    </section>
  );
}

function FyiRow({ fyi }: { fyi: Fyi }) {
  return (
    <li className="flex items-baseline gap-2 text-body">
      <span className="text-meta text-muted">
        [{fyi.project}{fyi.slug ? ` · ${fyi.slug}` : ""}]
      </span>
      <span className="text-ink-2">{fyi.text}</span>
      {age(fyi.at) ? <span className="ml-auto text-meta text-muted">{age(fyi.at)}</span> : null}
    </li>
  );
}

export default function Inbox() {
  const overview = useOverview();
  if (overview.isPending) return <p className="text-muted">Loading…</p>;
  if (overview.isError) return <p className="text-danger">{overview.error.message}</p>;
  const { queue, fyis, wip, quota } = overview.data;
  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header className="flex items-baseline gap-3">
        <h1 className="text-page-title font-semibold">Inbox</h1>
        <span className="text-meta text-muted">
          {quota.known && quota.five_hour != null && quota.seven_day != null
            ? `5h ${Math.round(quota.five_hour)}% · 7d ${Math.round(quota.seven_day)}%`
            : "quota unknown"}
        </span>
      </header>
      <section className="space-y-3">
        <h2 className="label">Decisions ({queue.length})</h2>
        {queue.length === 0 ? (
          <p className="text-muted">Nothing needs you.</p>
        ) : (
          queue.map((d) => <DecisionCard key={`${d.project}/${d.slug}`} d={d} />)
        )}
      </section>
      <Running wip={wip} />
      <section className="space-y-2">
        <h2 className="label">FYI</h2>
        {fyis.length === 0 ? (
          <p className="text-muted">No FYIs yet.</p>
        ) : (
          <ul className="space-y-1">
            {fyis.map((f, i) => (
              <FyiRow key={`${f.project}-${f.at ?? i}`} fyi={f} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
