import { Link, useParams } from "react-router";
import { InlineProse, Prose, ProseRepository } from "../components/Prose";
import { TokenUsage } from "../components/TokenUsage";
import { useOverview, useProject, useTask } from "../data/api";

/*
 * The task's report view (SPEC.md §3.4, §5.2 note 1): the full report that no longer travels in the
 * system prompt. The expanded system card links here as Full report, and as Digest (anchor) when the
 * task recorded one. Structured report fields read as sections; report.md and the digest read as prose.
 */

function rec(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}
function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}
function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function Section({ title, children, id }: { title: string; id?: string; children: React.ReactNode }) {
  return (
    <section className="report-section" id={id} aria-label={title}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

/** One report value as text: a scalar as itself, a list one item per line, an object as its fields. */
function line(v: unknown): string {
  if (v == null) return "";
  if (typeof v !== "object") return String(v);
  if (Array.isArray(v)) return v.map(line).join("\n");
  return Object.entries(v as Record<string, unknown>)
    .filter(([, value]) => value != null && value !== "")
    .map(([key, value]) => `${key.replace(/_/g, " ")}: ${line(value)}`)
    .join(" · ");
}

export default function TaskReport() {
  const { name = "", slug = "" } = useParams();
  const task = useTask(name, slug);
  const overview = useOverview();
  const project = useProject(name);
  if (task.isPending) {
    return (
      <div className="page" aria-label="Loading">
        <div className="skeleton h-6 w-48" />
      </div>
    );
  }
  if (task.isError) {
    return (
      <div className="page">
        <p className="text-danger">
          Could not load the report.{" "}
          <button type="button" className="link" onClick={() => void task.refetch()}>
            Retry
          </button>
        </p>
      </div>
    );
  }
  const view = task.data;
  const report = rec(view.report_json);
  const landed = rec(report.landed);
  const prs = arr(landed.prs).map(rec);
  const runs = arr(landed.main_runs).map(rec);
  const review = arr(report.review).map(rec);
  const blocked = str(report.blocked) || (report.blocked && typeof report.blocked === "object" ? line(report.blocked) : "");
  const digest = str(view.files?.digest);
  const markdown = str(view.files?.report);
  const lists: [string, unknown][] = [
    ["Decisions", report.decisions],
    ["FYI", report.fyi],
    ["Follow-ups", report.follow_ups],
    ["Deviations", report.deviations],
  ];
  const empty = Object.keys(report).length === 0 && !markdown && !digest;

  return (
    <ProseRepository value={project.data?.repository}>
    <div className="page report-page">
      <p className="text-meta">
        <Link to={`/projects/${name}/tasks/${slug}`}>← {view.title || slug}</Link>
      </p>
      <h1 className="text-[18px] font-semibold">Report</h1>
      <TokenUsage usage={view.token_usage} running={view.state === "running"} engines={overview.data?.engines} />
      {empty ? <p className="text-muted">No report yet.</p> : null}
      {prs.length > 0 || runs.length > 0 || landed.deploy != null ? (
        <Section title="Landed">
          <ul>
            {prs.map((pr, index) => (
              <li key={index}>
                <InlineProse text={`PR #${String(pr.number ?? "?")} ${pr.merged ? "merged" : "open"}${str(pr.title) ? ` · ${str(pr.title)}` : ""}`} />
              </li>
            ))}
            {runs.map((run, index) => (
              <li key={`run-${index}`}>
                Main checks {str(run.conclusion) || "pending"}
                {run.id != null ? ` · run ${String(run.id)}` : ""}
              </li>
            ))}
            {landed.deploy != null ? <li>Deploy: <InlineProse text={line(landed.deploy)} /></li> : null}
          </ul>
        </Section>
      ) : null}
      {review.length > 0 ? (
        <Section title="Review">
          <ul>
            {review.map((finding, index) => (
              <li key={index}>
                <InlineProse text={str(finding.summary) || str(finding.title) || line(finding)} />
                {str(finding.severity) ? ` · ${str(finding.severity)}` : ""}
                {str(finding.disposition) ? ` · ${str(finding.disposition)}` : ""}
                {str(finding.reason) ? <InlineProse text={` · ${str(finding.reason)}`} /> : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
      {blocked ? (
        <Section title="Blocked">
          <p className="text-danger"><InlineProse text={blocked} /></p>
        </Section>
      ) : null}
      {lists.map(([title, value]) =>
        arr(value).length > 0 ? (
          <Section key={title} title={title}>
            <ul>
              {arr(value).map((item, index) => (
                <li key={index}><Prose text={line(item)} /></li>
              ))}
            </ul>
          </Section>
        ) : null,
      )}
      {report.spend != null && Object.keys(rec(report.spend)).length > 0 ? (
        <Section title="Spend">
          <p>{line(report.spend)}</p>
        </Section>
      ) : null}
      {markdown ? (
        <Section title="Report notes">
          <Prose text={markdown} />
        </Section>
      ) : null}
      {digest ? (
        <Section title="Digest" id="digest">
          <Prose text={digest} />
        </Section>
      ) : null}
    </div>
    </ProseRepository>
  );
}
