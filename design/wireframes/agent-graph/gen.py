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
LIST = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h12M8 12h12M8 18h12"/><circle cx="4" cy="6" r="1" fill="currentColor"/><circle cx="4" cy="12" r="1" fill="currentColor"/><circle cx="4" cy="18" r="1" fill="currentColor"/></svg>'
GRAPH = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><circle cx="5" cy="5" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="18" cy="19" r="2"/><path d="M6.5 6.5l3.3 3.3M17.3 7.2l-3.2 2.9M16.6 17.6l-2.6-3.4"/></svg>'
P = "harbor"


def board(name, w, h, body):
    (out / f"{name}.html").write_text(HEAD.format(title=name, w=w, h=h) + body + TAIL)


# ---- the fictional project: what the work panel's reads already say, plus the one new live field ----
# (title, second line, state, pulse, helpers [(state, description)], after, kind)
TASKS = [
    ("Add offline sync for the field app", "L2 working", "running", True,
     [("running", "Survey sync conflict cases"), ("running", "Write the migration tests")], None, "task"),
    ("Fix the login redirect loop", "Your turn", "waiting", False, [], None, "task"),
    ("Rework the export pipeline", "L2 working", "running", False, [("done", "Check the CSV encodings")], None, "task"),
    ("Ship the export dashboard", "Planned", "idle", False, [], 2, "task"),
    ("Upgrade the payment SDK", "The task session ended before completion", "danger", False, [], None, "task"),
    ("Update the onboarding copy", "Queued", "idle", False, [], None, "task"),
    ("Dependency check", "Agent run", "running", True, [], None, "run"),
]


def toggle(on="graph"):
    def b(key, label, icon):
        return f'<span class="tg{" on" if key == on else ""}" role="radio" aria-checked="{str(key == on).lower()}">{icon}{label}</span>'
    return f'<span class="tgl" role="radiogroup" aria-label="Show work as">{b("list", "List", LIST)}{b("graph", "Graph", GRAPH)}</span>'


def wp_head(on="graph", count="7 current · 3 done this week"):
    return (f'<div class="wh2"><div><h2>Work</h2><span class="ct">{count}</span></div>{toggle(on)}</div>')


R, GAP = 150, 132  # ring radius; the least distance between neighbours along the ring


def track_height(n):
    """The racetrack's straight sides: none up to seven tasks, then just long enough to keep GAP between neighbours."""
    return max(0.0, (GAP * n - 2 * math.pi * R) / 2)


def ring_positions(n, cx, cy):
    # Clockwise from the top, in the list's order, evenly spaced along a racetrack around the coordinator.
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
                    pts.append((cx + R * math.cos(a), c + R * math.sin(a), a))
                else:
                    y = s0 + (d if e > s0 else -d)
                    pts.append((cx + c * R, y, 0 if c > 0 else math.pi))
                break
            d -= length
    return pts


def constellation(tasks=TASKS, w=444, l3="Answering", l3_pulse=True, focus=None, still=False, fading=True):
    # The height follows the work: the ring, the satellites above it and the labels under its lowest node.
    h = int(2 * R + track_height(len(tasks)) + 130) if tasks else 190
    cx, cy = w / 2, (R + track_height(len(tasks)) / 2 + 44) if tasks else 50
    pts = ring_positions(len(tasks), cx, cy)
    edges, nodes = [], []
    for i, ((title, line, state, pulse, helpers, after, kind), (x, y, a)) in enumerate(zip(tasks, pts)):
        edges.append(f'<line class="spoke" x1="{cx:.0f}" y1="{cy:.0f}" x2="{x:.0f}" y2="{y:.0f}"/>')
        if after is not None:
            ax, ay, _ = pts[after]
            dx, dy = x - ax, y - ay
            d = math.hypot(dx, dy)
            ux, uy = dx / d, dy / d
            # bow toward the coordinator, so the arc leaves each node on its inner side, away from the labels
            mx, my = (ax + x) / 2, (ay + y) / 2
            qx, qy = mx + (cx - mx) * 0.45, my + (cy - my) * 0.45
            def toward(px, py, r):
                vx, vy = qx - px, qy - py
                vd = math.hypot(vx, vy) or 1
                return px + vx / vd * r, py + vy / vd * r
            sx, sy = toward(ax, ay, 25)
            ex, ey = toward(x, y, 27)
            edges.append(f'<path class="dep" d="M{sx:.0f},{sy:.0f} Q{qx:.0f},{qy:.0f} {ex:.0f},{ey:.0f}" marker-end="url(#arrow)"/>')
        live = " breathe" if pulse and not still else (" still-live" if pulse else "")
        foc = " focus" if focus == i else ""
        shape = " run" if kind == "run" else ""
        badge = '<b class="gq">1</b>' if state == "waiting" else ""
        sat = ""
        for j, (hs, desc) in enumerate(helpers):
            # satellites sit on the node's upper rim, clear of its label below
            ha = -math.pi / 2 + (j - (len(helpers) - 1) / 2) * 0.8
            hx, hy = 20 + 31 * math.cos(ha) - 7, 20 + 31 * math.sin(ha) - 7
            fade = " fade" if hs != "running" and fading else ""
            sat += (f'<span class="sat s-{hs}{fade}" style="left:{hx:.0f}px;top:{hy:.0f}px" title="{desc}" '
                    f'aria-label="Helper: {desc}, {"running" if hs == "running" else hs}"></span>')
        hcount = sum(1 for hs, _ in helpers if hs == "running")
        hl = f' · {hcount}&nbsp;helper{"s" if hcount != 1 else ""}' if hcount else ""
        nodes.append(
            f'<a class="gn s-{state}{live}{foc}{shape}" href="#" style="left:{x - 20:.0f}px;top:{y - 20:.0f}px" '
            f'aria-label="{title} · {line}">'
            f'<span class="disc"></span>{badge}{sat}'
            f'<span class="gl"><span class="gt">{title}</span><span class="gm">{line}{hl}</span></span></a>')
    coord = (f'<a class="gn coord{" breathe" if l3_pulse and not still else (" still-live" if l3_pulse else "")}{" focus" if focus == "l3" else ""}" href="#" '
             f'style="left:{cx - 30:.0f}px;top:{cy - 30:.0f}px" aria-label="L3 · {l3} · message L3">'
             f'<span class="disc">L3</span><span class="gl"><span class="gt">Coordinator</span><span class="gm">{l3}</span></span></a>')
    svg = (f'<svg class="edges" width="{w}" height="{h}" aria-hidden="true"><defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" '
           f'markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="arrowhead"/></marker></defs>'
           + "".join(edges) + '</svg>')
    return f'<div class="graph" style="width:{w}px;height:{h}px" role="group" aria-label="Current work as a graph">{svg}{coord}{"".join(nodes)}</div>'


def foot():
    return (f'<div class="wfold">{CHEVR}Done this week (3)</div>'
            f'<div class="wagents">Agents · 2{CHEVR}</div>')


def list_rows():
    def row(title, meta, dot, attention=""):
        att = f'<div class="ta">{attention}</div>' if attention else ""
        return (f'<a class="trow" href="#"><span class="dot {dot}"></span><div style="flex:1">'
                f'<div class="tt">{title}</div>{att}<div class="tm">{meta}</div></div>{CHEVR}</a>')
    return ('<div class="sh">Current <span>(7)</span></div>'
            + row("Add offline sync for the field app", "L2 working · Opus on Claude · started 12 min ago", "")
            + row("Fix the login redirect loop", "L2 working · Opus on Claude · started 2 h ago", "held", "Needs you · 1 question")
            + row("Rework the export pipeline", "L2 working · Opus on Codex · started 41 min ago", "")
            + row("Ship the export dashboard", "Planned · Waiting for Rework the export pipeline.", "idle")
            + row("Upgrade the payment SDK", "The session ended before it finished.", "bad")
            + row("Update the onboarding copy", "Queued", "idle")
            + row("Dependency check", "Agent run", ""))


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


def desk(panel, width=480, h=900):
    return (f'<div style="display:grid;grid-template-columns:260px minmax(0,1fr) {width}px;height:{h}px">{RAIL}{pane()}'
            f'<aside class="work g-work">{panel}</aside></div>')


# ---- 1. recommended: constellation in the work panel ----
board("DesktopGraph", 1440, 900, desk(wp_head() + constellation() + foot()))
board("DesktopList", 1440, 900, desk(wp_head("list") + list_rows() + foot(), width=340))

# below 1280 the panel is the overlay the header's panel button opens, 480 wide in graph view
board("DesktopOverlay", 1100, 900,
      '<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:900px">' + RAIL + pane() + '</div>'
      '<div class="scrim"></div><aside class="work g-work ov">' + wp_head() + constellation() + foot() + '</aside>')


# ---- states sheet ----
def st(label, note, body, w=480):
    return f'<div class="st" style="width:{w}px"><div class="lab">{label}<span>{note}</span></div>{body}</div>'


def small(tasks, **kw):
    return constellation(tasks, **kw)


T = TASKS
many = [(f"Task {n}: {t}", line, s, p, hs, None, k) for n, (t, line, s, p, hs, _, k) in enumerate(T * 2, 1)][:13]
long_title = [("Make every validation capture land beside its review so reviewers never chase artifacts across folders",
               "L2 working", "running", True, [], None, "task")] + T[1:3]
states = [
    st("Loading", "The first read: one skeleton disc in the coordinator's place, no guessed nodes.",
       '<div class="graph" style="width:444px;height:200px"><span class="skeleton-disc"></span></div>'),
    st("No current work", "Only the coordinator, idle. The list's empty line stays as the one sentence.",
       constellation([], l3="Ready", l3_pulse=False) + '<p class="muted gempty">No current tasks. Ask L3 to start something.</p>'),
    st("Read failed, nothing saved", "The same error and Retry the list shows; no graph is drawn.",
       '<p class="err">Could not read the project’s work. <a class="u" href="#">Retry</a></p>'),
    st("Saved after a failed refresh", "Nodes stay clickable; the count says saved and the notice offers Retry.",
       '<p class="err">Showing saved work. <a class="u" href="#">Retry</a></p>' + small(T[:4], still=True, l3="Ready", l3_pulse=False)),
    st("A helper finishes", "Its satellite turns green, then fades out a minute later; failed is red, stopped muted.",
       small(T[2:3] + T[0:1])),
    st("Keyboard focus", "Tab follows the list's order: coordinator, then each task and its helpers. Enter opens.",
       small(T[:4], focus=1)),
    st("Reduced motion", "No pulse, glide or fade. A live node keeps a static halo instead of breathing.",
       small(T[:4], still=True)),
    st("Long title", "Two lines, then an ellipsis; the full title is the link's accessible name.",
       small(long_title)),
    st("Thirteen current tasks", "Past seven, the ring stretches into a racetrack that keeps its spacing; the panel scrolls.",
       constellation(many)),
]
board("States", 1040, 3000,
      '<div style="padding:28px;display:flex;flex-wrap:wrap;gap:24px;align-content:flex-start">' + "".join(states) + '</div>')


# ---- alternatives, in the same panel, for comparison ----
def altitude_map():
    rows = [
        ("You", [("Fix the login redirect loop", "waiting")]),
        ("Coordinator", [("L3 · Answering", "running")]),
        ("Tasks", [("Add offline sync", "running"), ("Rework export", "running"), ("Payment SDK", "danger"),
                   ("Export dashboard", "idle"), ("Onboarding copy", "idle")]),
        ("Helpers", [("Sync conflicts", "running"), ("Migration tests", "running"), ("CSV encodings", "done")]),
    ]
    out_ = '<div class="amap">'
    for label, items in rows:
        out_ += f'<div class="arow2"><span class="alab">{label}</span><div class="aitems">'
        for i, (t, s) in enumerate(items):
            x = 8 + i * 17 if label == "Tasks" else 30 + i * 22
            out_ += f'<span class="anode s-{s}" style="left:{x}%"><span class="disc"></span><span class="gt">{t}</span></span>'
        out_ += '</div></div>'
    out_ += '<div class="aaxis"><span>queued</span><span>working</span><span>review</span><span>merging</span></div></div>'
    return out_


def pipeline():
    cols = [("Queued", [("Onboarding copy", "idle"), ("Export dashboard", "idle")]),
            ("Working", [("Offline sync", "running"), ("Rework export", "running"), ("Login loop", "waiting"), ("Payment SDK", "danger")]),
            ("Review", []), ("Merge", [])]
    body = '<div class="pipe">'
    for name, items in cols:
        body += f'<div class="pcol"><div class="ph3">{name} <span>{len(items)}</span></div>'
        for t, s in items:
            h = '<span class="psat"></span><span class="psat"></span>' if t == "Offline sync" else ""
            body += f'<span class="pnode s-{s}"><span class="dot"></span>{t}{h}</span>'
        body += '</div>'
    return body + '</div>'


board("AltAltitudeMap", 1440, 900, desk(wp_head() + altitude_map() + foot()))
board("AltPipeline", 1440, 900, desk(wp_head() + pipeline() + foot()))
