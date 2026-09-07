import type { CSSProperties } from "react";
import { useMonitor } from "../data/api";
import type { CodexQuota, Quota, RoutingRow, Session } from "../data/api";
import { asOf, engineName, older, RESERVE_PERCENT, SESSION_STALE_MS, when } from "../data/observed";

/**
 * Both seats' quota side by side, which engine a new turn would get, one card per live session, and
 * the raw list of Claude worker processes. Everything here is display: nothing on this page decides
 * anything. `/api/monitor` rows are passthrough, so the columns that only some kinds carry (cwd,
 * edits, agent, rotate_next) arrive typed `unknown` and are narrowed here rather than in api.ts.
 *
 * Age is the point of the page. A figure with no data at all is unknown and says how to get it; a
 * figure whose snapshot has aged past what the router itself trusts is stale — shown, dimmed, and
 * labelled, never silently passed off as current.
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

/** "2h 10m", "45m", "3d 4h" — a span in human units, never seconds. */
function span(ms: number): string {
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours}h ${minutes % 60}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days}d ${hours % 24}h` : `${days}d`;
}

/** "resets in 2h 10m (Thu 21:30)" — relative first, the clock time in parentheses. */
function resetText(value: unknown): string {
  const at = when(value);
  if (at == null) return "";
  const clock = new Date(at).toLocaleString(undefined, {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
  });
  const left = at - Date.now();
  return left > 0 ? `resets in ${span(left)} (${clock})` : `reset due (${clock})`;
}

/** A provider window named by the length it reports: 300 → "5-hour", 10080 → "7-day". */
function windowName(minutes: unknown): string {
  const value = num(minutes);
  if (value == null || value <= 0) return "window";
  if (value % 1440 === 0) return `${value / 1440}-day`;
  if (value % 60 === 0) return `${value / 60}-hour`;
  return `${value}-minute`;
}

function capitalize(text: string): string {
  return text ? text[0]?.toUpperCase() + text.slice(1) : text;
}

/** Context/quota meter. Past the reserve line it turns danger-red; a stale figure is dimmed. */
function Bar({ percent, stale }: { percent: number; stale?: boolean }) {
  const p = Math.max(0, Math.min(100, percent));
  const style: CSSProperties = { width: `${p}%` };
  if (p >= RESERVE_PERCENT) style.background = "var(--danger)";
  return (
    <div className={`bar${stale ? " opacity-50" : ""}`}>
      <div className="bar-fill" style={style} />
    </div>
  );
}

function Window({
  name,
  percent,
  resets,
  stale,
}: {
  name: string;
  percent: number;
  resets: string;
  stale: boolean;
}) {
  return (
    <div className="space-y-1">
      <p className={`text-meta ${stale ? "text-muted" : "text-ink-2"}`}>
        {name} {Math.round(percent)}%
      </p>
      <Bar percent={percent} stale={stale} />
      {resets ? <p className="text-meta text-muted">{resets}</p> : null}
    </div>
  );
}

function ProviderHead({
  name,
  plan,
  observed,
  stale,
}: {
  name: string;
  plan?: string;
  observed: string;
  stale: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <h3 className="grow text-card-title">{name}</h3>
      {plan ? <span className="pill">{capitalize(plan)}</span> : null}
      {stale ? <span className="pill">stale</span> : null}
      {observed ? <span className="text-meta text-muted">{observed}</span> : null}
    </div>
  );
}

function ClaudeCard({ quota }: { quota: Quota }) {
  const five = num(quota.five_hour);
  const seven = num(quota.seven_day);
  const missing = five == null || seven == null;
  const stale = !missing && !quota.known;
  return (
    <section className="card space-y-3">
      <ProviderHead name="Claude" observed={missing ? "" : asOf(quota.at)} stale={stale} />
      {missing ? (
        <p className="text-meta text-muted">
          unknown — needs the statusline wrapper (<code>alt install-statusline</code>) and one
          interactive session
        </p>
      ) : (
        <>
          <Window
            name="5-hour"
            percent={five}
            resets={resetText(quota.five_hour_resets)}
            stale={stale}
          />
          <Window
            name="7-day"
            percent={seven}
            resets={resetText(quota.seven_day_resets)}
            stale={stale}
          />
        </>
      )}
    </section>
  );
}

function CodexCard({ quota }: { quota: CodexQuota | null | undefined }) {
  const primary = num(quota?.primary_used);
  const secondary = num(quota?.secondary_used);
  const stale = primary != null && !quota?.known;
  return (
    <section className="card space-y-3">
      <ProviderHead
        name="Codex"
        plan={str(quota?.plan_type)}
        observed={primary == null ? "" : asOf(quota?.read_at)}
        stale={stale}
      />
      {primary == null ? (
        <p className="text-meta text-muted">
          unknown — {str(quota?.why) || "the Codex seat has not been read yet"}
        </p>
      ) : (
        <>
          <Window
            name={windowName(quota?.primary_window_minutes)}
            percent={primary}
            resets={resetText(quota?.primary_resets)}
            stale={stale}
          />
          {secondary == null ? (
            <p className="text-meta text-muted">no second window reported</p>
          ) : (
            <Window
              name={windowName(quota?.secondary_window_minutes)}
              percent={secondary}
              resets={resetText(quota?.secondary_resets)}
              stale={stale}
            />
          )}
        </>
      )}
    </section>
  );
}

/** "L3 · altitude (Auto)", "L2 · new task" — who the row answers for. */
function roleLabel(row: RoutingRow): string {
  if (row.role !== "l3") return "L2 · new task";
  const pin = str(row.pin);
  return `L3 · ${str(row.project)} (${pin ? `pinned to ${engineName(pin)}` : "Auto"})`;
}

function RoutingCard({ rows }: { rows: RoutingRow[] }) {
  return (
    <section className="card space-y-2">
      <h2 className="label">Routing now</h2>
      {rows.length === 0 ? (
        <p className="text-meta text-muted">no roles to route</p>
      ) : (
        rows.map((row, i) => (
          <p key={`${row.role}-${str(row.project)}-${i}`} className="text-meta text-muted">
            <span className="text-body text-ink-2">{roleLabel(row)}</span> →{" "}
            <span className={row.engine ? "text-ink-2" : "text-danger"}>
              {engineName(row.engine) || "no engine"}
            </span>
            {row.why ? ` — ${row.why}` : ""}
          </p>
        ))
      )}
    </section>
  );
}

function SessionCard({ session }: { session: Session }) {
  const project = str(session.project);
  const slug = str(session.slug);
  const cwd = str(session["cwd"]);
  const context = num(session.context_percent);
  const agent = dict(session["agent"]);
  const observed = asOf(session.at);
  // A worker that is running should report every few minutes; anything else is as old as it says.
  const stale = str(session.state) === "running" && older(session.at, SESSION_STALE_MS);

  const meta: string[] = [`context ${context == null ? "?" : Math.round(context)}%`];
  if (session["edits"] != null) meta.push(`edits ${num(session["edits"]) ?? 0}`);
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
        {stale ? <span className="pill">stale</span> : null}
        {observed ? <span className="text-meta text-muted">{observed}</span> : null}
      </div>
      {cwd ? <p className="truncate text-meta text-muted">{cwd}</p> : null}
      <p className="text-meta text-muted">{meta.join(" · ")}</p>
      <Bar percent={context ?? 0} stale={stale} />
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

      <section className="space-y-2">
        <h2 className="label">Seat quota</h2>
        <div className="grid gap-3 md:grid-cols-2">
          <ClaudeCard quota={quota} />
          <CodexCard quota={monitor.data.quota_codex} />
        </div>
      </section>

      <RoutingCard rows={monitor.data.routing ?? []} />

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
