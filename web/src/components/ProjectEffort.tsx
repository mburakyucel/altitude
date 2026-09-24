import { useEffort, useSetEffort } from "../data/api";

/** Project defaults describe requested effort, independently of the active session. */
export function ProjectEffort({ name }: { name: string }) {
  const effort = useEffort(name);
  const save = useSetEffort(name);
  return <section className="project-effort" aria-label="Reasoning effort">
    <h3>Reasoning effort</h3>
    {effort.isPending ? <p className="text-meta text-muted" role="status">Loading effort settings…</p>
      : effort.isError ? <p className="text-meta text-danger" role="alert">
        Could not load effort settings. <button type="button" className="link" onClick={() => void effort.refetch()}>Retry</button>
      </p> : <>
        <fieldset disabled={save.isPending} className="project-effort-fields">
          {(["l3", "l2"] as const).map((role) => <div key={role}>
            <label className="project-engine">{role.toUpperCase()} effort
              <select className="composer-pill-select" value={effort.data[role] ?? ""}
                onChange={(event) => save.mutate({ role, effort: event.target.value || null })}>
                <option value="">Default</option>
                {effort.data.choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
              </select>
            </label>
            <p className="text-meta text-muted">Default: {effort.data.defaults[role]}</p>
          </div>)}
        </fieldset>
        {save.isError ? <p className="text-meta text-danger" role="alert">
          {save.error.message} <button type="button" className="link" onClick={() => save.mutate(save.variables)}>Retry save</button>
        </p> : save.isPending || save.isSuccess ? <p className="text-meta text-muted" role="status">{save.isPending ? "Saving effort…" : "Effort saved."}</p> : null}
        <p className="text-meta text-muted">L3 changes apply next turn. L2 defaults apply to fresh attempts; started tasks keep their effort. Per-task overrides win.</p>
        <p className="text-meta text-muted">Native uses the engine's own settings. These choices request effort; they do not confirm what the engine used. Higher effort can use more time and tokens.</p>
      </>}
  </section>;
}
