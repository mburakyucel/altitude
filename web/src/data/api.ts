import { useQuery, useQueryClient } from "@tanstack/react-query";
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

export const QuotaSchema = z
  .object({
    known: z.boolean(),
    five_hour: z.number().nullish(),
    seven_day: z.number().nullish(),
    at: z.number().nullish(),
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
 * /api/project's `decisions` are the same rows as the Inbox queue (altitude/tasks.py decisions()),
 * but the project page has always read them defensively — every field stays optional here so a
 * thin row still parses, while the card fields keep their types instead of arriving as `unknown`.
 */
export const ProjectDecisionSchema = DecisionSchema.partial().passthrough();

export const FyiSchema = z
  .object({
    project: z.string(),
    at: z.string().nullish(),
    text: z.string(),
    slug: z.string().nullish(),
  })
  .passthrough();

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
  })
  .passthrough();

export const OverviewSchema = z
  .object({
    projects: z.array(ProjectRowSchema),
    queue: z.array(DecisionSchema),
    fyis: z.array(FyiSchema),
    wip: WipSchema,
    quota: QuotaSchema,
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
  })
  .passthrough();

export const TaskMessageSchema = z
  .object({
    id: z.string(),
    at: z.string().nullish(),
    role: z.enum(["burak", "l2"]),
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

export const TranscriptEventSchema = z.object({
  seq: z.number(), source: z.string(), kind: z.string(), type: z.string(),
  at: z.string().nullish(), session_id: z.string().nullish(), text: z.string(),
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
  })
  .passthrough();

export const ChatViewSchema = z
  .object({
    history: z.array(ChatMessageSchema),
    busy: z.boolean(),
    l3: z.record(z.string(), z.unknown()).nullish(),
  })
  .passthrough();

export type Quota = z.infer<typeof QuotaSchema>;
export type Decision = z.infer<typeof DecisionSchema>;
export type ProjectDecision = z.infer<typeof ProjectDecisionSchema>;
export type Fyi = z.infer<typeof FyiSchema>;
export type Wip = z.infer<typeof WipSchema>;
export type ProjectRow = z.infer<typeof ProjectRowSchema>;
export type Restart = z.infer<typeof RestartSchema>;
export type Overview = z.infer<typeof OverviewSchema>;
export type TaskRow = z.infer<typeof TaskRowSchema>;
export type ProjectView = z.infer<typeof ProjectViewSchema>;
export type TaskMessage = z.infer<typeof TaskMessageSchema>;
export type TaskView = z.infer<typeof TaskViewSchema>;
export type TranscriptEvent = z.infer<typeof TranscriptEventSchema>;
export type Session = z.infer<typeof SessionSchema>;
export type MonitorView = z.infer<typeof MonitorSchema>;
export type DigestView = z.infer<typeof DigestSchema>;
export type ChatMessage = z.infer<typeof ChatMessageSchema>;
export type ChatView = z.infer<typeof ChatViewSchema>;

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

export function useTranscript(project: string, slug: string, engine: string, sessionId: string, raw: boolean) {
  const query = new URLSearchParams({ engine, session_id: sessionId, raw: raw ? "1" : "0" });
  return useQuery({
    queryKey: ["transcript", project, slug, engine, sessionId, raw],
    queryFn: async () => TranscriptSchema.parse(await api(`/api/transcript/${project}/${slug}?${query}`)),
    refetchInterval: 2_000,
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
    refetchInterval: pollInterval,
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
  return useOptimisticMutation<DecideInput, unknown, Overview>({
    mutationFn: (input) => post("/api/decide", input),
    queryKey: ["overview"],
    update: (cached, input) =>
      cached && {
        ...cached,
        queue: cached.queue.filter((d) => d.project !== input.project || d.slug !== input.slug),
      },
    failureMessage: "Couldn't record the decision — it was put back.",
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
    // Every action moves a task's state, which the Inbox badge and the Projects counts read from
    // ["overview"] — invalidate it too or both sit stale for a full 20s poll.
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
  return useOptimisticMutation<ProjectAddInput, unknown, Overview>({
    mutationFn: (input) => post("/api/project/add", input),
    queryKey: ["overview"],
    update: () => undefined,
    failureMessage: "Couldn't add the project.",
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
  session_id?: string | null;
  context_percent?: number | null;
  turns?: number | null;
  cost?: unknown;
  error?: string | null;
  engine?: string | null;
}

/** An explicit engine for one L3 turn; undefined leaves the choice to the project pin or the quota. */
export type ChatEngine = "claude" | "codex";

/**
 * POST /api/chat and stream the NDJSON reply: {"t": "..."} lines feed onText, the final
 * {"done": {...}} is returned (the caller surfaces done.error). Polling is paused for the
 * duration. Non-2xx throws ApiError — 409 means "L3 is busy": toast it, do not retry.
 */
export async function streamChat(
  project: string,
  text: string,
  onText: (chunk: string) => void,
  engine?: ChatEngine,
): Promise<ChatDone> {
  setChatStreaming(true);
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(engine ? { project, text, engine } : { project, text }),
    });
    if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
    if (!res.body) throw new ApiError(res.status, "no response body");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let done: ChatDone = {};
    const handleLine = (line: string) => {
      if (!line.trim()) return;
      let parsed: { t?: unknown; done?: ChatDone };
      try {
        parsed = JSON.parse(line) as { t?: unknown; done?: ChatDone };
      } catch {
        return; // tolerate a torn line
      }
      if (typeof parsed.t === "string") onText(parsed.t);
      if (parsed.done) done = parsed.done;
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
