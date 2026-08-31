import type { CSSProperties } from "react";
import { useMonitor } from "../data/api";
import type { Quota, Session } from "../data/api";

/**
 * Seat quota, one card per live session, and the raw list of
 * Claude worker processes. `/api/monitor` rows are passthrough, so the columns that
 * only some kinds carry (cwd, l1_runs, edits, agent, rotate_next)
 * arrive typed `unknown` and are narrowed here rather than in api.ts.
 */
function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function dict(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function arr(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** "5m", "3h", "2d" — empty when the timestamp is missing or unparseable. */
function age(value: unknown): string {
  if (typeof value !== "string" || !value) return "";
  const then = Date.parse(value);
  if (Number.isNaN(then)) return "";
  const seconds = (Date.now() - then) / 1000;
  if (seconds < 0) return "";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86_400)}d`;
}

/** Context/quota meter. Past the reserve line it turns danger-red. */
function Bar({ percent }: { percent: number }) {
  const p = Math.max(0, Math.min(100, percent));
  const style: CSSProperties = { width: `${p}%` };
  if (p >= 70) style.background = "var(--danger)";
  return (
    <div className="bar">
      <div className="bar-fill" style={style} />
    </div>
  );
}

function QuotaCard({ quota }: { quota: Quota }) {
  const five = num(quota.five_hour);
  const seven = num(quota.seven_day);
  if (!quota.known || five == null || seven == null) {
    return (
      <section className="card space-y-1">
        <h2 className="label">Seat quota</h2>
        <p className="text-meta text-muted">
          unknown — needs the statusline wrapper (<code>alt install-statusline</code>) and one
          interactive session
        </p>
      </section>
    );
  }
  return (
    <section className="card space-y-2">
      <h2 className="label">Seat quota</h2>
      <p className="text-meta text-muted">5-hour window {Math.round(five)}%</p>
      <Bar percent={five} />
      <p className="text-meta text-muted">7-day {Math.round(seven)}%</p>
      <Bar percent={seven} />
    </section>
  );
}

function SessionCard({ session }: { session: Session }) {
  const project = str(session.project);
  const slug = str(session.slug);
  const cwd = str(session["cwd"]);
  const context = num(session.context_percent);
  const agent = dict(session["agent"]);
  const when = age(session.at);

  const meta: string[] = [`context ${context ?? "?"}%`];
  if (session["l1_runs"] != null) {
    meta.push(`L1 runs ${num(session["l1_runs"]) ?? 0}`);
    meta.push(`edits ${num(session["edits"]) ?? 0}`);
  }
  const status = [str(agent["status"]), str(agent["state"])].filter(Boolean).join(" ");
  if (status) meta.push(status);
  if (str(session.context_state)) meta.push(str(session.context_state));
  if (session["rotate_next"]) meta.push("rotating");

  return (
    <article className="card space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="pill">{session.kind}</span>
        <b className="grow text-body">
          {project}
          {slug ? ` / ${slug}` : ""}
        </b>
        {when ? <span className="text-meta text-muted">{when}</span> : null}
      </div>
      {cwd ? <p className="truncate text-meta text-muted">{cwd}</p> : null}
      <p className="text-meta text-muted">{meta.join(" · ")}</p>
      <Bar percent={context ?? 0} />
    </article>
  );
}

export default function Monitor() {
  const monitor = useMonitor();
  if (monitor.isPending) return <p className="text-muted">Loading…</p>;
  if (monitor.isError) return <p className="text-danger">{monitor.error.message}</p>;

  const { quota, sessions } = monitor.data;
  const workers = arr(monitor.data.agents)
    .map(dict)
    .map((a) =>
      `${str(a["id"]).slice(0, 8)} ${str(a["name"])} ${str(a["status"])} ${str(a["state"])} ${str(a["cwd"])}`.trimEnd(),
    );

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-page-title font-semibold">Monitor</h1>

      <QuotaCard quota={quota} />

      <section className="space-y-2">
        <h2 className="label">Sessions ({sessions.length})</h2>
        {sessions.length === 0 ? (
          <p className="card text-muted">no sessions</p>
        ) : (
          sessions.map((session, i) => (
            <SessionCard key={`${session.kind}-${str(session.session_id)}-${i}`} session={session} />
          ))
        )}
      </section>

      <section className="space-y-2">
        <h2 className="label">Claude workers</h2>
        <pre className="card overflow-x-auto whitespace-pre-wrap text-meta text-ink-2">
          {workers.length > 0 ? workers.join("\n") : "none"}
        </pre>
      </section>

    </div>
  );
}
