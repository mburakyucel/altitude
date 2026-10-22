"""Write the agent graph boards beside this file: python3 design/wireframes/agent-graph/gen.py"""
import math
import pathlib

out = pathlib.Path(__file__).resolve().parent
HEAD = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Agent graph: {title}</title>
<link rel="stylesheet" href="../wireframes.css"><link rel="stylesheet" href="graph.css"></head>
<body><div class="root" data-theme="light" style="width:{w}px;height:{h}px">
'''
TAIL = '''</div><script>if(location.search.indexOf('dark')>=0)document.querySelector('.root').dataset.theme='dark'</script></body></html>
'''
CHEVR = '<svg class="i sm chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>'
GO = '<svg class="go" viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5l5 5-5 5"/></svg>'
LIST = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h12M8 12h12M8 18h12"/><circle cx="4" cy="6" r="1" fill="currentColor"/><circle cx="4" cy="12" r="1" fill="currentColor"/><circle cx="4" cy="18" r="1" fill="currentColor"/></svg>'
GRAPH = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><circle cx="5" cy="5" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="18" cy="19" r="2"/><path d="M6.5 6.5l3.3 3.3M17.3 7.2l-3.2 2.9M16.6 17.6l-2.6-3.4"/></svg>'
P = "harbor"
PANEL = 480          # the work panel's width with Graph chosen: the task page's live-panel width
W = PANEL - 36       # the graph inside the panel's padding
R = 150              # half-circle radius of the ring
GAP = 132            # the least distance between neighbours along the ring
LMIN = 0             # a plain circle until the work needs a racetrack


def board(name, w, h, body):
    (out / f"{name}.html").write_text(HEAD.format(title=name, w=w, h=h) + body + TAIL)


# ---- the fictional project, as the work panel's reads already describe it ----
# (title, attention, meta, state, live, active helpers, after, kind)
TASKS = [
    ("Add offline sync for the field app", "", "L2 working · Opus on Claude · started 12 min ago", "running", True, 2, None, "task"),
    ("Fix the login redirect loop", "Your turn · 1 question", "L2 working · Opus on Claude · started 2 hr ago", "waiting", False, 0, None, "task"),
    ("Rework the export pipeline", "", "L2 working · Opus on Codex · started 41 min ago", "running", True, 1, None, "task"),
    ("Ship the export dashboard", "", "Planned · Waiting for “Rework the export pipeline” to finish.", "idle", False, 0, 2, "task"),
    ("Upgrade the payment SDK", "", "The task session ended before completion. Waiting for the coordinator to check the blocker.",
     "danger", False, 0, None, "task"),
    ("Update the onboarding copy", "", "Queued · waits for a free task slot", "idle", False, 0, None, "task"),
    ("Dependency check", "", "Agent run · 4 of 20 min", "running", True, 0, None, "run"),
]


def ordered(tasks):
    """The ring starts with the tasks whose turn is yours, then follows the list's order."""
    return sorted(tasks, key=lambda t: t[3] != "waiting")


def state_line(attention, meta):
    """The row's attention text whole, else the row's meta to its first " · ", or its first sentence."""
    if attention:
        return attention
    head = meta.split(" · ")[0]
    return head.split(". ")[0].rstrip(".")


def toggle(on="graph"):
    def b(key, label, icon):
        return f'<span class="tg{" on" if key == on else ""}" role="button" aria-pressed="{str(key == on).lower()}">{icon}{label}</span>'
    return f'<span class="tgl" role="group" aria-label="Show work as">{b("list", "List", LIST)}{b("graph", "Graph", GRAPH)}</span>'


def wp_head(on="graph", count="7 current · 3 done this week"):
    return f'<div class="wh2"><div><h2>Work</h2><span class="ct">{count}</span></div>{toggle(on)}</div>'


def track_height(n):
    """The ring's straight sides: LMIN up to the ring's natural capacity, then long enough to keep GAP."""
    return max(LMIN, (GAP * n - 2 * math.pi * R) / 2)


def ring_positions(n, cx, cy):
    # Clockwise from the top, evenly spaced along a racetrack around the coordinator.
    L = track_height(n)
    top, bottom = cy - L / 2, cy + L / 2
    segs = [("arc", top, -math.pi / 2, 0), ("line", 1, top, bottom), ("arc", bottom, 0, math.pi),
            ("line", -1, bottom, top), ("arc", top, math.pi, 1.5 * math.pi)]
    lengths = [R * (e - s0) if k == "arc" else abs(e - s0) for k, _, s0, e in segs]
    total = sum(lengths)
    pts = []
    for i in range(n):
        d = total * i / n
        for (kind, c, s0, e), length in zip(segs, lengths):
            if d <= length + 1e-9:
                if kind == "arc":
                    a = s0 + d / R
                    pts.append((cx + R * math.cos(a), c + R * math.sin(a)))
                else:
                    pts.append((cx + c * R, s0 + (d if e > s0 else -d)))
                break
            d -= length
    return pts


def constellation(tasks=TASKS, l3="Answering", l3_live=True, focus=None, still=False, stale=False, unknown=()):
    tasks = ordered(tasks)
    n = len(tasks)
    L = track_height(n)
    h = int(2 * R + L + 140) if n else 110
    cx, cy = W / 2, (R + L / 2 + 40) if n else 40
    pts = ring_positions(n, cx, cy)
    edges, nodes = [], []
    where = {t[0]: i for i, t in enumerate(tasks)}
    for i, ((title, attention, meta, state, live, helpers, after, kind), (x, y)) in enumerate(zip(tasks, pts)):
        edges.append(f'<line class="spoke" x1="{cx:.0f}" y1="{cy:.0f}" x2="{x:.0f}" y2="{y:.0f}"/>')
        if after is not None and TASKS[after][0] in where:
            ax, ay = pts[where[TASKS[after][0]]]
            # bow toward the coordinator, so the arc leaves each node on its inner side, away from the labels
            mx, my = (ax + x) / 2, (ay + y) / 2
            qx, qy = mx + (cx - mx) * 0.5, my + (cy - my) * 0.5

            def toward(px, py, r):
                vx, vy = qx - px, qy - py
                vd = math.hypot(vx, vy) or 1
                return px + vx / vd * r, py + vy / vd * r
            sx, sy = toward(ax, ay, 24)
            ex, ey = toward(x, y, 26)
            edges.append(f'<path class="dep" d="M{sx:.0f},{sy:.0f} Q{qx:.0f},{qy:.0f} {ex:.0f},{ey:.0f}" marker-end="url(#arrow)"/>')
        cue = "" if stale or not live else (" breathe" if not still else " halo")
        helpers = 0 if stale or title in unknown else helpers
        pips = "".join(f'<span class="pip" style="left:{20 + 25 * math.cos(-math.pi / 2 + (j - (helpers - 1) / 2) * 0.62) - 5:.0f}px;'
                       f'top:{20 + 25 * math.sin(-math.pi / 2 + (j - (helpers - 1) / 2) * 0.62) - 5:.0f}px"></span>'
                       for j in range(helpers))
        line = state_line(attention, meta)
        active = f'{helpers} helper{"s" if helpers != 1 else ""} active' if helpers else ""
        hl = f' · {active.replace(" helper", "&nbsp;helper")}' if helpers else ""
        badge = '<b class="gq">1</b>' if attention.startswith("Your turn") else ""
        extra = "helper activity unknown" if title in unknown else ""
        name = " · ".join(filter(None, (title, attention, meta, active, extra)))
        nodes.append(
            f'<a class="gn s-{state}{cue}{" focus" if focus == i else ""}{" run" if kind == "run" else ""}" href="#" '
            f'style="left:{x - 20:.0f}px;top:{y - 20:.0f}px" aria-label="{name}" title="{name}">'
            f'<span class="disc"></span>{badge}{pips}'
            f'<span class="gl"><span class="gt">{" ".join(title.split()[:-1])} <span class="nw">{title.split()[-1]}{GO}</span></span><span class="gm">{line}{hl}</span></span></a>')
    hub_cue = "" if stale or not l3_live else (" breathe" if not still else " halo")
    hub = (f'<div class="hub{hub_cue}" style="left:{cx - 22:.0f}px;top:{cy - 22:.0f}px" aria-hidden="true">'
           f'<span class="disc">L3</span><span class="hl">{"" if stale else l3}</span></div>')
    svg = (f'<svg class="edges" width="{W}" height="{h}" aria-hidden="true"><defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" '
           f'markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="arrowhead"/></marker></defs>'
           + "".join(edges) + '</svg>')
    return (f'<div class="graph" style="width:{W}px;height:{h}px" role="group" aria-label="Current work as a graph">'
            f'{svg}{hub}{"".join(nodes)}</div>')


def foot():
    return (f'<div class="wfold">{CHEVR}Done this week (3)</div>'
            f'<div class="wagents">Agents · 2{CHEVR}</div>')


DOTS = {"running": "", "waiting": "held", "danger": "bad", "idle": "idle"}


def list_rows():
    def row(title, attention, meta, state, *_):
        att = f'<div class="ta">{attention}</div>' if attention else ""
        return (f'<a class="trow" href="#"><span class="dot {DOTS[state]}"></span><div style="flex:1">'
                f'<div class="tt">{title}</div>{att}<div class="tm">{meta}</div></div>{CHEVR}</a>')
    return '<div class="sh">Current <span>(7)</span></div>' + "".join(row(*t) for t in TASKS)


RAIL = ('<aside class="rail"><div class="brand"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div>'
        '<div class="ri">Needs you<span class="badge">1</span></div><div class="rsec"><span>Projects</span></div>'
        '<div class="ri"><span class="dot idle"></span>altitude</div>'
        f'<div class="ri sel"><span class="dot held"></span>{P}</div>'
        '<div class="engines"><div class="erow"><div class="t"><b>Claude</b><span>38% of week</span></div><div class="meter"><i style="width:38%"></i></div></div>'
        '<div class="erow"><div class="t"><b>Codex</b><span>22% of week</span></div><div class="meter"><i style="width:22%"></i></div></div></div>'
        '<div class="ri" style="height:36px">Monitor</div><div class="who"><span class="avatar">O</span>Operator</div></aside>')

CONVO = ('<div class="day">Today</div>'
         '<div class="me">What is running right now?</div>'
         '<div class="l3"><p>Five tasks and one agent run. The login fix waits on your answer about session length; '
         'the payment SDK upgrade ended early and I am looking at why.</p></div>'
         '<div class="me">Start the export dashboard once the pipeline rework lands.</div>'
         '<div class="l3"><p>Planned it after the pipeline rework. It starts on its own when that task is done.</p></div>')


def composer():
    return ('<div class="composer"><div class="ph2">Message L3 about harbor</div><div class="crow">'
            '<span class="l3pill">L3 · <b>Auto</b> · Default effort</span><span style="flex:1"></span></div></div>')


def pane():
    return ('<main class="pane"><header class="ph"><div><h1>harbor</h1><div class="sub">L3 is answering · 4 tasks in flight · 1 question needs you</div></div>'
            '<div class="acts"><span class="btn">Terminal</span></div></header>'
            f'<div class="convo"><div class="col">{CONVO}</div><div style="height:22px"></div>{composer()}<div style="height:18px"></div></div></main>')


def desk(panel, h=900, panel_w=PANEL):
    return (f'<div style="display:grid;grid-template-columns:260px minmax(0,1fr) {panel_w}px;height:{h}px">{RAIL}{pane()}'
            f'<aside class="work g-work">{panel}</aside></div>')


# ---- the constellation in the work panel, the list it toggles with, and the overlay below 1280 ----
board("DesktopGraph", 1440, 900, desk(wp_head() + constellation() + foot()))
board("DesktopList", 1440, 900, desk(wp_head("list") + list_rows() + foot(), panel_w=340))
board("DesktopOverlay", 1100, 900,
      '<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:900px">' + RAIL + pane() + '</div>'
      f'<div class="scrim"></div><aside class="work g-work ov" style="width:{PANEL}px">' + wp_head() + constellation() + foot() + '</aside>')


# ---- states sheet ----
def st(label, note, body):
    return f'<div class="st" style="width:{PANEL}px"><div class="lab">{label}<span>{note}</span></div>{body}</div>'


T = TASKS
many = [(f"Task {n}: {t[0]}", *t[1:6], None, t[7]) for n, t in enumerate(T * 2, 1)][:13]
long_title = [("Make every validation capture land beside its review so reviewers never chase artifacts across folders",
               "", "L2 working · Opus on Claude · started 3 min ago", "running", True, 0, None, "task")] + T[1:3]
states = [
    st("Loading", "The first read: one skeleton disc where L3 goes, no guessed nodes.",
       f'<div class="graph" style="width:{W}px;height:110px"><span class="skeleton-disc" style="left:{W / 2 - 22:.0f}px"></span></div>'),
    st("No current work", "L3 alone, and the list's own empty line.",
       constellation([], l3="Ready", l3_live=False) + '<p class="muted gempty">No current tasks. Ask L3 to start something.</p>'),
    st("Read failed, nothing saved", "The list's error and Retry; no graph.",
       '<p class="err">Could not read the project’s work. <a class="u" href="#">Retry</a></p>'),
    st("Saved after a failed refresh", "Nodes stay and open; no halo, helper or L3 word claims anything is live.",
       '<p class="err">Showing saved work. <a class="u" href="#">Retry</a></p>' + constellation(T[:4], stale=True)),
    st("Keyboard focus", "Tab: the switch, then each task in ring order. Enter opens. L3 is not a stop.",
       constellation(T[:4], focus=0)),
    st("Reduced motion", "No breathing, glide or fade: live nodes keep a static halo.",
       constellation(T[:4], still=True)),
    st("Long title", "Two lines and an ellipsis; the full row text is the accessible name.",
       constellation(long_title)),
    st("Helper activity unknown", "No pips and no count; the accessible name says so. Nothing guesses zero.",
       constellation(T[2:3] + T[0:1], unknown=("Rework the export pipeline",))),
    st("Thirteen current tasks", "The ring stretches into a taller racetrack that keeps its spacing; the panel scrolls.",
       constellation(many)),
]
board("States", 1150, 3000,
      '<div style="padding:28px;display:flex;flex-wrap:wrap;gap:24px;align-content:flex-start">' + "".join(states) + '</div>')
