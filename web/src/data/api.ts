import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import { z } from "zod";
import { readAlertState } from "./alerts";
import { finishPending, forgetPending, handBack, overlayOverview, overlayProject, overlayTask, startPending, usePending } from "./pending";
import type { PendingAnswer, PendingWrite } from "./pending";
import { forgetRecovery } from "./recovery";
import { useRefused, useSave, useSaving } from "./saves";
import { useRequest } from "./useRequest";

/**
 * The one place the UI talks to altd. One fetch wrapper, one zod schema per endpoint
 * (lenient at the edges: unknown keys pass through, optional fields are nullish, enum-like
 * strings stay plain strings so a new server value never breaks the page), one query hook
 * per GET endpoint (20s polling, faster for active conversations), one change stream that refreshes
 * task, decision and project queries as their records move, and a mutation for every POST. Answers and
 * approvals show at the press through the pending writes in ./pending, and settings through the saves in
 * ./saves, both overlaid on the reads.
 *
 * Paths are interpolated raw (no encodeURIComponent): the server matches path parts without
 * percent-decoding, and project/task names are slugs.
 */

/** Typed error: HTTP status plus the server's {"error": ...} message when present. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string, readonly code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Every API reply of 401 means this browser is not paired (any more): the pairing gate listens for it. */
export const UNPAIRED_EVENT = "altitude:unpaired";

async function failure(res: Response): Promise<ApiError> {
  if (res.status === 401) window.dispatchEvent(new Event(UNPAIRED_EVENT));
  try {
    const body = await res.json() as { error?: unknown; code?: unknown };
    return new ApiError(res.status, typeof body.error === "string" && body.error ? body.error : `HTTP ${res.status}`, typeof body.code === "string" ? body.code : undefined);
  } catch { return new ApiError(res.status, `HTTP ${res.status}`); }
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
  if (!res.ok) throw await failure(res);
  return (await res.json()) as T;
}

function post<T = unknown>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}

// ---- pairing ---------------------------------------------------------------------------

/** How this browser reaches Altitude (SPEC.md §3.16): `local` is the computer running it, `check` whether
 * Altitude can check this browser's trust in its certificate, and `command` whether `alt trust` on this computer
 * can trust it. */
const TrustSchema = z.object({
  local: z.boolean(), https: z.boolean(), check: z.boolean(), command: z.boolean(),
  guide: z.string().optional(),
  certificate: z.object({ name: z.string() }).nullish(),
});
export type Trust = z.infer<typeof TrustSchema>;
export const AccessSchema = z.object({ paired: z.boolean(), device: z.string().nullish(), trust: TrustSchema }).passthrough();
export type Access = z.infer<typeof AccessSchema>;
export const DeviceSchema = z.object({ id: z.string(), name: z.string(), paired: z.string(), used: z.string() }).passthrough();
export type Device = z.infer<typeof DeviceSchema>;
const CertificateSchema = z.union([
  z.object({ name: z.string(), expires: z.string(), sha256: z.string(), scope: z.string() }),
  z.object({ error: z.string() }),
]);
export type Certificate = z.infer<typeof CertificateSchema>;
const DevicesSchema = z.object({ devices: z.array(DeviceSchema), current: z.string().nullish(), certificate: CertificateSchema.nullish() }).passthrough();
export type Devices = z.infer<typeof DevicesSchema>;
export const PairingCodeSchema = z.object({
  code: z.string(), minutes: z.number(), address: z.string().nullish(), qr: z.array(z.string()).nullish(),
  certificate: z.object({ name: z.string(), sha256: z.array(z.string()) }).nullish(),
}).passthrough();
export type PairingCode = z.infer<typeof PairingCodeSchema>;

export async function readAccess(): Promise<Access> {
  return AccessSchema.parse(await api("/api/access"));
}

/** Pair this browser with a code from `alt pair` or another device's Settings. The Home Screen app says so,
 * because it keeps its own cookies apart from Safari's. */
export async function pairDevice(code: string): Promise<void> {
  const standalone = window.matchMedia?.("(display-mode: standalone)").matches
    || (navigator as Navigator & { standalone?: boolean }).standalone === true;
  await post("/api/pair", { code, standalone });
}

export type TrustCheck = "trusted" | "untrusted" | "unknown";

/** Ask whether this browser trusts Altitude's certificate. After the challenge, Altitude presents a second
 * certificate on new connections, which a browser that does not trust the CA refuses: the fetch rejects and Altitude
 * records the refusal, which the next attempt reads. A connection opened before the challenge (`retry`) tries again,
 * and an expired challenge (404) starts over. Three attempts without an answer, including a refusal Altitude never
 * heard about, mean Altitude could not check: a failed request alone never says the browser is untrusted. */
export async function checkTrust(): Promise<TrustCheck> {
  let challenge = "";
  for (let attempt = 0; attempt < 3; attempt++) {
    let reply: Response;
    try {
      if (!challenge) challenge = (await post<{ challenge: string }>("/api/trust", {})).challenge;
    } catch {
      return "unknown";
    }
    try {
      reply = await fetch(`/api/trust/${encodeURIComponent(challenge)}`, { cache: "no-store", headers: { Accept: "application/json" } });
    } catch {
      continue;
    }
    if (reply.status === 404) challenge = "";
    else if (!reply.ok) return "unknown";
    else {
      const answer = await reply.json() as { trusted?: unknown; retry?: unknown };
      if (answer.retry !== true) return answer.trusted === true ? "trusted" : "untrusted";
    }
  }
  return "unknown";
}

/**
 * Paired devices; another one being removed has already left the list. This browser's own row stays, saying
 * Removing…, until the pairing screen replaces the page.
 */
export function useDevices() {
  const removing = useSaving<string>(["devices"]);
  const select = useCallback((data: Devices) => removing.length
    ? { ...data, devices: data.devices.filter((device) => device.id === data.current || !removing.includes(device.id)) } : data, [removing]);
  return useQuery({ queryKey: ["devices"], queryFn: async () => DevicesSchema.parse(await api("/api/devices")), select });
}

/** Remove a device by id. The list is read again, so removing this browser reaches the pairing screen at once. */
export function useRemoveDevice() {
  return useSave<string, Devices>(["devices"], revokeDevice, (client) => client.invalidateQueries({ queryKey: ["devices"] }));
}

export async function makePairingCode(): Promise<PairingCode> {
  return PairingCodeSchema.parse(await post("/api/devices/code", {}));
}

export const PhoneShareSchema = z.object({ link: z.string(), seconds: z.number(), name: z.string(), sha256: z.string(), qr: z.array(z.string()) }).passthrough();
export type PhoneShare = z.infer<typeof PhoneShareSchema>;

/** Set up a device: open the service's ten-minute certificate share window, replacing an earlier one. */
export async function openPhoneShare(): Promise<PhoneShare> {
  return PhoneShareSchema.parse(await post("/api/devices/share", {}));
}

export async function closePhoneShare(link: string): Promise<void> {
  await post("/api/devices/share-close", { link });
}

export async function revokeDevice(id: string): Promise<Devices> {
  return DevicesSchema.parse(await post("/api/devices/revoke", { id }));
}

// ---- polling and the change stream -----------------------------------------------------

const pollInterval = () => 20_000;
const CHANGE_RECONNECT_MS = 5_000;

/**
 * The app's one subscription to `GET /api/changes`, mounted by the shell. A `change` event names the
 * projects whose task, decision or hold records moved; their mounted project and task queries refetch
 * with the overview, monitor and repairs (a repair's record moving sends an event naming no project). Every open, first or after a dropped connection or daemon activation,
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
      void queryClient.invalidateQueries({ queryKey: ["repairs"] });
      void queryClient.invalidateQueries({ queryKey: ["repair"] });
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
 * One seat's reading, as that seat reports it. A seat names either a five-hour and a seven-day
 * window or windows that name their own length in minutes; a window the seat does not
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

/** Files an owner showed on a reply or question revision (altitude/tasks.py shown_view): what its card shows,
 * whether its saved files are all there, and the reader address of a later version of the same document. */
export const ShownSchema = z.object({
  url: z.string().nullable(), available: z.boolean(), newer_url: z.string().nullable(),
  document: z.object({ name: z.string(), path: z.string(), heading: z.string(), excerpt: z.string() }).nullable(),
  images: z.array(z.object({ name: z.string(), path: z.string(), url: z.string(), width: z.number(), height: z.number(), embedded: z.boolean() })),
});
export type Shown = z.infer<typeof ShownSchema>;
export type ShownImage = Shown["images"][number];

/** The pictures a held PR's commit adds, replaces or removes (altitude/tasks.py pr_images_view): one entry per path
 * with the saved copy of each side it has, and the changed pictures its card cannot show. */
const PrPictureSideSchema = z.object({ url: z.string(), width: z.number(), height: z.number() });
export const PrImagesSchema = z.object({
  available: z.boolean(),
  pictures: z.array(z.object({ path: z.string(), name: z.string(), change: z.enum(["added", "changed", "removed"]),
    before: PrPictureSideSchema.nullable(), after: PrPictureSideSchema.nullable() })),
  not_shown: z.array(z.string()),
});
export type PrImages = z.infer<typeof PrImagesSchema>;

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
    shown: ShownSchema.nullish(),
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
    /** The task terminal's notice for this question's `run` command: it looks finished, or the terminal ended first. */
    ran: z.object({ at: z.string(), command: z.string(), finished: z.boolean() }).nullish(),
    asked_again: z.boolean().nullish(),
    alert_held: z.boolean().nullish(),
    /** The identity every alert uses: the question group since the operator's latest reply (altitude/digest.py). */
    alert_key: z.string().nullish(),
    pr: z.number().nullish(),
    pr_images: PrImagesSchema.nullish(),
    review_hold_id: z.string().nullish(),
    /** A proposal or merge question's review (SPEC.md §3.8.3): its kind's latest review, or state "none". */
    review: z.object({
      subject: z.enum(["proposal", "changes"]), id: z.string().nullable(),
      state: z.enum(["none", "requested", "running", "completed", "failed", "cancelled", "withdrawn"]),
      findings: z.number().default(0), open: z.number().default(0),
      reconciled: z.boolean().default(false), earlier: z.boolean().default(false),
      skipped_by: z.enum(["l2", "operator"]).nullish(), reason: z.string().default(""),
    }).nullish(),
  })
  .passthrough();

/**
 * An operator step (altitude/tasks.py `publish_step`): a command only the operator can run, which L3 handed them
 * for the project. It belongs to no task and names the `tasks` waiting on it.
 */
export const StepSchema = z.object({
  project: z.string(), slug: z.null(), kind: z.literal("step"), id: z.string(), question: z.string(),
  asked_by: z.literal("l3"), asked: z.string(), alert_key: z.string(), alert_held: z.boolean().nullish(),
  tasks: z.array(z.object({ slug: z.string(), title: z.string() })),
});

/**
 * A routine that paused itself (altitude/project_agents.py `needs`): it belongs to no task, carries the agent's id
 * and name and its last failed run, and has no `alert_key`, so it never alerts a device.
 */
export const AgentRowSchema = z.object({
  project: z.string(), slug: z.null(), kind: z.literal("agent"), id: z.string(), agent: z.string(), title: z.string(),
  run: z.number().nullable(), question: z.string(), asked_by: z.literal("altitude"), asked: z.string(), since: z.string(),
});

export const QuestionGroupSchema = z.object({
  id: z.string(), revision: z.number(), anchor_id: z.string().nullish(),
  questions: z.array(DecisionSchema),
}).passthrough();

/**
 * /api/project's `decisions` are the same rows as the overview queue (altitude/tasks.py decisions()),
 * but the project page has always read them defensively — every field stays optional here so a
 * thin row still parses, while the card fields keep their types instead of arriving as `unknown`. An operator step
 * has no task, so its `slug` is null.
 */
export const ProjectDecisionSchema = DecisionSchema.partial().passthrough().extend({ slug: z.string().nullish() });

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
    /** False only when the engine's executable is missing on the computer running Altitude. */
    installed: z.boolean().nullish(),
    known: z.boolean(),
    stale: z.boolean().nullish(),
    at: z.string().nullish(),
    /** The router's reserve (config.RESERVE): past this share of a window optional work is skipped. */
    reserve: z.number(),
  })
  .passthrough();

/**
 * A model choice tried ahead of routing (config.validate_choice): an engine with an optional model, an
 * effort, or both. Null is Auto. New tasks holds one for every project; each project holds one for its L3.
 */
export const ChoiceSchema = z.object({
  engine: z.string().nullish(), model: z.string().nullish(), effort: z.string().nullish(),
}).nullable();
export type Choice = z.infer<typeof ChoiceSchema>;
/** config.choice_options(): what a choice offers, in the seam's words. */
const ChoiceOptionsSchema = z.object({
  models: z.array(z.object({ engine: z.string(), model: z.string().nullable(), label: z.string() })),
  efforts: z.record(z.string(), z.array(z.object({ value: z.string(), label: z.string() }))),
  task_example: z.string().optional(),
});
export type ChoiceOptions = z.infer<typeof ChoiceOptionsSchema>;
/** server.new_tasks_view(): the choice for every project's new tasks, why it cannot start now, and Only-engine projects. */
export const NewTasksSchema = ChoiceOptionsSchema.extend({
  value: ChoiceSchema,
  unavailable: z.string().nullish(),
  only: z.array(z.object({ project: z.string(), engine: z.string() })),
});
export type NewTasks = z.infer<typeof NewTasksSchema>;

/**
 * An installed copy's version and the newer release it follows that its daemon last found (null for a source
 * deployment). `attempt` is an automatic or requested update that has not reached its version yet; `waiting_for`
 * names the work a pending automatic update waits for, as the daemon's last quiet-point check found it.
 */
export const UpdateSchema = z.object({
  automatic_pending: z.boolean(),
  current: z.string(),
  available: z.object({ version: z.string(), notes: z.string() }).nullish(),
  check: z.boolean(),
  automatic: z.boolean(),
  installed: z.object({ version: z.string(), notes: z.string() }).nullable(),
  command: z.string().nullable(),
  managed: z.literal("image").optional(),
  reason: z.string().optional(),
  checked: z.string().nullish(),
  /** `passing`: the failure's cause passes on its own; `retry`: the attempt repeated one that failed for the same release. */
  attempt: z.object({ version: z.string(), state: z.enum(["running", "failed"]), error: z.string().nullish(),
    passing: z.boolean().optional(), retry: z.boolean().optional() }).passthrough().nullish(),
  waiting_for: z.array(z.string()),
});

const ContainerLifecycleSchema = z.object({
  ready: z.boolean(), reason: z.string().nullable(), instance: z.string().nullable(),
  continue_command: z.string().optional(), admitted_calls_active: z.boolean().optional(),
});
export type ContainerLifecycle = z.infer<typeof ContainerLifecycleSchema>;

/** One suggested task as its card draws it (suggestions.item); target, Seen in and count are the review's alone. */
export const ImprovementSuggestionSchema = z.object({
  ref: z.string(),
  n: z.number(),
  target: z.string().optional(),
  title: z.string(),
  explanation: z.string(),
  /** Each cited task once; a cited project chat has no slug. */
  seen: z.array(z.object({ slug: z.string().nullable(), title: z.string() })).default([]),
  count: z.string().nullish(),
  state: z.enum(["undecided", "accepted", "dismissed", "not decided"]),
  task: z.string().nullable(),
  undo: z.boolean(),
  opening: z.boolean(),
  /** Undecided, or accepted without its task for a week after the press: Needs you lists its card. */
  waits: z.boolean(),
});
export type ImprovementSuggestion = z.infer<typeof ImprovementSuggestionSchema>;
/** One improvement review as chat and Needs you show it (improve.view): its running line, ending or card. */
export const ImprovementRunSchema = z.object({
  kind: z.literal("review"),
  id: z.string(),
  state: z.enum(["reading", "checking", "posted", "nothing", "stopped", "failed"]),
  started: z.string(),
  ended: z.string().nullable(),
  posted: z.string().nullish(),
  tasks: z.number(),
  window: z.string(),
  reason: z.string().nullable(),
  line: z.string().optional(),
  suggestions: z.array(ImprovementSuggestionSchema),
  details: z.array(z.tuple([z.string(), z.string()])),
});
export type ImprovementRun = z.infer<typeof ImprovementRunSchema>;
/** A routine run's suggested tasks (project_agents.suggested): what L3 is checking, kept and set aside. */
export const AgentSuggestionsSchema = z.object({
  kind: z.literal("agent"),
  /** The run's ref, agent:<agent>/<n>; its suggestions' refs extend it. */
  id: z.string(),
  agent: z.string(), n: z.number(), name: z.string(),
  /** What fired the run, as its page says it. */
  line: z.string().nullable(),
  at: z.string(), posted: z.string().nullable(),
  /** How many L3 is checking; nothing is shown before it posts. */
  checking: z.number(),
  suggestions: z.array(ImprovementSuggestionSchema),
  /** A check past its deadline sets all aside with no reason of L3's: `unfinished` says why once. */
  set_aside: z.array(z.object({ n: z.number(), title: z.string(), reason: z.string().nullable() })),
  unfinished: z.boolean(),
});
export type AgentSuggestions = z.infer<typeof AgentSuggestionsSchema>;
/** A card of suggested tasks: a Weekly review's or a routine run's. */
export type SuggestionCard = ImprovementRun | AgentSuggestions;

/**
 * A routine's definition (design/wireframes/project-agents/PROPOSAL.md): its brief, schedules in the machine's
 * clock (days 0 Monday to 6 Sunday, "HH:MM"), Tell me in chat, model choice (null is the default model) and per-run
 * limit in minutes. Every sentence about it (schedules, "Next: …", outcomes) is written by the server.
 */
export const ProjectAgentDefinitionSchema = z.object({
  name: z.string(),
  brief: z.string(),
  schedules: z.array(z.object({ days: z.array(z.number()), time: z.string() })),
  /** The trigger "When it's sent an event": `alt agent send` from this computer starts or joins a run. */
  events: z.boolean(),
  tell: z.enum(["found", "every", "never"]),
  model: ChoiceSchema,
  limit: z.number(),
  /** What Altitude may change when a run asks (`alt act`): ["worktrees"] for worktrees and branches; empty, it only reports. */
  may: z.array(z.literal("worktrees")),
  /** The operator's commands, in order: a run asks for one by its name (`alt act run NAME`) and Altitude runs the
   * words as the operator. Set only by the operator's own press or terminal, never by an offer. */
  commands: z.array(z.object({ name: z.string(), words: z.string() })),
});
export type ProjectAgentDefinition = z.infer<typeof ProjectAgentDefinitionSchema>;
const ToneSchema = z.enum(["accent", "danger", "muted"]);
/** L3's offer to create or change a routine, drawn from the definition it stored; only the operator's press saves it. */
export const ProjectAgentOfferSchema = z.object({
  id: z.string(), turn_id: z.string(), kind: z.enum(["new", "change"]),
  agent: z.string().nullable(), agent_name: z.string().nullable(),
  definition: ProjectAgentDefinitionSchema, line: z.string(), changes: z.array(z.string()), on: z.boolean(),
  /** A new routine's "May change worktrees and branches"; null when it only reports or the offer is a change. */
  may: z.string().nullable(),
  state: z.enum(["open", "created", "saved", "refused", "lapsed"]), reason: z.string().nullable(), result_agent: z.string().nullable(),
});
export type ProjectAgentOffer = z.infer<typeof ProjectAgentOfferSchema>;

export const OverviewSchema = z
  .object({
    deployment: z.enum(["native", "container"]).optional(),
    lifecycle: ContainerLifecycleSchema.nullish(),
    projects: z.array(ProjectRowSchema),
    queue: z.array(z.union([StepSchema, AgentRowSchema, DecisionSchema])),
    /** Improvement cards waiting for a decision or a task: listed after the queue, never counted with it. */
    suggestions: z.array(z.discriminatedUnion("kind", [ImprovementRunSchema, AgentSuggestionsSchema])
      .and(z.object({ project: z.string() }))).default([]),
    wip: WipSchema,
    quota: SeatQuotaSchema,
    engines: z.array(EngineReadoutSchema).default([]),
    new_tasks: NewTasksSchema.nullish(),
    /** The folders First run scans, named relative to home. */
    roots: z.array(z.string()).default([]),
    /** The operator's name (saved, ALTITUDE_OPERATOR or Git's user.name), shown in the rail's operator row; absent reads “You”. */
    operator: z.string().nullish(),
    restart: RestartSchema.nullish(),
    update: UpdateSchema.nullish(),
    /** The repair running now, and the newest app repair's questions that are open or expired with its session. */
    repair: z.object({
      running: z.object({ id: z.string().nullable(), route: z.string(), title: z.string().nullish() }).nullable(),
      questions: z.array(z.object({ id: z.string(), kind: z.enum(["ask", "step"]), title: z.string(), reason: z.string().nullable(),
        command: z.string(), asked: z.string(), answer: z.enum(["yes", "no", "ran", "expired"]).nullable(),
        repair: z.string(), repair_title: z.string().nullish() })),
    }).nullish(),
    now: z.string().nullish(),
  })
  .passthrough();

const PlannedWaitSchema = z.object({ reason: z.string(), after: z.string().nullable(), after_title: z.string().nullish() });

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
    /** Who suggested the task: a routine's name or the Weekly improvement review. */
    origin: z.string().nullish(),
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
    improvements: z.array(ImprovementRunSchema).default([]),
    /** Routine runs waiting or running (Work's Current rows), routines not retired, and L3's offers for chat. */
    project_agents: z.object({
      runs: z.array(z.object({ agent: z.string(), name: z.string(), n: z.number(), state: z.enum(["waiting", "running"]), line: z.string() })),
      count: z.number(),
      offers: z.array(ProjectAgentOfferSchema),
    }).nullish(),
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

/** Create task under the reply of `offerTurn`: Altitude writes the message, and a repeat returns the saved one. */
export async function sendCreateTask(project: string, offerTurn: string) {
  return api<{ queued?: unknown; sent?: boolean }>("/api/chat", { method: "POST", body: JSON.stringify({ project, offer_turn: offerTurn }) });
}

/** A press on an improvement review: Stop on its running line; Make a task, Dismiss, Undo or Try again on a suggestion. */
export async function sendImprovement(project: string, action: "stop" | "accept" | "dismiss" | "undo" | "retry", requestId: string, ref?: string) {
  return api<{ ok: boolean }>("/api/improve", { method: "POST", body: JSON.stringify({ project, action, ref, request_id: requestId }) });
}

/** Dismiss or Undo on a suggestion: the operator's own choice, folded or unfolded in chat and Needs you at the press. */
export type SuggestionChange = { project: string; ref: string; action: "dismiss" | "undo" };
const SUGGESTION_SAVES = ["suggestion"];
const suggestionReads = ({ project }: SuggestionChange) => [["overview"], ["project", project]];

/** A card as Altitude will save it: a dismissed suggestion folds with Undo, an undone one waits for a decision again. */
export const withSuggestions = <Card extends SuggestionCard>(run: Card, project: string, changes: SuggestionChange[]): Card => {
  const mine = changes.filter((change) => change.project === project && change.ref.startsWith(`${run.id}/`));
  return mine.length ? { ...run, suggestions: run.suggestions.map((suggestion) => mine.reduce((shown, change) => change.ref !== shown.ref ? shown
    : change.action === "dismiss" ? { ...shown, state: "dismissed" as const, undo: true } : { ...shown, state: "undecided" as const, undo: false }, suggestion)) } : run;
};
/** Needs you lists a card while a suggestion waits for a decision, or for its task a week after the press (suggestions.item). */
const waitsInNeedsYou = (run: SuggestionCard) => run.suggestions.some((suggestion) => suggestion.state === "undecided" || suggestion.state === "accepted" && suggestion.waits);

/** The card a suggestion belongs to, from the read that shows it: a review's in its project, a routine run's on its page. */
function cardOf(client: QueryClient, project: string, ref: string): SuggestionCard | undefined {
  const id = ref.slice(0, ref.lastIndexOf("/"));
  const agent = /^agent:([^/]+)\/(\d+)$/.exec(id);
  if (agent) return client.getQueryData<ProjectAgentRunDetail>([...agentsKey(project), agent[1], "runs", Number(agent[2])])?.suggested ?? undefined;
  return client.getQueryData<ProjectView>(["project", project])?.improvements.find((item) => item.id === id);
}

/**
 * Needs you's runs with the choices on their way. Undo in chat or on a run's page brings back a run Needs you had
 * dropped, placed where Altitude lists it (by project, newest post first); a run with nothing left waiting leaves.
 */
function waitingRuns(client: QueryClient, overview: Overview, changes: SuggestionChange[]): Overview["suggestions"] {
  const order = overview.projects.map((project) => project.name);
  const runs = [...overview.suggestions];
  for (const change of changes) {
    if (change.action !== "undo" || runs.some((run) => run.project === change.project && change.ref.startsWith(`${run.id}/`))) continue;
    const run = cardOf(client, change.project, change.ref);
    if (!run) continue;
    const before = runs.findIndex((item) => order.indexOf(item.project) > order.indexOf(change.project)
      || item.project === change.project && (item.posted ?? "") < (run.posted ?? ""));
    runs.splice(before < 0 ? runs.length : before, 0, { ...run, project: change.project });
  }
  return runs.map((run) => ({ ...withSuggestions(run, run.project, changes), project: run.project })).filter(waitsInNeedsYou);
}

export function useSuggestionChanges() {
  return useSaving<SuggestionChange>(SUGGESTION_SAVES);
}

const suggestionId = (change: SuggestionChange) => `${change.project}/${change.ref}`;
/** Refused Dismiss and Undo presses, by project and ref. A press the record no longer allows (409) needs no reason: the card shows where it stands. */
export function useSuggestionRefusals() {
  const refused = useRefused<SuggestionChange>(SUGGESTION_SAVES, suggestionId);
  return useMemo(() => new Map([...refused].filter(([, { error }]) => !(error instanceof ApiError && error.status === 409))), [refused]);
}

export function useSaveSuggestion() {
  return useSave<SuggestionChange, unknown>(SUGGESTION_SAVES, ({ project, ref, action }) => sendImprovement(project, action, crypto.randomUUID().replaceAll("-", ""), ref),
    (client, _result, { project }) => Promise.all([
      client.refetchQueries({ queryKey: ["project", project], exact: true }),
      client.refetchQueries({ queryKey: ["overview"], exact: true }),
      client.invalidateQueries({ queryKey: ["chat", project] }),
      client.invalidateQueries({ queryKey: agentsKey(project) }),
    ]), suggestionReads);
}

/** Turn on, Pause and Resume of the Weekly improvement review, or its Run now; `terms` is the sheet version the
 * operator's Turn on agreed to. */
export async function sendWeekly(project: string, change: { action: "weekly"; on: boolean } | { action: "run" }, terms?: string) {
  return api<{ ok: boolean; run?: string }>("/api/improve", {
    method: "POST",
    body: JSON.stringify({ project, ...change, request_id: crypto.randomUUID().replaceAll("-", ""), ...(terms ? { terms } : {}) }),
  });
}

// ---- project agents (design/wireframes/project-agents/PROPOSAL.md) ----------------------------

const AgentStateSchema = z.enum(["new", "on", "paused", "retired"]);
export const ProjectAgentSummarySchema = z.object({
  id: z.string(), name: z.string(), state: AgentStateSchema,
  triggers: z.string(), news: z.string(), news_at: z.string().nullable(),
  /** "Changes worktrees and branches · Runs 2 commands" after the triggers; null for a routine that only reports. */
  may: z.string().nullable(),
  cue: z.enum(["running", "paused"]).nullable(), retired_line: z.string().nullable(),
});
export type ProjectAgentSummary = z.infer<typeof ProjectAgentSummarySchema>;
/** A built-in routine; one that may change things carries the terms its Turn on sheet asks about. */
const BuiltInSchema = z.object({ key: z.string(), name: z.string(), line: z.string(), terms: z.string().nullish() });
/** What the Weekly improvement review's sheet shows; Turn on sends its version back. */
const WeeklyTermsSchema = z.object({ version: z.string(), title: z.string(), body: z.string(), sent: z.string() });
export type WeeklyTerms = z.infer<typeof WeeklyTermsSchema>;
/** The machine's zone label and its UTC offset in minutes now: schedule times are written in this clock. */
const AgentClockSchema = z.object({ zone: z.string(), offset: z.number() });
export const ProjectAgentsSchema = z.object({
  clock: AgentClockSchema,
  budget: z.object({ minutes: z.number(), choices: z.array(z.number()), used: z.number(), spent: z.boolean() }),
  agents: z.array(ProjectAgentSummarySchema),
  retired: z.array(ProjectAgentSummarySchema),
  builtins: z.array(BuiltInSchema),
  /** Altitude's own project: the Weekly improvement review, the operator's routine or a Built in row (improve.agent). */
  weekly: z.object({ terms: WeeklyTermsSchema, agent: ProjectAgentSummarySchema.nullable(), builtin: BuiltInSchema.nullable() }).nullish(),
  offers: z.array(ProjectAgentOfferSchema),
});
export type ProjectAgents = z.infer<typeof ProjectAgentsSchema>;
export type AgentClock = z.infer<typeof AgentClockSchema>;
export const ProjectAgentRunSchema = z.object({
  n: z.number(),
  state: z.enum(["waiting", "running", "found", "nothing", "failed", "stopped", "limit", "skipped"]),
  outcome: z.string(), tone: ToneSchema,
  at: z.string(), started: z.string().nullable(), late: z.boolean(), ended: z.string().nullable(),
  minutes: z.number().nullable(), tokens: z.number().nullable(), line: z.string().nullable(),
  page: z.boolean(), trigger_line: z.string().nullable(),
  /** The events the run took, oldest first: each one's arrival and first line. */
  events: z.array(z.object({ at: z.string(), line: z.string() })),
});
export type ProjectAgentRun = z.infer<typeof ProjectAgentRunSchema>;
const AgentActionSchema = z.enum(["run", "pause", "resume", "turn_on", "stop", "open"]);
export type ProjectAgentAction = z.infer<typeof AgentActionSchema>;
export const ProjectAgentDetailSchema = z.object({
  id: z.string(), name: z.string(), state: AgentStateSchema, builtin: z.string().nullable(),
  /** Sent back with an edit: a stale version is refused. */
  version: z.number(),
  status: z.object({ line: z.string(), note: z.string().nullable(), actions: z.array(AgentActionSchema), run: z.number().nullable(), spinning: z.boolean() }),
  definition: ProjectAgentDefinitionSchema,
  definition_line: z.string(),
  /** "May change worktrees and branches, with Undo" and "May run as you: prune-images and clear-cache", each when it
   * applies; empty for a routine that only reports. */
  may_lines: z.array(z.string()),
  /** The commands running now, which saving without them or with other words stops. */
  running_commands: z.array(z.string()),
  runs: z.array(ProjectAgentRunSchema),
  more: z.boolean(),
  /** With the event trigger: the command that sends it an event, the id the examples use when the command names the
   * routine by its name (else null), and examples to copy (project_agents.recipes). */
  send: z.object({ command: z.string(), id: z.string().nullable(), recipes: z.array(z.object({ what: z.string(), text: z.string() })) }).nullable(),
});
export type ProjectAgentDetail = z.infer<typeof ProjectAgentDetailSchema>;
/** One request a run made: its opening words and target ("Set aside worktree" agent-7c41e2d0), a muted line,
 * Undo while it can be undone, and why the last Undo was refused. A command (project_agents._command_row) has no
 * Undo: Stop while it runs, the danger tone for its own failure and the output it kept once it ended; a request for
 * something that is not a command name has no name. */
const AgentChangeSchema = z.object({
  n: z.number(), kind: z.enum(["worktree", "branch", "command"]),
  outcome: z.enum(["done", "kept", "refused", "failed", "running", "timed_out", "stopped", "not_started"]),
  head: z.string(), name: z.string().nullable(), note: z.string().nullable(), undone: z.boolean(), undo: z.boolean(),
  refused: z.string().nullable(),
  tone: z.literal("danger").nullish(), stop: z.boolean().optional(), stopping: z.boolean().optional(),
  output: z.string().nullish(),
});
export const ProjectAgentRunDetailSchema = ProjectAgentRunSchema.extend({
  agent: z.object({ id: z.string(), name: z.string() }),
  /** None until the run starts and its engine is chosen. */
  model: z.string().nullable(), limit: z.number(),
  /** The brief as the run read it. */
  brief: z.string(),
  report: z.string().nullable(),
  session: z.object({ engine: z.string(), session_id: z.string(), attempt: z.number() }).nullable(),
  /** The run's suggested tasks, when it made any. */
  suggested: AgentSuggestionsSchema.nullable(),
  /** What the run asked Altitude to change, in order (project_agents._change_row), and whether Undo all shows. */
  changes: z.array(AgentChangeSchema),
  undo_all: z.boolean(),
});
export type ProjectAgentRunDetail = z.infer<typeof ProjectAgentRunDetailSchema>;
export type AgentChange = z.infer<typeof AgentChangeSchema>;
const WeeklyActionSchema = z.enum(["run", "pause", "resume", "turn_on", "stop"]);
export type WeeklyAction = z.infer<typeof WeeklyActionSchema>;
/** The Weekly improvement review's page (improve.page): its status, recent runs and skipped weeks, and what it does. */
export const WeeklyReviewSchema = z.object({
  name: z.string(), terms: WeeklyTermsSchema, agreed: z.boolean(),
  status: z.object({
    line: z.string(), actions: z.array(WeeklyActionSchema), run: z.string().nullable(), spinning: z.boolean(), paused: z.boolean(),
    /** Run now's line under the card, or why it cannot run; absent while a review runs. */
    now: z.object({ line: z.string().nullable(), unavailable: z.string().nullable() }).nullable(),
  }),
  runs: z.array(z.object({
    id: z.string().nullable(), state: z.enum(["posted", "nothing", "failed", "stopped", "skipped"]), outcome: z.string(), tone: ToneSchema,
    at: z.string(), minutes: z.number().nullable(), line: z.string(),
  })),
  what: z.object({ text: z.string(), sent: z.string(), past: z.object({ line: z.string(), latest: z.string().nullable() }).nullable() }),
});
export type WeeklyReview = z.infer<typeof WeeklyReviewSchema>;

const agentsKey = (project: string) => ["project", project, "agents"];
type BudgetChange = { minutes: number };

/** A project's routines, budget and built-ins; a budget on its way shows at once. Under the project's key, so its
 * change events and press refetches refresh it. */
export function useProjectAgents(project: string, enabled = true) {
  const saving = useSaving<BudgetChange>(agentsKey(project));
  const select = useCallback((data: ProjectAgents) => saving.reduce((view, change) => ({ ...view, budget: { ...view.budget, minutes: change.minutes } }), data), [saving]);
  return useQuery({
    queryKey: agentsKey(project),
    queryFn: async () => ProjectAgentsSchema.parse(await api(`/api/agents/${project}`)),
    enabled: Boolean(project) && enabled,
    refetchInterval: pollInterval,
    select,
  });
}

/** One routine with its latest `runs` runs, read every two seconds while a run waits or runs. */
export function useProjectAgent(project: string, id: string, runs = 10) {
  return useQuery({
    queryKey: [...agentsKey(project), id, runs],
    queryFn: async () => ProjectAgentDetailSchema.parse(await api(`/api/agents/${project}/${id}?runs=${runs}`)),
    enabled: Boolean(project && id),
    // Show older keeps the loaded runs on screen while the longer list arrives.
    placeholderData: (previous) => previous,
    refetchInterval: (query) => query.state.data?.status.run != null ? 2_000 : pollInterval(),
  });
}

export function useProjectAgentRun(project: string, id: string, n: number) {
  return useQuery({
    queryKey: [...agentsKey(project), id, "runs", n],
    queryFn: async () => ProjectAgentRunDetailSchema.parse(await api(`/api/agents/${project}/${id}/runs/${n}`)),
    enabled: Boolean(project && id),
    // Read every two seconds while it waits, runs or L3 checks its suggestions.
    refetchInterval: (query) => ["waiting", "running"].includes(query.state.data?.state ?? "")
      || Boolean(query.state.data?.suggested?.checking) ? 2_000 : pollInterval(),
  });
}

/** The Weekly improvement review's page, read every two seconds while it runs. */
export function useWeeklyReview(project: string) {
  return useQuery({
    queryKey: [...agentsKey(project), "weekly"],
    queryFn: async () => WeeklyReviewSchema.parse(await api(`/api/agents/${project}/weekly`)),
    enabled: Boolean(project),
    refetchInterval: (query) => query.state.data?.status.run != null ? 2_000 : pollInterval(),
  });
}

export const pressId = () => crypto.randomUUID().replaceAll("-", "");

/** One press on routines. A retry of the same press passes its `requestId` again, so a press whose answer was lost
 * returns its first receipt instead of saving twice. */
export async function sendProjectAgent<T = Record<string, unknown>>(project: string, action: string, fields: Record<string, unknown> = {},
  requestId = pressId()) {
  return api<{ ok: true } & T>("/api/agents", {
    method: "POST",
    body: JSON.stringify({ project, action, request_id: requestId, ...fields }),
  });
}

/** The weekly budget; the save ends once the routines' read shows it. */
export function useSaveAgentBudget(project: string) {
  return useSave<BudgetChange, unknown>(agentsKey(project), ({ minutes }) => sendProjectAgent(project, "budget", { minutes }),
    (client) => client.refetchQueries({ queryKey: agentsKey(project), exact: true }));
}

export const TaskMessageSchema = z
  .object({
    id: z.string(),
    at: z.string().nullish(),
    role: z.string(),
    text: z.string(),
    summary: z.string().nullish(),
    /** A reopened task's earlier completion, with that completion's digest. */
    completion: z.boolean().nullish(),
    digest: z.string().nullish(),
    review_id: z.string().nullish(),
    delivery: z.object({ state: z.enum(["queued", "sending", "removed", "delivered", "unconfirmed"]), at: z.string().nullable(), removable: z.boolean().optional(), send_now: z.boolean().optional(), send_now_reason: z.string().nullish(), send_now_pending: z.boolean().optional() }).nullish(),
    images: z.array(MessageImageSchema).nullish(),
    /** An L2 reply's GIFs from one validation run, opened on the task's captures page. */
    captures: z.array(z.object({ name: z.string(), title: z.string() }).passthrough()).nullish(),
    capture_run: z.number().nullish(),
    shown: ShownSchema.nullish(),
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
  /** Distinct model requests behind the counters; unknown for provider aggregates. */
  requests: z.number().nullish(),
};

/** A session's newest own request input against its window; absent when no reliable reading exists. */
export const TokenContextSchema = z.object({
  tokens: z.number(),
  window: z.number().nullish(),
  percent: z.number().nullish(),
  observed_at: z.string().nullish(),
  engine: z.string().nullish(),
  session_id: z.string().nullish(),
});

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
  context: TokenContextSchema.nullish(),
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
  dispositions: z.array(z.object({ finding_id: z.string(), disposition: z.enum(["fixed", "dismissed", "open"]), reason: z.string() })).default([]),
  unresolved: z.array(z.string()).default([]),
  snapshot: ReviewSnapshotSchema.nullish(),
  reconciled: ReviewSnapshotSchema.extend({ reason: z.string() }).nullish(),
  coverage: z.enum(["current", "earlier", "unknown", "assessed"]),
  earlier: z.boolean().default(false), waiting: z.enum(["reviewer", "owner", "resume"]).nullish(),
  cancel_requested: z.boolean().nullish(), withdrawn_by: z.string().nullish(), withdrawal_reason: z.string().nullish(),
  can_withdraw: z.boolean(), can_cancel: z.boolean(), can_again: z.boolean(),
  /** The delivery a review belongs to: the PR whose merge closed it, the step after the PR that waits, or the current one. */
  merged_head: z.string().nullish(), merged_pr: z.number().nullish(), next: z.boolean().default(false),
}).passthrough();
export type Review = z.infer<typeof ReviewSchema>;
/** One delivery's reviews share this key: its merged head, "next" or "current". */
export const deliveryOf = (review: Review) => review.merged_head ?? (review.next ? "next" : "current");
/** `latest` is the kind's latest unmerged review (a finished task's last one otherwise); `open` counts the open findings of
 * every review of the kind in its delivery's merge gate, earlier ones included. */
const ReviewSubjectSchema = z.object({ available: z.boolean(), why: z.string(), latest: ReviewSchema.nullable(), open: z.number().default(0) });
/** A kind appears while a request could start or once it has a review; finished tasks keep only what was reviewed. */
export const TaskReviewSchema = z.object({
  history: z.array(ReviewSchema),
  subjects: z.object({ proposal: ReviewSubjectSchema.optional(), changes: ReviewSubjectSchema.optional() }),
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
    /** The project's GitHub page, for pull request links. */
    repository: z.string().nullish(),
    /** The engine, model and effort the task runs with, named as its chooser names them. */
    running_model: z.object({
      engine: z.string(), model: z.string().nullable(), effort: z.string(),
      chosen: z.string().nullable(), launched: z.string(), reported: z.string().nullable(),
    }).nullish(),
    files: z.record(z.string(), z.string()).nullish(),
    messages: z.array(TaskMessageSchema).nullish(),
    review: TaskReviewSchema.nullish(),
    question: DecisionSchema.nullish(),
    questions: z.array(DecisionSchema).nullish(),
    question_group: QuestionGroupSchema.nullish(),
    report_json: z.unknown().nullish(),
    token_usage: TokenUsageSchema.nullish(),
    live: z.unknown().nullish(),
    activity: z.object({
      generation: z.string().nullable(),
      state: z.enum(["available", "empty", "unavailable"]),
      commentary: z.object({ id: z.string(), text: z.string(), at: z.string().nullable(), time_kind: z.enum(["source", "unknown"]) }).nullable(),
      observation: z.object({ at: z.string().nullable(), label: z.string() }).nullable(),
      // Work the worker keeps running beside its turn; null when its engine reports none.
      background: z.array(z.object({
        id: z.string(), label: z.string(), kind: z.enum(["command", "agent"]), call: z.string().nullable(),
        started_at: z.string().nullable(), status: z.enum(["running", "completed", "failed", "stopped"]),
        ended_at: z.string().nullable(),
      })).nullish(),
      error: z.string().optional(),
    }).nullish(),
    steering: z.object({
      state: z.enum(["running", "stopping", "stopped", "resuming", "stop_unconfirmed", "idle"]),
      stop_id: z.string().nullable(), generation: z.string().nullable(), error: z.string().nullable(),
    }).nullish(),
    transfer: z.object({
      current_engine: z.string().nullable(), current_label: z.string().nullable(), eligible: z.boolean(), reason: z.string().nullish(),
      current_model: z.string().nullable(), current_model_label: z.string().nullable(), current_effort: z.string().nullable(), current_effort_label: z.string(),
      quota_limited: z.boolean(), refused: z.string().nullish(), resume_hold: z.string().nullish(), preparation: z.boolean(),
      targets: z.array(z.object({ engine: z.string(), label: z.string(), available: z.boolean(), reason: z.string().nullish(),
        default_effort: z.string().nullable(), efforts: z.array(z.object({ value: z.string(), label: z.string() })),
        models: z.array(z.object({ value: z.string(), model: z.string(), label: z.string(), available: z.boolean(), reason: z.string().nullish() })),
      })),
      observed: z.object({ attempt: z.number().nullable(), agent_id: z.string().nullable(), session_id: z.string().nullable(), block_id: z.string().nullable() }),
      request: z.object({ id: z.string(), status: z.string(), phase: z.string().nullish(), engine: z.string(), target_label: z.string(), at: z.string().nullish(), note: z.string().nullish(), now: z.boolean().nullish(),
        selection: z.object({ model: z.string(), effort: z.string().nullable() }).nullish(),
        by: z.string().nullish(), reason: z.string().nullish(),
      }).nullish(),
    }).nullish(),
  })
  .passthrough();

// One row of the Live session timeline. `role` says who speaks (user, assistant, tool, system); a tool call
// carries its tool, a one-line summary, and the tool_use_id its result row shares; a Codex command carries
// its own output.
export const TranscriptEventSchema = z.object({
  id: z.string(), order: z.string(), version: z.number(),
  source: z.string(), kind: z.string(), type: z.string(), role: z.string().nullish(),
  at: z.string().nullish(), session_id: z.string().nullish(), text: z.string(),
  tool: z.string().nullish(), summary: z.string().nullish(), tool_use_id: z.string().nullish(),
  output: z.string().nullish(), status: z.string().nullish(), error: z.boolean().nullish(),
  truncated: z.boolean().nullish(),
}).passthrough();
export const TranscriptSchema = z.object({
  project: z.string(), slug: z.string(), engine: z.string(), session_id: z.string(), attempt: z.number(), cursor: z.string(),
  lower: z.string(), next: z.string(), more: z.boolean(), reset: z.boolean(),
  has_earlier: z.boolean(), has_engine_records: z.boolean(), deleted: z.array(z.string()),
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

/** One row of Monitor's Routines (SPEC.md §3.14): what comes next, its cue, and the latest run that ran. */
export const MonitorAgentSchema = z.object({
  project: z.string(), id: z.string(), name: z.string(), next: z.string(), cue: z.enum(["running", "waiting", "paused"]).nullable(),
  last: z.object({ outcome: z.string(), at: z.string().nullable(), minutes: z.number().nullable(), tokens: z.number().nullable() }).nullable(),
});

export const MonitorSchema = z
  .object({
    seats: z.array(MonitorSeatSchema).nullish(),
    /** The router's reserve (config.RESERVE): each seat meter's line, past which optional work is skipped. */
    reserve: z.number(),
    routing: z.array(RoutingRowSchema).nullish(),
    sessions: z.array(SessionSchema),
    agents: z.array(MonitorAgentSchema),
    clock: AgentClockSchema,
  })
  .passthrough();

export const DigestSchema = z
  .object({
    text: z.string().nullish(),
  })
  .passthrough();

const ProjectMessageSchema = z.object({
  sender: z.string(), recipient: z.string(), exchange_id: z.string(), message_id: z.string(),
  summary: z.string(), reply_to: z.string().nullish(),
  supplied_turn_id: z.string().optional(),
  direction: z.enum(["sent", "incoming"]),
  status: z.enum(["sent", "queued", "supplied", "registration-changed"]),
});

const ChatDeliverySchema = z.object({ state: z.enum(["delivered", "unconfirmed"]), at: z.string() });

export const ChatMessageSchema = z
  .object({
    at: z.string().nullish(),
    role: z.string(),
    text: z.string(),
    project_message: ProjectMessageSchema.optional(),
    delivery: ChatDeliverySchema.optional(),
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
    /** On the assistant row of a turn that filed GitHub issues: their titles and links (SPEC.md §4.1). */
    issues: z.array(z.object({ title: z.string(), url: z.string() })).nullish(),
    /** On an assistant row: the task title its reply offers under Create task (SPEC.md §3.3). */
    offer: z.string().nullish(),
    /** On a user row: the turn whose Create task this message pressed. */
    offer_turn: z.string().nullish(),
    /** Retained interrupted chat history: its text is the partial reply, possibly empty. */
    interrupted: z.boolean().nullish(),
    /** On a user row the page sent directly: its send id. */
    request_id: z.string().nullish(),
    /** On a user row delivered from the queue: the queue rows it carries, each a send id when the page sent it. */
    queue_ids: z.array(z.string()).nullish(),
    /** On an improvement review's row (trigger "improvement"): the review it shows, read from the project view. */
    improvement: z.string().nullish(),
    /** On a routine's result row (trigger "project-agent"): the routine, its run when one is meant, and the dot. */
    project_agent: z.object({ agent: z.string(), n: z.number().nullable(), tone: ToneSchema }).nullish(),
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
    project_message: ProjectMessageSchema.optional(),
    images: z.array(MessageImageSchema).nullish(),
    /** Only on the acknowledgement of a message just queued: its place in the queue, 1 first. */
    position: z.number().nullish(),
    send_now: z.boolean().optional(),
    send_now_reason: z.string().nullish(),
    /** Set while the running turn takes this message in; it can no longer be removed. */
    sending: z.string().nullish(),
    /** A follow-up on a decision names its task (SPEC.md §5.2 note 6). */
    slug: z.string().nullish(),
    /** A Create task press: the turn whose reply it answers (SPEC.md §3.3). */
    offer_turn: z.string().nullish(),
    /** A kept message: already in the conversation under this turn, waiting for an available engine (SPEC.md §4.2). */
    turn_id: z.string().nullish(),
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
    send_now_reason: z.string().nullish(),
    l3: z.record(z.string(), z.unknown()).nullish(),
  })
  .passthrough();

export type MonitorSeat = z.infer<typeof MonitorSeatSchema>;
export type RoutingRow = z.infer<typeof RoutingRowSchema>;
export type Decision = z.infer<typeof DecisionSchema>;
export type Step = z.infer<typeof StepSchema>;
/** One Needs you item: a task's decision or a project's operator step. */
export type AgentRow = z.infer<typeof AgentRowSchema>;
export type Attention = Decision | Step | AgentRow;
export type ProjectDecision = z.infer<typeof ProjectDecisionSchema>;
export type Wip = z.infer<typeof WipSchema>;
export type ProjectRow = z.infer<typeof ProjectRowSchema>;
export type EngineReadout = z.infer<typeof EngineReadoutSchema>;
export type Restart = z.infer<typeof RestartSchema>;
export type Update = z.infer<typeof UpdateSchema>;
export type Overview = z.infer<typeof OverviewSchema>;
export type TaskRow = z.infer<typeof TaskRowSchema>;
export type ProjectView = z.infer<typeof ProjectViewSchema>;
export type TaskMessage = z.infer<typeof TaskMessageSchema>;
export type TokenSession = z.infer<typeof TokenSessionSchema>;
export type TaskTokenUsage = z.infer<typeof TokenUsageSchema>;
export type TokenContext = z.infer<typeof TokenContextSchema>;
export type TaskView = z.infer<typeof TaskViewSchema>;
export type TranscriptEvent = z.infer<typeof TranscriptEventSchema>;
export type Transcript = z.infer<typeof TranscriptSchema>;
export type Session = z.infer<typeof SessionSchema>;
export type MonitorView = z.infer<typeof MonitorSchema>;
export type MonitorAgent = z.infer<typeof MonitorAgentSchema>;
export type DigestView = z.infer<typeof DigestSchema>;
export type ChatMessage = z.infer<typeof ChatMessageSchema>;
export type QueuedMessage = z.infer<typeof QueuedMessageSchema>;
export type ActiveTurn = z.infer<typeof ActiveTurnSchema>;
export type ChatView = z.infer<typeof ChatViewSchema>;

/** Host voice on this computer: why it cannot run, or where its one-time setup stands. */
const HostVoiceSchema = z.union([
  z.object({ state: z.literal("unavailable"), reason: z.string() }),
  z.object({
    state: z.enum(["absent", "setting-up", "ready", "failed", "outdated"]), download_bytes: z.number(),
    done_bytes: z.number().optional(), reason: z.string().optional(),
    update: z.object({
      state: z.enum(["preparing", "downloading", "waiting", "checking", "failed", "cancelled"]),
      done_bytes: z.number().optional(), download_bytes: z.number().optional(), reason: z.string().optional(),
    }).optional(),
  }),
]);
const VoiceSchema = z.object({ backend: z.enum(["browser", "host", "service"]), selection: z.string(), host: HostVoiceSchema,
  error: z.literal("service_response").optional(),
  service: z.object({ url: z.string(), model: z.string().optional(), key_present: z.boolean() }).optional() });
export type VoiceBackend = z.infer<typeof VoiceSchema>["backend"];
export type VoiceSettings = z.infer<typeof VoiceSchema>;
export type HostVoice = z.infer<typeof HostVoiceSchema>;
export type VoiceUpdate = { backend: VoiceBackend; selection: string; url?: string; model?: string; key?: string; remove_key?: boolean };

/** Which backend this installation transcribes with; "browser" never uploads audio. */
export async function readVoiceSettings(signal?: AbortSignal): Promise<VoiceSettings> {
  return VoiceSchema.parse(await api("/api/voice", { signal }));
}

export async function saveVoiceSettings(value: VoiceUpdate): Promise<VoiceSettings> {
  return VoiceSchema.parse(await post("/api/voice", value));
}

const FoldersSchema = z.object({
  path: z.string(), parts: z.array(z.string()), readable: z.boolean(),
  location: z.enum(["native", "container"]).optional(),
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
  network: z.union([z.object({ computer: z.string(), host: z.string(), port: z.number(), url: z.string(),
    scope: z.enum(["local", "private", "network"]), listener_scope: z.enum(["local", "private", "all", "network"]),
    state: z.literal("running"), packaged: z.boolean() }), z.object({ error: z.string() })]).nullish(),
  lifecycle: ContainerLifecycleSchema.nullish(),
  operator: z.string().nullish(), incident_repository: z.string().nullish(), altitude_repository: z.string(),
  terminal: z.boolean().default(false), update_check: z.boolean().default(true), update_automatic: z.boolean().default(true),
  terminal_unavailable: z.string().nullish(), deployment: z.enum(["native", "container"]).optional(),
  container_shell: z.string().nullish(),
  validation: z.boolean().default(true), validation_unavailable: z.string().nullish(),
});
export type Machine = z.infer<typeof MachineSchema>;

/** The operator's name and where system incidents are published, if anywhere; a switch on its way shows at once. */
export function useMachine() {
  const saving = useSaving<MachineChange>(["machine"]);
  const select = useCallback((data: Machine) => saving.reduce<Machine>((machine, change) => ({ ...machine, ...change }), data), [saving]);
  return useQuery({ queryKey: ["machine"], queryFn: async () => MachineSchema.parse(await api("/api/machine")), refetchOnWindowFocus: false, select });
}

type MachineChange = { terminal: boolean } | { validation: boolean };

/** Turn the operator's terminal or the agents' validation runs on or off for this computer. */
export function useMachineSwitch(setting: "terminal" | "validation") {
  return useSave<MachineChange, Machine>(["machine"], (change) => "terminal" in change ? saveTerminalAccess(change.terminal) : saveValidationAccess(change.validation),
    async (client, machine) => {
      // Only this switch's answer is taken: the other switch's save may have landed after this reply was made.
      client.setQueryData<Machine>(["machine"], (old) => old && (setting === "terminal"
        ? { ...old, terminal: machine.terminal, terminal_unavailable: machine.terminal_unavailable }
        : { ...old, validation: machine.validation, validation_unavailable: machine.validation_unavailable }));
      if (setting === "terminal") await client.invalidateQueries({ queryKey: ["terminal"] });
    });
}

/**
 * Repairs (docs/OPERATIONS.md#repairs): agent sessions you start from this page or with `alt repair` to find out what
 * is wrong with this installation and fix it. `status` is what the chip says: working or waiting while the session
 * runs, stopped, checking, or the outcome of Altitude's own check once it has ended.
 */
const RepairOutcomeSchema = z.enum(["repaired", "changed_by_hand", "confirm", "not_repaired", "no_report"]);
const RepairStatusSchema = z.enum(["working", "waiting", "stopped", "checking", "repaired", "changed_by_hand", "confirm",
  "not_repaired", "no_report"]);
export type RepairStatus = z.infer<typeof RepairStatusSchema>;
export const RepairRowSchema = z.object({
  id: z.string(), title: z.string(), route: z.string(), engine: z.string(), started: z.string(),
  ended: z.string().nullable(), outcome: RepairOutcomeSchema.nullable(), status: RepairStatusSchema,
});
export type RepairRow = z.infer<typeof RepairRowSchema>;
const RunningRepairSchema = z.object({ id: z.string().nullable(), route: z.string(), title: z.string().nullish() });
export type RunningRepair = z.infer<typeof RunningRepairSchema>;
export const RepairsSchema = z.object({
  running: RunningRepairSchema.nullable(),
  /** The coding agent a new repair would use now, which the start sheet names; none when no agent can run. */
  engine: z.string().nullish(),
  repairs: z.array(RepairRowSchema),
});
const RepairReportSchema = z.object({ wrong: z.string(), changed: z.string(), remains: z.string(), try: z.string().optional() });
const RepairCheckSchema = z.object({
  at: z.string(), version: z.string().nullable(), running: z.string().nullish(), matches: z.boolean().nullable(),
  failing: z.array(z.object({ name: z.string() }).passthrough()),
});
/** One line of an app repair's page, oldest first: a system line, what you wrote, the agent's words and steps, its
 * questions, and an earlier round's report. */
export const RepairItemSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("event"), at: z.string(), event: z.enum(["started", "restarting", "back", "kept_going", "failed"]),
    text: z.string().nullish(), resumed: z.boolean().optional() }),
  z.object({ kind: z.literal("you"), at: z.string(), text: z.string() }),
  z.object({ kind: z.literal("say"), at: z.string().nullish(), text: z.string() }),
  z.object({ kind: z.literal("step"), at: z.string().nullish(), verb: z.enum(["ran", "failed", "changed", "fetched", "running", "cut"]),
    text: z.string(), detail: z.string(), output: z.string(), exit: z.number().nullish() }),
  z.object({ kind: z.literal("question"), at: z.string(), id: z.string(), type: z.enum(["ask", "step"]), title: z.string(),
    reason: z.string().nullable(), command: z.string(), answer: z.enum(["yes", "no", "ran", "expired"]).nullable(),
    answered: z.string().nullable() }),
  z.object({ kind: z.literal("report"), at: z.string().nullish(), report: RepairReportSchema, check: RepairCheckSchema.nullable(),
    outcome: RepairOutcomeSchema.nullable() }),
]);
export type RepairItem = z.infer<typeof RepairItemSchema>;
export const RepairSchema = RepairRowSchema.extend({
  problem: z.string().nullable(),
  model: z.string().nullish(),
  update: z.object({ version: z.string(), error: z.string().nullish() }).nullable(),
  report: RepairReportSchema.nullable(),
  check: RepairCheckSchema.nullable(),
  brief: z.string().nullable(),
  /** How the session ended: its report, Stop, the time limit, a failure, or a recorder gone without its end. */
  end: z.enum(["report", "stop", "time", "failed", "interrupted", "refused"]).nullish(),
  stop: z.object({ at: z.string(), update: z.boolean() }).nullish(),
  stop_unconfirmed: z.boolean().nullish(),
  left_running: z.array(z.object({ kind: z.string(), name: z.string() })).nullish(),
  timeline: z.array(RepairItemSchema).optional(),
  /** Seconds left before the time limit while the session runs. */
  left: z.number().nullish(),
  keep_going: z.boolean().optional(),
  stopping: z.boolean().optional(),
}).passthrough();
export type Repair = z.infer<typeof RepairSchema>;

/** Every repair on this computer, newest first, and the one running now; followed while one runs or awaits its check. */
export function useRepairs() {
  return useQuery({
    queryKey: ["repairs"], queryFn: async () => RepairsSchema.parse(await api("/api/repairs")),
    refetchInterval: (query) => query.state.data && (query.state.data.running || query.state.data.repairs.some((row) => !row.outcome)) ? 5000 : false,
  });
}

/** One repair; while its session runs the page follows its steps every two seconds. */
export function useRepair(id: string | undefined) {
  return useQuery({
    queryKey: ["repair", id], enabled: Boolean(id),
    queryFn: async () => RepairSchema.parse(await api(`/api/repairs/${encodeURIComponent(id ?? "")}`)),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 3,
    refetchInterval: (query) => !query.state.data ? false : !query.state.data.ended ? 2000 : !query.state.data.outcome ? 5000 : false,
  });
}

/** Why a repair could not start or change, and the running repair a refusal names, which the sheet links. */
export class RepairRefused extends ApiError {
  constructor(status: number, message: string, readonly running: string | null) {
    super(status, message);
  }
}

/** A repair request: each names itself, so a repeated tap or a retried request acts once. */
export async function repairRequest(path: string, body: Record<string, unknown>, request: string): Promise<Repair> {
  const res = await fetch(path, { method: "POST", headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, request }) });
  if (res.ok) return RepairSchema.parse(await res.json());
  if (res.status === 401) window.dispatchEvent(new Event(UNPAIRED_EVENT));
  const reply = await res.json().catch(() => ({})) as { error?: unknown; running?: unknown };
  throw new RepairRefused(res.status, typeof reply.error === "string" && reply.error ? reply.error : `HTTP ${res.status}`,
    typeof reply.running === "string" ? reply.running : null);
}

export function newRequest() {
  return crypto.randomUUID();
}

/** Start, steer, extend or stop a repair; the page and every list of repairs follow the result. */
export function useRepairAction<T>(request: (input: T) => Promise<Repair>) {
  const client = useQueryClient();
  return useMutation<Repair, RepairRefused | Error, T>({
    mutationFn: request,
    onSuccess: (repair) => client.setQueryData(["repair", repair.id], repair),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["repairs"] });
      void client.invalidateQueries({ queryKey: ["overview"] });
    },
  });
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

/** Let task owners run validation containers on this computer, or not; off also stops a running one. */
export async function saveValidationAccess(enabled: boolean): Promise<Machine> {
  return MachineSchema.parse(await post("/api/validation-access", { enabled }));
}

/** Turn the daemon's twice-daily check for a newer release on or off; off also hides the notice. */
export async function saveUpdateCheck(enabled: boolean): Promise<Machine & { update: Update | null }> {
  return MachineSchema.extend({ update: UpdateSchema.nullable() }).parse(await post("/api/update-check", { enabled }));
}

/** Install new releases automatically at a quiet point, or prompt before installing. */
export async function saveUpdateAutomatic(enabled: boolean): Promise<Machine & { update: Update | null }> {
  return MachineSchema.extend({ update: UpdateSchema.nullable() }).parse(await post("/api/update-automatic", { enabled }));
}

/** Install exactly the newer release the notice shows, through the same verified `alt update`. */
export async function startUpdate(version: string): Promise<Update> {
  return z.object({ update: UpdateSchema }).parse(await post("/api/update", { version })).update;
}

// ---- the operator's terminal -----------------------------------------------------------

/**
 * One terminal per task worktree or project folder (server `terminal.view`): `none` or `running`, and
 * `exited` only in a stream's end event. `offset` is the absolute output position the replay reaches;
 * `id` tells one terminal from its replacement; `busy` names a foreground command Close would stop.
 */
export const TerminalStatusSchema = z
  .object({
    state: z.string(),
    id: z.string().nullish(),
    enabled: z.boolean(),
    folder: z.string().nullish(),
    offset: z.number().nullish(),
    exit_code: z.number().nullish(),
    reason: z.string().nullish(),
    error: z.string().nullish(),
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
  // A refusal is the server's answer: it shows at once, with Retry.
  return useQuery({ queryKey: ["terminal", project, task ?? null], queryFn: () => terminalStatus(project, task), refetchOnWindowFocus: false, retry: false });
}

export async function terminalOpen(project: string, task?: string): Promise<TerminalStatus> {
  return TerminalStatusSchema.parse(await post(`${terminalPath(project)}/open`, { task }));
}

/** Input, resize and close for terminal `id`, and `command`: the chat command for altd to type at the prompt (as a paste
 * when `bracketed`), which the terminal's reader (the task's owner, or the coordinator) hears about once it has run. Each
 * answers ok or the server's error: why the command wasn't typed, or 410 once the terminal was replaced. */
export function terminalSend(project: string, action: "input" | "command" | "resize" | "close", body: { task?: string; id: string; data?: string; text?: string; bracketed?: boolean; cols?: number; rows?: number }) {
  return post(`${terminalPath(project)}/${action}`, body);
}

/** Terminal `id`'s output from `offset`: `output` events carry base64 bytes and the next offset, `end` the final status. */
export function terminalStream(project: string, task: string | undefined, id: string, offset: number): EventSource {
  const query = new URLSearchParams({ ...(task ? { task } : {}), id, offset: String(offset) });
  return new EventSource(`${terminalPath(project)}/stream?${query}`);
}

const PrerequisitesSchema = z.object({
  items: z.array(z.object({
    key: z.string(), label: z.string(), state: z.enum(["met", "unmet", "optional"]),
    detail: z.string().nullish(), command: z.string().nullish(),
    /** Alternatives that each satisfy the row, such as one coding agent of several, with their own commands. */
    options: z.array(z.object({ label: z.string(), command: z.string() })).nullish(),
    /** The engine whose sign-in this page performs itself (`useSignIn`), with its label. */
    sign_in: z.object({ engine: z.string(), label: z.string() }).nullish(),
  })),
});
export type Prerequisite = z.infer<typeof PrerequisitesSchema>["items"][number];

/** Altitude's own sign-in on an engine, held by the service: every paired browser sees the same open one. `saved`
 * is when the engine's saved sign-in was saved; `started` when the open or failed one began. */
const SignInSchema = z.object({
  attempt: z.string().nullable(), state: z.enum(["idle", "opening", "waiting", "finishing", "failed", "done"]),
  link: z.string().nullable(), started: z.number().nullable(), error: z.string().nullable(), saved: z.number().nullable(),
});
export type SignIn = z.infer<typeof SignInSchema>;
const signInOpen = (state?: SignIn["state"]) => state === "opening" || state === "waiting" || state === "finishing";

/** The engine's sign-in, read again while one is open so a sign-in finished or cancelled elsewhere shows here. */
export function useSignIn(engine: string) {
  return useQuery({
    queryKey: ["sign-in", engine], queryFn: async () => SignInSchema.parse(await api(`/api/sign-in/${engine}`)),
    refetchInterval: (query) => (signInOpen(query.state.data?.state) ? 5000 : false), retry: false, gcTime: 0,
  });
}

/** Start a sign-in (answering once the engine's link is ready), type its code (answering once the engine has
 * answered), or cancel it. */
export async function changeSignIn(engine: string, body: { action: "start" } | { action: "finish"; attempt: string; code: string }
  | { action: "cancel"; attempt: string }): Promise<SignIn> {
  return SignInSchema.parse(await post(`/api/sign-in/${engine}`, body));
}

/** Set up, cancel setup of, or remove host voice; answers the settings with the new host state. */
export async function changeHostVoice(action: "setup" | "cancel" | "remove"): Promise<VoiceSettings> {
  return VoiceSchema.parse(await post("/api/voice/host", { action }));
}

const HostTextSchema = z.object({ text: z.string(), final: z.boolean().optional() }).passthrough();

/** Start a host voice recording for the selection the page read. */
/** Opens a host recording. A replay names the `owner` its first recording answered, so it stays with that device. */
export async function startHostVoice(selection: string, signal?: AbortSignal, owner?: string): Promise<{ id: string; owner: string }> {
  const headers: Record<string, string> = { "X-Voice-Selection": selection };
  if (owner) headers["X-Voice-Owner"] = owner;
  return z.object({ id: z.string(), owner: z.string() }).passthrough().parse(
    await api("/api/voice/live", { method: "POST", body: "{}", headers, signal }));
}

/** Chunk `seq` of 16 kHz 16-bit samples; answers the text so far, or the final text. */
export async function sendHostVoice(id: string, selection: string, seq: number, pcm: Int16Array, final: boolean, signal?: AbortSignal) {
  return HostTextSchema.parse(await api(`/api/voice/live/${id}/audio?seq=${seq}&final=${final ? 1 : 0}`, {
    method: "POST", body: pcm as Int16Array<ArrayBuffer>, headers: { "Content-Type": "application/octet-stream", "X-Voice-Selection": selection }, signal,
  }));
}

export async function cancelHostVoice(id: string): Promise<void> {
  await post(`/api/voice/live/${id}/cancel`, {});
}

/** One after-stop upload; never retried because an unanswered request may already have reached the service. */
export async function sendServiceVoice(selection: string, pcm: Int16Array, signal: AbortSignal) {
  return z.object({ text: z.string() }).parse(await api("/api/voice/service", {
    method: "POST", body: pcm as Int16Array<ArrayBuffer>, signal,
    headers: { "Content-Type": "application/octet-stream", "X-Voice-Selection": selection },
  }));
}

/** What the agents need on the computer running Altitude; each read checks again. */
export function usePrerequisites(enabled = true) {
  return useQuery({
    queryKey: ["prerequisites"], queryFn: async () => PrerequisitesSchema.parse(await api("/api/prerequisites")).items,
    enabled, refetchOnWindowFocus: false, retry: false, gcTime: 0,
  });
}

// ---- query hooks (20s polling) ---------------------------------------------------------

/** The one overview read every page shares; also read directly to confirm a removal whose response was lost. */
export const overviewQuery = {
  queryKey: ["overview"],
  queryFn: async () => OverviewSchema.parse(await api("/api/overview")),
};

/** A setting the overview carries, on its way: an update switch, or New tasks with the value the picker showed. */
type UpdateChange = { update: { check: boolean } | { automatic: boolean } };
type NewTasksChange = { new_tasks: Choice; expected: Choice };
type OverviewChange = UpdateChange | NewTasksChange;
/** Checks off hide the newer release and stop automatic updates; automatic updates off stop the one pending. */
const applyUpdate = (update: Update, change: UpdateChange["update"]): Update => {
  const check = "check" in change ? change.check : update.check;
  const automatic = check && ("automatic" in change ? change.automatic : update.automatic);
  return { ...update, check, automatic, ...check ? {} : { available: null }, ...automatic ? {} : { automatic_pending: false, waiting_for: [] } };
};
const applyOverview = (data: Overview, change: OverviewChange): Overview => "update" in change
  ? data.update ? { ...data, update: applyUpdate(data.update, change.update) } : data
  : data.new_tasks ? { ...data, new_tasks: { ...data.new_tasks, value: change.new_tasks, unavailable: null } } : data;

/**
 * Needs you, the badge and every page read the overview with the operator's writes on their way laid over it.
 * A view that mounts often reads the shell's copy without fetching it again (`refetchOnMount` false).
 */
export function useOverview(refetchOnMount = true) {
  const client = useQueryClient();
  const pending = usePending();
  const saving = useSaving<OverviewChange>(["overview"]);
  const suggestions = useSuggestionChanges();
  const skips = useReviewSkips();
  const select = useCallback((data: Overview) => {
    const view = skipsOverview(saving.reduce(applyOverview, overlayOverview(client, data, pending)), skips);
    return suggestions.length ? { ...view, suggestions: waitingRuns(client, view, suggestions) } : view;
  }, [client, pending, saving, suggestions, skips]);
  return useQuery({ ...overviewQuery, refetchInterval: pollInterval, refetchOnMount, select });
}

/** Check for new versions, or install them automatically; the response carries the machine and the update. */
export function useSaveUpdates() {
  return useSave<UpdateChange, Machine & { update: Update | null }>(["overview"],
    ({ update }) => "automatic" in update ? saveUpdateAutomatic(update.automatic) : saveUpdateCheck(update.check),
    (client, { update, update_check, update_automatic }) => {
      client.setQueryData<Machine>(["machine"], (old) => old && { ...old, update_check, update_automatic });
      client.setQueryData<Overview>(["overview"], (overview) => overview && { ...overview, update });
    });
}

/** The overview as Altitude saved it: alerts count what the record asks, never what a send on its way hides. */
export function useOverviewRecord() {
  return useQuery({ ...overviewQuery, refetchInterval: pollInterval });
}

/** A project's task rows, current and archived, by slug: what the task cards its chat and suggestions name read. */
export function taskMap(view: ProjectView | undefined): Map<string, TaskRow> {
  return new Map([...(view?.tasks ?? []), ...(view?.archive ?? [])].map((row) => [row.slug, row]));
}

export function useProject(name: string, enabled = true) {
  const client = useQueryClient();
  const pending = usePending();
  const suggestions = useSuggestionChanges();
  const skips = useReviewSkips();
  const select = useCallback((data: ProjectView) => {
    const view = skipsProject(overlayProject(client, data, pending), skips);
    return suggestions.length ? { ...view, improvements: view.improvements.map((run) => withSuggestions(run, data.name, suggestions)) } : view;
  }, [client, pending, suggestions, skips]);
  return useQuery({
    queryKey: ["project", name],
    queryFn: async () => ProjectViewSchema.parse(await api(`/api/project/${name}`)),
    refetchInterval: pollInterval,
    enabled: Boolean(name) && enabled,
    select,
  });
}

export const taskQuery = (project: string, slug: string) => ({
  queryKey: ["task", project, slug],
  queryFn: async () => TaskViewSchema.parse(await api(`/api/task/${project}/${slug}`)),
});

export function useTask(project: string, slug: string) {
  const client = useQueryClient();
  const pending = usePending();
  const skips = useReviewSkips();
  const select = useCallback((data: TaskView) => skipsTask(overlayTask(client, project, data, pending), project, skips), [client, project, pending, skips]);
  return useQuery({
    ...taskQuery(project, slug),
    refetchInterval: (query) => query.state.data?.state === "running" || ["stopping", "stop_unconfirmed", "resuming"].includes(query.state.data?.steering?.state ?? "") ? 2_000 : pollInterval(),
    enabled: Boolean(project && slug),
    select,
  });
}

const ShownFilesSchema = ShownSchema.extend({
  document: ShownSchema.shape.document.unwrap().extend({ text: z.string(), markdown: z.boolean() }).nullable(),
});
export type ShownFiles = z.infer<typeof ShownFilesSchema>;

/** One shown set for the reader, read while it is open and not kept after it closes: saved files never change. */
export function useShownFiles(project: string, slug: string, identity: string, revision?: string) {
  const path = [project, slug, identity, ...(revision ? [revision] : [])].map(encodeURIComponent).join("/");
  return useQuery({
    queryKey: ["shown", path],
    queryFn: async ({ signal }) => ShownFilesSchema.parse(await api(`/api/shown/${path}`, { signal })),
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

const TaskCapturesSchema = z.object({
  run: z.number(), at: z.string().nullable(), conversation_url: z.string(),
  captures: z.array(z.object({ title: z.string(), url: z.string(), bytes: z.number(), width: z.number(), height: z.number(), frames: z.number(), seconds: z.number() })),
});
export type TaskCapture = z.infer<typeof TaskCapturesSchema>["captures"][number];

/** One reply's validation captures; kept only while their page is open. */
export function useTaskCaptures(project: string, slug: string, message: string) {
  return useQuery({
    queryKey: ["task-captures", project, slug, message],
    queryFn: async () => TaskCapturesSchema.parse(await api(`/api/captures/${project}/${slug}/${message}`)),
    retry: false,
    gcTime: 0,
  });
}

type TranscriptSync = { rows: Map<string, TranscriptEvent>; cursor: string; after: string; lower: string };
type TranscriptReader = {
  data?: Transcript; cursor: string; error: unknown; historyError: unknown;
  fetching: boolean; historyPending: boolean; catchingUp: boolean; sync?: TranscriptSync;
  tail: boolean; lastRead: number;
};

function mergeTranscript(rows: TranscriptEvent[], page: Transcript): TranscriptEvent[] {
  const merged = new Map(rows.map(row => [row.id, row]));
  for (const id of page.deleted) merged.delete(id);
  for (const row of page.events) {
    const previous = merged.get(row.id);
    if (!previous || previous.version <= row.version) merged.set(row.id, row);
  }
  return [...merged.values()].sort((a, b) => a.order < b.order ? -1 : a.order > b.order ? 1 : 0);
}

/** Whose transcript: a task's by slug, or a routine's run, read from under its routine. */
export type TranscriptSource = string | { agent: string; run: number };
const transcriptPath = (project: string, source: TranscriptSource) => typeof source === "string"
  ? `/api/transcript/${project}/${source}` : `/api/agents/${project}/${source.agent}/runs/${source.run}/transcript`;

/** Only the mounted viewer retains rows. One serialized reader owns history, deltas and epoch
 * reconciliation; aborted requests never publish into a different task, attempt or representation. */
export function useTranscript(
  project: string,
  source: TranscriptSource,
  engine: string,
  sessionId: string,
  raw: boolean,
  live = true,
  enabled = true,
  attempt = 0,
  following = true,
) {
  const slug = typeof source === "string" ? source : `${source.agent}/runs/${source.run}`;
  const reader = useMemo<TranscriptReader>(() => ({ cursor: "", error: null, historyError: null,
    fetching: false, historyPending: false, catchingUp: false, tail: false, lastRead: 0 }), [project, slug, engine, sessionId, attempt, raw]);
  const follows = useRef(following);
  follows.current = following;
  const [, redraw] = useReducer(n => n + 1, 0);
  const actions = useRef({ refetch: () => {}, loadOlder: () => {} });

  useEffect(() => {
    if (!enabled || !project || !slug || !engine || !sessionId) {
      actions.current = { refetch: () => {}, loadOlder: () => {} };
      return;
    }
    let stopped = false;
    let busy = false;
    let historyWanted = false;
    let continuations = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    // A cancelled multi-page reconciliation restarts from its original loaded boundary.
    reader.sync = undefined;
    reader.catchingUp = Boolean(reader.data);
    const publish = () => { if (!stopped) redraw(); };
    const beginSync = () => {
      if (follows.current) {
        reader.sync = undefined;
        reader.tail = true;
      } else reader.sync = { rows: new Map(), cursor: "", after: "", lower: reader.data?.lower ?? "" };
      reader.catchingUp = true;
    };
    const schedule = (delay: number) => {
      clearTimeout(timer);
      timer = setTimeout(() => void read(), delay);
    };
    const read = async () => {
      if (stopped || busy || document.visibilityState === "hidden") return;
      clearTimeout(timer);
      busy = true;
      if (follows.current && reader.data && (reader.sync || Date.now() - reader.lastRead >= 60_000)) {
        reader.sync = undefined;
        reader.tail = true;
        reader.catchingUp = true;
      }
      const history = historyWanted && !reader.sync && !reader.tail && !!reader.data?.has_earlier;
      if (history || !reader.data?.has_earlier) historyWanted = false;
      const sync = reader.sync;
      const mode = sync ? "reconcile" : history ? "history" : reader.data && !reader.tail ? "delta" : "initial";
      const query = new URLSearchParams({ engine, session_id: sessionId, attempt: String(attempt), raw: raw ? "1" : "0", mode });
      if (sync) {
        query.set("lower", sync.lower);
        query.set("after", sync.after);
        query.set("cursor", sync.cursor);
      } else if (reader.data && mode !== "initial") {
        query.set("cursor", reader.cursor);
        query.set("lower", reader.data.lower);
        if (history) query.set("before", reader.data.lower);
      }
      controller = new AbortController();
      reader.fetching = true;
      reader.historyPending = history;
      if (history) reader.historyError = null;
      publish();
      let again = false;
      try {
        const page = TranscriptSchema.parse(await api(`${transcriptPath(project, source)}?${query}`, { signal: controller.signal }));
        if (stopped) return;
        if (page.project !== project || (typeof source === "string" && page.slug !== source) || page.engine !== engine || page.session_id !== sessionId || page.attempt !== attempt) {
          throw new ApiError(404, "The session changed. Refresh the task.");
        }
        reader.lastRead = Date.now();
        if (page.reset) {
          beginSync();
          again = true;
        } else if (mode === "initial" && reader.data && !follows.current) {
          // Pause can arrive while a following-tail refresh is in flight. Preserve the
          // reading window and reconcile it instead of replacing its visible anchor.
          reader.tail = false;
          beginSync();
          again = true;
        } else if (sync) {
          if (!sync.cursor) sync.cursor = page.cursor;
          for (const row of page.events) sync.rows.set(row.id, row);
          sync.after = page.next;
          if (!page.more) {
            reader.data = { ...page, lower: page.has_earlier ? sync.lower : "",
              events: mergeTranscript([], { ...page, events: [...sync.rows.values()] }), cursor: sync.cursor };
            reader.cursor = sync.cursor;
            reader.sync = undefined;
          }
          again = true; // The baseline watermark catches writes that raced the forward traversal.
        } else if (history && reader.data) {
          reader.data = { ...reader.data, events: mergeTranscript(reader.data.events, page),
            lower: page.lower, has_earlier: page.has_earlier, has_engine_records: page.has_engine_records };
          // A history page does not acknowledge changes to already-loaded rows.
          reader.historyError = null;
        } else {
          reader.data = { ...page, events: mergeTranscript(mode === "initial" ? [] : reader.data?.events ?? [], page),
            lower: mode === "initial" ? page.lower : page.has_earlier ? reader.data?.lower ?? "" : "" };
          reader.cursor = page.cursor;
          if (mode === "initial") reader.historyError = null;
          reader.tail = false;
          reader.catchingUp = page.more;
          again = page.more;
        }
        if (!history) reader.error = null;
      } catch (error) {
        if (stopped) return;
        if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
          reader.data = undefined;
          reader.cursor = "";
          reader.sync = undefined;
          reader.error = error;
          reader.historyError = null;
        } else if (history) reader.historyError = error;
        else reader.error = error;
      } finally {
        busy = false;
        if (!stopped) {
          reader.fetching = false;
          reader.historyPending = false;
          publish();
          if (historyWanted || again) {
            continuations += 1;
            schedule(continuations >= 2 ? (continuations = 0, 250) : 0);
          } else {
            continuations = 0;
            schedule(live ? 2_000 : 20_000);
          }
        }
      }
    };
    const refresh = () => { if (!busy) void read(); };
    actions.current = {
      refetch: refresh,
      loadOlder: () => {
        if (!reader.data?.has_earlier || reader.historyPending || reader.sync) return;
        historyWanted = true;
        if (!busy) void read();
      },
    };
    const visible = () => { if (document.visibilityState === "visible") refresh(); };
    window.addEventListener("online", refresh);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", visible);
    void read();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller?.abort();
      reader.fetching = false;
      reader.historyPending = false;
      actions.current = { refetch: () => {}, loadOlder: () => {} };
      window.removeEventListener("online", refresh);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [reader, project, slug, engine, sessionId, attempt, raw, enabled, live]);

  return { data: reader.data, error: reader.error, isPending: !reader.data && !reader.error,
    isError: Boolean(reader.error), isFetching: reader.fetching,
    historyPending: reader.historyPending, historyError: reader.historyError,
    reconnecting: Boolean(reader.sync), catchingUp: reader.catchingUp,
    refetch: () => actions.current.refetch(), loadOlder: () => actions.current.loadOlder() };
}

const TranscriptRecordSchema = z.object({ text: z.string(), next_offset: z.number().nullable() });
export async function fetchTranscriptRecord(project: string, source: TranscriptSource, engine: string, sessionId: string,
  attempt: number, record: string, offset: number, signal: AbortSignal) {
  const query = new URLSearchParams({ engine, session_id: sessionId, attempt: String(attempt),
    raw: "1", mode: "record", record, offset: String(offset) });
  return TranscriptRecordSchema.parse(await api(`${transcriptPath(project, source)}?${query}`, { signal }));
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

/** The project's conversation; `reach` names an improvement review whose line the history reads back to. */
export function useChat(project: string, limit = 60, enabled = true, reach?: string) {
  return useQuery({
    queryKey: ["chat", project],
    queryFn: async () => ChatViewSchema.parse(await api(`/api/chat/${project}?${new URLSearchParams({
      limit: String(limit), ...(reach ? { reach } : {}) })}`)),
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

/**
 * One write of the operator's own choice (SPEC.md §1.1 Actions answer at once): its entry shows the result in every
 * view from the press. Altitude's answer settles it in place; a refusal rolls it back; with no answer, the next read
 * of the task decides. The caller starts the entry (`startPending`); this resolves with the request's result, or
 * rejects with its error once the entry records it.
 */
export async function writePending<T>(client: QueryClient, entry: PendingWrite, request: () => Promise<T>): Promise<T> {
  const { project, slug } = entry;
  const views = [["overview"], ["project", project], ["task", project, slug]];
  try {
    const out = await request();
    await finishPending(client, entry.id, "saved");
    return out;
  } catch (error) {
    const refused = error instanceof ApiError && error.status < 500;
    await finishPending(client, entry.id, refused ? "refused" : "unconfirmed", error instanceof Error ? error : undefined,
      () => client.fetchQuery({ ...taskQuery(project, slug), staleTime: 0 }));
    throw error;
  } finally {
    for (const queryKey of views) void client.invalidateQueries({ queryKey });
  }
}

const sendId = () => crypto.randomUUID().replaceAll("-", "");

/**
 * A question card's answers (SPEC.md §3.8): the saved message keeps the send id, so the pills shown at the press are
 * the saved ones. Retry of unchanged picks sends under the same id, which Altitude answers with the saved answer if
 * the earlier send was saved after all.
 */
export function sendAnswers(client: QueryClient, input: DecideInput, answers: PendingAnswer[], picks: PendingWrite["picks"], id = sendId()): Promise<PendingWrite> {
  const entry: PendingWrite = { id, project: input.project, slug: input.slug, kind: "answers", state: "sending",
    ...handBack(client, input.project, input.slug), at: new Date().toISOString(),
    text: answers.map((answer) => answer.text).join("\n\n"), answers, picks };
  const outcome = startPending(client, entry);
  void writePending(client, entry, () => post("/api/decide", { ...input, request_id: id })).catch(() => undefined);
  return outcome;
}

/** #419: the review card's approval, the operator's own message the L2 merges with (SPEC.md §3.8.2). */
/** Approve merge; Retry passes the refused send's id, so a send saved after all is never saved twice. */
export function sendApproval(client: QueryClient, decision: Decision, id = sendId()): Promise<PendingWrite> {
  const { project, slug } = decision;
  const text = `Approved: merge PR #${decision.pr}.`;
  const entry: PendingWrite = { id, project, slug, kind: "approval", state: "sending", ...handBack(client, project, slug),
    at: new Date().toISOString(), text, answers: [], pr: decision.pr ?? undefined, hold: decision.review_hold_id ?? null };
  const outcome = startPending(client, entry);
  void writePending(client, entry, async () => {
    const row = await sendL2Message({ project, slug, text, review_pr: decision.pr ?? undefined, review_hold_id: decision.review_hold_id ?? null, request_id: id });
    await client.cancelQueries({ queryKey: ["task", project, slug], exact: true });
    client.setQueryData<TaskView>(["task", project, slug], (cached) =>
      cached && !cached.messages?.some((message) => message.id === row.id) ? { ...cached, messages: [...cached.messages ?? [], row] } : cached);
  }).catch(() => undefined);
  return outcome;
}

export interface TaskActionInput {
  project: string;
  slug: string;
  action: string;
  generation?: string | null;
  stop_id?: string | null;
  reason?: string;
  engine?: string;
  selection?: { model: string; effort: string | null };
  default_model?: string;
  request_id?: string;
  observed?: { attempt: number | null; agent_id: string | null; session_id: string | null; block_id: string | null };
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
    action: z.string().nullish(), fingerprint: z.string().nullish(), command: z.string().nullish(),
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

/** Whether setup holds the first conversation until a coding tool is ready; Start L3 would only wait the same way. */
export function useWaitsForTool(project: string) {
  return useSetup(project).data?.steps.find((step) => step.id === "coordinator")?.status === "waiting";
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
    // A started session holds Starting… until the chat and project read it, so Start L3 never returns meanwhile;
    // a refusal shows at once.
    onSettled: (_out, error) => {
      const reads = Promise.all([
        queryClient.invalidateQueries({ queryKey: ["chat", project] }),
        queryClient.invalidateQueries({ queryKey: ["project", project] }),
      ]);
      return error ? undefined : reads;
    },
  });
}

export function useProjectRemove() {
  const queryClient = useQueryClient();
  return useMutation<unknown, Error, { name: string }>({
    mutationFn: (input) => post("/api/project/remove", input),
    onSuccess: (_out, { name }) => forgetProject(queryClient, name),
  });
}

/** Drop a removed project from the shared reads, so no page acts on it as still managed. */
export async function forgetProject(queryClient: QueryClient, name: string) {
  await queryClient.cancelQueries({ queryKey: ["overview"] });
  queryClient.setQueryData<Overview>(["overview"], (cached) => cached && {
    ...cached,
    projects: cached.projects.map((row) => row.name === name ? { ...row, managed: false } : row),
    queue: cached.queue.filter((row) => row.project !== name),
  });
  forgetPending(queryClient, name);
  for (const kind of ["project", "chat", "chat-outbox", "task", "task-outbox", "transcript"]) {
    await queryClient.cancelQueries({ queryKey: [kind, name] });
    queryClient.removeQueries({ queryKey: [kind, name] });
  }
  // The composer's recovery of the project's conversations goes with it.
  forgetRecovery(name);
  void queryClient.invalidateQueries({ queryKey: ["overview"] });
  void queryClient.invalidateQueries({ queryKey: ["monitor"] });
}

export interface L2MessageInput {
  project: string;
  slug: string;
  text: string;
  stop_id?: string;
  review_pr?: number;
  review_hold_id?: string | null;
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

export type ReviewAction = "request" | "cancel" | "withdraw";
export async function taskReview(input: { project: string; slug: string; action: ReviewAction; subject?: ReviewSubject; request_id?: string; review_id?: string }): Promise<Review> {
  const result = await post<{ review: unknown }>("/api/task/review", input);
  return ReviewSchema.parse(result.review);
}

/**
 * Skip review: the operator's own choice, so the review reads skipped by you at the press in its box and on every
 * question line that names it, and its findings stop counting as open. Whether a new review can be requested is
 * Altitude's to say, read once the skip is saved.
 */
export type ReviewSkip = { project: string; slug: string; id: string; subject: ReviewSubject; unresolved: number; at: string };
const SKIPS = ["review-skip"];
const skipReads = ({ project, slug }: ReviewSkip) => [["task", project, slug], ["overview"], ["project", project]];
/**
 * A question line's review with the skips on their way: skipped by you when a skip names it, and its open count, which
 * covers its kind's whole merge gate, less the findings of each skip Altitude has not yet taken off. A line knows only
 * its kind's latest review, so by default only a skip of that one, still unskipped in the read, comes off.
 */
function skippedLine<Row extends { slug?: string | null; review?: Decision["review"] }>(row: Row, project: string, skips: ReviewSkip[],
  counted = (skip: ReviewSkip) => skip.id === row.review?.id && row.review.state !== "withdrawn"): Row {
  const review = row.review;
  const mine = review ? skips.filter((skip) => skip.project === project && skip.slug === row.slug && skip.subject === review.subject) : [];
  if (!review || !mine.length) return row;
  const open = Math.max(0, review.open - mine.filter(counted).reduce((sum, skip) => sum + skip.unresolved, 0));
  return { ...row, review: { ...review, open, ...(mine.some((skip) => skip.id === review.id) ? { state: "withdrawn" as const, skipped_by: "operator" as const, reason: "" } : {}) } };
}
const skippedReview = (review: Review, skip: ReviewSkip): Review => ({ ...review, state: "withdrawn", withdrawn_by: "operator",
  withdrawal_reason: "Skipped by the operator.", finished_at: skip.at, can_withdraw: false, can_cancel: false, can_again: false });

export function useReviewSkips() {
  return useSaving<ReviewSkip>(SKIPS);
}
const skipsOverview = (data: Overview, skips: ReviewSkip[]): Overview => skips.length
  ? { ...data, queue: data.queue.map((row) => row.kind === "step" ? row : skippedLine(row, row.project, skips)) } : data;
const skipsProject = (data: ProjectView, skips: ReviewSkip[]): ProjectView => skips.length && data.decisions
  ? { ...data, decisions: data.decisions.map((row) => skippedLine(row, data.name, skips)) } : data;
function skipsTask(data: TaskView, project: string, skips: ReviewSkip[]): TaskView {
  const mine = skips.filter((skip) => skip.project === project && skip.slug === data.slug);
  if (!mine.length || !data.review) return data;
  // The task read lists every review, so a skip comes off its counts only while that review still reads unskipped,
  // and only from the counts of its own delivery.
  const history = data.review.history;
  const within = (id: string | null | undefined) => { const entry = history.find((item) => item.id === id); return entry && deliveryOf(entry); };
  const counted = (delivery: string | undefined) => (skip: ReviewSkip) =>
    history.some((entry) => entry.id === skip.id && entry.state !== "withdrawn" && deliveryOf(entry) === delivery);
  const line = (question: Decision) => skippedLine(question, project, mine, counted(within(question.review?.id)));
  const review = (entry: Review) => { const skip = mine.find((item) => item.id === entry.id); return skip ? skippedReview(entry, skip) : entry; };
  const subjects = Object.fromEntries(Object.entries(data.review.subjects).map(([subject, entry]) => [subject, entry && {
    ...entry, latest: entry.latest && review(entry.latest),
    open: Math.max(0, entry.open - mine.filter((skip) => skip.subject === subject && counted(within(entry.latest?.id))(skip)).reduce((sum, skip) => sum + skip.unresolved, 0)),
  }]));
  return {
    ...data,
    review: { ...data.review, history: history.map(review), subjects: subjects as typeof data.review.subjects },
    question: data.question && line(data.question),
    questions: data.questions?.map(line),
    question_group: data.question_group && { ...data.question_group, questions: data.question_group.questions.map(line) },
  };
}

/** Skip a review; the save ends once the task, the overview and the project read show it. */
export function useSkipReview() {
  return useSave<ReviewSkip, Review>(SKIPS, ({ project, slug, id }) => taskReview({ project, slug, action: "withdraw", review_id: id }),
    (client, _review, { project, slug }) => Promise.all([
      client.refetchQueries({ queryKey: ["task", project, slug], exact: true }),
      client.refetchQueries({ queryKey: ["overview"], exact: true }),
      client.refetchQueries({ queryKey: ["project", project], exact: true }),
    ]), skipReads);
}
/** Refused skips, by project, task and review. A skip the record no longer allows (409) needs no reason: the box shows where it stands. */
export function useReviewSkipRefusals() {
  const refused = useRefused<ReviewSkip>(SKIPS, reviewSkipId);
  return useMemo(() => [...refused.values()].filter(({ error }) => !(error instanceof ApiError && error.status === 409)), [refused]);
}
const reviewSkipId = (skip: ReviewSkip) => `${skip.project}/${skip.slug}/${skip.id}`;

export function useL3Reset(project: string) {
  const queryClient = useQueryClient();
  return useRequest({
    // The Rotate button lives on a card rendered from ["project", project] (session id, context
    // %, turns) — invalidating only ["chat", project] leaves the card showing the dead session.
    mutationFn: async () => {
      const out = await post("/api/l3/reset", { project });
      await queryClient.invalidateQueries({ queryKey: ["project", project] });
      return out;
    },
    queryKey: ["chat", project],
    failureMessage: "Couldn't reset the L3 session.",
  });
}

/** Drop a message that has not started yet. */
export function useChatDequeue(project: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => post("/api/chat/remove", { project, id }),
    onSettled: () => client.invalidateQueries({ queryKey: ["chat", project] }),
  });
}

/** Request immediate delivery; only the next canonical read establishes its outcome. */
export function useSendNow(project: string, slug?: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => post(slug ? "/api/l2/send-now" : "/api/chat/send-now", { project, ...(slug ? { slug } : {}), id }),
    onSettled: () => client.invalidateQueries({ queryKey: slug ? ["task", project, slug] : ["chat", project] }),
  });
}

/** Save New tasks; `expected` is the value the dialog showed, so a change made elsewhere is refused, not overwritten. */
export function useSaveNewTasks() {
  return useSave<NewTasksChange, NewTasks>(["overview"],
    async ({ new_tasks, expected }) => NewTasksSchema.parse(await post("/api/new-tasks", { value: new_tasks, expected })),
    (client, result) => { client.setQueryData<Overview>(["overview"], (old) => old && { ...old, new_tasks: result }); });
}


/** config.project_model_settings(): each project's own model and effort per role and engine, and its routing models. */
const ProjectModelSettingsSchema = z.object({
  rows: z.array(z.object({ id: z.string(), project: z.string(), label: z.string(), detail: z.string(), expected: z.unknown(), editable: z.boolean() })),
});
export type ProjectModelSettings = z.infer<typeof ProjectModelSettingsSchema>;
export function useProjectModelSettings() {
  return useQuery({ queryKey: ["project-model-settings"], queryFn: async () => ProjectModelSettingsSchema.parse(await api("/api/project-model-settings")), retry: false, refetchOnMount: "always" });
}
export function useResetProjectModelSetting() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: { id: string; expected: unknown }) => ProjectModelSettingsSchema.parse(await post("/api/project-model-settings/reset", input)),
    onSuccess: (result) => {
      client.setQueryData(["project-model-settings"], result);
      void client.invalidateQueries({ queryKey: ["defaults"] });
    },
  });
}

/** config.defaults_view(): the project's task pin, L3 choice, routing and requested model/effort per role and engine. */
const DefaultFieldSchema = z.object({
  setting: z.string(), value: z.string().nullable(), default: z.string(),
});
const ProjectDefaultsSchema = ChoiceOptionsSchema.extend({
  l2_engine: z.string().nullish(),
  l3_choice: ChoiceSchema.optional().transform((value) => value ?? null),
  l3_unavailable: z.string().nullish(),
  l2_preference: z.string().nullish(),
  routing: z.string().nullish(),
  engines: z.array(z.object({ value: z.string(), label: z.string(), routed: z.boolean(), efforts: z.array(z.string()) })),
  roles: z.array(z.object({
    role: z.enum(["l3", "l2"]),
    engines: z.array(z.object({
      engine: z.string(), label: z.string(),
      model: DefaultFieldSchema,
      effort: DefaultFieldSchema.extend({ choices: z.array(z.object({ value: z.string(), label: z.string() })) }),
    })),
  })),
});
export type ProjectDefaults = z.infer<typeof ProjectDefaultsSchema>;

/** The project's settings, with each one on its way shown as chosen. */
export function useProjectDefaults(project: string) {
  const saving = useSaving<DefaultChange>(["defaults", project]);
  const select = useCallback((data: ProjectDefaults) => saving.reduce((view, { setting, value }) => withDefault(view, setting, value), data), [saving]);
  return useQuery({
    queryKey: ["defaults", project],
    queryFn: async () => ProjectDefaultsSchema.parse(await api(`/api/defaults/${project}`)),
    enabled: Boolean(project),
    retry: false,
    refetchOnMount: "always",
    select,
  });
}

/** The project settings a page or button saves whole, apart from the per-engine model and effort. */
const PROJECT_FIELDS = ["l2_preference", "l2_engine", "l3_choice"] as const;

type DefaultChange = { setting: string; value: string | Choice | null; expected?: string | Choice | null };

/** One project setting set to `value`; the per-engine model and effort live in their role's rows. */
function withDefault(old: ProjectDefaults, setting: string, value: string | Choice | null): ProjectDefaults {
  // Only Altitude can say whether a new coordinator pick is unavailable.
  if (setting === "l3_choice") return { ...old, l3_choice: value as Choice, l3_unavailable: null };
  if ((PROJECT_FIELDS as readonly string[]).includes(setting)) return { ...old, [setting]: value };
  return { ...old, roles: old.roles.map((row) => ({ ...row, engines: row.engines.map((e) => ({
    ...e,
    model: e.model.setting === setting ? { ...e.model, value: value as string | null } : e.model,
    effort: e.effort.setting === setting ? { ...e.effort, value: value as string | null } : e.effort,
  })) })) };
}

/**
 * One saved project setting. `expected`, when given, is the value shown: a change made elsewhere meanwhile is
 * refused with 409. The read shows the change from the press; the answer merges only the acknowledged field, so a
 * slower response to another field's save cannot restore its old value.
 */
export function useSetDefault(project: string) {
  return useSave<DefaultChange, ProjectDefaults>(["defaults", project],
    async (input) => ProjectDefaultsSchema.parse(await post("/api/defaults", { project, ...input })),
    (client, result, { setting }) => client.setQueryData<ProjectDefaults>(["defaults", project], (old) => {
      if (!old) return result;
      if (setting === "l3_choice") return { ...old, l3_choice: result.l3_choice, l3_unavailable: result.l3_unavailable };
      if ((PROJECT_FIELDS as readonly string[]).includes(setting)) return withDefault(old, setting, result[setting as typeof PROJECT_FIELDS[number]] ?? null);
      const saved = result.roles.flatMap((row) => row.engines).flatMap((e) => [e.model, e.effort]).find((f) => f.setting === setting);
      return withDefault(old, setting, saved?.value ?? null);
    }));
}

export function useRestart() {
  return useRequest({
    mutationFn: () => post("/api/restart", {}),
    queryKey: ["overview"],
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
  /** The turn's server-side identity, so the page can key its bubble on it while the reply streams. With `user`, a
   * Send now message joined the turn: the reply so far is complete and the rest answers `user` under this turn. */
  onTurn?: (turn: ActiveTurn, user?: string, delivery?: ChatMessage["delivery"]) => void;
}

/**
 * POST /api/chat and read the reply. A free L3 streams NDJSON: a first {"turn": {...}} names the
 * turn, {"t": "..."} lines feed onText, a {"turn": {...}, "user": "..."} line marks a Send now message
 * the turn took in, and the final {"done": {...}} comes back (the caller surfaces done.error). A busy L3 answers with a single {"queued": {...}} object instead — the same line
 * reader takes both. Conversation polling continues independently. Non-2xx throws ApiError.
 */
export async function streamChat(
  project: string,
  text: string,
  handlers: ChatStreamHandlers,
  /** A follow-up from a decision page names the decision's task; its rows carry the slug (SPEC.md §4.3). The
   * send id names the stored row or queue row, so the page reconciles its bubble by identity. */
  options: { slug?: string; request_id?: string } = {},
): Promise<ChatSent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project, text, ...(options.slug ? { slug: options.slug } : {}),
      ...(options.request_id ? { request_id: options.request_id } : {}) }),
  });
  if (!res.ok) throw await failure(res);
  if (!res.body) throw new Error("no response body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done: ChatSent = {};
  const handleLine = (line: string) => {
    if (!line.trim()) return;
    let parsed: { t?: unknown; done?: ChatDone; queued?: unknown; turn?: unknown; user?: unknown; delivery?: unknown };
    try {
      parsed = JSON.parse(line) as { t?: unknown; done?: ChatDone; queued?: unknown; turn?: unknown; user?: unknown };
    } catch {
      return; // tolerate a torn line
    }
    if (parsed.turn) {
      const turn = ActiveTurnSchema.safeParse(parsed.turn);
      if (turn.success) {
        done = { ...done, turn: turn.data };
        handlers.onAccepted?.();
        const delivery = ChatDeliverySchema.safeParse(parsed.delivery);
        handlers.onTurn?.(turn.data, typeof parsed.user === "string" ? parsed.user : undefined, delivery.success ? delivery.data : undefined);
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
