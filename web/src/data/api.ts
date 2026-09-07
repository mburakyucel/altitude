import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { useOptimisticMutation } from "./useOptimisticMutation";

/**
 * The one place the UI talks to altd. One fetch wrapper, one zod schema per endpoint
 * (lenient at the edges: unknown keys pass through, optional fields are nullish, enum-like
 * strings stay plain strings so a new server value never breaks the page), one query hook
 * per GET endpoint (20s polling, paused while a chat stream is open), and mutation hooks
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

let chatStreaming = false;

/** streamChat() sets this; exported so the chat route can pause polling around a stream. */
export function setChatStreaming(on: boolean): void {
  chatStreaming = on;
}

export function isChatStreaming(): boolean {
  return chatStreaming;
}

const pollInterval = () => (chatStreaming ? false : 20_000);

// ---- schemas (mirror server.py responses; lenient at the edges) ------------------------

/**
 * monitor.quota(): the Claude seat's two windows. `known` is the routing contract — false once the
 * newest statusline snapshot passes its freshness age. `stale` then says the figures are still
 * here, only old; a quota with neither figure is genuinely unknown. Reset times are epoch seconds.
 */
export const QuotaSchema = z
  .object({
    known: z.boolean(),
    five_hour: z.number().nullish(),
    seven_day: z.number().nullish(),
    five_hour_resets: z.number().nullish(),
    seven_day_resets: z.number().nullish(),
    stale: z.boolean().nullish(),
    at: z.number().nullish(),
  })
  .passthrough();

/**
 * route.quota_codex(): the Codex seat's account-wide windows. Each window names its own length in
 * minutes; a window the provider does not report is absent, never zero. Resets are ISO strings.
 */
export const CodexQuotaSchema = z
  .object({
    known: z.boolean(),
    primary_used: z.number().nullish(),
    primary_window_minutes: z.number().nullish(),
    primary_resets: z.string().nullish(),
    secondary_used: z.number().nullish(),
    secondary_window_minutes: z.number().nullish(),
    secondary_resets: z.string().nullish(),
    plan_type: z.string().nullish(),
    read_at: z.string().nullish(),
    stale: z.boolean().nullish(),
    why: z.string().nullish(),
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

// Rows for tasks blocked on user input.
export const DecisionSchema = z
  .object({
    project: z.string(),
    slug: z.string(),
    kind: z.string().nullish(),
    title: z.string().nullish(),
    question: z.string().nullish(),
    context: z.string().nullish(),
    detail: z.string().nullish(),
    asked: z.string().nullish(),
    options: z.array(z.string()).nullish(),
  })
  .passthrough();

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
    // why: "dispatch" (state queued) or "resume" (blocked with resume_after) — digest.py wip().
    waiting: z.array(
      z.object({ project: z.string(), slug: z.string(), why: z.string().nullish() }).passthrough(),
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
    quota: QuotaSchema,
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
    inbox: z.array(z.record(z.string(), z.unknown())).nullish(),
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

export const TaskViewSchema = z
  .object({
    slug: z.string(),
    state: z.string().nullish(),
    title: z.string().nullish(),
    resume_after: z.string().nullish(),
    files: z.record(z.string(), z.string()).nullish(),
    messages: z.array(TaskMessageSchema).nullish(),
    events: z.array(z.record(z.string(), z.unknown())).nullish(),
    report_json: z.unknown().nullish(),
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
    at: z.unknown().nullish(),
  })
  .passthrough();

export const MonitorSchema = z
  .object({
    quota: QuotaSchema,
    quota_codex: CodexQuotaSchema.nullish(),
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
  })
  .passthrough();

/** Server-owned identity of the L3 turn running now; prompt content is deliberately absent. */
export const ActiveTurnSchema = z
  .object({
    id: z.string(),
    started_at: z.string(),
    trigger: z.string(),
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

export type Quota = z.infer<typeof QuotaSchema>;
export type CodexQuota = z.infer<typeof CodexQuotaSchema>;
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

export function useProject(name: string) {
  return useQuery({
    queryKey: ["project", name],
    queryFn: async () => ProjectViewSchema.parse(await api(`/api/project/${name}`)),
    refetchInterval: pollInterval,
    enabled: Boolean(name),
  });
}

export function useTask(project: string, slug: string) {
  return useQuery({
    queryKey: ["task", project, slug],
    queryFn: async () => TaskViewSchema.parse(await api(`/api/task/${project}/${slug}`)),
    refetchInterval: pollInterval,
    enabled: Boolean(project && slug),
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

export function useChat(project: string, limit = 60) {
  return useQuery({
    queryKey: ["chat", project],
    queryFn: async () => ChatViewSchema.parse(await api(`/api/chat/${project}?limit=${limit}`)),
    refetchInterval: (query) => {
      const view = query.state.data;
      return chatStreaming ? false : view?.active || view?.busy || view?.queued?.length ? 2_000 : 20_000;
    },
    enabled: Boolean(project),
  });
}

// ---- mutation hooks --------------------------------------------------------------------

export interface DecideInput {
  project: string;
  slug: string;
  /** Index into the decision's options list (the server does int(option)). */
  option?: number;
  note?: string;
}

export function useDecide() {
  const queryClient = useQueryClient();
  return useMutation<unknown, Error, DecideInput>({
    mutationFn: (input) => post("/api/decide", input),
    // The card collapses first (200ms), then the next read drops the row and moves the task.
    onSettled: (_out, _error, input) => {
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      void queryClient.invalidateQueries({ queryKey: ["project", input.project] });
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
  return useMutation<unknown, Error, ProjectAddInput>({
    mutationFn: (input) => post("/api/project/add", input),
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
  return useOptimisticMutation<{ name: string }, unknown, Overview>({
    mutationFn: (input) => post("/api/project/remove", input),
    queryKey: ["overview"],
    update: () => undefined,
    failureMessage: "Couldn't remove the project.",
  });
}

export interface L2MessageInput {
  project: string;
  slug: string;
  text: string;
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
 * reader takes both. Polling is paused for the duration. Non-2xx throws ApiError.
 */
export async function streamChat(
  project: string,
  text: string,
  handlers: ChatStreamHandlers,
): Promise<ChatSent> {
  setChatStreaming(true);
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, text }),
    });
    if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
    if (!res.body) throw new ApiError(res.status, "no response body");
    handlers.onAccepted?.();

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
          handlers.onTurn?.(turn.data);
        }
      }
      if (typeof parsed.t === "string") handlers.onText(parsed.t);
      if (parsed.done) done = { ...done, ...parsed.done };
      if (parsed.queued) done = { ...done, queued: QueuedMessageSchema.parse(parsed.queued) };
    };

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
    return done;
  } finally {
    setChatStreaming(false);
  }
}
