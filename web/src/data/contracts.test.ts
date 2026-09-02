import { describe, expect, it } from "vitest";
import fixtures from "../../../schemas/fixtures/projections.v1.json";
import {
  OperationalProjectionSchema,
  ProviderQuotaObservationSchema,
  PublicationScopeSchema,
  TaskProjectionSchema,
} from "./contracts";

describe("dormant v2 projection wire contracts", () => {
  it("accepts the shared version 1 fixtures", () => {
    expect(TaskProjectionSchema.parse(fixtures.valid.task).wire_version).toBe(1);
    expect(OperationalProjectionSchema.parse(fixtures.valid.operational).wire_version).toBe(1);
  });

  it("rejects the same unknown versions and extra authority fields as Python", () => {
    expect(() => TaskProjectionSchema.parse(fixtures.invalid.task_unknown_version)).toThrow();
    expect(() => TaskProjectionSchema.parse(fixtures.invalid.task_extra_authority)).toThrow();
    expect(() =>
      OperationalProjectionSchema.parse(fixtures.invalid.operational_unknown_version),
    ).toThrow();
    expect(() =>
      OperationalProjectionSchema.parse(fixtures.invalid.operational_extra_authority),
    ).toThrow();
  });

  it("accepts and rejects the shared integral JSON number cases", () => {
    for (const value of fixtures.numeric_cases.version.accepted) {
      expect(TaskProjectionSchema.parse({ ...fixtures.valid.task, wire_version: value }).wire_version).toBe(1);
    }
    for (const value of fixtures.numeric_cases.version.rejected) {
      expect(() => TaskProjectionSchema.parse({ ...fixtures.valid.task, wire_version: value })).toThrow();
    }
    const publication = { receipt_id: "receipt-1", status: "merged", pr_number: null };
    for (const value of fixtures.numeric_cases.publication_pr_number.accepted) {
      expect(TaskProjectionSchema.parse({
        ...fixtures.valid.task, publication: { ...publication, pr_number: value },
      }).publication?.pr_number).toBe(value);
    }
    for (const value of fixtures.numeric_cases.publication_pr_number.rejected) {
      expect(() => TaskProjectionSchema.parse({
        ...fixtures.valid.task, publication: { ...publication, pr_number: value },
      })).toThrow();
    }
    for (const value of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
      expect(() => TaskProjectionSchema.parse({ ...fixtures.valid.task, wire_version: value })).toThrow();
      expect(() => TaskProjectionSchema.parse({
        ...fixtures.valid.task, publication: { ...publication, pr_number: value },
      })).toThrow();
    }
  });

  it("rejects unknown projection variants", () => {
    expect(() => TaskProjectionSchema.parse({ ...fixtures.valid.task, state: "reported" })).toThrow();
    expect(() => OperationalProjectionSchema.parse({
      ...fixtures.valid.operational,
      recovery: { state: "paused", episode_id: "r-1", reason: "not a contract variant" },
    })).toThrow();
  });

  it("keeps publication scope normalized and closed", () => {
    expect(() =>
      PublicationScopeSchema.parse({ version: 1, kind: "paths", paths: ["a/../b"] }),
    ).toThrow();
    expect(() =>
      PublicationScopeSchema.parse({ version: 1, kind: "paths", paths: ["a", "a"] }),
    ).toThrow();
    expect(() =>
      PublicationScopeSchema.parse({ version: 1, kind: "policy_derived", paths: ["self-authorized"] }),
    ).toThrow();
    expect(() => PublicationScopeSchema.parse({ version: 1, kind: "paths", paths: ["nul\0path"] })).toThrow();
  });

  it("does not turn an unknown quota observation into exhaustion", () => {
    expect(
      ProviderQuotaObservationSchema.parse({
        version: 1,
        provider: "codex",
        freshness: "unknown",
        observed_at: null,
        weekly_remaining_percent: null,
        short_remaining_percent: null,
        availability: "unknown",
        retry_at: null,
      }).availability,
    ).toBe("unknown");
    expect(() =>
      ProviderQuotaObservationSchema.parse({
        version: 1,
        provider: "codex",
        freshness: "unknown",
        observed_at: null,
        weekly_remaining_percent: null,
        short_remaining_percent: null,
        availability: "quota_limited",
        retry_at: "2026-09-02T09:00:00+00:00",
      }),
    ).toThrow();
  });
});
