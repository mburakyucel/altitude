import { useEffect, useId, useRef } from "react";
import { Link } from "react-router";
import { TokenUsageSchema } from "../data/api";
import type { TaskRow } from "../data/api";
import type { DotState } from "../shell/projects";
import { setSelectedProject } from "../shell/scope";
import "./agent-graph.css";

/*
 * Work's graph (SPEC.md §3.7): every current task on a ring around the coordinator, the operator's turns
 * first, each node reading what its row reads and opening what its row opens. Live cues come from the
 * task's own usage reading, which the service refreshes and the change stream delivers.
 */

/** The graph's width inside the 480px panel, the ring's half-circle radius and the least spacing between neighbours. */
export const GRAPH_WIDTH = 448;
const RADIUS = 150;
const SPACING = 132;
const NODE = 40;
/** A usage reading older than this claims nothing live; a request within ACTIVE_MS is live (the activity line's rule). */
export const FRESH_MS = 30_000;
export const ACTIVE_MS = 60_000;
const MOVE_MS = 200;
/** More helpers than this still count in the state line; the rim shows this many pips. */
const MAX_PIPS = 5;

export interface GraphNode {
  key: string;
  title: string;
  attention: string | null;
  meta: string;
  dot: DotState;
  to: string;
  linkState: unknown;
  questions: number;
  /** An agent run: the same node with rounded-square corners. */
  run: boolean;
  /** The key of the current node this one is planned after. */
  after: string | null;
  live: boolean;
  /** Helpers active now; null when the engine gave no helper evidence. */
  helpers: number | null;
}

export interface Activity {
  live: boolean;
  helpers: number | null;
}

function time(value: unknown): number | null {
  const parsed = typeof value === "string" ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * A running task's live cues from its usage reading: the worker's own newest request, and its helpers' newest
 * requests, within ACTIVE_MS. A reading older than FRESH_MS, or a saved read, claims nothing.
 */
export function taskActivity(task: TaskRow, now: number, saved: boolean): Activity {
  const usage = TokenUsageSchema.safeParse(task["token_usage"]);
  const checked = usage.success ? time(usage.data.checked_at) : null;
  if (task.state !== "running" || saved || !usage.success || checked == null || now - checked > FRESH_MS) {
    return { live: false, helpers: task.state === "running" && !saved && !usage.success ? null : 0 };
  }
  const session = typeof task["session_id"] === "string" ? task["session_id"] : null;
  const recent = (at: unknown) => {
    const t = time(at);
    return t != null && now - t < ACTIVE_MS;
  };
  const own = usage.data.sessions.find((row) => row.session_id === session);
  const helpers = usage.data.helpers;
  return {
    live: recent(own?.observed_at),
    helpers: !helpers || helpers.status === "unknown" ? null
      : helpers.sessions.filter((row) => row.owner_session_id === session && recent(row.observed_at)).length,
  };
}

/** The node's state line: the row's attention text whole, else its meta to the first " · ", or its first sentence. */
export function stateLine(attention: string | null, meta: string): string {
  if (attention) return attention;
  const head = meta.split(" · ")[0] ?? "";
  return head.split(". ")[0]!.replace(/\.$/, "");
}

/** The ring's straight sides: none up to its natural capacity, then long enough to keep SPACING. */
export function trackLength(n: number): number {
  return Math.max(0, (SPACING * n - 2 * Math.PI * RADIUS) / 2);
}

/** Node centres, clockwise from the top, evenly spaced along a racetrack around (cx, cy). */
export function ringPoints(n: number, cx: number, cy: number): [number, number][] {
  const length = trackLength(n);
  const top = cy - length / 2;
  const bottom = cy + length / 2;
  type Segment = { arc: true; c: number; from: number; to: number } | { arc: false; side: number; from: number; to: number };
  const segments: Segment[] = [
    { arc: true, c: top, from: -Math.PI / 2, to: 0 },
    { arc: false, side: 1, from: top, to: bottom },
    { arc: true, c: bottom, from: 0, to: Math.PI },
    { arc: false, side: -1, from: bottom, to: top },
    { arc: true, c: top, from: Math.PI, to: 1.5 * Math.PI },
  ];
  const lengths = segments.map((s) => (s.arc ? RADIUS * (s.to - s.from) : Math.abs(s.to - s.from)));
  const total = lengths.reduce((a, b) => a + b, 0);
  const points: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    let d = (total * i) / n;
    for (const [index, s] of segments.entries()) {
      const span = lengths[index]!;
      if (d <= span + 1e-9) {
        if (s.arc) {
          const a = s.from + d / RADIUS;
          points.push([cx + RADIUS * Math.cos(a), s.c + RADIUS * Math.sin(a)]);
        } else {
          points.push([cx + s.side * RADIUS, s.from + (s.to > s.from ? d : -d)]);
        }
        break;
      }
      d -= span;
    }
  }
  return points;
}

function helperText(node: GraphNode): string {
  if (node.helpers == null) return "helper activity unknown";
  return node.helpers ? `${node.helpers} helper${node.helpers === 1 ? "" : "s"} active` : "";
}

export function nodeLabel(node: GraphNode): string {
  return [node.title, node.attention, node.meta, helperText(node)].filter(Boolean).join(" · ");
}

/** The operator's turns first, then the list's order. */
export function ringOrder(nodes: GraphNode[]): GraphNode[] {
  return [...nodes.filter((n) => n.dot === "waiting"), ...nodes.filter((n) => n.dot !== "waiting")];
}

function Title({ text }: { text: string }) {
  const words = text.split(" ");
  const last = words.pop() ?? "";
  return (
    <span className="ag-title">
      {words.length ? `${words.join(" ")} ` : ""}
      <span className="ag-last">
        {last}
        <svg className="ag-go" aria-hidden viewBox="0 0 20 20" width="12" height="12">
          <path d="M8 5l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    </span>
  );
}

export function AgentGraph({
  project,
  nodes,
  l3,
  saved,
}: {
  project: string;
  nodes: GraphNode[];
  /** The coordinator's compact word, and whether it is answering or handling something now. */
  l3: { word: string; live: boolean };
  /** A saved read: nodes stay and open, but nothing claims to be live. */
  saved: boolean;
}) {
  const arrow = `ag-arrow-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const ring = ringOrder(nodes);
  const n = ring.length;
  const length = trackLength(n);
  const height = n ? Math.round(2 * RADIUS + length + 150) : 110;
  const cx = GRAPH_WIDTH / 2;
  const cy = n ? RADIUS + length / 2 + 40 : 40;
  const points = ringPoints(n, cx, cy);
  const at = new Map(ring.map((node, i) => [node.key, points[i]!]));

  // A node that joins after the first reading fades in at its slot (200ms); the others glide to theirs.
  const seen = useRef<Set<string> | null>(null);
  const joined = useRef(new Map<string, number>());
  const keys = new Set(ring.map((node) => node.key));
  if (seen.current) for (const key of keys) if (!seen.current.has(key)) joined.current.set(key, Date.now());
  useEffect(() => {
    seen.current = keys;
  });
  const entering = (key: string) => Date.now() - (joined.current.get(key) ?? 0) < MOVE_MS * 5;

  const edges = ring.flatMap((node) => {
    const [x, y] = at.get(node.key)!;
    const spoke = <line key={`spoke:${node.key}`} className="ag-spoke" x1={cx} y1={cy} x2={x} y2={y} />;
    const from = node.after ? at.get(node.after) : undefined;
    if (!from) return [spoke];
    // Along the line between the two tasks, bowed toward the coordinator: it clears the helper pips on the rims
    // and the labels under the nodes.
    const [ax, ay] = from;
    const qx = (ax + x) / 2 + (cx - (ax + x) / 2) * 0.35;
    const qy = (ay + y) / 2 + (cy - (ay + y) / 2) * 0.35;
    const toward = (px: number, py: number, tx: number, ty: number, r: number) => {
      const d = Math.hypot(tx - px, ty - py) || 1;
      return [px + ((tx - px) / d) * r, py + ((ty - py) / d) * r] as const;
    };
    const [sx, sy] = toward(ax, ay, x, y, 24);
    const [ex, ey] = toward(x, y, ax, ay, 27);
    return [spoke, <path key={`after:${node.key}`} className="ag-after" d={`M${sx},${sy} Q${qx},${qy} ${ex},${ey}`} markerEnd={`url(#${arrow})`} />];
  });

  return (
    <div className="ag" style={{ width: GRAPH_WIDTH, height }} role="group" aria-label="Current work as a graph">
      <svg className="ag-edges" width={GRAPH_WIDTH} height={height} aria-hidden>
        <defs>
          <marker id={arrow} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" className="ag-arrowhead" />
          </marker>
        </defs>
        {edges}
      </svg>
      <div className="ag-hub" style={{ left: cx - 22, top: cy - 22 }} data-live={(!saved && l3.live) || undefined}>
        <span className="ag-hub-disc">L3</span>
        {saved ? null : <span className="ag-hub-word">{l3.word}</span>}
      </div>
      {ring.map((node) => {
        const [x, y] = at.get(node.key)!;
        const label = nodeLabel(node);
        const helpers = helperText(node);
        const pips = Math.min(node.helpers ?? 0, MAX_PIPS);
        return (
          <Link
            key={node.key}
            className="ag-node"
            to={node.to}
            state={node.linkState}
            onClick={() => setSelectedProject(project)}
            aria-label={label}
            title={label}
            style={{ left: x - NODE / 2, top: y - NODE / 2 }}
            data-state={node.dot}
            data-run={node.run || undefined}
            data-live={node.live || undefined}
            data-enter={entering(node.key) || undefined}
          >
            <span className="ag-disc" aria-hidden />
            {node.questions ? <span className="ag-count" aria-hidden>{node.questions}</span> : null}
            {Array.from({ length: pips }, (_, j) => {
              const a = -Math.PI / 2 + (j - (pips - 1) / 2) * 0.62;
              return <span key={j} className="ag-pip" aria-hidden style={{ left: 20 + 25 * Math.cos(a) - 5, top: 20 + 25 * Math.sin(a) - 5 }} />;
            })}
            <span className="ag-label" aria-hidden>
              <Title text={node.title} />
              <span className="ag-state">
                {stateLine(node.attention, node.meta)}
                {node.helpers ? ` · ${helpers}` : ""}
              </span>
            </span>
          </Link>
        );
      })}
    </div>
  );
}
