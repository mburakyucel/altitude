import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { Link, NavLink, useMatch, useParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { sendL2Message, taskAction, useOverview, useTask } from "../data/api";
import type { Decision, Overview, TaskMessage, TaskView } from "../data/api";
import { agoText, when } from "../data/observed";
import VoiceComposer from "../components/VoiceComposer";
import { DecisionCard } from "../components/DecisionCard";
import { useViewport } from "../shell/breakpoints";
import { Overlay } from "../shell/Overlay";
import LiveSession, { Prose } from "./LiveSession";

// TaskView is a passthrough schema: everything the server sends beyond the declared fields (attempt,
// session_id, l2_engine, prs, hold_merge, fault, ...) arrives typed `unknown`, so narrow it here.
function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
function rec(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}
function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}
function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** The operator's role in a task conversation row, as the server records it. */
const OPERATOR = "burak" as const;

// ---- what the page says about the task ---------------------------------------------------------

type Tone = "ok" | "held" | "danger" | undefined;

interface Chip {
  text: string;
  tone?: Tone;
}

interface Facts {
  state: string;
  dot: "running" | "waiting" | "danger" | "idle";
  /** The state chip, engine and model, PR with its checks state, hold reason (SPEC.md §3.10). */
  chips: Chip[];
  /** The muted line under the title: attempt, when it started or finished, context used. */
  sub: string;
  /** What a queued task waits for; shown where the live panel would be. */
  waiting: string | null;
  /** A block that is a fault: the one-sentence reason, and L3 has been told. */
  fault: string | null;
  /** A block waiting on L3's answer, or on the operator before the decision row arrives. */
  blocked: string | null;
  engineLabel: string;
  finished: boolean;
  canMessage: boolean;
  canStop: boolean;
  canReject: boolean;
  /** The composer's hint line for this state. */
  hint: string;
}

function oneSentence(text: string): string {
  const first = text.trim().split(/(?<=[.!?])\s+/)[0] ?? "";
  return /[.!?]$/.test(first) ? first : `${first}.`;
}

export function taskFacts(task: TaskView, overview: Overview | undefined, project: string): Facts {
  const state = task.state ?? "";
  const held = state === "blocked" && Boolean(task.resume_after);
  const faultKind = str(task["fault"]);
  const reason = str(task["blocked_reason"]);
  const waitsOnL3 = state === "blocked" && !held && !faultKind && str(task["waiting_on"]) === "l3";
  const finished = state === "done" || state === "rejected";

  const engineId = str(task["l2_engine"]) || str(task["engine"]);
  const engineLabel = overview?.engines.find((e) => e.engine === engineId)?.label ?? engineId;
  const model = str(task["engine_model"]) || str(task["model"]);
  const engineChip = [model ? sentence(model) : "", engineLabel].filter(Boolean).join(" on ");

  const prs = arr(task["prs"]).map((n) => num(n)).filter((n): n is number => n != null);
  const number = prs[prs.length - 1];
  const landed = rec(rec(task.report_json)["landed"]);
  const merged = arr(landed["prs"]).some((p) => num(rec(p)["number"]) === number && rec(p)["merged"] === true);
  const runs = arr(landed["main_runs"]);
  const conclusion = str(rec(runs[runs.length - 1])["conclusion"]);
  const checks =
    conclusion === "success" ? "main checks passed" : conclusion === "failure" ? "main checks failed" : conclusion ? `main run ${conclusion}` : "";
  const prChip: Chip | null =
    number != null
      ? {
          text: [`PR #${number} ${merged ? "merged" : "open"}`, checks].filter(Boolean).join(" · "),
          tone: conclusion === "failure" ? "danger" : merged ? "ok" : undefined,
        }
      : null;
  const hold = str(task["hold_merge"]);

  const label = held ? "Queued" : sentence(state || "unknown");
  const dot: Facts["dot"] =
    faultKind || state === "rejected" ? "danger" : state === "running" ? "running" : state === "blocked" && !held ? "waiting" : "idle";

  const attempt = num(task["attempt"]) ?? 0;
  const context = num(rec(task.live)["context_percent"]);
  const sub = [
    attempt > 0 ? `attempt ${attempt}` : "",
    state === "running" && agoText(task["dispatched"]) ? `started ${agoText(task["dispatched"])}` : "",
    finished && agoText(task["updated"]) ? `${state} ${agoText(task["updated"])}` : "",
    state === "running" && context != null ? `${Math.round(context)}% of its context used` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  const why = overview?.wip.waiting.find((w) => w.project === project && w.slug === task.slug)?.why;
  const waiting =
    state === "queued"
      ? `Waits for ${why === "resume" ? "resume" : "dispatch"}`
      : held
        ? `Waits for resume${reason ? ` · ${reason}` : ""}`
        : null;

  return {
    state,
    dot,
    chips: [{ text: label }, ...(engineChip ? [{ text: engineChip }] : []), ...(prChip ? [prChip] : []), ...(hold ? [{ text: `Merge held · ${hold}`, tone: "held" as const }] : [])],
    sub,
    waiting,
    fault: faultKind ? `${oneSentence(reason || `A ${faultKind} fault blocked the task`)} L3 has been told.` : null,
    blocked: waitsOnL3 ? `Waits for L3's answer${reason ? ` · ${reason}` : ""}` : null,
    engineLabel,
    finished,
    canMessage: state === "running" || state === "blocked",
    canStop: state === "running",
    canReject: ["queued", "running", "blocked", "reported"].includes(state),
    hint:
      state === "running"
        ? "Reaches the L2 at its next checkpoint."
        : held
          ? "Delivered when Altitude resumes the L2."
          : "Sending resumes the L2 with your message.",
  };
}

// ---- the conversation (SPEC.md §3.3 bubbles and prose, §3.6 composer, §3.10 states) ------------

function dayLabel(at: number): string {
  const day = new Date(at);
  const today = new Date();
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).valueOf();
  const diff = Math.round((startOf(today) - startOf(day)) / 86_400_000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return day.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function clock(at: number): string {
  return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** A long press (touch or pen, not a mouse) reveals the row's time on the phone (SPEC.md §3.3). */
function useLongPress(onLong: () => void) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clear = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };
  return {
    onPointerDown: (event: ReactPointerEvent) => {
      if (event.pointerType === "mouse") return;
      clear();
      timer.current = setTimeout(onLong, 500);
    },
    onPointerUp: clear,
    onPointerCancel: clear,
    onPointerLeave: clear,
  };
}

function Row({ message, pending = false }: { message: Pick<TaskMessage, "role" | "text" | "at">; pending?: boolean }) {
  const [shown, setShown] = useState(false);
  const press = useLongPress(() => setShown((v) => !v));
  const mine = message.role === OPERATOR;
  const at = when(message.at);
  return (
    <div
      className="msg-row"
      data-role={message.role}
      data-mine={mine || undefined}
      data-pending={pending || undefined}
      data-time-shown={shown || undefined}
      {...press}
    >
      {mine ? (
        <div className="bubble">{message.text}</div>
      ) : (
        <div className="reply">
          {message.role === "l3" ? <span className="reply-from">L3</span> : null}
          <Prose text={message.text} />
        </div>
      )}
      {at != null ? (
        <time className="msg-time" dateTime={message.at ?? undefined} title={new Date(at).toLocaleString()}>
          {clock(at)}
        </time>
      ) : null}
    </div>
  );
}

function TaskConversation({
  project,
  task,
  facts,
  decision,
}: {
  project: string;
  task: TaskView;
  facts: Facts;
  decision: Decision | undefined;
}) {
  const queryClient = useQueryClient();
  const scroller = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const messages = task.messages ?? [];
  const count = messages.length + (pending ? 1 : 0);

  useEffect(() => {
    const node = scroller.current;
    if (node && following.current) node.scrollTop = node.scrollHeight;
  }, [count, decision]);

  // The bubble shows at once at 60%; accepted, it is the stored row at full opacity; refused, it leaves,
  // the draft returns, and the hint reads "Not sent. Retry." (SPEC.md §3.6).
  const send = async (text: string) => {
    const ready = text.trim();
    if (!ready || pending) return;
    following.current = true;
    setFailed(false);
    setPending(ready);
    setDraft("");
    try {
      const row = await sendL2Message({ project, slug: task.slug, text: ready });
      queryClient.setQueryData<TaskView>(["task", project, task.slug], (cached) =>
        cached ? { ...cached, messages: [...(cached.messages ?? []), row] } : cached,
      );
      setPending(null);
      void queryClient.invalidateQueries({ queryKey: ["task", project, task.slug] });
    } catch {
      setPending(null);
      setDraft(ready);
      setFailed(true);
    }
  };

  const rows: ReactNode[] = [];
  let lastDay = "";
  messages.forEach((message, index) => {
    const at = when(message.at);
    const day = at != null ? dayLabel(at) : "";
    if (day && day !== lastDay) {
      rows.push(
        <div key={`day-${day}`} className="day-divider" role="separator">
          {day}
        </div>,
      );
      lastDay = day;
    }
    rows.push(<Row key={message.id || `${message.at ?? "message"}-${index}`} message={message} />);
  });

  return (
    <section className="task-convo" aria-label="Task conversation">
      <div
        className="task-convo-scroll"
        ref={scroller}
        onScroll={(event) => {
          const node = event.currentTarget;
          following.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 48;
        }}
      >
        <div className="convo-col">
          {decision ? (
            <div className="task-decision">
              <DecisionCard decision={decision} />
            </div>
          ) : null}
          {messages.length === 0 && !pending ? (
            <p className="convo-empty text-muted">
              {facts.finished ? "No messages on this task." : "No messages yet."}
            </p>
          ) : null}
          {rows}
          {pending ? <Row message={{ role: OPERATOR, text: pending, at: new Date().toISOString() }} pending /> : null}
        </div>
      </div>
      {facts.canMessage ? (
        <div className="task-composer">
          <VoiceComposer
            value={draft}
            onChange={setDraft}
            onSubmit={send}
            ariaLabel="Message the L2"
            placeholder="Message the L2"
            rows={2}
            submitting={pending != null}
          />
          <p className={`composer-hint ${failed ? "text-danger" : "text-muted"}`} role={failed ? "alert" : undefined}>
            {failed ? (
              <>
                Not sent.{" "}
                <button type="button" className="link" onClick={() => void send(draft)}>
                  Retry
                </button>
              </>
            ) : (
              facts.hint
            )}
          </p>
        </div>
      ) : null}
    </section>
  );
}

// ---- Stop and Reject: quiet text buttons, an inline confirm, no browser dialog (SPEC.md §3.10) --

type Confirm = "" | "stop" | "reject";

function useTaskActions(project: string, slug: string) {
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState<Confirm>("");
  const [reason, setReason] = useState("");
  const act = useMutation({
    mutationFn: taskAction,
    onSuccess: () => {
      setConfirm("");
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["task", project, slug] });
      void queryClient.invalidateQueries({ queryKey: ["project", project] });
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
    },
  });
  const open = useCallback(
    (which: Confirm) => {
      act.reset();
      setConfirm(which);
    },
    [act],
  );
  const run = () => {
    const text = reason.trim();
    act.mutate({ project, slug, action: confirm, ...(confirm === "reject" && text ? { reason: text } : {}) });
  };
  return { confirm, open, reason, setReason, run, pending: act.isPending, error: act.isError };
}

function ActionButtons({ facts, actions }: { facts: Facts; actions: ReturnType<typeof useTaskActions> }) {
  if (!facts.canStop && !facts.canReject) return null;
  return (
    <>
      {facts.canStop ? (
        <button type="button" className="btn btn-ghost task-action" onClick={() => actions.open("stop")}>
          Stop
        </button>
      ) : null}
      {facts.canReject ? (
        <button type="button" className="btn btn-ghost task-action" onClick={() => actions.open("reject")}>
          Reject
        </button>
      ) : null}
    </>
  );
}

function ConfirmRow({ actions }: { actions: ReturnType<typeof useTaskActions> }) {
  if (!actions.confirm) return null;
  const stop = actions.confirm === "stop";
  const question = stop ? "Stop this task?" : "Reject this task?";
  return (
    <div className="task-confirm" role="group" aria-label={question}>
      <p className="task-confirm-text">
        {stop ? "Stop this task? Its worker ends; the branch stays." : "Reject this task? Its worker ends and the task is archived."}
      </p>
      {stop ? null : (
        <input
          className="field"
          aria-label="Reason (optional)"
          placeholder="Reason (optional)"
          value={actions.reason}
          onChange={(event) => actions.setReason(event.target.value)}
        />
      )}
      <div className="task-confirm-actions">
        <button type="button" className="btn btn-primary" disabled={actions.pending} onClick={actions.run}>
          {actions.pending ? <span className="spinner" aria-hidden /> : null}
          {stop ? "Stop" : "Reject"}
        </button>
        <button type="button" className="btn btn-ghost" disabled={actions.pending} onClick={() => actions.open("")}>
          Cancel
        </button>
      </div>
      {actions.error ? (
        <p className="text-meta text-danger" role="alert">
          Could not {stop ? "stop" : "reject"} the task.{" "}
          <button type="button" className="link" onClick={actions.run}>
            Retry
          </button>
        </p>
      ) : null}
    </div>
  );
}

// ---- the page ----------------------------------------------------------------------------------

function Chips({ chips }: { chips: Chip[] }) {
  return (
    <div className="task-chips">
      {chips.map((chip) => (
        <span key={chip.text} className="chip" data-tone={chip.tone}>
          {chip.text}
        </span>
      ))}
    </div>
  );
}

function BlockLines({ facts }: { facts: Facts }) {
  if (facts.fault) {
    return (
      <p className="task-line text-danger" role="status">
        {facts.fault}
      </p>
    );
  }
  if (facts.blocked) {
    return (
      <p className="task-line text-muted" role="status">
        {facts.blocked}
      </p>
    );
  }
  return null;
}

function PanelIcon() {
  return (
    <svg aria-hidden viewBox="0 0 20 20" width="20" height="20">
      <rect x="3" y="4" width="14" height="12" rx="2" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M12 4v12" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function TaskPage({
  project,
  task,
  overview,
  liveRoute,
}: {
  project: string;
  task: TaskView;
  overview: Overview | undefined;
  liveRoute: boolean;
}) {
  const { phone, panelInline } = useViewport();
  const facts = taskFacts(task, overview, project);
  const decision = overview?.queue.find((d) => d.project === project && d.slug === task.slug);
  // A block on the operator shows its card; the reason line stands in until the row has loaded.
  const blockFacts =
    facts.state === "blocked" && !decision && !facts.fault && !facts.blocked && !facts.waiting && overview
      ? { ...facts, blocked: str(task["blocked_reason"]) || "Blocked" }
      : facts;
  const actions = useTaskActions(project, task.slug);
  // Inline at the panel width, open by default; below it an overlay the operator opens (SPEC.md §2.2 rule).
  const [panelOpen, setPanelOpen] = useState(panelInline || liveRoute);
  useEffect(() => {
    if (liveRoute) setPanelOpen(true);
  }, [liveRoute]);
  const closePanel = useCallback(() => setPanelOpen(false), []);
  const base = `/projects/${project}/tasks/${task.slug}`;
  const title = task.title || task.slug;

  const panel = <LiveSession project={project} task={task} engineLabel={facts.engineLabel} waiting={facts.waiting} />;
  const conversation = <TaskConversation project={project} task={task} facts={facts} decision={decision} />;

  if (phone) {
    return (
      <div className="task-page" data-phone>
        <div className="task-phone-head">
          <p className="task-state-line">
            <span className="dot" data-state={facts.dot} aria-hidden />
            {facts.chips.map((chip, index) => (
              <span key={chip.text} data-tone={chip.tone}>
                {index > 0 ? " · " : ""}
                {chip.text}
              </span>
            ))}
          </p>
          <div className="task-actions">
            <ActionButtons facts={facts} actions={actions} />
          </div>
          <ConfirmRow actions={actions} />
          <BlockLines facts={blockFacts} />
        </div>
        <nav className="task-tabs" aria-label="Task views">
          <NavLink className="task-tab" to={base} end>
            Conversation
          </NavLink>
          <NavLink className="task-tab" to={`${base}/live`}>
            Live session
          </NavLink>
        </nav>
        {liveRoute ? panel : conversation}
      </div>
    );
  }

  return (
    <div className="task-page">
      <header className="task-header">
        <div className="task-crumb-row">
          <Link className="task-crumb" to={`/projects/${project}`}>
            ‹ {project}
          </Link>
          <div className="task-actions">
            <ActionButtons facts={facts} actions={actions} />
            <button
              type="button"
              className="icon-btn"
              aria-label="Live session"
              aria-pressed={panelOpen}
              onClick={() => setPanelOpen((open) => !open)}
            >
              <PanelIcon />
            </button>
          </div>
        </div>
        <h1 className="task-title">
          <span className="dot" data-state={facts.dot} aria-hidden />
          <span>{title}</span>
        </h1>
        {facts.sub ? <p className="task-sub">{facts.sub}</p> : null}
        <Chips chips={facts.chips} />
        <ConfirmRow actions={actions} />
        <BlockLines facts={blockFacts} />
      </header>
      <div className="task-body">
        <div className="task-main">{conversation}</div>
        {panelOpen ? (
          panelInline ? (
            panel
          ) : (
            <Overlay label="Live session" side="right" onClose={closePanel}>
              {panel}
            </Overlay>
          )
        ) : null}
      </div>
    </div>
  );
}

function TaskSkeleton({ phone }: { phone: boolean }) {
  return (
    <div className="task-page" data-phone={phone || undefined} aria-label="Loading">
      <header className="task-header">
        {phone ? null : <div className="skeleton h-4 w-24" />}
        {phone ? null : <div className="skeleton h-6 w-80 max-w-full" />}
        <div className="flex gap-2">
          <div className="skeleton h-5 w-20" />
          <div className="skeleton h-5 w-28" />
        </div>
      </header>
      <div className="task-body">
        <div className="task-main">
          <div className="convo-col gap-4 p-4">
            <div className="skeleton h-12 w-2/3 self-end" />
            <div className="skeleton h-16 w-4/5" />
            <div className="skeleton h-10 w-1/2 self-end" />
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The task page (SPEC.md §3.10): the operator's conversation with the L2 beside the worker's live
 * session, Stop and Reject with an inline confirm; on the phone a state line and two tabs, the
 * composer pinned above the tab bar on the Conversation tab. `/live` selects the Live session tab and
 * opens the desktop panel.
 */
export default function Task() {
  const params = useParams();
  const project = params["name"] ?? "";
  const slug = params["slug"] ?? "";
  const liveRoute = Boolean(useMatch("/projects/:name/tasks/:slug/live"));
  const task = useTask(project, slug);
  const overview = useOverview();
  const { phone } = useViewport();
  if (task.isPending) return <TaskSkeleton phone={phone} />;
  if (task.isError) {
    return (
      <div className="page">
        <p className="text-danger">
          Could not load the task.{" "}
          <button type="button" className="link" onClick={() => task.refetch()}>
            Retry
          </button>
        </p>
      </div>
    );
  }
  return (
    <TaskPage key={`${project}:${slug}`} project={project} task={task.data} overview={overview.data} liveRoute={liveRoute} />
  );
}
