/** Dormant v2 wire validators. No production API path imports this module yet. */
import { z } from "zod";

const Text = z.string().refine((value) => value.trim().length > 0, "must not be empty");
const Percent = z.number().min(0).max(100).nullable();
const Provider = z.enum(["claude", "codex"]);
const RepoPath = Text.refine((path) => {
  const parts = path.split("/");
  return !path.startsWith("/") && !path.endsWith("/") && !path.includes("\\") && !path.includes("\0") &&
    parts.every((part) => part !== "" && part !== "." && part !== "..");
}, "must be a normalized repo-relative POSIX path");
const PathScope = z.object({
  version: z.literal(1), kind: z.literal("paths"), paths: z.array(RepoPath).min(1),
}).strict().superRefine((scope, context) => {
  if (new Set(scope.paths).size !== scope.paths.length)
    context.addIssue({ code: "custom", path: ["paths"], message: "paths must be unique" });
});
export const PublicationScopeSchema = z.union([
  PathScope,
  z.object({ version: z.literal(1), kind: z.literal("policy_derived") }).strict(),
]);
export const ProviderQuotaObservationSchema = z.object({
  version: z.literal(1),
  provider: Provider,
  freshness: z.enum(["fresh", "stale", "unknown"]),
  observed_at: Text.nullable(),
  weekly_remaining_percent: Percent,
  short_remaining_percent: Percent,
  availability: z.enum(["available", "unknown", "quota_limited", "capacity_limited"]),
  retry_at: Text.nullable(),
}).strict().superRefine((item, context) => {
  const noMeasurement = item.observed_at === null && item.weekly_remaining_percent === null &&
    item.short_remaining_percent === null;
  if (item.freshness === "unknown" && !noMeasurement)
    context.addIssue({ code: "custom", message: "unknown freshness cannot carry observations" });
  if (item.freshness !== "unknown" && item.observed_at === null)
    context.addIssue({ code: "custom", path: ["observed_at"], message: "observation time is required" });
  if ((item.availability === "unknown") !== (item.freshness === "unknown"))
    context.addIssue({ code: "custom", path: ["availability"], message: "unknown remains eligible uncertainty" });
  const limited = item.availability === "quota_limited" || item.availability === "capacity_limited";
  if (limited !== (item.retry_at !== null))
    context.addIssue({ code: "custom", path: ["retry_at"], message: "only a failure carries retry_at" });
});
const Message = z.object({ at: Text, role: z.enum(["user", "l2"]), text: Text }).strict();
const Publication = z.object({
  receipt_id: Text, status: z.enum(["published", "merged", "held", "failed"]),
  pr_number: z.number().int().min(1).nullable(),
}).strict();
const Worker = z.object({
  provider: Provider, status: z.enum(["starting", "live", "stopping", "stopped", "unknown"]),
}).strict();
const Blocked = z.object({
  kind: z.enum(["question", "operational_hold", "preempted_by_episode", "provider_unavailable", "other"]),
  summary: Text, resume_at: Text.nullable(),
}).strict();
export const TaskProjectionSchema = z.object({
  wire_version: z.literal(1),
  kind: z.literal("task"),
  project: Text,
  slug: Text,
  title: Text,
  state: z.enum(["queued", "running", "settling", "blocked", "done", "rejected"]),
  publication_scope: PublicationScopeSchema,
  conversation: z.array(Message),
  publication: Publication.nullable(),
  current_worker: Worker.nullable(),
  blocked: Blocked.nullable(),
  updated_at: Text,
}).strict();
const Session = z.object({
  provider: Provider, role: z.enum(["l3", "l2", "helper"]),
  status: z.enum(["live", "idle", "stopped", "unknown"]),
  task: Text.nullable(), context_percent: Percent,
}).strict();
const Service = z.object({
  name: Text, state: z.enum(["healthy", "degraded", "stopped", "unknown"]), source_sha: Text.nullable(),
}).strict();
const Recovery = z.object({
  state: z.enum(["clear", "open", "waiting_operator", "held"]),
  episode_id: Text.nullable(), reason: Text.nullable(),
}).strict().superRefine((item, context) => {
  const invalid = item.state === "clear" ? item.episode_id !== null || item.reason !== null :
    item.episode_id === null || item.reason === null;
  if (invalid)
    context.addIssue({ code: "custom", message: "clear has no episode; active recovery requires one" });
});
export const OperationalProjectionSchema = z.object({
  wire_version: z.literal(1), kind: z.literal("operational"), generated_at: Text,
  providers: z.array(ProviderQuotaObservationSchema), sessions: z.array(Session),
  services: z.array(Service), recovery: Recovery,
}).strict().superRefine((item, context) => {
  const providers = item.providers.map((provider) => provider.provider);
  if (new Set(providers).size !== providers.length)
    context.addIssue({ code: "custom", path: ["providers"], message: "providers must be unique" });
  const services = item.services.map((service) => service.name);
  if (new Set(services).size !== services.length)
    context.addIssue({ code: "custom", path: ["services"], message: "services must be unique" });
});
