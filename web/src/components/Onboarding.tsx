import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { saveIncidentReports, saveOperatorName, useMachine, usePrerequisites } from "../data/api";
import type { Machine } from "../data/api";
import "./onboarding.css";

type Save = { status: "idle" | "saving" } | { status: "failed"; error: Error };

/** Save one machine setting, publish it to the overview and the machine query, and report the outcome. */
function useMachineSave() {
  const client = useQueryClient();
  const [save, setSave] = useState<Save>({ status: "idle" });
  const run = async (write: () => Promise<Machine>) => {
    setSave({ status: "saving" });
    try {
      client.setQueryData(["machine"], await write());
      await client.invalidateQueries({ queryKey: ["overview"] });
      setSave({ status: "idle" });
      return true;
    } catch (error) {
      setSave({ status: "failed", error: error as Error });
      return false;
    }
  };
  return [save, run, () => setSave({ status: "idle" })] as const;
}

function Failure({ save }: { save: Save }) {
  return save.status === "failed" ? <p role="alert" className="text-meta text-danger">{save.error.message}</p> : null;
}

function Loading({ machine }: { machine: ReturnType<typeof useMachine> }) {
  return machine.isError
    ? <p role="alert" className="text-meta text-danger">Could not load this setting. <button type="button" className="link" onClick={() => void machine.refetch()}>Retry</button></p>
    : <p role="status" className="text-meta text-muted">Loading…</p>;
}

/**
 * The operator's name (SPEC.md §3.12): filled in from the saved name, ALTITUDE_OPERATOR or Git's user.name.
 * `actions` renders the step's buttons around the submit control, labelled `save` (default Continue); saving calls `onSaved`.
 */
type FormProps = { onSaved?: () => void; actions: (submit: ReactNode) => ReactNode; save?: string };

export function NameForm({ onSaved, actions, save: label }: FormProps) {
  const machine = useMachine();
  const [name, setName] = useState<string | null>(null);
  const [save, run, reset] = useMachineSave();
  if (!machine.data) return <><Loading machine={machine} />{actions(null)}</>;
  const value = name ?? machine.data.operator ?? "";
  const submit = async () => {
    if (await run(() => saveOperatorName(value))) onSaved?.();
  };
  return <form className="onboarding-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
    <div className="settings-card">
      <label className="onboarding-field">Your name
        <input value={value} maxLength={80} autoComplete="name" placeholder="Your name" disabled={save.status === "saving"}
          onChange={(event) => { setName(event.target.value); reset(); }} />
      </label>
      <p className="text-meta text-muted">{machine.data.operator ? "Filled in from your settings or your Git name. " : ""}Change it here or later in Settings.</p>
      <Failure save={save} />
    </div>
    {actions(<button type="submit" className="btn btn-primary" disabled={save.status === "saving"}>{save.status === "saving" ? "Saving…" : label ?? "Continue"}</button>)}
  </form>;
}

/** The copyable command for the operator's own terminal. */
function Command({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);
  return <div className="onboarding-command">
    <code>{text}</code>
    <button type="button" className="btn" onClick={() => void navigator.clipboard?.writeText(text).then(() => setCopied(true))}>
      {copied ? "Copied" : "Copy"}
    </button>
  </div>;
}

const marks = { met: "✓", unmet: "!", optional: "–" } as const;

/** What the agents need, from the doctor checks; `actions` receives Check again and whether everything required is met. */
export function PrerequisiteList({ actions }: { actions: (check: ReactNode, ready: boolean) => ReactNode }) {
  const checks = usePrerequisites();
  const checking = checks.isFetching;
  const check = <button type="button" className="btn" disabled={checking} onClick={() => void checks.refetch()}>
    {checking ? "Checking…" : "Check again"}
  </button>;
  return <>
    <div className="settings-card onboarding-checks" aria-busy={checking}>
      {checks.isPending ? <p role="status" className="text-meta text-muted">Checking this computer…</p>
        : checks.isError ? <p role="alert" className="text-meta text-danger">Could not check this computer: {checks.error.message}</p>
          : checks.data.map((item) => <div key={item.key} className="onboarding-check">
            <span className={`onboarding-mark onboarding-${item.state}`} aria-hidden>{marks[item.state]}</span>
            <div className="min-w-0">
              <strong>{item.label}</strong>
              <span className="sr-only">{item.state === "met" ? " — ready" : item.state === "optional" ? " — optional" : " — needs attention"}</span>
              {item.detail ? <p className="text-meta text-muted">{item.detail}</p> : null}
              {item.command && item.state === "unmet" ? <Command text={item.command} /> : null}
            </div>
          </div>)}
    </div>
    {actions(check, !!checks.data?.every((item) => item.state !== "unmet"))}
  </>;
}

/** Incident publication: off keeps incidents on this computer; on names a GitHub repository, Altitude's by default. */
export function IncidentReportsForm({ onSaved, actions, save: label }: FormProps) {
  const machine = useMachine();
  const [on, setOn] = useState<boolean | null>(null);
  const [repository, setRepository] = useState<string | null>(null);
  const [save, run, reset] = useMachineSave();
  if (!machine.data) return <><Loading machine={machine} />{actions(null)}</>;
  const publishing = on ?? !!machine.data.incident_repository;
  const target = repository ?? machine.data.incident_repository ?? machine.data.altitude_repository;
  const changed = publishing !== !!machine.data.incident_repository || (publishing && target.trim() !== machine.data.incident_repository);
  const submit = async () => {
    if (!changed || await run(() => saveIncidentReports(publishing ? target.trim() : null))) onSaved?.();
  };
  const busy = save.status === "saving";
  return <form className="onboarding-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
    <fieldset className="settings-card" disabled={busy}>
      <legend className="sr-only">Incident reports</legend>
      <div className="voice-option">
        <label className="voice-choice"><input type="radio" name="incident-reports" checked={!publishing} onChange={() => { setOn(false); reset(); }} />
          <span>Keep incidents on this computer</span></label>
        <p className="text-meta text-muted">Default. Nothing is published.</p>
      </div>
      <div className="voice-option">
        <label className="voice-choice"><input type="radio" name="incident-reports" checked={publishing} onChange={() => { setOn(true); reset(); }} />
          <span>Also publish them as GitHub issues</span></label>
        <p className="text-meta text-muted">Each new system incident opens one issue in Altitude’s own repository, or in a fork you name instead.</p>
        {publishing ? <div className="voice-endpoint">
          <label>Repository<input value={target} required spellCheck={false} autoCapitalize="off"
            onChange={(event) => { setRepository(event.target.value); reset(); }} /></label>
          <p className="text-meta text-muted">{target.trim() === machine.data.altitude_repository ? "Altitude’s repository. Replace it with your fork to receive the issues yourself." : "Checked with your signed-in GitHub CLI when you save."}</p>
        </div> : null}
      </div>
    </fieldset>
    <Failure save={save} />
    <p className="onboarding-note"><strong>What leaves this computer.</strong> Only the system-level cause and a fictional or redacted
      reproduction. Paths, project and task names, your name, ids, addresses and credentials are removed before publishing, and
      project code or conversations are never included. {target.trim() === machine.data.altitude_repository ? "Altitude’s repository is public, so these issues are public." : "A public repository makes these issues public."}</p>
    {actions(<button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Checking…" : label ?? (publishing && changed ? "Save and continue" : "Continue")}</button>)}
  </form>;
}

const steps = [
  { key: "name", title: "Your name" },
  { key: "agents", title: "Prerequisites" },
  { key: "incidents", title: "Incident reports" },
  { key: "projects", title: "Projects" },
] as const;
type Step = (typeof steps)[number]["key"];

/**
 * First run while no project is managed (SPEC.md §3.12): name, what the agents need, incident reports, then
 * projects. The step lives in `?step=` so reload and Back keep the place; every step is skippable and is a
 * Settings row afterwards. Adding a project opens its Setup and ends the flow.
 */
export default function Onboarding({ projects }: { projects: ReactNode }) {
  const [params, setParams] = useSearchParams();
  const index = Math.max(0, steps.findIndex((step) => step.key === params.get("step")));
  const current = steps[index]!;
  const go = (step: Step) => setParams((current) => { const next = new URLSearchParams(current); next.set("step", step); return next; });
  const back = index > 0 ? <button type="button" className="btn btn-ghost" onClick={() => go(steps[index - 1]!.key)}>‹ Back</button> : null;
  const next = () => go(steps[Math.min(index + 1, steps.length - 1)]!.key);
  const skip = <button type="button" className="btn btn-ghost" onClick={next}>Skip</button>;
  const nav = (...controls: ReactNode[]) => <div className="onboarding-nav">{back}<span className="grow" />{controls}</div>;
  const step = current.key;
  return <section className="first-run onboarding" aria-label="First run">
    <ol className="onboarding-steps" aria-label={`Step ${index + 1} of ${steps.length}`}>
      {steps.map((item, i) => <li key={item.key} aria-current={i === index ? "step" : undefined} className={i < index ? "done" : undefined}>
        <span aria-hidden>{i < index ? "✓" : i + 1}</span><span className="onboarding-step-title">{item.title}</span>
      </li>)}
    </ol>
    <p className="onboarding-step-count text-meta">Step {index + 1} of {steps.length} · {current.title}</p>
    {step === "name" ? <>
      <header><h1>Welcome to Altitude</h1><p className="onboarding-lead">Altitude runs coding agents on your projects on this computer. Agents and screens use your name wherever they would otherwise say “the operator”.</p></header>
      <NameForm onSaved={next} actions={(submit) => nav(<span key="skip">{skip}</span>, <span key="submit">{submit}</span>)} />
    </> : step === "agents" ? <>
      <header><h1>What the agents need</h1><p className="onboarding-lead">Agents use the command-line tools on this computer. Run any missing command in a terminal there, then check again. Altitude never asks for a password or token in the browser.</p></header>
      <PrerequisiteList actions={(check, ready) => nav(<span key="check">{check}</span>,
        <button key="next" type="button" className={ready ? "btn btn-primary" : "btn"} onClick={next}>{ready ? "Continue" : "Continue anyway"}</button>)} />
    </> : step === "incidents" ? <>
      <header><h1>Report Altitude’s own faults?</h1><p className="onboarding-lead">When Altitude’s machinery fails, it records an incident on this computer. It can also open a GitHub issue for each one, so the fault gets fixed in Altitude itself.</p></header>
      <IncidentReportsForm onSaved={next} actions={(submit) => nav(<span key="skip">{skip}</span>, <span key="submit">{submit}</span>)} />
    </> : <>
      <header><h1>Add your projects</h1><p className="onboarding-lead">A project is a folder on this computer, usually a Git checkout. Choose the folder that holds your projects; Altitude lists the folders directly inside it and nothing deeper.</p></header>
      {projects}
      {nav()}
    </>}
  </section>;
}
