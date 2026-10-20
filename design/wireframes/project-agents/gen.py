"""Write the project agents boards beside this file: python3 design/wireframes/project-agents/gen.py"""
import pathlib

out = pathlib.Path(__file__).resolve().parent
HEAD = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Agents: {title}</title>
<link rel="stylesheet" href="../wireframes.css"><link rel="stylesheet" href="agents.css"></head>
<body><div class="root" data-theme="light" style="width:{w}px;height:{h}px">
'''
TAIL = '''</div><script>if(location.search.indexOf('dark')>=0)document.querySelector('.root').dataset.theme='dark'</script></body></html>
'''
CHEVL = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>'
CHEVR = '<svg class="i sm chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>'
DOWN = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>'
PAUSE = '<svg class="i sm pausem" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6v12M15 6v12"/></svg>'
PULSE = '<span class="pulse"></span>'
U = 'class="u" href="#"'
P = "harbor"


def board(name, w, h, body):
    (out / f"{name}.html").write_text(HEAD.format(title=name, w=w, h=h) + body + TAIL)


def rail(monitor=False, on=P):
    def item(name, dot):
        style = ' style="background:var(--bubble);font-weight:600"' if name == on and not monitor else ""
        return f'<div class="ri"{style}><span class="dot {dot}"></span>{name}</div>'
    mon = ' style="height:36px;background:var(--bubble);font-weight:600"' if monitor else ' style="height:36px"'
    return ('<aside class="rail"><div class="brand"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div>'
            '<div class="ri">Needs you<span class="badge">1</span></div><div class="rsec"><span>Projects</span></div>'
            + item("altitude", "idle") + item(P, "held") +
            '<div class="engines"><div class="erow"><div class="t"><b>Claude</b><span>38% of week</span></div><div class="meter"><i style="width:38%"></i></div></div>'
            '<div class="erow"><div class="t"><b>Codex</b><span>22% of week</span></div><div class="meter"><i style="width:22%"></i></div></div></div>'
            f'<div class="ri"{mon}>Monitor</div><div class="who"><span class="avatar">O</span>Operator</div></aside>')


def desk(inner, h=900, monitor=False, on=P):
    return (f'<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:{h}px">' + rail(monitor, on)
            + f'<main class="pane" style="overflow:hidden"><div class="route-content" style="max-width:760px;width:100%;overflow:visible">{inner}</div></main></div>')


def tabbar(on=None):
    tabs = [("chat", "Chat"), ("work", "Work"), ("needs", "Needs you"), ("monitor", "Monitor")]
    return '<nav class="tabbar">' + "".join(f'<div class="tab{" on" if k == on else ""}">{t}</div>' for k, t in tabs) + '</nav>'


def phone(inner, back=P, title="Agents", h=844, overlay=""):
    return (f'<div class="m" style="height:{h}px;position:relative"><div></div><header class="mh"><a class="btn" href="#" style="padding:0 8px">{CHEVL}{back}</a>'
            f'<h1 style="font-size:17px;margin:0 0 0 8px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{title}</h1></header>'
            f'<div class="route-content" style="overflow:hidden;padding:4px 16px 20px">{inner}</div><div></div>{tabbar("work")}{overlay}</div>')


# ---- the agents page ----
AGENTS = [
    ("Dependency check", "Weekdays at 07:00 · Found something 2 h ago"),
    ("Review each merge", PULSE + "When a pull request merges · Running, 2 of 20 min"),
    ("Upload timing", "Mondays at 08:00 · Waiting for Review each merge"),
    ("What landed", PAUSE + "Fridays at 17:00 · Paused"),
]


def arow(name, line):
    return f'<a class="arow" href="#"><span class="tx"><b>{name}</b><small>{line}</small></span>{CHEVR}</a>'


def budget(state=None):
    sel = "2 h a week"
    used = '<small class="muted">1 h 10 min used this week</small>'
    extra = ""
    if state == "saving":
        sel, used = "5 h a week", '<small class="muted" style="display:flex;gap:6px;align-items:center"><span class="spin"></span>Saving…</small>'
    elif state == "refused":
        extra = '<div class="err" style="padding:0 18px 12px">Not saved: the setting changed in another window. <a class="u" href="#">Retry</a></div>'
    elif state == "spent":
        used = '<small style="color:var(--chip-claimed-text)">2 h used this week · agents wait until Monday</small>'
    return (f'<div class="afoot budget"><span class="tx"><b>Agents may use up to</b>{used}</span>'
            f'<span class="field" style="min-width:9rem;flex:none;color:var(--text-primary)">{sel}&nbsp;{DOWN}</span></div>{extra}')


BUILTIN = [("Build health", "Mondays at 08:00 · Runs the project’s checks on a clean copy and says what broke")]
BUILTIN_ALL = [("Dependency check", "Weekdays at 07:00 · Looks for security fixes and major releases of what the project uses"),
               ("Build health", BUILTIN[0][1]),
               ("What landed", "Fridays at 17:00 · Sums up what merged this week and anything left unfinished")]
WEEKLY_ROW = ("Weekly improvement review", "Mondays at 08:00 · Suggests fixes for friction that keeps coming back in this project’s tasks")


def brow(name, line, state=None):
    btn = '<span class="btn">Turn on</span>'
    if state == "saving":
        btn = '<span class="btn" style="min-width:76px" aria-label="Turn on, saving"><span class="spin"></span></span>'
    err = '<div class="err" style="margin-top:6px">Not turned on: the agents changed in another window.</div>' if state == "refused" else ""
    return f'<div class="arow brow"><span class="tx"><b>{name}</b><small>{line}</small>{err}</span>{btn}</div>'


def builtin(rows=BUILTIN, state=None):
    items = "".join(brow(n, l, state if i == 0 else None) for i, (n, l) in enumerate(rows))
    return ('<h2 style="margin-top:24px">Built in</h2><p class="muted" style="margin:-4px 0 10px;font-size:13.5px">Ready to turn on; each becomes yours to change.</p>'
            f'<div class="card alist">{items}</div>')


def section(rows=None, state=None, bstate=None, retired=True, top=True, built=BUILTIN, bistate=None):
    rows = AGENTS if rows is None else rows
    head = (f'<a class="btn setting-back" href="#">{CHEVL}{P}</a><h1 style="margin-bottom:4px">Agents</h1>'
            '<p class="muted" style="margin:0 0 14px">Each one runs a brief when its trigger fires, and tells you what it found.</p>')
    if not top:
        head = head.split("</h1>")[1]
    if state == "loading":
        return head + '<div class="card"><span class="spin" role="status" aria-label="Loading agents…"></span></div>'
    if state == "failed":
        return head + '<div class="card"><p style="margin:0 0 10px">Could not load agents.</p><span class="btn">Retry</span></div>'
    items = "".join(arow(*a) for a in rows)
    card = f'<div class="card alist">{items}{budget(bstate)}<div class="afoot"><span class="btn">New agent</span></div></div>'
    tail = f'<p class="muted" style="margin:10px 2px 0"><a {U}>2 retired</a></p>' if retired and rows else ""
    return head + card + tail + (builtin(built, bistate) if built else "")


board("DesktopAgents", 1440, 900, desk(section()))
board("PhoneAgents", 390, 1000, phone(section(top=False), h=1000))

# ---- one agent ----
BRIEF = ("Check every dependency in pyproject.toml and web/package.json for security fixes and major releases. "
         "Use <code>make audit</code> if it exists. Say which packages need an update, how urgent each is, and what the change would touch.")


def status(state="idle"):
    run_now, pause = '<span class="btn">Run now</span>', '<span class="btn">Pause</span>'
    rows = {
        "idle": ("Next: tomorrow at 07:00", run_now + pause, ""),
        "running": ('<span class="spin"></span>Running · 4 of 20 min · <a ' + U + '>Open</a>', '<span class="btn">Stop</span>' + pause, ""),
        "stopping": ('<span class="spin"></span>Running · 4 of 20 min · <a ' + U + '>Open</a>',
                     '<span class="btn" aria-disabled="true"><span class="spin"></span>Stopping…</span>' + pause, ""),
        "waiting": ("Waiting for a free task slot", '<span class="btn">Stop</span>', ""),
        "waiting-agent": ("Waiting for Review each merge", '<span class="btn">Stop</span>', ""),
        "new": ("Not on yet", run_now + '<span class="btn">Turn on</span>', ""),
        "starting": ("Next: tomorrow at 07:00", '<span class="btn" style="min-width:86px" aria-label="Run now, starting"><span class="spin"></span>Starting…</span>'
                     '<span class="btn" aria-disabled="true">Pause</span>', ""),
        "refused": ("Next: tomorrow at 07:00", run_now + pause, '<div class="err">Only 12 min of this week’s budget is left.</div>'),
        "paused": (PAUSE + "Paused", '<span class="btn">Resume</span>' + run_now, ""),
        "self-paused": (PAUSE + "Paused itself after 3 failed runs", '<span class="btn">Resume</span>' + run_now, ""),
        "pause-refused": ("Next: tomorrow at 07:00", run_now + pause,
                          '<div class="err">Not saved: the agent changed in another window. <a class="u" href="#">Retry</a></div>'),
    }
    line, btns, extra = rows[state]
    return f'<div class="card"><div class="status"><span class="tx">{line}</span><span class="btns">{btns}</span></div>{extra}</div>'


RUNS = [
    ("found", "Today 07:00", "Found something", "6 min · 4.1M tokens", "2 packages have security fixes (requests, urllib3)."),
    ("", "Yesterday 07:00", "Nothing found", "5 min · 3.6M tokens", "Every dependency is current."),
    ("", "Oct 16, 11:20", "Stopped by you", "3 min · 1.2M tokens", "Stopped while it was installing the web dependencies."),
    ("fail", "Oct 16, 07:00", "Couldn’t finish", "2 min · 0.9M tokens", "<code>make audit</code> is not a target."),
    ("skip", "Oct 15, 07:00", "Skipped", "", "Claude’s allowance was inside the reserve."),
    ("fail", "Oct 14, 07:00", "Stopped at its limit", "20 min · 11.2M tokens", "It was still checking the web dependencies."),
]


def runs(rows=RUNS, more=True):
    if not rows:
        return '<h2 style="margin-top:24px">Recent runs</h2><div class="card"><p class="muted" style="margin:0">No runs yet.</p></div>'
    items = ""
    for kind, when, outcome, cost, line in rows:
        d = {"found": "d found", "fail": "d fail"}.get(kind, "d")
        cost_html = f'<span>{cost}</span>' if cost else ""
        inner = f'<span class="{d}"></span><div class="tx"><div class="hd"><b>{outcome}</b><span>{when}</span>{cost_html}</div><p>{line}</p></div>'
        items += (f'<div class="run skip">{inner}</div>' if kind == "skip" else f'<a class="run" href="#">{inner}{CHEVR}</a>')
    moreh = f'<p class="muted" style="margin:10px 2px 0"><a {U}>Show older</a></p>' if more else ""
    return f'<h2 style="margin-top:24px">Recent runs</h2><div class="card runs">{items}</div>{moreh}'


def definition(edit=True):
    btn = '<div style="margin-top:12px"><span class="btn">Edit</span></div>' if edit else ""
    return ('<h2 style="margin-top:24px">What it does</h2><div class="card">'
            f'<p class="clamp" style="margin:0">{BRIEF}</p><p style="margin:4px 0 0"><a {U}>Show all</a></p>'
            '<p class="muted" style="margin:10px 0 0">Weekdays at 07:00 · Tells you when it finds something · Default model · Stops after 20 min</p>'
            f'{btn}</div>')


RETIRE = ('<h2 style="margin-top:24px">Retire agent</h2><div class="card retire"><span class="muted" style="font-size:14px">It won’t run again. Past runs stay readable.</span>'
          '<span class="btn redbtn">Retire…</span></div>')


def agent_page(state="idle"):
    return status(state) + runs() + definition() + RETIRE


AGENT_HEAD = f'<a class="btn setting-back" href="#">{CHEVL}{P}</a><h1 style="margin-bottom:16px">Dependency check</h1>'
board("DesktopAgent", 1440, 1240, desk(AGENT_HEAD + agent_page(), h=1240))
board("PhoneAgent", 390, 1520, phone(agent_page("running"), back=P, title="Dependency check", h=1520))


# ---- one run ----
REPORT = ('<p>Two packages have security fixes, both in <code>pyproject.toml</code>:</p>'
          '<ul><li><b>requests</b> 2.32.2 → 2.32.4: fixes a credential leak on redirects. Urgent; nothing else changes.</li>'
          '<li><b>urllib3</b> 2.2.1 → 2.2.2: fixes proxy header handling. Soon; <code>uploader.py</code> is the only caller.</li></ul>'
          '<p>Every package in <code>web/package.json</code> is current. <code>make audit</code> passed.</p>')
SESSION = ('<div class="tl" style="gap:8px">'
           '<div class="prose">Reading the dependency files, then checking each package for advisories.</div>'
           '<div class="tool" data-kind="command"><b>Bash</b>pip index versions requests<span class="n">0.8 s</span></div>'
           '<div class="tool" data-kind="command"><b>Bash</b>make audit<span class="n"><span class="spin"></span></span></div></div>')
FOLDS = ''.join(f'<div class="fold" style="margin-top:4px">{CHEVR.replace("chev", "")}{t}</div>' for t in ("Brief it ran", "Session"))


def run_page(state="ended"):
    meta = '<p class="muted" style="margin:0 0 16px;font-size:13.5px">On its weekday schedule · Sonnet{}</p>'
    if state == "running":
        return (meta.format("")
                + '<div class="card"><div class="status"><span class="tx"><span class="spin"></span>Running · 4 of 20 min</span>'
                  '<span class="btns"><span class="btn">Stop</span></span></div></div>'
                + f'<div class="card" style="margin-top:12px">{SESSION}</div>')
    if state == "loading":
        return '<div class="card"><span class="spin" role="status" aria-label="Loading this run…"></span></div>'
    if state == "failed":
        return '<div class="card"><p style="margin:0 0 10px">Could not load this run.</p><span class="btn">Retry</span></div>'
    if state == "fail":
        return (meta.format(" · 2 min · 0.9M tokens")
                + '<div class="card"><div class="runhead"><span class="d fail"></span><b>Couldn’t finish</b></div>'
                  '<p style="margin:6px 0 0"><code>make audit</code> is not a target, and the brief asks for it.</p></div>' + FOLDS)
    return (meta.format(" · 6 min · 4.1M tokens")
            + '<div class="card"><div class="runhead"><span class="d found"></span><b>Found something</b></div>'
              f'<div class="report">{REPORT}</div></div>'
            + FOLDS)


RUN_HEAD = f'<a class="btn setting-back" href="#">{CHEVL}Dependency check</a><h1 style="margin-bottom:4px">Run · Today 07:00</h1>'
board("DesktopRun", 1440, 900, desk(RUN_HEAD + run_page()))
board("PhoneRun", 390, 844, phone(run_page("running"), back="Dependency check", title="Run · Today 07:00"))


# ---- the Weekly improvement review as a built-in agent (Altitude's own project, step 2) ----
WEEKLY_RUNS = [
    ("found", "Oct 19, 08:00", "3 suggestions", "4 min", "Owners re-ask settled questions; a review box hides its reason; landing waits are unclear."),
    ("", "Oct 12, 08:00", "Nothing to suggest", "3 min", "6 tasks read."),
    ("skip", "Oct 5, 08:00", "Skipped", "", "3 tasks had finished; it needs 5."),
]


def weekly_page():
    card = ('<div class="card"><div class="status"><span class="tx">Next: Monday, Oct 26, if 2 more tasks finish</span>'
            '<span class="btns"><span class="btn" aria-disabled="true" style="opacity:.45">Run now</span><span class="btn">Pause</span></span></div>'
            '<p class="muted" style="margin:8px 0 0;font-size:13px">Run now needs 5 finished tasks since Oct 19; 3 so far.</p></div>')
    what = ('<h2 style="margin-top:24px">What it does</h2><div class="card">'
            '<p style="margin:0">Finds friction that keeps coming back in this project’s tasks and suggests fixes, as cards in chat you can make into tasks.</p>'
            '<p class="muted" style="margin:10px 0 0">Counts and short chat quotes · Claude Sonnet 5.5 on your plan · capped per run, plus one L3 check</p>'
            f'<p class="muted" style="margin:6px 0 0">4 runs · 7 suggestions · 3 became tasks · 2 dismissed · <a {U}>Latest</a></p></div>')
    return card + runs(rows=WEEKLY_RUNS, more=False) + what


WEEKLY_HEAD = (f'<a class="btn setting-back" href="#">{CHEVL}altitude</a><h1 style="margin-bottom:2px">Weekly improvement review</h1>'
               '<p class="muted" style="margin:0 0 16px">Built in</p>')
board("DesktopWeekly", 1440, 900, desk(WEEKLY_HEAD + weekly_page(), on="altitude"))
board("PhoneWeekly", 390, 1000, phone('<p class="muted" style="margin:0 0 12px">Built in</p>' + weekly_page(), back="altitude", title="Weekly improvement review", h=1000))


# ---- the form: new and edit ----
def days(on=(0, 1, 2, 3, 4), time="07:00"):
    letters = "MTWTFSS"
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    chips = "".join(f'<span class="{"on" if i in on else ""}" role="checkbox" aria-checked="{str(i in on).lower()}" aria-label="{names[i]}">{letters[i]}</span>' for i in range(7))
    return f'<div class="days">{chips}<span class="time field" style="flex:none;min-width:0">{time}</span></div>'


CLOCK = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>'
ZAP = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M13 3L5 13h6l-1 8 8-10h-6l1-8z"/></svg>'
XM = '<span class="x" role="button" aria-label="Remove this trigger"><svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17"/></svg></span>'


def triggers(kind="schedule", open_schedule=True, empty=False, step1=False, noday=False):
    """The Runs when list: each trigger one row with Remove; a schedule row opens its days and time."""
    if empty:
        rows = '<div class="tr"><span class="tx muted">Only when you run it</span></div>'
    else:
        none = '<div class="err" style="margin-top:8px">Pick at least one day.</div>' if noday else ""
        sched = (f'<div class="tr open"><div class="row1">{CLOCK}<span class="tx">On a schedule</span>{XM}</div>{days(on=() if noday else (0, 1, 2, 3, 4))}{none}</div>' if open_schedule
                 else f'<div class="tr">{CLOCK}<span class="tx">Weekdays at 07:00</span>{XM}</div>')
        merge = f'<div class="tr">{ZAP}<span class="tx">A pull request merges<small>Its run reads that pull request.</small></span>{XM}</div>'
        rows = {"schedule": sched, "event": merge, "both": sched + merge}[kind]
    add = "+ Add a schedule" if step1 else "+ Add a trigger"
    return f'<div class="trig">{rows}<div class="add"><span class="btn">{add}</span></div></div>'


TRIGGER_MENU = ('<div class="menu-mock" role="menu"><div class="hi">On a schedule</div><hr><div class="gh">In Altitude</div>'
                '<div>A pull request merges</div><div>A task finishes</div><div>An incident is filed</div>'
                '<hr><div class="gh">On GitHub</div><div>An issue is opened</div><div>Checks fail on the main branch</div><div>A release is published</div></div>')


EVHINT = '<span class="hint">Events that arrive during a run go to its next run together.</span>'


def seg(on=0):
    opts = ["When it finds something", "Every run", "Never"]
    return '<div class="seg segw">' + "".join(f'<span class="{"on" if i == on else ""}">{o}</span>' for i, o in enumerate(opts)) + '</div>'


MERGE_BRIEF = ("Read the pull request that merged and check that every behaviour it changes has a test. "
               "Say which change lacks one and where the test would go.")


def form(new=False, nameless=False, saving=False, refused=False, trig="schedule", name="Dependency check", brief=BRIEF):
    nm = ('<div class="field fld" style="color:var(--text-muted)">For example: Dependency check</div>' if new
          else f'<div class="field fld">{name}</div>')
    br = ('<div class="field ta" style="color:var(--text-muted)">For example: check whether the build got slower this week, and say which step.</div>' if new
          else f'<div class="field ta">{brief}</div>')
    body = (f'<label class="flab" style="margin-top:6px">Name</label>{nm}'
            f'<label class="flab">What it does</label>{br}<span class="hint">Write it as you would brief a task. It can use anything in the repository.</span>'
            f'<label class="flab">Runs when</label>{triggers(trig, open_schedule=new)}{EVHINT if trig != "schedule" else ""}'
            f'<label class="flab">Tell me in chat</label>{seg()}'
            f'<div style="display:flex;gap:0 24px;flex-wrap:wrap;align-items:flex-end"><div><label class="flab">Model</label><span class="newtasks"><span><b>Default</b> · Default effort</span>{DOWN}</span></div>'
            f'<div><label class="flab">Each run stops after</label><span class="field" style="min-width:8rem;flex:none;color:var(--text-primary)">20 min&nbsp;{DOWN}</span></div></div>')
    primary = "Create agent" if new else "Save"
    if saving:
        b = f'<span class="btn primary" style="min-width:72px" aria-label="{primary}, saving"><span class="spin"></span></span><span class="btn ghost" aria-disabled="true">Cancel</span>'
    elif nameless:
        b = f'<span class="btn primary" aria-disabled="true" style="opacity:.45">{primary}</span><span class="btn ghost">Cancel</span>'
    else:
        b = f'<span class="btn primary">{primary}</span><span class="btn ghost">Cancel</span>'
    body += f'<div style="display:flex;gap:8px;margin-top:20px">{b}</div>'
    if nameless:
        body += '<div class="err">Give it a name and a brief.</div>'
    if refused:
        body += '<div class="err">Not saved: the agent changed in another window. <a class="u" href="#">Retry</a></div>'
    return f'<div class="card" style="padding-top:8px">{body}</div>'


board("DesktopEdit", 1440, 960, desk(f'<a class="btn setting-back" href="#">{CHEVL}{P}</a><h1>New agent</h1>' + form(new=True), h=960))
board("PhoneEdit", 390, 1150, phone(form(trig="event", name="Review each merge", brief=MERGE_BRIEF), back="Review each merge", title="Edit", h=1150))


# ---- chat ----
def sysa(name, text, kind="plain", link="Open"):
    dd = {"found": "dd found", "fail": "dd fail"}.get(kind, "dd")
    ln = f' <a href="#">{link}</a>' if link else ""
    return f'<div class="sysa"><span class="ln"></span><span class="t"><span class="{dd}"></span><b>{name}</b> {text}{ln}</span><span class="ln"></span></div>'


def offer(state="shown"):
    """L3's offer card: Altitude draws it from the stored definition; only the operator's press saves it."""
    btns = '<span class="btn primary">Create agent</span><span class="btn">Edit first</span>'
    foot = ""
    if state == "saving":
        btns = ('<span class="btn primary" style="min-width:112px" aria-label="Create agent, saving"><span class="spin"></span></span>'
                '<span class="btn" aria-disabled="true">Edit first</span>')
    elif state == "created":
        btns, foot = "", f'<p class="ok" style="margin:10px 0 0">Created · <a {U}>Open</a></p>'
    elif state == "refused":
        btns, foot = "", '<div class="err">Not saved: Dependency check changed since this offer.</div>'
    elif state == "lapsed":
        btns, foot = "", '<p class="muted" style="margin:10px 0 0;font-size:13px">This offer lapsed.</p>'
    row = f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">{btns}</div>' if btns else ""
    return ('<div class="acard"><small class="muted">New agent</small><b>Dependency check</b>'
            '<small class="muted">Weekdays at 07:00 · Tells you when it finds something · On when created</small>'
            f'<p class="clamp">{BRIEF}</p><a {U} style="font-size:13px">Show all</a>{row}{foot}</div>')


CHAT = ('<div class="day">Today</div>'
        '<div class="me">Can you set up a check every weekday morning for dependencies with security fixes? Only tell me when there is one.</div>'
        '<div class="l3"><p>Here it is. Say if you’d like it on a smaller model.</p>' + offer() + '</div>'
        + '<div class="day">Tomorrow</div>'
        + sysa("Dependency check", "found something: 2 packages have security fixes.", "found")
        + sysa("Review each merge", "found something on PR #809: the upload limit moved without its test.", "found")
        + '<div class="me">Pause the dependency one for now.</div>'
        + '<div class="l3"><p>Done.</p></div>'
        + sysa("Paused Dependency check", "·", link="Resume"))


def phone_chat(inner):
    return ('<div class="m"><div></div><header class="mh"><div><span class="name">harbor</span><div class="subl" style="padding-left:4px;margin-top:-6px">L3 · Ready</div></div><span class="sp"></span></header>'
            f'<div class="mbody"><div class="mcol">{inner}</div></div>'
            '<div class="mcomp"><div class="composer"><div class="ph2">Message L3</div></div></div>' + tabbar("chat") + '</div>')


board("PhoneChat", 390, 844, phone_chat(CHAT[CHAT.index('<div class="me">'):CHAT.index('<div class="me">Pause')]))
board("DesktopChat", 1440, 900,
      '<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:900px">' + rail() +
      '<main style="display:grid;grid-template-rows:64px minmax(0,1fr) auto;min-height:0;background:var(--surface)">'
      '<div class="ph"><div><h1>harbor</h1><div class="sub">L3 · Ready</div></div></div>'
      '<div style="overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end;padding:0 28px">'
      f'<div style="max-width:760px;width:100%;margin:0 auto;display:flex;flex-direction:column;gap:16px;padding-bottom:16px">{CHAT}</div></div>'
      '<div style="padding:12px 28px 20px"><div class="composer" style="max-width:760px;margin:0 auto"><div class="ph2">Message L3</div></div></div></main></div>')


# ---- monitor ----
def mon_agents():
    rows = [("harbor · Review each merge", "Running", "Found something on PR #809 · yesterday · 6 min · 3.9M tokens"),
            ("harbor · Dependency check", "Next tomorrow 07:00", "Found something · 2 h ago · 6 min · 4.1M tokens"),
            ("harbor · Upload timing", "Waiting", "Nothing found · Oct 12 · 9 min · 5.2M tokens"),
            ("harbor · What landed", "Paused", "Skipped Fri: the week’s budget was used")]
    items = "".join(f'<a class="agl" href="#"><div class="tx"><div class="monitor-row"><b>{a}</b><span class="muted">{b}</span></div>'
                    f'<p class="muted" style="margin:0">{c}</p></div>{CHEVR}</a>' for a, b, c in rows)
    return f'<h2>Agents (4)</h2><div class="card" style="padding:4px 18px">{items}</div>'


SEATS = ('<h2>Seats</h2><div class="card"><div class="monitor-row"><h3 style="margin:0">Claude Code</h3><span class="muted">reading 3m old</span></div>'
         '<div class="monitor-row" style="margin-top:8px"><span>7-day</span><span>38%</span></div><div class="meter"><i style="width:38%"></i></div></div>')
SESSIONS = ('<h2>Sessions (2)</h2><div class="sessionrow"><div class="monitor-row"><span class="chip">L3</span><b>harbor</b><span class="muted">1 min ago</span></div>'
            '<p class="muted">Claude Code · Fable · context 12%</p><div class="meter"><i style="width:12%"></i></div></div>'
            '<div class="sessionrow"><div class="monitor-row"><span class="chip">Agent</span><b>harbor / Review each merge</b><span class="muted">just now</span></div>'
            '<p class="muted">Claude Code · Sonnet · context 6%</p><div class="meter"><i style="width:6%"></i></div></div>')
board("DesktopMonitor", 1440, 900, desk('<h1>Monitor</h1>' + SEATS + SESSIONS + mon_agents(), monitor=True))
board("PhoneMonitor", 390, 844,
      '<div class="m"><div></div><header class="mh"><div class="brand" style="margin:0;padding:0 4px;height:40px"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div><span class="sp"></span></header>'
      '<div class="route-content" style="overflow:hidden;padding:4px 16px">' + SESSIONS + mon_agents() + '</div><div></div>' + tabbar("monitor") + '</div>')


# ---- work: the project's task list ----
def trow(title, status, dot="dot"):
    return (f'<a class="trow" href="#"><span class="{dot}"></span><div style="flex:1">'
            f'<div class="tt">{title}</div><div class="tm">{status}</div></div>{CHEVR}</a>')


def work_rows():
    fold = f'<div class="fold">{CHEVR.replace("chev", "")}Done this week · 2</div>'
    agents = f'<a class="fold agentsrow" href="#">Agents · 4{CHEVR}</a>'
    return ('<div><div class="sh">Current <span>3</span></div>'
            + trow("Review each merge", "Agent run · PR #812 · 2 of 20 min")
            + trow("Speed up the upload test", "Running · PR #811 green")
            + trow("Upload timing", "Agent run · waiting for Review each merge", "dot q")
            + '</div>' + fold + agents)


board("DesktopWork", 1440, 900,
      '<div style="display:grid;grid-template-columns:260px minmax(0,1fr) 340px;height:900px">' + rail() +
      '<main style="display:grid;grid-template-rows:64px minmax(0,1fr) auto;min-height:0;background:var(--surface)">'
      '<div class="ph"><div><h1>harbor</h1><div class="sub">L3 answered 2 h ago on Claude · 1 task in flight</div></div></div>'
      '<div style="overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end;padding:0 28px">'
      '<div style="max-width:760px;width:100%;margin:0 auto;display:flex;flex-direction:column;gap:16px;padding-bottom:16px">'
      + sysa("Dependency check", "found something: 2 packages have security fixes.", "found")
      + sysa("Review each merge", "found something on PR #809: the upload limit moved without its test.", "found")
      + '</div></div><div style="padding:12px 28px 20px"><div class="composer" style="max-width:760px;margin:0 auto"><div class="ph2">Message L3</div></div></div></main>'
      '<aside class="work"><div class="wh"><h2>Work</h2><span>3 current · 2 done this week</span></div>' + work_rows() + '</aside></div>')
board("PhoneWork", 390, 844,
      '<div class="m"><div></div><header class="mh"><div><span class="name">harbor</span><div class="subl" style="padding-left:4px;margin-top:-6px">L3 · Ready</div></div><span class="sp"></span></header>'
      '<div class="mbody top"><div class="wh"><h2>Work</h2><span>3 current · 2 done this week</span></div>' + work_rows() + '</div><div></div>' + tabbar("work") + '</div>')


# ---- dialogs ----
def dialog(title, text, buttons, extra="", static=False):
    style = ' style="position:static;box-shadow:none;border:1px solid var(--hairline)"' if static else ""
    return f'<div class="dlg"{style} role="dialog"><h2>{title}</h2><p>{text}</p><div class="bt">{buttons}</div>{extra}</div>'


FOCUS = ' style="outline:2px solid var(--accent);outline-offset:2px"'
RETIRE_TEXT = "It won’t run again, from its triggers or Run now. Its past runs stay readable under Retired."
board("PhoneRetire", 390, 844, phone(agent_page(), back=P, title="Dependency check",
      overlay='<div class="scrim"></div>' + dialog("Retire Dependency check?", RETIRE_TEXT,
                                                   f'<span class="btn"{FOCUS}>Cancel</span><span class="btn redfill">Retire</span>')))


# ---- states ----
def st(label, note, body):
    return f'<div class="st"><div class="lab">{label}<span>{note}</span></div>{body}</div>'


RETIRED_LIST = ('<h2 style="margin-top:0">Retired agents</h2><div class="card alist">'
                + arow("Issue labels", "Retired Oct 19 · 23 runs") + arow("Old digest", "Retired Sep 30 · 41 runs") + '</div>')
RETIRED_PAGE = ('<h1 style="margin:0 0 4px">Issue labels</h1><p class="muted" style="margin:0 0 4px">Retired Oct 19</p>'
                + runs(rows=RUNS[:2], more=False) + definition(edit=False))
CELLS = [
    ("1 · Section loading", "a spinner named Loading agents…", section(state="loading", top=False)),
    ("2 · Section read failed", "other settings stay usable", section(state="failed", top=False)),
    ("3 · No agents of your own yet", "the budget, New agent and every built-in agent", section(rows=[], top=False, built=BUILTIN_ALL)),
    ("4 · Budget saving", "the choice shows at once", section(rows=AGENTS[:1], bstate="saving", retired=False, top=False, built=None)),
    ("5 · Budget refused", "back to the saved value", section(rows=AGENTS[:1], bstate="refused", retired=False, top=False, built=None)),
    ("6 · Budget spent", "agents wait until Monday", section(rows=[AGENTS[3]], bstate="spent", retired=False, top=False, built=None)),
    ("7 · Run now starting", "held until the run is read; same width", status("starting")),
    ("8 · Run now refused", "Run now again, with its reason", status("refused")),
    ("9 · Waiting", "for a task slot, or for the project’s other agent run", status("waiting") + '<div style="height:10px"></div>' + status("waiting-agent")),
    ("10 · Running, then Stop pressed", "Open goes to the run; Pause lets this run finish", status("running") + '<div style="height:10px"></div>' + status("stopping")),
    ("11 · Paused", "Resume answers at once", status("paused")),
    ("12 · Pause refused", "the line and button return", status("pause-refused")),
    ("13 · Every kind of run", "skips have no page, so no chevron", runs(more=False)),
    ("14 · No runs yet", "", runs(rows=[], more=False)),
    ("15 · Edit saving", "Cancel waits", form(saving=True)),
    ("16 · Edit refused", "the edit stays in the fields", form(refused=True)),
    ("17 · New agent without a name", "pressed Create agent focuses the first empty field", form(new=True, nameless=True)),
    ("18 · Leaving with unsaved changes", "Keep editing has focus",
     dialog("Discard your changes?", "Your edits to Dependency check haven’t been saved.",
            f'<span class="btn"{FOCUS}>Keep editing</span><span class="btn redbtn">Discard</span>', static=True)),
    ("19 · Retire pending", "held until the agent reads as retired",
     dialog("Retire Dependency check?", RETIRE_TEXT,
            '<span class="btn" aria-disabled="true">Cancel</span><span class="btn redfill" aria-disabled="true"><span class="spin"></span>Retiring…</span>', static=True)),
    ("20 · Retire refused", "Cancel works",
     dialog("Retire Dependency check?", RETIRE_TEXT, f'<span class="btn"{FOCUS}>Cancel</span><span class="btn redfill">Retire</span>',
            '<div class="err">Not retired: the agent changed in another window.</div>', static=True)),
    ("21 · Retired agents", "from “2 retired” under the section", RETIRED_LIST),
    ("22 · A retired agent", "runs and what it did, read-only", RETIRED_PAGE),
    ("23 · Chat lines", "quiet system lines; never an alert or a Needs you count",
     '<div style="display:flex;flex-direction:column;gap:14px;padding:6px 0">'
     + sysa("Dependency check", "found something: 2 packages have security fixes.", "found")
     + sysa("Dependency check", "found nothing.")
     + sysa("Dependency check", "couldn’t finish: <code>make audit</code> is not a target.", "fail")
     + sysa("Dependency check", "stopped at its 20-minute limit.", "fail")
     + sysa("Dependency check", "stopped by you.")
     + sysa("Retired Dependency check", "", link="") + '</div>'),
    ("24 · Add a trigger", "step 1 adds schedules only; from step 3 a menu, Escape returns focus to the button",
     triggers("schedule", open_schedule=False, step1=True) + '<div style="height:12px"></div>'
     + triggers("schedule", open_schedule=False) + '<div style="margin:-8px 0 0 12px;position:relative">' + TRIGGER_MENU + '</div>'),
    ("25 · No trigger", "the agent runs only when you press Run now", triggers(empty=True)),
    ("26 · One run for several events", "events during a run wait and go together",
     runs(rows=[("found", "Today 10:12", "Found something", "7 min · 4.4M tokens", "For PR #810, #811 and #812: #811 changed the retry limit without a test."),
                ("", "Today 09:40", "Nothing found", "4 min · 2.1M tokens", "For PR #809: every change has a test.")], more=False)),
    ("27 · Paused after repeated failures", "three runs in a row couldn’t finish",
     status("self-paused") + '<div style="height:12px"></div>'
     + sysa("Dependency check", "paused itself: 3 runs in a row couldn’t finish.", "fail", link="Open")),
    ("28 · A late run, a run that waited too long", "one catch-up after Altitude was off; a wait ends at six hours",
     runs(rows=[("", "Oct 13, 07:00, ran at 09:14", "Nothing found", "5 min · 3.4M tokens", "Every dependency is current."),
                ("skip", "Oct 12, 07:00", "Skipped", "", "No free slot for 6 hours.")], more=False)),
    ("29 · A new agent", "saved paused; try it, then turn it on", status("new")),
    ("30 · A schedule with no day", "Save waits for a day", triggers(noday=True)),
    ("31 · Offer in chat", "drawn by Altitude from the stored definition; only your press saves it", offer()),
    ("32 · Offer saving, then created", "the card stays where it was", offer("saving") + '<div style="height:12px"></div>' + offer("created")),
    ("33 · Offer refused, offer lapsed", "no buttons once it cannot be saved", offer("refused") + '<div style="height:12px"></div>' + offer("lapsed")),
    ("34 · Run page loading, read failed", "", run_page("loading") + '<div style="height:12px"></div>' + run_page("failed")),
    ("35 · A run that couldn’t finish", "its reason in the report’s place", run_page("fail")),
    ("36 · The project menu", "Agents… is always there, after Project settings…",
     '<div class="menu-mock" role="menu" style="position:static"><div class="hi">Project settings…</div><div>Agents…</div>'
     '<div>Setup… <span class="muted">Ready</span></div><div>Reset L3 conversation…</div><div>All settings…</div></div>'),
    ("37 · Turning a built-in agent on", "it joins your agents, on, with its schedule", builtin(state="saving")),
    ("38 · Turn on refused", "the button returns with the reason", builtin(state="refused")),
    ("39 · Altitude’s own project", "the Weekly review is built in there (step 2); its Turn on opens its terms first",
     builtin(rows=[WEEKLY_ROW] + BUILTIN_ALL[:2])),
]
board("States", 1200, 9860, '<div style="padding:36px 40px 14px"><h1 style="font-size:20px;font-weight:600;margin:0">Agents: states at 1440</h1>'
      '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:900px">Every state of the agents page, the agent page, the form, the dialogs and chat, as PROPOSAL.md specifies them.</p></div>'
      '<div class="sheetgrid">' + "".join(st(*c) for c in CELLS) + '</div>')
board("PhoneStates", 1720, 6960, '<div style="padding:36px 40px 14px"><h1 style="font-size:20px;font-weight:600;margin:0">Agents: states at 390</h1>'
      '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px">The same states at phone width: stacked choices, 44px targets, full-width cards.</p></div>'
      '<div class="sheetgrid phonesheet">' + "".join(st(*c) for c in CELLS) + '</div>')
print("written")
