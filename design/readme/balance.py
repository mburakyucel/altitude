"""Write docs/images/balance.svg: new tasks go to whichever engine has used less of its week, so both are used evenly.

A looping SMIL animation in the style of docs/images/altitude.svg, with no script, so GitHub plays it in an <img>.
Its meters fill with use and read "N% of week", as the app's engine meters do. Each task waits at the fork
while the engine that has used less is marked, travels the branch into it, and that engine's meter fills; after
the last task a new week empties both.
Usage: python3 design/readme/balance.py
"""
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "docs/images/balance.svg"
W, H, T = 760, 340, 20.0  # canvas and loop length in seconds
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"
INK, MUTED, LINE, ACCENT, TINT = "#1f2328", "#59636e", "#d1d9e0", "#6366f1", "#eef2ff"
ENGINES = [("Claude", 4), ("Codex", 7)]  # name, percent of the week used at the start
TASKS = [("Cursor tokens", 8), ("Resumable backfill", 7), ("Latency baseline", 6), ("Retry-After on 429s", 7),
         ("Per-tenant limits", 9), ("Index switch", 6), ("SAML sign-on", 7), ("Contract tests", 6)]
CHIP_X, CHIP_W, MID = 32, 212, 182  # the waiting task, centred on MID
FORK = 276
CARD_X, CARD_W, CARD_Y, CARD_H = 330, 398, (70, 214), 100
BAR_X, BAR_W = CARD_X + 26, CARD_W - 52
START, STEP = 0.8, 2.0
EASE = ".45 0 .25 1"


def k(t):
    return f"{t / T:.4f}"


def anim(attr, frames, mode="linear"):
    """One looping animation of attr through (time, value) frames; linear frames ease in and out."""
    times = ";".join(k(t) for t, _ in frames)
    values = ";".join(str(v) for _, v in frames)
    ease = f' keySplines="{";".join([EASE] * (len(frames) - 1))}"' if mode == "spline" else ""
    return (f'<animate attributeName="{attr}" dur="{T}s" repeatCount="indefinite" calcMode="{mode}"{ease} '
            f'keyTimes="{times}" values="{values}"/>')


def branch(e):
    y = CARD_Y[e] + CARD_H / 2
    return f"M{CHIP_X + CHIP_W} {MID} H{FORK} C{FORK + 30} {MID} {FORK + 24} {y} {CARD_X} {y}"


def main():
    # Who takes each task: the engine that has used less of its week, Claude on a tie.
    used, plan = [p for _, p in ENGINES], []
    for n, (title, cost) in enumerate(TASKS):
        pick = 0 if used[0] <= used[1] else 1
        t0 = START + n * STEP
        used[pick] += cost
        plan.append({"title": title, "pick": pick, "t0": t0, "go": t0 + 0.5, "arrive": t0 + 1.3, "after": used[pick]})
    week, empty, fade = plan[-1]["arrive"] + 1.0, plan[-1]["arrive"] + 1.3, T - 0.6

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="{FONT}">',
           '<defs><filter id="s" x="-10%" y="-20%" width="120%" height="160%"><feDropShadow dx="0" dy="1.5" '
           'stdDeviation="2" flood-color="#0f172a" flood-opacity=".08"/></filter></defs>',
           f'<rect width="{W}" height="{H}" rx="16" fill="#fbfbfd"/>',
           f'<text x="{CHIP_X}" y="46" font-size="15" fill="#4f46e5" font-weight="700" letter-spacing="1.3">NEW TASKS</text>',
           f'<text x="{CARD_X}" y="46" font-size="15" fill="#4f46e5" font-weight="700" letter-spacing="1.3">WEEK USED</text>']

    # The fork: idle grey, the taken branch lit while a task travels it.
    for e in range(2):
        lit = [(0, LINE)]
        for p in plan:
            if p["pick"] == e:
                lit += [(p["go"] - 0.1, LINE), (p["go"], ACCENT), (p["arrive"] + 0.1, ACCENT), (p["arrive"] + 0.4, LINE)]
        lit.append((T, LINE))
        out.append(f'<path d="{branch(e)}" fill="none" stroke="{LINE}" stroke-width="2" stroke-linecap="round">'
                   f'{anim("stroke", lit)}</path>')
    out.append(f'<circle cx="{FORK}" cy="{MID}" r="4" fill="{LINE}"/>')

    # A new week: its label, then both meters empty.
    out.append(f'<g opacity="0"><rect x="{W - 32 - 96}" y="28" width="96" height="26" rx="13" fill="{TINT}"/>'
               f'<text x="{W - 32 - 48}" y="46" font-size="15" fill="#4f46e5" font-weight="600" text-anchor="middle">'
               f'New week</text>{anim("opacity", [(0, 0), (week, 0), (week + 0.3, 1), (fade - 0.4, 1), (fade, 0), (T, 0)])}</g>')

    for e, (name, percent) in enumerate(ENGINES):
        y, mine = CARD_Y[e], [p for p in plan if p["pick"] == e]
        pulse = [(0, LINE)]
        for p in mine:
            pulse += [(p["arrive"] - 0.1, LINE), (p["arrive"], ACCENT), (p["arrive"] + 0.7, LINE)]
        pulse.append((T, LINE))
        out.append(f'<rect x="{CARD_X}" y="{y}" width="{CARD_W}" height="{CARD_H}" rx="14" fill="#fff" stroke="{LINE}" '
                   f'stroke-width="1.5" filter="url(#s)">{anim("stroke", pulse)}</rect>')
        out.append(f'<text x="{BAR_X}" y="{y + 40}" font-size="24" fill="{INK}" font-weight="700">{name}</text>')
        out.append(f'<rect x="{BAR_X}" y="{y + 60}" width="{BAR_W}" height="14" rx="7" fill="#eef0f3"/>')
        width = lambda p: round(BAR_W * p / 100, 1)
        bar, last = [(0, width(percent))], percent
        for p in mine:
            bar += [(p["arrive"], width(last)), (p["arrive"] + 0.7, width(p["after"]))]
            last = p["after"]
        bar += [(empty, width(last)), (empty + 1.4, width(percent)), (T, width(percent))]
        out.append(f'<rect x="{BAR_X}" y="{y + 60}" height="14" rx="7" fill="{ACCENT}" width="{width(percent)}">'
                   f'{anim("width", bar, "spline")}</rect>')
        # The percentage: one text per value, each shown while it is current.
        shown = [(0, percent)] + [(p["arrive"] + 0.35, p["after"]) for p in mine]
        for i, (since, value) in enumerate(shown):
            until = shown[i + 1][0] if i + 1 < len(shown) else empty + 0.7
            frames = [(0, 1), (until, 0), (empty + 0.7, 1)] if i == 0 else [(0, 0), (since, 1), (until, 0)]
            out.append(f'<text x="{CARD_X + CARD_W - 26}" y="{y + 40}" font-size="20" fill="{MUTED}" text-anchor="end" '
                       f'opacity="{1 if i == 0 else 0}">{value}% of week{anim("opacity", frames, "discrete")}</text>')

    # "Less used" marks the engine the next task goes to.
    for e in range(2):
        frames, on = [], None
        for p in plan:
            state = int(p["pick"] == e)
            if state != on:
                frames.append((0 if not frames else p["t0"] - 0.2, state))
                on = state
        frames.append((week, 0))
        name_w = 86 if ENGINES[e][0] == "Claude" else 78
        out.append(f'<g opacity="{frames[0][1]}"><rect x="{BAR_X + name_w + 10}" y="{CARD_Y[e] + 20}" width="96" height="27" '
                   f'rx="13.5" fill="{TINT}"/><text x="{BAR_X + name_w + 58}" y="{CARD_Y[e] + 39}" font-size="15" fill="#4f46e5" '
                   f'font-weight="600" text-anchor="middle">less used</text>{anim("opacity", frames, "discrete")}</g>')

    # Each task: it slides in, waits a beat, slides into the fork, and a dot carries it along the branch.
    for p in plan:
        t0, go, arrive = p["t0"], p["go"], p["arrive"]
        out.append(f'<g opacity="0"><rect x="{CHIP_X}" y="{MID - 20}" width="{CHIP_W}" height="40" rx="20" fill="#fff" '
                   f'stroke="#a5b4fc" stroke-width="1.5" filter="url(#s)"/><text x="{CHIP_X + 20}" y="{MID + 6}" '
                   f'font-size="18" fill="{INK}" font-weight="500">{p["title"]}</text>'
                   f'<animateTransform attributeName="transform" type="translate" dur="{T}s" repeatCount="indefinite" '
                   f'calcMode="spline" keySplines="0 0 1 1;{EASE};0 0 1 1;{EASE};0 0 1 1" '
                   f'keyTimes="0;{k(t0)};{k(t0 + 0.35)};{k(go)};{k(go + 0.3)};1" values="-18 0;-18 0;0 0;0 0;28 0;28 0"/>'
                   f'{anim("opacity", [(0, 0), (t0, 0), (t0 + 0.3, 1), (go + 0.05, 1), (go + 0.3, 0), (T, 0)])}</g>')
        out.append(f'<circle r="6" fill="{ACCENT}" opacity="0">'
                   f'<animateMotion dur="{T}s" repeatCount="indefinite" path="{branch(p["pick"])}" calcMode="spline" '
                   f'keyPoints="0;0;1;1" keyTimes="0;{k(go + 0.15)};{k(arrive)};1" keySplines="0 0 1 1;{EASE};0 0 1 1"/>'
                   f'{anim("opacity", [(0, 0), (go + 0.15, 0), (go + 0.25, 1), (arrive - 0.05, 1), (arrive + 0.1, 0), (T, 0)])}'
                   f'</circle>')
    out.append("</svg>")
    OUT.write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
