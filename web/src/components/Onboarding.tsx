import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { StatusMark, BusyLabel } from "./StatusMark";
import { saveIncidentReports, saveOperatorName, useMachine, usePrerequisites } from "../data/api";
import type { Machine } from "../data/api";
import { HostingCard } from "./Network";
import { SignIn } from "./SignIn";
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
    : <p className="text-meta text-muted"><StatusMark label="Loading…" /></p>;
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
    {actions(<button type="submit" className="btn btn-primary" disabled={save.status === "saving"}><BusyLabel busy={save.status === "saving"} label={label ?? "Continue"} working="Saving…" /></button>)}
  </form>;
}

/** The copyable command for the operator's own terminal. */
export function Command({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);
  return <div className="onboarding-command">
    <code>{text}</code>
    <button type="button" className="btn" onClick={() => void navigator.clipboard?.writeText(text).then(() => setCopied(true))}>
      {copied ? <StatusMark label="Copied" busy={false} /> : "Copy"}
    </button>
  </div>;
}

const marks = { met: "✓", unmet: "!", optional: "–" } as const;

/** What the agents need, from the doctor checks; `actions` receives Check again and whether everything required is met. */
export function PrerequisiteList({ actions }: { actions: (check: ReactNode, ready: boolean) => ReactNode }) {
  const checks = usePrerequisites();
  const machine = useMachine();
  const checking = checks.isFetching;
  const check = <button type="button" className="btn" disabled={checking} onClick={() => void checks.refetch()}>
    <BusyLabel busy={checking} label={checks.isError ? "Retry" : "Check again"} working="Checking…" />
  </button>;
  return <>
    {machine.data?.container_shell ? <div className="onboarding-note">Run on the host to open the container shell:<Command text={machine.data.container_shell} /></div> : null}
    <div className="settings-card onboarding-checks" aria-busy={checking}>
      {checks.isPending ? <p className="text-meta text-muted"><StatusMark label="Checking coding tools…" /></p>
        : checks.isError ? <p role="alert" className="text-meta text-danger">Could not check coding tools: {checks.error.message}</p>
          : checks.data.map((item) => <div key={item.key} className="onboarding-check">
            <span className={`onboarding-mark onboarding-${item.state}`} aria-hidden>{marks[item.state]}</span>
            <div className="min-w-0">
              <strong>{item.label}</strong>
              <span className="block text-meta text-muted">{item.state === "met" ? "Ready" : item.state === "optional" ? "Optional" : "Needs attention"}</span>
              {item.detail ? <p className="text-meta text-muted">{item.detail}</p> : null}
              {item.sign_in ? <SignIn engine={item.sign_in.engine} label={item.sign_in.label} signedIn={item.state === "met"} />
                : item.command && item.state === "unmet" ? <Command text={item.command} /> : null}
              {item.state === "unmet" ? item.options?.map((option) => <div key={option.label} className="onboarding-option">
                <span className="text-meta">{option.label}</span><Command text={option.command} />
              </div>) : null}
            </div>
          </div>)}
    </div>
    {actions(check, !checks.isError && !checks.isFetching && !!checks.data?.every((item) => item.state !== "unmet"))}
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
        {publishing ? <div className="choice-fields">
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
    {actions(<button type="submit" className="btn btn-primary" disabled={busy}><BusyLabel busy={busy} label={label ?? (publishing && changed ? "Save and continue" : "Continue")} working="Checking…" /></button>)}
  </form>;
}

const steps = [
  { key: "welcome", title: "Your Altitude" },
  { key: "agents", title: "Coding tools" },
  { key: "projects", title: "Project" },
] as const;
type Step = (typeof steps)[number]["key"];

/** Numbered positions describe navigation, never completion or readiness. */
export default function Onboarding({ projects, busy = false }: { projects: ReactNode; busy?: boolean }) {
  const inContainer = useMachine().data?.deployment === "container";
  const [params, setParams] = useSearchParams();
  const index = Math.max(0, steps.findIndex((step) => step.key === params.get("step")));
  const current = steps[index]!;
  const go = (step: Step) => setParams((current) => { const next = new URLSearchParams(current); next.set("step", step); return next; });
  const back = index > 0 ? <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => go(steps[index - 1]!.key)}>‹ Back</button> : null;
  const next = () => go(steps[Math.min(index + 1, steps.length - 1)]!.key);
  const nav = (...controls: ReactNode[]) => <div className="onboarding-nav">{back}<span className="grow" />{controls}</div>;
  const step = current.key;
  return <section className="first-run onboarding" aria-label="First run">
    <ol className="onboarding-steps" aria-label={`Step ${index + 1} of ${steps.length}`}>
      {steps.map((item, i) => <li key={item.key} aria-current={i === index ? "step" : undefined}>
        <span aria-hidden>{i + 1}</span><span className="onboarding-step-title">{item.title}</span>
      </li>)}
    </ol>
    <p className="onboarding-step-count text-meta">Step {index + 1} of {steps.length} · {current.title}</p>
    {step === "welcome" ? <>
      <header><h1>Welcome to Altitude</h1><p className="onboarding-lead">Altitude runs coding agents on projects {inContainer ? "inside the Linux container on the hosting computer" : "on the hosting computer"}. This browser is your control panel.</p></header>
      <HostingCard />
      <details className="settings-card"><summary className="link">Optional settings</summary>
        <p className="text-meta text-muted">Choose these now or later in Settings.</p>
        <div className="optional-settings">
          <Link className="link" to="/settings/name">Your name</Link>
          <Link className="link" to="/settings/incident-reports">Incident reports</Link>
          <Link className="link" to="/settings/voice">Voice input</Link>
        </div>
      </details>
      {nav(<button key="next" type="button" className="btn btn-primary" onClick={next}>Continue</button>)}
    </> : step === "agents" ? <>
      <header><h1>What the agents need</h1><p className="onboarding-lead">{inContainer ? "These checks run inside the container. Open its shell from a host terminal, run the commands below there or use Sign in, then check again. Host tools and sign-ins do not count." : "Run any command below in a terminal on the hosting computer, or use Sign in, then check again."} Altitude never asks for your password or a token.</p></header>
      <PrerequisiteList actions={(check, ready) => <>
        <div className="onboarding-recheck">{check}
          {!ready ? <p className="text-meta text-muted">You can add a project now. Required tools still need attention before agent work.</p> : null}</div>
        {nav(<button key="next" type="button" className={ready ? "btn btn-primary" : "btn"} onClick={next}>{ready ? "Continue" : "Choose a project anyway"}</button>)}
      </>} />
    </> : <>
      <header><h1>Add a project</h1><p className="onboarding-lead">{inContainer ? "Clone or import your repositories into /home/altitude/Projects from the container shell. These folders live in a persistent volume; host folders are not shared." : "Choose a repository on the hosting computer."} Adding it registers it with Altitude and sets up its Git guards and coordinator. Existing custom hooks require your review. Agent work uses your coding account’s allowance.</p>
        <p className="onboarding-lead">Setup may start the coordinator’s first introduction; it reuses an existing conversation.</p></header>
      {projects}
      {nav()}
    </>}
  </section>;
}
