import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { z } from "zod";
import { readAlertState } from "./alerts";
import { useOptimisticMutation } from "./useOptimisticMutation";

/**
 * The one place the UI talks to altd. One fetch wrapper, one zod schema per endpoint
 * (lenient at the edges: unknown keys pass through, optional fields are nullish, enum-like
 * strings stay plain strings so a new server value never breaks the page), one query hook
 * per GET endpoint (20s polling, faster for active conversations), one change stream that refreshes
 * task, decision and project queries as their records move, and mutation hooks over
 * useOptimisticMutation for every POST.
 *
 * Paths are interpolated raw (no encodeURIComponent): the server matches path parts without
 * percent-decoding, and project/task names are slugs.
 */

/** Typed error: HTTP status plus the server's {"error": ...} message when present. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { error?: unknown };
    if (typeof body.error === "string" && body.error) return body.error;
  } catch {
    // body was not JSON
  }
  return `HTTP ${res.status}`;
}

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body != null ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (await res.json()) as T;
}

function post<T = unknown>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}

// ---- polling and the change stream -----------------------------------------------------

const pollInterval = () => 20_000;
const CHANGE_RECONNECT_MS = 5_000;

/**
 * The app's one subscription to `GET /api/changes`, mounted by the shell. A `change` event names the
 * projects whose task, decision or hold records moved; their mounted project and task queries refetch
 * with the overview and monitor. Every open, first or after a dropped connection or daemon activation,
 * refetches all of them: the server reads its baseline before the stream opens, so nothing changed
 * between a snapshot and the subscription is lost. The chat conversation keeps its own reads.
 * EventSource retries network failures itself; a refused stream closes it and this reconnects. A hidden
 * tab closes its stream, so background tabs hold none of the browser's few connections per host, and
 * reopens it when shown. A tab with decision alerts on keeps its stream, because that is how a new
 * decision reaches an alert while the page is out of sight. Polling stays the floor while the stream is down.
 */
export function useChangeStream() {
  const queryClient = useQueryClient();
  useEffect(() => {
    let source: EventSource | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const refresh = (projects?: string[]) => {
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      void queryClient.invalidateQueries({ queryKey: ["monitor"] });
      for (const kind of ["project", "task"]) {
        for (const project of projects ?? [undefined]) {
          void queryClient.invalidateQueries({ queryKey: project === undefined ? [kind] : [kind, project] });
        }
      }
    };
    const disconnect = () => {
      clearTimeout(retry);
      source?.close();
      source = undefined;
    };
    const connect = () => {
      disconnect();
      const stream = new EventSource("/api/changes");
      stream.onopen = () => refresh();
      stream.addEventListener("change", (event) => refresh((JSON.parse(event.data) as { projects: string[] }).projects));
      stream.onerror = () => {
        if (stream.readyState === EventSource.CLOSED) retry = setTimeout(connect, CHANGE_RECONNECT_MS);
      };
      source = stream;
    };
    // An alerting tab keeps the stream it already holds; only a closed one reconnects.
    const visibility = () => {
      if (document.hidden && readAlertState() !== "on") disconnect();
      else if (!source || source.readyState === EventSource.CLOSED) connect();
    };
    if (!document.hidden) connect();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      document.removeEventListener("visibilitychange", visibility);
      disconnect();
    };
  }, [queryClient]);
}

// ---- schemas (mirror server.py responses; lenient at the edges) ------------------------

/**
 * One seat's reading, as that seat reports it. A seat names either the two windows a statusline
 * snapshot carries or windows that name their own length in minutes; a window the seat does not
 * report is absent, never zero. `known` is the routing contract — false once the snapshot behind it
 * passes its freshness age; `stale` then says the figures are still here, only old, and a reading
 * with no figure at all is genuinely unknown, with `why` saying what would produce one.
 */
export const SeatQuotaSchema = z
  .object({
    known: z.boolean(),
    five_hour: z.number().nullish(),
    seven_day: z.number().nullish(),
    five_hour_resets: z.number().nullish(),
    seven_day_resets: z.number().nullish(),
    primary_used: z.number().nullish(),
    primary_window_minutes: z.number().nullish(),
    primary_resets: z.string().nullish(),
    secondary_used: z.number().nullish(),
    secondary_window_minutes: z.number().nullish(),
    secondary_resets: z.string().nullish(),
    plan_type: z.string().nullish(),
    at: z.number().nullish(),
    read_at: z.string().nullish(),
    stale: z.boolean().nullish(),
    why: z.string().nullish(),
  })
  .passthrough();

/** route.seats(): one seat per configured engine, in the seam's order, under the seam's own label. */
export const MonitorSeatSchema = z
  .object({
    engine: z.string(),
    label: z.string(),
    quota: SeatQuotaSchema,
  })
  .passthrough();

/** monitor.routing(): the engine each role would get for a turn started now. `engine` is null when
 * the router would find none available; `why` is the router's own sentence. Display only. */
export const RoutingRowSchema = z
  .object({
    role: z.string(),
    project: z.string().nullish(),
    pin: z.string().nullish(),
    current: z.string().nullish(),
    engine: z.string().nullish(),
    why: z.string().nullish(),
  })
  .passthrough();

/** The same durable dilemma is projected into Needs you and the owning task conversation. */
export const DecisionSchema = z
  .object({
    project: z.string(),
    slug: z.string(),
    kind: z.string().nullish(),
    asked_by: z.string().nullish(),
    title: z.string().nullish(),
    id: z.string().nullish(),
    revision: z.number().nullish(),
    design_url: z.string().nullish(),
    anchor_id: z.string().nullish(),
    group_id: z.string().nullish(),
    group_revision: z.number().nullish(),
    group_anchor_id: z.string().nullish(),
    options: z.array(z.object({ key: z.string(), label: z.string(), text: z.string() })).nullish(),
    recommended_key: z.string().nullish(),
    status: z.string().nullish(),
    audience: z.string().nullish(),
    state: z.string().nullish(),
    resume_after: z.string().nullish(),
    question: z.string().nullish(),
    context: z.string().nullish(),
    detail: z.string().nullish(),
    asked: z.string().nullish(),
    since: z.string().nullish(),
    recommendation: z
      .object({ text: z.string().nullish(), label: z.string().nullish(), why: z.string().nullish() })
      .passthrough()
      .nullish(),
    resolution: z.object({
      disposition: z.string(), text: z.string(), by: z.string(), at: z.string(),
      message_id: z.string().nullish(), source: z.string().nullish(),
    }).passthrough().nullish(),
    response: z.object({ text: z.string(), at: z.string(), message_id: z.string() }).nullish(),
    asked_again: z.boolean().nullish(),
    pr: z.number().nullish(),
  })
  .passthrough();

export const QuestionGroupSchema = z.object({
  id: z.string(), revision: z.number(), anchor_id: z.string().nullish(),
  questions: z.array(DecisionSchema),
}).passthrough();

/**
 * /api/project's `decisions` are the same rows as the overview queue (altitude/tasks.py decisions()),
 * but the project page has always read them defensively — every field stays optional here so a
 * thin row still parses, while the card fields keep their types instead of arriving as `unknown`.
 */
export const ProjectDecisionSchema = DecisionSchema.partial().passthrough();

export const WipSchema = z
  .object({
    per_project: z.record(z.string(), z.number()),
    machine: z.number(),
    limit_machine: z.number().nullish(),
    // why: "planned", "dispatch" (state queued) or "resume" (blocked with resume_after); hold: the queue's own
    // text for what the task waits on (the WIP limit, an engine hold, a pending activation, a resume
    // checkpoint, or "ready for dispatch") — digest.py wip().
    waiting: z.array(
      z
        .object({ project: z.string(), slug: z.string(), why: z.string().nullish(), hold: z.string().nullish() })
        .passthrough(),
    ),
  })
  .passthrough();

export const ProjectRowSchema = z
  .object({
    name: z.string(),
    folder: z.string().nullish(),
    path: z.string().nullish(),
    managed: z.boolean(),
    git: z.boolean().nullish(),
    counts: z.record(z.string(), z.number()).nullish(),
    l3: z.record(z.string(), z.unknown()).nullish(),
    hold: z.unknown().nullish(),
  })
  .passthrough();

// monitor/restart-pending.json (written by dispatch.pull_after_done) plus what the Restart button waits for.
export const RestartSchema = z
  .object({
    since: z.string().nullish(),
    head: z.string().nullish(),
    files: z.array(z.string()).nullish(),
    waiting_for: z.array(z.string()),
    requested_at: z.string().nullish(),
    failed: z.string().nullish(),
  })
  .passthrough();

/**
 * route.engine_readouts(): one row per configured engine, in the seam's order, for the rail's engine
 * readout. `label` is the display name the seam gives; `week` is the share of the weekly window used,
 * null when there has never been a reading; `stale` keeps an old figure and says so; `at` is when the
 * reading was taken. The rail renders the rows without knowing which engine is which.
 */
export const EngineReadoutSchema = z
  .object({
    engine: z.string(),
    label: z.string(),
    week: z.number().nullish(),
    known: z.boolean(),
    stale: z.boolean().nullish(),
    at: z.string().nullish(),
  })
  .passthrough();

export const OverviewSchema = z
  .object({
    projects: z.array(ProjectRowSchema),
    queue: z.array(DecisionSchema),
    wip: WipSchema,
    quota: SeatQuotaSchema,
    engines: z.array(EngineReadoutSchema).default([]),
    /** The folders First run scans, named relative to home. */
    roots: z.array(z.string()).default([]),
    /** The operator's name (saved, ALTITUDE_OPERATOR or Git's user.name), shown in the rail's operator row; absent reads “You”. */
    operator: z.string().nullish(),
    restart: RestartSchema.nullish(),
    now: z.string().nullish(),
  })
  .passthrough();

const PlannedWaitSchema = z.object({ reason: z.string(), after: z.string().nullable() });

export const TaskRowSchema = z
  .object({
    slug: z.string(),
    state: z.string().nullish(),
    title: z.string().nullish(),
    updated: z.string().nullish(),
    // Set to the usage-limit reset timestamp when Altitude holds a blocked L2 to resume it
    // itself (server.py on_l2_finished), cleared back to null on resume (dispatch.py).
    resume_after: z.string().nullish(),
    planned_wait: PlannedWaitSchema.nullish(),
    live: z.unknown().nullish(),
    progress_tail: z.unknown().nullish(),
    has: z.record(z.string(), z.boolean()).nullish(),
  })
  .passthrough();

export const ProjectViewSchema = z
  .object({
    name: z.string(),
    config: z.record(z.string(), z.unknown()).nullish(),
    l3: z.record(z.string(), z.unknown()).nullish(),
    busy: z.boolean().nullish(),
    tasks: z.array(TaskRowSchema),
    archive: z.array(TaskRowSchema).nullish(),
    decisions: z.array(ProjectDecisionSchema).nullish(),
    log: z.array(z.record(z.string(), z.unknown())).nullish(),
    incidents: z.array(z.record(z.string(), z.unknown())).nullish(),
    hold: z.unknown().nullish(),
    state_md: z.string().nullish(),
    /** The wireframe viewer's URL when the checkout has boards; absent otherwise. */
    design_viewer: z.string().nullish(),
    repository: z.string().nullish(),
  })
  .passthrough();

export const MessageImageSchema = z.object({
  id: z.string(), name: z.string(), mime_type: z.string(), size: z.number(),
  width: z.number(), height: z.number(), source_message_id: z.string(),
  source_task: z.string().nullish(),
}).passthrough();
export type MessageImage = z.infer<typeof MessageImageSchema>;
export interface ImageUpload { name: string; data: string }
export interface ImageSend { request_id: string; images?: ImageUpload[]; image_ids?: string[] }
export const ImageCapabilitySchema = z.object({
  available: z.boolean(), reason: z.string().nullish(), max_count: z.number().positive(),
  max_bytes: z.number().positive(), max_total_bytes: z.number().positive(),
  max_pixels: z.number().positive(), max_dimension: z.number().positive(),
});
export type ImageCapability = z.infer<typeof ImageCapabilitySchema>;
/** A lost response may follow a durable write. Only an explicit client refusal restores a draft. */
export function imageSendRefused(error: unknown): boolean {
  return error instanceof ApiError && error.status >= 400 && error.status < 500;
}

/** Admission is bounded independently of an agent turn; retry keeps the submission identity. */
async function admitImages<T>(path: string, body: unknown): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60_000);
  try { return await api<T>(path, { method: "POST", body: JSON.stringify(body), signal: controller.signal }); }
  finally { clearTimeout(timer); }
}

export async function sendImageChat(project: string, text: string, input: ImageSend) {
  const out = await admitImages<{ accepted: boolean; queued?: unknown }>("/api/chat", { project, text, ...input });
  if (!out.accepted) throw new Error("Image send was not confirmed");
  return { accepted: true, queued: out.queued ? QueuedMessageSchema.parse(out.queued) : undefined };
}

export const TaskMessageSchema = z
  .object({
    id: z.string(),
    at: z.string().nullish(),
    role: z.string(),
    text: z.string(),
    review_id: z.string().nullish(),
    delivery: z.object({ state: z.enum(["queued", "sending", "removed", "delivered", "unconfirmed"]), at: z.string().nullable(), removable: z.boolean().optional() }).nullish(),
    images: z.array(MessageImageSchema).nullish(),
  })
  .passthrough();

// Passive task consumption, separate from context occupancy, quota and agent-authored spend.
// Cache counters are subsets of normalized input; reasoning is a subset of output.
const tokenCounters = {
  total_tokens: z.number().nullish(),
  input_tokens: z.number().nullish(),
  output_tokens: z.number().nullish(),
  cache_read_tokens: z.number().nullish(),
  cache_write_tokens: z.number().nullish(),
  reasoning_tokens: z.number().nullish(),
};

export const TokenSessionSchema = z.object({
  engine: z.string(),
  session_id: z.string(),
  parent_session_id: z.string().nullish(),
  attempt: z.number().nullish(),
  role: z.string(),
  status: z.string().default("unknown"),
  ...tokenCounters,
  observed_at: z.string().nullish(),
  notes: z.array(z.string()).default([]),
}).passthrough();

// Helper identities are an audit view of accounting already included in task totals.
export const HelperSessionSchema = TokenSessionSchema.extend({
  owner_session_id: z.string().nullish(),
  parentage: z.enum(["thread", "owner"]).nullish(),
  depth: z.number().nullish(),
  attempts: z.array(z.number()).default([]),
  provider_total_tokens: z.number().nullish(),
});

export const HelperUsageSchema = z.object({
  status: z.enum(["partial", "unknown"]),
  observed_count: z.number().nullable(),
  direct_count: z.number().nullable(),
  descendant_count: z.number().nullable(),
  unclassified_count: z.number().nullish(),
  total_tokens: z.number().nullable(),
  sessions: z.array(HelperSessionSchema),
});

export const TokenUsageSchema = z.object({
  status: z.string().default("unknown"),
  ...tokenCounters,
  checked_at: z.string().nullish(),
  observed_at: z.string().nullish(),
  finalized_at: z.string().nullish(),
  notes: z.array(z.string()).default([]),
  sessions: z.array(TokenSessionSchema).default([]),
  helpers: HelperUsageSchema.nullish(),
}).passthrough();

const ReviewSnapshotSchema = z.object({ head: z.string(), base: z.string(), tree: z.string(), context_hash: z.string(), context_ids: z.array(z.string()).optional(), captured_at: z.string().optional(), input_hash: z.string().optional(), captured_context_hash: z.string().optional(), selected_owner_evidence: z.boolean().optional(), limitations: z.array(z.string()).nullish(), proposal: z.object({ id: z.string(), at: z.string(), text: z.string() }).nullish(), proposal_id: z.string().optional(), proposal_hash: z.string().optional() }).passthrough();
export type ReviewSubject = "proposal" | "changes";
export const ReviewSchema = z.object({
  id: z.string(), requested_at: z.string(), requested_by: z.string(),
  subject: z.enum(["proposal", "changes"]).default("changes"), same_engine: z.boolean().default(false), fallback_reason: z.string().default(""), allowance_known: z.boolean().default(false),
  state: z.enum(["requested", "running", "completed", "failed", "cancelled", "withdrawn"]),
  engine_label: z.string().nullish(), model: z.string().nullish(),
  started_at: z.string().nullish(), finished_at: z.string().nullish(), error: z.string().nullish(), focus: z.string().default(""),
  result: z.object({ text: z.string(), findings: z.array(z.object({ id: z.string(), severity: z.string(), title: z.string(), body: z.string(), path: z.string().nullish(), line: z.number().nullish() })), limitations: z.array(z.string()).nullish() }).nullish(),
  dispositions: z.array(z.object({ finding_id: z.string(), disposition: z.enum(["fixed", "dismissed"]), reason: z.string() })).default([]),
  snapshot: ReviewSnapshotSchema.nullish(),
  reconciled: ReviewSnapshotSchema.extend({ reason: z.string() }).nullish(),
  coverage: z.enum(["current", "earlier", "unknown", "assessed"]),
  can_withdraw: z.boolean(), can_cancel: z.boolean(), can_retry: z.boolean(), can_review_latest: z.boolean(), can_review_again: z.boolean().default(false),
}).passthrough();
export type Review = z.infer<typeof ReviewSchema>;
const ReviewSubjectSchema = z.object({ available: z.boolean(), why: z.string(), latest: ReviewSchema.nullable() });
export const TaskReviewSchema = z.object({
  available: z.boolean(), why: z.string(), engine_label: z.string().nullable(), model: z.string().nullable(),
  allowance_known: z.boolean(), latest: ReviewSchema.nullable(), history: z.array(ReviewSchema),
  same_engine: z.boolean().default(false), fallback_reason: z.string().default(""),
  subjects: z.object({ proposal: ReviewSubjectSchema, changes: ReviewSubjectSchema }),
});

export const TaskViewSchema = z
  .object({
    slug: z.string(),
    state: z.string().nullish(),
    title: z.string().nullish(),
    resume_after: z.string().nullish(),
    planned_wait: PlannedWaitSchema.nullish(),
    /** The task's worktree while it has one: where its terminal opens. */
    worktree: z.string().nullish(),
    files: z.record(z.string(), z.string()).nullish(),
    messages: z.array(TaskMessageSchema).nullish(),
    review: TaskReviewSchema.nullish(),
    question: DecisionSchema.nullish(),
    questions: z.array(DecisionSchema).nullish(),
    question_group: QuestionGroupSchema.nullish(),
    events: z.array(z.record(z.string(), z.unknown())).nullish(),
    report_json: z.unknown().nullish(),
    token_usage: TokenUsageSchema.nullish(),
    live: z.unknown().nullish(),
    activity: z.object({
      generation: z.string().nullable(),
      state: z.enum(["available", "empty", "unavailable"]),
      commentary: z.object({ id: z.string(), text: z.string(), at: z.string().nullable(), time_kind: z.enum(["source", "unknown"]) }).nullable(),
      observation: z.object({ at: z.string().nullable(), label: z.string() }).nullable(),
      error: z.string().optional(),
    }).nullish(),
    steering: z.object({
      state: z.enum(["running", "stopping", "stopped", "resuming", "stop_unconfirmed", "idle"]),
      stop_id: z.string().nullable(), generation: z.string().nullable(), error: z.string().nullable(),
    }).nullish(),
  })
  .passthrough();

// One row of the Live session timeline. `role` says who speaks (user, assistant, tool, system); a tool call
// carries its tool, a one-line summary, and the tool_use_id its result row shares; a Codex command carries
// its own output.
export const TranscriptEventSchema = z.object({
  seq: z.number(), source: z.string(), kind: z.string(), type: z.string(), role: z.string().nullish(),
  at: z.string().nullish(), session_id: z.string().nullish(), text: z.string(),
  tool: z.string().nullish(), summary: z.string().nullish(), tool_use_id: z.string().nullish(),
  output: z.string().nullish(), status: z.string().nullish(), error: z.boolean().nullish(),
  truncated: z.boolean().nullish(), raw: z.unknown().nullish(),
}).passthrough();
export const TranscriptSchema = z.object({
  project: z.string(), slug: z.string(), engine: z.string(), session_id: z.string(), cursor: z.number(),
  events: z.array(TranscriptEventSchema), redaction: z.string(),
}).passthrough();

export const SessionSchema = z
  .object({
    kind: z.string(),
    session_id: z.string().nullish(),
    project: z.string().nullish(),
    slug: z.string().nullish(),
    context_percent: z.number().nullish(),
    engine: z.string().nullish(),
    context_state: z.string().nullish(),
    state: z.string().nullish(),
    token_usage: TokenUsageSchema.nullish(),
    at: z.unknown().nullish(),
  })
  .passthrough();

export const MonitorSchema = z
  .object({
    seats: z.array(MonitorSeatSchema).nullish(),
    routing: z.array(RoutingRowSchema).nullish(),
    sessions: z.array(SessionSchema),
    agents: z.unknown().nullish(),
  })
  .passthrough();

export const DigestSchema = z
  .object({
    text: z.string().nullish(),
  })
  .passthrough();

export const ChatMessageSchema = z
  .object({
    at: z.string().nullish(),
    role: z.string(),
    text: z.string(),
    images: z.array(MessageImageSchema).nullish(),
    trigger: z.string().nullish(),
    /** Explicit L3 selection recorded by tasks.fyi; historical authorship alone is ambiguous. */
    heads_up: z.boolean().nullish(),
    engine: z.string().nullish(),
    /** The id of the L3 turn the row belongs to; rows written before the id existed lack it. */
    turn_id: z.string().nullish(),
    /** A system row's task (an FYI, a decision follow-up) when the server recorded one. */
    slug: z.string().nullish(),
    /** On the assistant row of a turn that created tasks: their slugs (SPEC.md §5.2 note 4). */
    tasks: z.array(z.string()).nullish(),
  })
  .passthrough();

/** One message waiting for the next turn boundary; `id` is what removes it again. */
export const QueuedMessageSchema = z
  .object({
    id: z.string(),
    at: z.string().nullish(),
    trigger: z.string().nullish(),
    role: z.string().nullish(),
    text: z.string(),
    images: z.array(MessageImageSchema).nullish(),
    /** Only on the acknowledgement of a message just queued: its place in the queue, 1 first. */
    position: z.number().nullish(),
    /** A follow-up on a decision names its task (SPEC.md §5.2 note 6). */
    slug: z.string().nullish(),
  })
  .passthrough();

/** Server-owned identity of the L3 turn running now; prompt content is deliberately absent. */
export const ActiveTurnSchema = z
  .object({
    id: z.string(),
    started_at: z.string(),
    trigger: z.string(),
    /** The decision's task when the turn is a follow-up from its page or card (SPEC.md §5.2 note 6). */
    slug: z.string().nullish(),
  })
  .passthrough();

export const ChatViewSchema = z
  .object({
    history: z.array(ChatMessageSchema),
    active: ActiveTurnSchema.nullish(),
    busy: z.boolean(),
    /** Messages queued while L3 was busy, oldest first; they run in order at the next turn boundary. */
    queued: z.array(QueuedMessageSchema).nullish(),
    l3: z.record(z.string(), z.unknown()).nullish(),
    /** The project's L3 engine pin, one of the overview's engine names; null or absent means the
     * weekly quota decides. */
    engine: z.string().nullish(),
  })
  .passthrough();

export const VoiceTranscriptSchema = z.object({ text: z.string() }).passthrough();

export type MonitorSeat = z.infer<typeof MonitorSeatSchema>;
export type RoutingRow = z.infer<typeof RoutingRowSchema>;
export type Decision = z.infer<typeof DecisionSchema>;
export type ProjectDecision = z.infer<typeof ProjectDecisionSchema>;
export type Wip = z.infer<typeof WipSchema>;
export type ProjectRow = z.infer<typeof ProjectRowSchema>;
export type EngineReadout = z.infer<typeof EngineReadoutSchema>;
export type Restart = z.infer<typeof RestartSchema>;
export type Overview = z.infer<typeof OverviewSchema>;
export type TaskRow = z.infer<typeof TaskRowSchema>;
export type ProjectView = z.infer<typeof ProjectViewSchema>;
export type TaskMessage = z.infer<typeof TaskMessageSchema>;
export type TokenSession = z.infer<typeof TokenSessionSchema>;
export type TaskTokenUsage = z.infer<typeof TokenUsageSchema>;
export type TaskView = z.infer<typeof TaskViewSchema>;
export type TranscriptEvent = z.infer<typeof TranscriptEventSchema>;
export type Transcript = z.infer<typeof TranscriptSchema>;
export type Session = z.infer<typeof SessionSchema>;
export type MonitorView = z.infer<typeof MonitorSchema>;
export type DigestView = z.infer<typeof DigestSchema>;
export type ChatMessage = z.infer<typeof ChatMessageSchema>;
export type QueuedMessage = z.infer<typeof QueuedMessageSchema>;
export type ActiveTurn = z.infer<typeof ActiveTurnSchema>;
export type ChatView = z.infer<typeof ChatViewSchema>;

const VoiceSchema = z.object({
  backend: z.enum(["browser", "local", "endpoint"]), selection: z.string(),
  url: z.string(), model: z.string(), key_set: z.boolean(),
});
export type VoiceBackend = z.infer<typeof VoiceSchema>["backend"];
export type VoiceSettings = z.infer<typeof VoiceSchema>;
export type VoiceUpdate = { backend: VoiceBackend; selection: string; url?: string; model?: string; key?: string; keep_key?: boolean };

/** Which backend this installation transcribes with; "browser" never uploads audio. */
export async function readVoiceSettings(): Promise<VoiceSettings> {
  return VoiceSchema.parse(await api("/api/voice"));
}

export async function saveVoiceSettings(value: VoiceUpdate): Promise<VoiceSettings> {
  return VoiceSchema.parse(await post("/api/voice", value));
}

const FoldersSchema = z.object({
  path: z.string(), parts: z.array(z.string()), readable: z.boolean(),
  folders: z.array(z.object({ name: z.string(), path: z.string(), project: z.string().nullish(), git: z.boolean() })),
});
export type Folders = z.infer<typeof FoldersSchema>;

/** One folder on the computer running Altitude, opened by the operator: its subfolders, never files. */
export function useFolders(path: string | undefined) {
  return useQuery({
    queryKey: ["folders", path ?? ""],
    queryFn: async () => FoldersSchema.parse(await api(path ? `/api/folders?path=${encodeURIComponent(path)}` : "/api/folders")),
    refetchOnWindowFocus: false,
    gcTime: 0,
    retry: false,
  });
}

/** Choose the projects folder First run lists; an empty path returns to the installation's default. */
export async function saveProjectsFolder(path: string): Promise<{ roots: string[] }> {
  return post("/api/projects-folder", { path });
}

const MachineSchema = z.object({
  operator: z.string().nullish(), incident_repository: z.string().nullish(), altitude_repository: z.string(),
  terminal: z.boolean().default(false),
});
export type Machine = z.infer<typeof MachineSchema>;

/** The operator's name and where system incidents are published, if anywhere. */
export function useMachine() {
  return useQuery({ queryKey: ["machine"], queryFn: async () => MachineSchema.parse(await api("/api/machine")), refetchOnWindowFocus: false });
}

/** Save the name the screens and agents use; an empty name returns to the installation's default. */
export async function saveOperatorName(name: string): Promise<Machine> {
  return MachineSchema.parse(await post("/api/operator-name", { name }));
}

/** Publish system incidents to a GitHub repository, or keep them on this computer with null. */
export async function saveIncidentReports(repository: string | null): Promise<Machine> {
  return MachineSchema.parse(await post("/api/incident-reports", { repository }));
}

/** Turn the operator's terminal on or off for this computer; off also closes every open terminal. */
export async function saveTerminalAccess(enabled: boolean): Promise<Machine> {
  return MachineSchema.parse(await post("/api/terminal-access", { enabled }));
}

// ---- the operator's terminal -----------------------------------------------------------

/**
 * One terminal per task worktree or project folder (server `terminal.view`). `boot` names the altd
 * process holding it, so a page that saw another boot knows a restart ended its terminal. `offset` is
 * the absolute output position the replay reaches; `id` tells one terminal from its replacement; `busy` names a foreground command Close would stop.
 */
export const TerminalStatusSchema = z
  .object({
    state: z.string(),
    id: z.string().nullish(),
    boot: z.string(),
    enabled: z.boolean(),
    folder: z.string().nullish(),
    offset: z.number().nullish(),
    exit_code: z.number().nullish(),
    reason: z.string().nullish(),
    busy: z.string().nullish(),
  })
  .passthrough();
export type TerminalStatus = z.infer<typeof TerminalStatusSchema>;

const terminalPath = (project: string) => `/api/terminal/${project}`;
const terminalQuery = (task?: string) => (task ? `?task=${encodeURIComponent(task)}` : "");

export async function terminalStatus(project: string, task?: string): Promise<TerminalStatus> {
  return TerminalStatusSchema.parse(await api(`${terminalPath(project)}${terminalQuery(task)}`));
}

export function useTerminalStatus(project: string, task?: string) {
  return useQuery({ queryKey: ["terminal", project, task ?? null], queryFn: () => terminalStatus(project, task), refetchOnWindowFocus: false });
}

export async function terminalOpen(project: string, task?: string): Promise<TerminalStatus> {
  return TerminalStatusSchema.parse(await post(`${terminalPath(project)}/open`, { task }));
}

/** Input, resize, close and forget: each answers ok or the server's error. */
export function terminalSend(project: string, action: "input" | "resize" | "close" | "forget", body: { task?: string; data?: string; cols?: number; rows?: number }) {
  return post(`${terminalPath(project)}/${action}`, body);
}

/** The output stream from `offset`: `output` events carry base64 bytes and the next offset, `end` the final status. */
export function terminalStream(project: string, task: string | undefined, offset: number): EventSource {
  return new EventSource(`${terminalPath(project)}/stream${terminalQuery(task) || "?"}${task ? "&" : ""}offset=${offset}`);
}

const PrerequisitesSchema = z.object({
  items: z.array(z.object({
    key: z.string(), label: z.string(), state: z.enum(["met", "unmet", "optional"]),
    detail: z.string().nullish(), command: z.string().nullish(),
  })),
});
export type Prerequisite = z.infer<typeof PrerequisitesSchema>["items"][number];

/** What the agents need on the computer running Altitude; each read checks again. */
export function usePrerequisites() {
  return useQuery({
    queryKey: ["prerequisites"], queryFn: async () => PrerequisitesSchema.parse(await api("/api/prerequisites")).items,
    refetchOnWindowFocus: false, retry: false, gcTime: 0,
  });
}

/** Upload one browser-native audio blob for the server's local service or configured endpoint. */
export async function transcribeVoice(audio: Blob, selection: string, signal?: AbortSignal): Promise<string> {
  const result = VoiceTranscriptSchema.parse(
    await api("/api/transcribe", {
      method: "POST",
      body: audio,
      headers: { "Content-Type": audio.type || "application/octet-stream", "X-Voice-Selection": selection },
      signal,
    }),
  );
  return result.text;
}

// ---- query hooks (20s polling) ---------------------------------------------------------

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: async () => OverviewSchema.parse(await api("/api/overview")),
    refetchInterval: pollInterval,
  });
}

export function useProject(name: string, enabled = true) {
  return useQuery({
    queryKey: ["project", name],
    queryFn: async () => ProjectViewSchema.parse(await api(`/api/project/${name}`)),
    refetchInterval: pollInterval,
    enabled: Boolean(name) && enabled,
  });
}

export function useTask(project: string, slug: string) {
  return useQuery({
    queryKey: ["task", project, slug],
    queryFn: async () => TaskViewSchema.parse(await api(`/api/task/${project}/${slug}`)),
    refetchInterval: (query) => query.state.data?.state === "running" || ["stopping", "stop_unconfirmed", "resuming"].includes(query.state.data?.steering?.state ?? "") ? 2_000 : pollInterval(),
    enabled: Boolean(project && slug),
  });
}

const TaskDesignSchema = z.object({
  title: z.string(), revision: z.number(), text: z.string(),
  images: z.array(z.object({ title: z.string(), url: z.string() })),
  question_url: z.string(), current_question_url: z.string().nullable(), superseded: z.boolean(),
});

export function useTaskDesign(project: string, slug: string, question: string, revision: string) {
  return useQuery({
    queryKey: ["task-design", project, slug, question, revision],
    queryFn: async () => TaskDesignSchema.parse(await api(`/api/design/${project}/${slug}/${question}/${revision}`)),
    refetchInterval: 2_000,
    retry: false,
  });
}

/** The worker's session as a timeline. `live` (the task is running) polls every 2s; a finished or
 * paused session refreshes at the page's ordinary rate. A 404 is the server saying this attempt has
 * no session file; the page reads that from `ApiError.status`. */
export function useTranscript(
  project: string,
  slug: string,
  engine: string,
  sessionId: string,
  raw: boolean,
  live = true,
  enabled = true,
) {
  const query = new URLSearchParams({ engine, session_id: sessionId, raw: raw ? "1" : "0" });
  return useQuery<Transcript>({
    queryKey: ["transcript", project, slug, engine, sessionId, raw],
    queryFn: async () => TranscriptSchema.parse(await api(`/api/transcript/${project}/${slug}?${query}`)),
    refetchInterval: live ? 2_000 : pollInterval,
    // The poll is the retry: a failed read shows at once (a 404 is the server's answer, no session
    // file for this task generation) and the next interval reads again.
    retry: false,
    enabled: enabled && Boolean(project && slug && engine && sessionId),
  });
}

export function useMonitor() {
  return useQuery({
    queryKey: ["monitor"],
    queryFn: async () => MonitorSchema.parse(await api("/api/monitor")),
    refetchInterval: pollInterval,
  });
}

export function useDigest() {
  return useQuery({
    queryKey: ["digest"],
    queryFn: async () => DigestSchema.parse(await api("/api/digest")),
    refetchInterval: pollInterval,
  });
}

export function useChat(project: string, limit = 60, enabled = true) {
  return useQuery({
    queryKey: ["chat", project],
    queryFn: async () => ChatViewSchema.parse(await api(`/api/chat/${project}?limit=${limit}`)),
    refetchInterval: (query) => {
      const view = query.state.data;
      return view?.active || view?.busy || view?.queued?.length ? 2_000 : 20_000;
    },
    enabled: Boolean(project) && enabled,
  });
}

// ---- mutation hooks --------------------------------------------------------------------

export type QuestionAnswer = { question_id: string; revision: number } & ({ option_key: string; text?: never } | { text: string; option_key?: never });

export type DecideInput = {
  project: string;
  slug: string;
} & (QuestionAnswer
  | { group_id: string; group_revision: number; answers: QuestionAnswer[] });

export type QuestionGroup = z.infer<typeof QuestionGroupSchema>;

export function useDecide() {
  const queryClient = useQueryClient();
  return useMutation<{ question: Decision; question_group?: QuestionGroup | null }, Error, DecideInput>({
    mutationFn: async (input) => {
      const out = await post<{ question: unknown; question_group?: unknown }>("/api/decide", input);
      return { question: DecisionSchema.parse(out.question), question_group: out.question_group ? QuestionGroupSchema.parse(out.question_group) : null };
    },
    // Publish submitted responses to every view before polling again.
    onSuccess: async ({ question, question_group: group }, input) => {
      for (const queryKey of [["overview"], ["project", input.project], ["task", input.project, input.slug]]) {
        await queryClient.cancelQueries({ queryKey });
      }
      const updated = group ? [...group.questions.filter((q) => q.id !== question.id || q.revision !== question.revision), question] : [question];
      const same = (row: { id?: string | null; revision?: number | null }) => updated.some((q) => row.id === q.id && row.revision === q.revision);
      const belongs = (row: { id?: string | null; group_id?: string | null }) => group
        ? row.group_id === group.id || (question.group_id && row.group_id === question.group_id) || updated.some((q) => row.id === q.id)
        : row.id === question.id;
      const open = updated.filter((q) => q.status === "open" && q.audience === "operator" && !q.response);
      queryClient.setQueryData<Overview>(["overview"], (cached) => cached && {
        ...cached, queue: [...cached.queue.filter((row) => !(row.project === input.project && row.slug === input.slug && belongs(row))), ...open],
      });
      queryClient.setQueryData<ProjectView>(["project", input.project], (cached) => cached && {
        ...cached, decisions: [...(cached.decisions?.filter((row) => !(row.slug === input.slug && belongs(row))) ?? []), ...open],
      });
      queryClient.setQueryData<TaskView>(["task", input.project, input.slug], (cached) => cached && {
        ...cached, question: group ? group.questions.find((q) => q.status === "open") ?? group.questions.at(-1) : cached.question && same(cached.question) ? question : cached.question,
        ...(group ? { question_group: group } : {}),
        questions: [...(cached.questions?.filter((row) => !same(row)) ?? []), ...updated],
      });
    },
    onSettled: (_out, _error, input) => {
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      void queryClient.invalidateQueries({ queryKey: ["project", input.project] });
      void queryClient.invalidateQueries({ queryKey: ["task", input.project, input.slug] });
    },
  });
}

export interface TaskActionInput {
  project: string;
  slug: string;
  action: string;
  generation?: string | null;
  stop_id?: string | null;
  reason?: string;
}

export function useTaskAction(project: string) {
  return useOptimisticMutation<TaskActionInput, unknown, ProjectView>({
    mutationFn: (input) => post("/api/task/action", input),
    queryKey: ["project", project],
    update: () => undefined,
    failureMessage: "Task action failed.",
  });
}

export interface ProjectAddInput {
  name: string;
  path?: string;
  approval?: string;
}

export function addProject(input: ProjectAddInput): Promise<{ restored?: boolean }> {
  return post("/api/project/add", input);
}

export function useProjectAdd(onRegistered: (name: string) => void) {
  const queryClient = useQueryClient();
  return useMutation<{ restored?: boolean }, Error, ProjectAddInput>({
    mutationFn: addProject,
    // A restored project opens its page only once the overview lists it as managed, so success waits
    // for the refetch; a refused registration reads at once and waits for nothing.
    onSuccess: async (_result, input) => {
      await queryClient.invalidateQueries({ queryKey: ["overview"] });
      onRegistered(input.name);
    },
  });
}

export const SetupSchema = z.object({
  project: z.string(), status: z.string(), checked_at: z.string().nullish(),
  steps: z.array(z.object({
    id: z.string(), label: z.string(), status: z.string(), detail: z.string(),
    action: z.string().nullish(), fingerprint: z.string().nullish(),
    custom_hooks: z.object({ path: z.string(), events: z.array(z.string()) }).optional(),
  }).passthrough()),
  operation: z.object({ id: z.string(), state: z.string(), action: z.string() }).passthrough().nullish(),
  error: z.string().nullish(),
}).passthrough();
export type Setup = z.infer<typeof SetupSchema>;

export function useSetup(project: string) {
  return useQuery({
    queryKey: ["setup", project],
    queryFn: async () => SetupSchema.parse(await api(`/api/setup/${project}`)),
    refetchInterval: (query) => query.state.data?.status === "checking" ? 1_500 : 20_000,
    refetchOnMount: "always",
    enabled: Boolean(project),
  });
}

export function useSetupAction(project: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: { action: "check" | "repair" | "combine"; expected?: string }) =>
      SetupSchema.parse(await post("/api/project/setup", { project, ...input })),
    onMutate: () => client.cancelQueries({ queryKey: ["setup", project] }),
    onSuccess: (result) => client.setQueryData(["setup", project], result),
    onSettled: () => { void client.invalidateQueries({ queryKey: ["setup", project] }); },
  });
}

/** The project header's Start L3 for a managed project whose L3 never ran (SPEC.md §3.2). */
export function useL3Start(project: string) {
  const queryClient = useQueryClient();
  return useMutation<unknown, Error, void>({
    mutationFn: () => post("/api/l3/start", { project }),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["chat", project] });
      void queryClient.invalidateQueries({ queryKey: ["project", project] });
    },
  });
}

export function useProjectRemove() {
  const queryClient = useQueryClient();
  return useMutation<unknown, Error, { name: string }>({
    mutationFn: (input) => post("/api/project/remove", input),
    onSuccess: async (_out, { name }) => {
      await queryClient.cancelQueries({ queryKey: ["overview"] });
      queryClient.setQueryData<Overview>(["overview"], (cached) => cached && {
        ...cached,
        projects: cached.projects.map((row) => row.name === name ? { ...row, managed: false } : row),
        queue: cached.queue.filter((row) => row.project !== name),
      });
      for (const kind of ["project", "chat", "task", "transcript"]) {
        await queryClient.cancelQueries({ queryKey: [kind, name] });
        queryClient.removeQueries({ queryKey: [kind, name] });
      }
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      void queryClient.invalidateQueries({ queryKey: ["monitor"] });
    },
  });
}

export interface L2MessageInput {
  project: string;
  slug: string;
  text: string;
  stop_id?: string;
  question_id?: string;
  revision?: number;
  group_id?: string;
  group_revision?: number;
  request_id?: string;
  images?: ImageUpload[];
}

/** The task page's message to the L2 (SPEC.md §3.10): the page owns the bubble and the "Not sent.
 * Retry." hint itself, so this is the plain call rather than the toasting hook below. The reply
 * carries the stored row, which the page appends to the conversation it already holds. */
export async function sendL2Message(input: L2MessageInput): Promise<TaskMessage> {
  const out = input.images?.length
    ? await admitImages<{ message: unknown }>("/api/l2/message", input)
    : await post<{ message: unknown }>("/api/l2/message", input);
  return TaskMessageSchema.parse(out.message);
}

export const removeL2Message = (project: string, slug: string, id: string) => post("/api/l2/remove", { project, slug, id });

/** Stop or Reject from the task page's inline confirm (SPEC.md §3.10); failure reads inline there. */
export function taskAction(input: TaskActionInput): Promise<unknown> {
  return post("/api/task/action", input);
}

export type ReviewAction = "request" | "retry" | "rerun" | "cancel" | "withdraw";
export async function taskReview(input: { project: string; slug: string; action: ReviewAction; subject?: ReviewSubject; request_id?: string; review_id?: string; reason?: string }): Promise<Review> {
  const result = await post<{ review: unknown }>("/api/task/review", input);
  return ReviewSchema.parse(result.review);
}

export function useL3Reset(project: string) {
  const queryClient = useQueryClient();
  return useOptimisticMutation<void, unknown, ChatView>({
    // The Rotate button lives on a card rendered from ["project", project] (session id, context
    // %, turns) — invalidating only ["chat", project] leaves the card showing the dead session.
    mutationFn: async () => {
      const out = await post("/api/l3/reset", { project });
      void queryClient.invalidateQueries({ queryKey: ["project", project] });
      return out;
    },
    queryKey: ["chat", project],
    update: () => undefined,
    failureMessage: "Couldn't reset the L3 session.",
  });
}

/** Drop a message that has not started yet — the only edit a queued message allows. */
export function useChatDequeue(project: string) {
  return useOptimisticMutation<string, unknown, ChatView>({
    mutationFn: (id) => post("/api/chat/remove", { project, id }),
    queryKey: ["chat", project],
    update: () => undefined,
    failureMessage: "Couldn't remove the queued message.",
  });
}

/**
 * Pin the project's L3 to one engine (a name from the overview's engine readout), or clear the pin
 * with null so the weekly quota decides. The pin covers chat and server-triggered turns alike and
 * stays until changed.
 */
export function useL3Engine(project: string) {
  const client = useQueryClient();
  return useOptimisticMutation<string | null, unknown, ChatView>({
    mutationFn: async (engine) => {
      const result = await post("/api/l3/engine", { project, engine });
      await client.invalidateQueries({ queryKey: ["defaults", project] });
      return result;
    },
    queryKey: ["chat", project],
    update: (cached, engine) => cached && { ...cached, engine },
    failureMessage: "Couldn't change the L3 engine.",
  });
}

/** config.defaults_view(): the project's requested model/effort per role and engine, in the seam's order. */
const DefaultFieldSchema = z.object({
  setting: z.string(), value: z.string().nullable(), default: z.string(),
});
const ProjectDefaultsSchema = z.object({
  l3_engine: z.string().nullish(),
  roles: z.array(z.object({
    role: z.enum(["l3", "l2"]),
    engines: z.array(z.object({
      engine: z.string(), label: z.string(),
      model: DefaultFieldSchema.extend({ choices: z.array(z.string()) }),
      effort: DefaultFieldSchema.extend({ choices: z.array(z.object({ value: z.string(), label: z.string() })) }),
    })),
  })),
});
export type ProjectDefaults = z.infer<typeof ProjectDefaultsSchema>;

export function useProjectDefaults(project: string) {
  return useQuery({
    queryKey: ["defaults", project],
    queryFn: async () => ProjectDefaultsSchema.parse(await api(`/api/defaults/${project}`)),
    retry: false,
    refetchOnMount: "always",
  });
}

/** One saved default; each field owns its mutation so its Saving/Saved/error state stays beside it. */
export function useSetDefault(project: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: { setting: string; value: string | null }) =>
      ProjectDefaultsSchema.parse(await post("/api/defaults", { project, ...input })),
    onMutate: () => client.cancelQueries({ queryKey: ["defaults", project] }),
    // Merge only the acknowledged field: a slower response to another field's save must not restore its old value.
    onSuccess: (result, { setting }) => client.setQueryData<ProjectDefaults>(["defaults", project], (old) => {
      if (!old) return result;
      const saved = result.roles.flatMap((row) => row.engines).flatMap((e) => [e.model, e.effort]).find((f) => f.setting === setting);
      return { ...old, roles: old.roles.map((row) => ({ ...row, engines: row.engines.map((e) => ({
        ...e,
        model: e.model.setting === setting ? { ...e.model, value: saved?.value ?? null } : e.model,
        effort: e.effort.setting === setting ? { ...e.effort, value: saved?.value ?? null } : e.effort,
      })) })) };
    }),
  });
}

export function useRestart() {
  return useOptimisticMutation<void, unknown, Overview>({
    mutationFn: () => post("/api/restart", {}),
    queryKey: ["overview"],
    update: () => undefined,
    failureMessage: "Couldn't start the restart.",
  });
}

// ---- chat streaming --------------------------------------------------------------------

export interface ChatDone {
  turn_id?: string | null;
  session_id?: string | null;
  context_percent?: number | null;
  turns?: number | null;
  cost?: unknown;
  error?: string | null;
  engine?: string | null;
}

/** A message sent while L3 was busy comes back queued instead of streamed. */
export interface ChatSent extends ChatDone {
  queued?: QueuedMessage;
  /** The turn the server opened for the message, named before its first text (SPEC.md §4.2). */
  turn?: ActiveTurn;
}

export interface ChatStreamHandlers {
  onText: (chunk: string) => void;
  /** The server accepted the message: it is a stored row now, streamed or queued. */
  onAccepted?: () => void;
  /** The turn's server-side identity, so the page can key its bubble on it while the reply streams. */
  onTurn?: (turn: ActiveTurn) => void;
}

/**
 * POST /api/chat and read the reply. A free L3 streams NDJSON: a first {"turn": {...}} names the
 * turn, {"t": "..."} lines feed onText and the final {"done": {...}} comes back (the caller surfaces
 * done.error). A busy L3 answers with a single {"queued": {...}} object instead — the same line
 * reader takes both. Conversation polling continues independently. Non-2xx throws ApiError.
 */
export async function streamChat(
  project: string,
  text: string,
  handlers: ChatStreamHandlers,
  /** A follow-up from a decision page names the decision's task; its rows carry the slug (SPEC.md §4.3). */
  options: { slug?: string } = {},
): Promise<ChatSent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project, text, ...(options.slug ? { slug: options.slug } : {}) }),
  });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  if (!res.body) throw new Error("no response body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done: ChatSent = {};
  const handleLine = (line: string) => {
    if (!line.trim()) return;
    let parsed: { t?: unknown; done?: ChatDone; queued?: unknown; turn?: unknown };
    try {
      parsed = JSON.parse(line) as { t?: unknown; done?: ChatDone; queued?: unknown; turn?: unknown };
    } catch {
      return; // tolerate a torn line
    }
    if (parsed.turn) {
      const turn = ActiveTurnSchema.safeParse(parsed.turn);
      if (turn.success) {
        done = { ...done, turn: turn.data };
        handlers.onAccepted?.();
        handlers.onTurn?.(turn.data);
      }
    }
    if (typeof parsed.t === "string") handlers.onText(parsed.t);
    if (parsed.done) done = { ...done, ...parsed.done };
    if (parsed.queued) done = { ...done, queued: QueuedMessageSchema.parse(parsed.queued) };
    if (!done.turn && (done.turn_id || done.queued)) handlers.onAccepted?.();
  };

  try {
    for (;;) {
      const { value, done: eof } = await reader.read();
      if (eof) break;
      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        handleLine(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
      }
    }
    handleLine(buffer);
  } catch (error) {
    if (!done.turn && !done.turn_id && !done.queued) throw error;
  }
  if (!done.turn && !done.turn_id && !done.queued) {
    if (done.error) throw new ApiError(409, done.error);
    throw new Error("No delivery receipt");
  }
  return done;
}
