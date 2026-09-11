import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { useOptimisticMutation } from "./useOptimisticMutation";

/**
 * The one place the UI talks to altd. One fetch wrapper, one zod schema per endpoint
 * (lenient at the edges: unknown keys pass through, optional fields are nullish, enum-like
 * strings stay plain strings so a new server value never breaks the page), one query hook
 * per GET endpoint (20s polling, faster for active conversations), and mutation hooks
 * over useOptimisticMutation for every POST.
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

// ---- polling ---------------------------------------------------------------------------

const pollInterval = () => 20_000;

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
    limit_project: z.number().nullish(),
    limit_machine: z.number().nullish(),
    // why: "dispatch" (state queued) or "resume" (blocked with resume_after); hold: the queue's own
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
    /** The operator's configured name, shown in the rail's operator row. */
    operator: z.string().nullish(),
    restart: RestartSchema.nullish(),
    now: z.string().nullish(),
  })
  .passthrough();

export const TaskRowSchema = z
  .object({
    slug: z.string(),
    state: z.string().nullish(),
    title: z.string().nullish(),
    updated: z.string().nullish(),
    // Set to the usage-limit reset timestamp when Altitude holds a blocked L2 to resume it
    // itself (server.py on_l2_finished), cleared back to null on resume (dispatch.py).
    resume_after: z.string().nullish(),
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

export const TaskMessageSchema = z
  .object({
    id: z.string(),
    at: z.string().nullish(),
    role: z.enum(["burak", "l2", "l3"]),
    text: z.string(),
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

export const TaskViewSchema = z
  .object({
    slug: z.string(),
    state: z.string().nullish(),
    title: z.string().nullish(),
    resume_after: z.string().nullish(),
    files: z.record(z.string(), z.string()).nullish(),
    messages: z.array(TaskMessageSchema).nullish(),
    question: DecisionSchema.nullish(),
    questions: z.array(DecisionSchema).nullish(),
    question_group: QuestionGroupSchema.nullish(),
    events: z.array(z.record(z.string(), z.unknown())).nullish(),
    report_json: z.unknown().nullish(),
    token_usage: TokenUsageSchema.nullish(),
    live: z.unknown().nullish(),
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

/** Upload one browser-native audio blob; the server normalizes it for the existing local Whisper service. */
export async function transcribeVoice(audio: Blob, signal?: AbortSignal): Promise<string> {
  const result = VoiceTranscriptSchema.parse(
    await api("/api/transcribe", {
      method: "POST",
      body: audio,
      headers: { "Content-Type": audio.type || "application/octet-stream" },
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
    refetchInterval: (query) => query.state.data?.question?.status === "open" || query.state.data?.question_group?.questions.some((q) => q.status === "open") ? 2_000 : pollInterval(),
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
) {
  const query = new URLSearchParams({ engine, session_id: sessionId, raw: raw ? "1" : "0" });
  return useQuery<Transcript>({
    queryKey: ["transcript", project, slug, engine, sessionId, raw],
    queryFn: async () => TranscriptSchema.parse(await api(`/api/transcript/${project}/${slug}?${query}`)),
    refetchInterval: live ? 2_000 : pollInterval,
    // The poll is the retry: a failed read shows at once (a 404 is the server's answer, no session
    // file for this task generation) and the next interval reads again.
    retry: false,
    enabled: Boolean(project && slug && engine && sessionId),
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

export type DecideInput = {
  project: string;
  slug: string;
} & ({ question_id: string; revision: number; option_key?: string }
  | { group_id: string; group_revision: number; answers: { question_id: string; revision: number; option_key: string }[] });

export type QuestionGroup = z.infer<typeof QuestionGroupSchema>;

export function useDecide() {
  const queryClient = useQueryClient();
  return useMutation<{ question: Decision; question_group?: QuestionGroup | null }, Error, DecideInput>({
    mutationFn: async (input) => {
      const out = await post<{ question: unknown; question_group?: unknown }>("/api/decide", input);
      return { question: DecisionSchema.parse(out.question), question_group: out.question_group ? QuestionGroupSchema.parse(out.question_group) : null };
    },
    // Publish the saved receipt to every view before polling again. A send never uses this path.
    onSuccess: async ({ question, question_group: group }, input) => {
      for (const queryKey of [["overview"], ["project", input.project], ["task", input.project, input.slug]]) {
        await queryClient.cancelQueries({ queryKey });
      }
      const updated = group ? [...group.questions.filter((q) => q.id !== question.id || q.revision !== question.revision), question] : [question];
      const same = (row: { id?: string | null; revision?: number | null }) => updated.some((q) => row.id === q.id && row.revision === q.revision);
      const belongs = (row: { id?: string | null; group_id?: string | null }) => group
        ? row.group_id === group.id || (question.group_id && row.group_id === question.group_id) || updated.some((q) => row.id === q.id)
        : row.id === question.id;
      const open = updated.filter((q) => q.status === "open" && q.audience === "operator");
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
  reason?: string;
}

export function useTaskAction(project: string) {
  const queryClient = useQueryClient();
  return useOptimisticMutation<TaskActionInput, unknown, ProjectView>({
    // Every action moves a task's state, which the rail's badges and dots read from ["overview"];
    // invalidate it too or they sit stale for a full 20s poll.
    mutationFn: async (input) => {
      const out = await post("/api/task/action", input);
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      return out;
    },
    queryKey: ["project", project],
    update: () => undefined,
    failureMessage: "Task action failed.",
  });
}

export interface ProjectAddInput {
  name: string;
  path?: string;
  approval?: string;
  wip?: number;
}

export function useProjectAdd() {
  const queryClient = useQueryClient();
  return useMutation<{ restored?: boolean }, Error, ProjectAddInput>({
    mutationFn: (input) => post<{ restored?: boolean }>("/api/project/add", input),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["overview"] }),
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
  question_id?: string;
  revision?: number;
  group_id?: string;
  group_revision?: number;
}

/** The task page's message to the L2 (SPEC.md §3.10): the page owns the bubble and the "Not sent.
 * Retry." hint itself, so this is the plain call rather than the toasting hook below. The reply
 * carries the stored row, which the page appends to the conversation it already holds. */
export async function sendL2Message(input: L2MessageInput): Promise<TaskMessage> {
  const out = await post<{ message: unknown }>("/api/l2/message", input);
  return TaskMessageSchema.parse(out.message);
}

/** Stop or Reject from the task page's inline confirm (SPEC.md §3.10); failure reads inline there. */
export function taskAction(input: TaskActionInput): Promise<unknown> {
  return post("/api/task/action", input);
}

export function useL2Message(project: string) {
  const queryClient = useQueryClient();
  return useOptimisticMutation<L2MessageInput, unknown, ProjectView>({
    mutationFn: async (input) => {
      const out = await post("/api/l2/message", input);
      void queryClient.invalidateQueries({ queryKey: ["task", input.project, input.slug] });
      return out;
    },
    queryKey: ["project", project],
    update: () => undefined,
    failureMessage: "Couldn't send the message to the L2.",
  });
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
    update: (cached, id) =>
      cached && { ...cached, queued: (cached.queued ?? []).filter((q) => q.id !== id) },
    failureMessage: "Couldn't remove the queued message.",
  });
}

/**
 * Pin the project's L3 to one engine (a name from the overview's engine readout), or clear the pin
 * with null so the weekly quota decides. The pin covers chat and server-triggered turns alike and
 * stays until changed.
 */
export function useL3Engine(project: string) {
  return useOptimisticMutation<string | null, unknown, ChatView>({
    mutationFn: (engine) => post("/api/l3/engine", { project, engine }),
    queryKey: ["chat", project],
    update: (cached, engine) => cached && { ...cached, engine },
    failureMessage: "Couldn't change the L3 engine.",
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
