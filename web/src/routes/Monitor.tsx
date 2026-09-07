import { useMonitor, useOverview } from "../data/api";
import type { MonitorView, RoutingRow, Session } from "../data/api";
import { age, agoText, exactTime, modelName, older, RESERVE_PERCENT, SESSION_STALE_MS, when } from "../data/observed";

/**
 * Monitor (SPEC.md §3.14): one seat card per configured engine with both windows, their reset times
 * in human terms, the 70% reserve line and the age of the reading; where each role would go right now
 * and why; the sessions the monitor knows, each with its task. Everything here is display: nothing on
 * this page decides anything, and it reads `/api/monitor` and the shared overview only.
 *
 * Engine names come from the overview's `engines[]` rows, the seam's own labels. `/api/monitor` names
 * its two readings by seat rather than by engine row, so SEAT_FIELDS is the one place the page ties a
 * reading to an engine key; nothing here spells a display name.
 *
 * Age is the point of the page. A figure with no data at all says "No reading" and how to get one; a
 * figure whose snapshot has aged past what the router itself trusts is stale: shown, dimmed, labelled.
 * `/api/monitor` session rows are passthrough, so the fields only some kinds carry (cwd, edits, agent,
 * model, rotate_next) arrive typed `unknown` and are narrowed here rather than in api.ts.
 */
const SEAT_FIELDS: Record<string, "quota" | "quota_codex"> = { claude: "quota", codex: "quota_codex" };

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

/** "2h 10m", "45m", "3d 4h": a span in human units, never seconds. */
function span(ms: number): string {
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours}h ${minutes % 60}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days}d ${hours % 24}h` : `${days}d`;
}

/** "resets in 2h 10m (Thu 21:30)": relative first, the clock time in parentheses. */
function resetText(value: unknown): string {
  const at = when(value);
  if (at == null) return "";
  const clock = new Date(at).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
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

/** "reading 3m old", the rail's own words for the age of a reading (SPEC.md §3.1); empty without one. */
function readingAge(at: unknown): string {
  const old = age(at);
  return old ? `reading ${old} old` : "";
}

interface SeatWindow {
  name: string;
  percent: number;
  resets: string;
}

interface Seat {
  engine: string;
  plan: string;
  at: unknown;
  stale: boolean;
  /** Null when the seat has never been read; then `fix` says what produces a reading. */
  windows: SeatWindow[] | null;
  fix: string;
  /** The provider reports one window and no second one: absent, never zero. */
  secondAbsent: boolean;
}

/** The monitor's reading for one configured engine, in one shape whichever seat it comes from. */
export function seatFor(engine: string, data: MonitorView): Seat {
  const field = SEAT_FIELDS[engine];
  if (field === "quota") {
    const quota = data.quota;
    const five = num(quota.five_hour);
    const seven = num(quota.seven_day);
    const missing = five == null || seven == null;
    return {
      engine,
      plan: "",
      at: quota.at,
      stale: !missing && !quota.known,
      windows: missing
        ? null
        : [
            { name: "5-hour", percent: five, resets: resetText(quota.five_hour_resets) },
            { name: "7-day", percent: seven, resets: resetText(quota.seven_day_resets) },
          ],
      fix: "Needs the statusline wrapper (alt install-statusline) and one interactive session.",
      secondAbsent: false,
    };
  }
  if (field === "quota_codex") {
    const quota = data.quota_codex;
    const primary = num(quota?.primary_used);
    const secondary = num(quota?.secondary_used);
    const windows: SeatWindow[] = [];
    if (primary != null) {
      windows.push({ name: windowName(quota?.primary_window_minutes), percent: primary, resets: resetText(quota?.primary_resets) });
      if (secondary != null) {
        windows.push({ name: windowName(quota?.secondary_window_minutes), percent: secondary, resets: resetText(quota?.secondary_resets) });
      }
    }
    return {
      engine,
      plan: str(quota?.plan_type),
      at: quota?.read_at,
      stale: primary != null && !quota?.known,
      windows: primary == null ? null : windows,
      fix: capitalize(str(quota?.why)) || "The seat has not been read yet.",
      secondAbsent: primary != null && secondary == null,
    };
  }
  return { engine, plan: "", at: null, stale: false, windows: null, fix: "", secondAbsent: false };
}

/** A meter with the reserve line drawn; past the line the fill turns danger, a stale figure is dimmed. */
function Meter({ percent, reserve, stale }: { percent: number; reserve?: boolean; stale?: boolean }) {
  const p = Math.max(0, Math.min(100, percent));
  return (
    <div className="meter monitor-meter" data-stale={stale || undefined} aria-hidden>
      <div className="meter-fill" data-danger={p >= RESERVE_PERCENT} style={{ width: `${p}%` }} />
      {reserve ? <span className="meter-reserve" style={{ left: `${RESERVE_PERCENT}%` }} /> : null}
    </div>
  );
}

function Window({ window, stale }: { window: SeatWindow; stale: boolean }) {
  return (
    <div className="monitor-window" data-stale={stale || undefined}>
      <div className="monitor-window-row">
        <span>{window.name}</span>
        <span>{Math.round(window.percent)}%</span>
      </div>
      <Meter percent={window.percent} reserve stale={stale} />
      {window.resets ? <p className="monitor-window-reset">{window.resets}</p> : null}
    </div>
  );
}

function SeatCard({ seat, label }: { seat: Seat; label: string }) {
  return (
    <section className="card monitor-card monitor-seat" aria-label={label}>
      <div className="monitor-row">
        <h3 className="monitor-seat-name">{label}</h3>
        {seat.plan ? <span className="chip">{capitalize(seat.plan)}</span> : null}
        {seat.stale ? <span className="chip chip-stale">Stale</span> : null}
        {seat.windows ? (
          <span className="monitor-age" title={exactTime(seat.at)}>
            {readingAge(seat.at)}
          </span>
        ) : null}
      </div>
      {seat.windows ? (
        seat.windows.map((window) => <Window key={window.name} window={window} stale={seat.stale} />)
      ) : (
        <p className="monitor-muted">No reading. {seat.fix}</p>
      )}
      {seat.secondAbsent ? <p className="monitor-muted">No second window reported.</p> : null}
    </section>
  );
}

/** "L3 · altitude · Auto", "L3 · altitude · pinned to Codex", "L2 · new task": who the row answers for. */
function roleLabel(row: RoutingRow, label: (engine: unknown) => string): string {
  if (row.role !== "l3") return "L2 · new task";
  const pin = str(row.pin);
  return `L3 · ${str(row.project)} · ${pin ? `pinned to ${label(pin)}` : "Auto"}`;
}

function RoutingCard({ rows, label }: { rows: RoutingRow[]; label: (engine: unknown) => string }) {
  return (
    <section className="monitor-section" aria-labelledby="monitor-routing">
      <h2 id="monitor-routing" className="monitor-head">
        Routing now
      </h2>
      <div className="card monitor-card">
        {rows.length === 0 ? (
          <p className="monitor-muted">No roles to route.</p>
        ) : (
          <ul className="monitor-routes">
            {rows.map((row, i) => (
              <li key={`${row.role}-${str(row.project)}-${i}`} className="monitor-route">
                <div className="monitor-route-row">
                  <span className="monitor-route-role">{roleLabel(row, label)}</span>
                  <span className={row.engine ? "monitor-route-engine" : "monitor-route-engine text-danger"}>
                    {row.engine ? label(row.engine) : "No engine"}
                  </span>
                </div>
                {row.why ? <p className="monitor-route-why">{row.why}</p> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

/** "L2", "L3", "Statusline": the kind the monitor reports, in sentence case. */
function kindLabel(kind: string): string {
  return /^l[23]$/.test(kind) ? kind.toUpperCase() : capitalize(kind);
}

function SessionRow({ session, label }: { session: Session; label: (engine: unknown) => string }) {
  const project = str(session.project);
  const slug = str(session.slug);
  const cwd = str(session["cwd"]);
  const model = str(session["model"]);
  const context = num(session.context_percent);
  const agent = dict(session["agent"]);
  // A worker that is running should report every few minutes; anything else is as old as it says.
  const stale = str(session.state) === "running" && older(session.at, SESSION_STALE_MS);
  const title = project ? `${project}${slug ? ` / ${slug}` : ""}` : cwd;

  const meta: string[] = [];
  if (session.engine) meta.push(label(session.engine));
  if (model) meta.push(modelName(model));
  meta.push(context == null ? "context unknown" : `context ${Math.round(context)}%`);
  if (session["edits"] != null) meta.push(`edits ${num(session["edits"]) ?? 0}`);
  const status = [str(agent["status"]), str(agent["state"])].filter(Boolean).join(" ");
  if (status) meta.push(status);
  if (str(session.context_state)) meta.push(str(session.context_state));
  if (session["rotate_next"]) meta.push("rotating");
  const observed = agoText(session.at);

  return (
    <li className="card monitor-card monitor-session" data-stale={stale || undefined}>
      <div className="monitor-row">
        <span className="chip">{kindLabel(session.kind)}</span>
        <b className="monitor-session-title" title={title}>
          {title}
        </b>
        {stale ? <span className="chip chip-stale">Stale</span> : null}
        {observed ? (
          <span className="monitor-age" title={exactTime(session.at)}>
            {observed}
          </span>
        ) : null}
      </div>
      <p className="monitor-muted">{meta.join(" · ")}</p>
      {context == null ? null : <Meter percent={context} stale={stale} />}
    </li>
  );
}

function Skeleton() {
  return (
    <div className="page-body" aria-label="Loading">
      <div className="monitor-seats">
        <div className="skeleton h-36" />
        <div className="skeleton h-36" />
      </div>
      <div className="skeleton h-28" />
      <div className="skeleton h-20" />
      <div className="skeleton h-20" />
    </div>
  );
}

export default function Monitor() {
  const monitor = useMonitor();
  const overview = useOverview();
  const engines = overview.data?.engines ?? [];
  const labels = new Map(engines.map((row) => [row.engine, row.label]));
  const label = (engine: unknown) => labels.get(str(engine)) ?? str(engine);
  // The seam's configured engines, in its order; the monitor's own seats when the overview is not here.
  const seatEngines = engines.length > 0 ? engines.map((row) => row.engine) : Object.keys(SEAT_FIELDS);

  return (
    <div className="page">
      <h1 className="text-page-title font-semibold">Monitor</h1>
      {monitor.isPending ? (
        <Skeleton />
      ) : monitor.isError ? (
        <p className="text-danger">
          Could not read the monitor.{" "}
          <button type="button" className="link" onClick={() => monitor.refetch()}>
            Retry
          </button>
        </p>
      ) : (
        <div className="page-body">
          <section className="monitor-section" aria-labelledby="monitor-seats">
            <h2 id="monitor-seats" className="monitor-head">
              Seats
            </h2>
            <div className="monitor-seats">
              {seatEngines.map((engine) => (
                <SeatCard key={engine} seat={seatFor(engine, monitor.data)} label={label(engine)} />
              ))}
            </div>
            <p className="monitor-muted">The mark on each meter is the {RESERVE_PERCENT}% reserve line.</p>
          </section>
          <RoutingCard rows={monitor.data.routing ?? []} label={label} />
          <section className="monitor-section" aria-labelledby="monitor-sessions">
            <h2 id="monitor-sessions" className="monitor-head">
              Sessions ({monitor.data.sessions.length})
            </h2>
            {monitor.data.sessions.length === 0 ? (
              <p className="monitor-muted">No live sessions.</p>
            ) : (
              <ul className="monitor-sessions">
                {monitor.data.sessions.map((session, i) => (
                  <SessionRow key={`${session.kind}-${str(session.session_id)}-${i}`} session={session} label={label} />
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
