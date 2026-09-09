import { useL3Engine } from "../data/api";
import type { EngineReadout } from "../data/api";

/** The same project pin, in desktop composer or phone project details. */
export function L3EngineSelect({ name, engine, engines }: { name: string; engine: string; engines: EngineReadout[] }) {
  const pin = useL3Engine(name);
  return <>
    <select className="composer-pill-select" aria-label="L3 engine"
      value={engines.some((e) => e.engine === engine) ? engine : ""}
      disabled={pin.isPending} onChange={(event) => pin.mutate(event.target.value || null)}>
      <option value="">Auto</option>
      {engines.map((e) => <option key={e.engine} value={e.engine}>{e.label}</option>)}
    </select>
    {pin.isError ? <p className="text-danger text-meta" role="alert">{pin.error.message}</p> : null}
  </>;
}
