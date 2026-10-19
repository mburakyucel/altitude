"""Write the scheduled agents boards beside this file: python3 design/wireframes/periodic-agents/gen.py"""
import pathlib

out = pathlib.Path(__file__).resolve().parent
HEAD = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Scheduled agents: {title}</title>
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


def rail(monitor=False):
    def item(name, dot):
        style = ' style="background:var(--bubble);font-weight:600"' if name == P and not monitor else ""
        return f'<div class="ri"{style}><span class="dot {dot}"></span>{name}</div>'
    mon = ' style="height:36px;background:var(--bubble);font-weight:600"' if monitor else ' style="height:36px"'
    return ('<aside class="rail"><div class="brand"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div>'
            '<div class="ri">Needs you<span class="badge">1</span></div><div class="rsec"><span>Projects</span></div>'
            + item("altitude", "idle") + item(P, "held") +
            '<div class="engines"><div class="erow"><div class="t"><b>Claude</b><span>38% of week</span></div><div class="meter"><i style="width:38%"></i></div></div>'
            '<div class="erow"><div class="t"><b>Codex</b><span>22% of week</span></div><div class="meter"><i style="width:22%"></i></div></div></div>'
            f'<div class="ri"{mon}>Monitor</div><div class="who"><span class="avatar">O</span>Operator</div></aside>')


def desk(inner, h=900, monitor=False):
    return (f'<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:{h}px">' + rail(monitor)
            + f'<main class="pane" style="overflow:hidden"><div class="route-content" style="max-width:760px;width:100%;overflow:visible">{inner}</div></main></div>')


def tabbar(on=None):
    tabs = [("chat", "Chat"), ("work", "Work"), ("needs", "Needs you"), ("monitor", "Monitor")]
    return '<nav class="tabbar">' + "".join(f'<div class="tab{" on" if k == on else ""}">{t}</div>' for k, t in tabs) + '</nav>'


def phone(inner, back="Settings", title=P, h=844, overlay=""):
    return (f'<div class="m" style="height:{h}px;position:relative"><div></div><header class="mh"><a class="btn" href="#" style="padding:0 8px">{CHEVL}{back}</a>'
            f'<h1 style="font-size:17px;margin:0 0 0 8px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{title}</h1></header>'
            f'<div class="route-content" style="overflow:hidden;padding:4px 16px 20px">{inner}</div><div></div>{tabbar()}{overlay}</div>')


# ---- the settings section ----
AGENTS = [
    ("Dependency check", "Weekdays at 07:00 · Found something 2 h ago"),
    ("Build health", PULSE + "Every day at 06:30 · Running, 4 of 20 min"),
    ("Clean checkout", PAUSE + "Mondays at 08:00 · Paused"),
    ("What landed", "Fridays at 17:00 · Skipped Fri: the week’s budget was used"),
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


def section(rows=None, state=None, bstate=None, retired=True):
    rows = AGENTS if rows is None else rows
    head = ('<h2 id="agents" style="margin-top:24px">Scheduled agents</h2>'
            '<p class="muted" style="margin-top:-6px">Agents that run on a schedule you set and tell you what they find.</p>')
    if state == "loading":
        return head + '<div class="card"><span class="spin" role="status" aria-label="Loading agents…"></span></div>'
    if state == "failed":
        return head + '<div class="card"><p style="margin:0 0 10px">Could not load agents.</p><span class="btn">Retry</span></div>'
    items = "".join(arow(*a) for a in rows)
    card = f'<div class="card alist">{items}{budget(bstate)}<div class="afoot"><span class="btn">New agent</span></div></div>'
    tail = f'<p class="muted" style="margin:10px 2px 0"><a {U}>2 retired</a></p>' if retired and rows else ""
    return head + card + tail


SETTINGS_TOP = ('<a class="btn setting-back" href="#">' + CHEVL + 'Settings</a><h1>harbor</h1>'
                '<p class="muted" style="margin:0 0 6px">Applies to this project only.</p>'
                '<h2 style="margin-top:20px">L3</h2><p class="muted" style="margin-top:-6px">Who answers in this project’s chat. Its model and effort are under the chat’s message box.</p>'
                '<div class="card"><p style="margin:0">Last reply reported Opus · High.</p></div>'
                '<h2 style="margin-top:20px">Routing</h2><div class="card"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center;padding:2px 0">'
                '<span><b>Tasks</b><br><small class="muted">Auto, Prefer or Only an engine.</small></span><span class="field" style="min-width:9rem;flex:none">Auto</span></div></div>')
PROJECT = ('<h2 style="margin-top:24px">Project</h2><div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px">'
           '<span><b>Setup</b><br><small class="muted">Ready</small></span></div></div>')

board("DesktopSettings", 1440, 1060, desk(SETTINGS_TOP + section() + PROJECT, h=1060))
board("PhoneSettings", 390, 844, phone(section() + PROJECT))

# ---- one agent ----
BRIEF = ("Check every dependency in pyproject.toml and web/package.json for security fixes and major releases. "
         "Use <code>make audit</code> if it exists. Say which packages need an update, how urgent each is, and what the change would touch.")


def status(state="idle"):
    run_now, pause = '<span class="btn">Run now</span>', '<span class="btn">Pause</span>'
    rows = {
        "idle": ("Next: tomorrow at 07:00", run_now + pause, ""),
        "running": ('<span class="spin"></span>Running · 4 of 20 min · <a ' + U + '>Open</a>', '<span class="btn">Stop</span>', ""),
        "stopping": ('<span class="spin"></span>Running · 4 of 20 min · <a ' + U + '>Open</a>',
                     '<span class="btn" aria-disabled="true"><span class="spin"></span>Stopping…</span>', ""),
        "waiting": ("Waiting for a free task slot", '<span class="btn">Stop</span>', ""),
        "starting": ("Next: tomorrow at 07:00", '<span class="btn" style="min-width:86px" aria-label="Run now, starting"><span class="spin"></span>Starting…</span>'
                     '<span class="btn" aria-disabled="true">Pause</span>', ""),
        "refused": ("Next: tomorrow at 07:00", run_now + pause, '<div class="err">This week’s budget is used.</div>'),
        "paused": (PAUSE + "Paused", '<span class="btn">Resume</span>' + run_now, ""),
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


# ---- the form: new and edit ----
def days(on=(0, 1, 2, 3, 4), time="07:00"):
    letters = "MTWTFSS"
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    chips = "".join(f'<span class="{"on" if i in on else ""}" role="checkbox" aria-checked="{str(i in on).lower()}" aria-label="{names[i]}">{letters[i]}</span>' for i in range(7))
    return f'<div class="days">{chips}<span class="time field" style="flex:none;min-width:0">{time}</span></div>'


def seg(on=0):
    opts = ["When it finds something", "Every run", "Never"]
    return '<div class="seg segw">' + "".join(f'<span class="{"on" if i == on else ""}">{o}</span>' for i, o in enumerate(opts)) + '</div>'


def form(new=False, nameless=False, saving=False, refused=False):
    nm = ('<div class="field fld" style="color:var(--text-muted)">For example: Dependency check</div>' if new
          else '<div class="field fld">Dependency check</div>')
    ex = ('<p class="muted ex" style="margin:10px 0 0">Start from an example: <a href="#">Nightly dependency check</a> · '
          '<a href="#">Weekly build health</a> · <a href="#">Daily summary of what landed</a></p>') if new else ""
    br = ('<div class="field ta" style="color:var(--text-muted)">For example: check whether the build got slower this week, and say which step.</div>' if new
          else f'<div class="field ta">{BRIEF}</div>')
    body = (f'<label class="flab" style="margin-top:6px">Name</label>{nm}{ex}'
            f'<label class="flab">What it does</label>{br}<span class="hint">Write it as you would brief a task. It can use anything in the repository, such as a skill or script.</span>'
            f'<label class="flab">When</label>{days()}'
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
board("PhoneEdit", 390, 1080, phone(form(), back="Dependency check", title="Edit", h=1080))


# ---- chat ----
def sysa(name, text, kind="plain", link="Open", brief=False):
    dd = {"found": "dd found", "fail": "dd fail"}.get(kind, "dd")
    ln = f' <a href="#">{link}</a>' if link else ""
    extra = ('<span class="bq">Check every dependency in pyproject.toml and web/package.json for security fixes and major releases. Use <code>make audit</code> if it…'
             ' <a href="#">Show all</a></span>') if brief else ""
    return f'<div class="sysa"><span class="ln"></span><span class="{dd}"></span><span class="t"><b>{name}</b> {text}{ln}{extra}</span><span class="ln"></span></div>'


CREATED = sysa("Created Dependency check", "· weekdays at 07:00 · tells you when it finds something ·", link="Edit", brief=True)
CHAT = ('<div class="day">Today</div>'
        '<div class="me">Can you set up a check every weekday morning for dependencies with security fixes? Only tell me when there is one.</div>'
        '<div class="l3"><p>Set up. Say if you’d like it on a smaller model.</p></div>' + CREATED
        + '<div class="day">Tomorrow</div>'
        + sysa("Dependency check", "found something: 2 packages have security fixes (requests, urllib3).", "found")
        + sysa("Build health", "couldn’t finish: <code>make bench</code> is not a target.", "fail")
        + '<div class="me">Pause the build one until I add the target.</div>'
        + '<div class="l3"><p>Done.</p></div>'
        + sysa("Paused Build health", "·", link="Resume"))


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
    rows = [("harbor · Build health", "Running 4 of 20 min", "Nothing found · yesterday · 7 min · 3.9M tokens"),
            ("harbor · Dependency check", "Next tomorrow 07:00", "Found something · 2 h ago · 6 min · 4.1M tokens"),
            ("harbor · What landed", "Next Fri 17:00", "Skipped Fri: the week’s budget was used"),
            ("harbor · Clean checkout", "Paused", "Nothing found · Oct 17 · 9 min · 5.2M tokens")]
    items = "".join(f'<a class="agl" href="#"><div class="tx"><div class="monitor-row"><b>{a}</b><span class="muted">{b}</span></div>'
                    f'<p class="muted" style="margin:0">{c}</p></div>{CHEVR}</a>' for a, b, c in rows)
    return f'<h2>Scheduled agents (4)</h2><div class="card" style="padding:4px 18px">{items}</div>'


SEATS = ('<h2>Seats</h2><div class="card"><div class="monitor-row"><h3 style="margin:0">Claude Code</h3><span class="muted">reading 3m old</span></div>'
         '<div class="monitor-row" style="margin-top:8px"><span>7-day</span><span>38%</span></div><div class="meter"><i style="width:38%"></i></div></div>')
SESSIONS = ('<h2>Sessions (2)</h2><div class="sessionrow"><div class="monitor-row"><span class="chip">L3</span><b>harbor</b><span class="muted">1 min ago</span></div>'
            '<p class="muted">Claude Code · Fable · context 12%</p><div class="meter"><i style="width:12%"></i></div></div>'
            '<div class="sessionrow"><div class="monitor-row"><span class="chip">L2</span><b>harbor / Build health · scheduled</b><span class="muted">just now</span></div>'
            '<p class="muted">Claude Code · Sonnet · context 6%</p><div class="meter"><i style="width:6%"></i></div></div>')
board("DesktopMonitor", 1440, 900, desk('<h1>Monitor</h1>' + SEATS + SESSIONS + mon_agents(), monitor=True))
board("PhoneMonitor", 390, 844,
      '<div class="m"><div></div><header class="mh"><div class="brand" style="margin:0;padding:0 4px;height:40px"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div><span class="sp"></span></header>'
      '<div class="route-content" style="overflow:hidden;padding:4px 16px">' + SESSIONS + mon_agents() + '</div><div></div>' + tabbar("monitor") + '</div>')


# ---- dialogs ----
def dialog(title, text, buttons, extra="", static=False):
    style = ' style="position:static;box-shadow:none;border:1px solid var(--hairline)"' if static else ""
    return f'<div class="dlg"{style} role="dialog"><h2>{title}</h2><p>{text}</p><div class="bt">{buttons}</div>{extra}</div>'


FOCUS = ' style="outline:2px solid var(--accent);outline-offset:2px"'
RETIRE_TEXT = "It won’t run again, from its schedule or Run now. Its past runs stay readable under Retired."
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
    ("1 · Section loading", "a spinner named Loading agents…", section(state="loading")),
    ("2 · Section read failed", "other settings stay usable", section(state="failed")),
    ("3 · No agents yet", "the line, the budget and New agent", section(rows=[])),
    ("4 · Budget saving", "the choice shows at once", section(rows=AGENTS[:1], bstate="saving", retired=False)),
    ("5 · Budget refused", "back to the saved value", section(rows=AGENTS[:1], bstate="refused", retired=False)),
    ("6 · Budget spent", "agents wait until Monday", section(rows=[AGENTS[3]], bstate="spent", retired=False)),
    ("7 · Run now starting", "held until the run is read; same width", status("starting")),
    ("8 · Run now refused", "Run now again, with its reason", status("refused")),
    ("9 · Waiting for a slot", "the machine’s task cap is full", status("waiting")),
    ("10 · Running, then Stop pressed", "Open goes to the run’s live page", status("running") + '<div style="height:10px"></div>' + status("stopping")),
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
     + sysa("Dependency check", "found something: 2 packages have security fixes (requests, urllib3).", "found")
     + sysa("Dependency check", "found nothing: every dependency is current.")
     + sysa("Dependency check", "couldn’t finish: <code>make audit</code> is not a target.", "fail")
     + sysa("Dependency check", "stopped at its 20-minute limit.", "fail")
     + sysa("Dependency check", "stopped by you.")
     + CREATED
     + sysa("Changed Dependency check", "· runs at 06:00 ·", link="Edit")
     + sysa("Retired Dependency check", "", link="") + '</div>'),
]
board("States", 1200, 5960, '<div style="padding:36px 40px 14px"><h1 style="font-size:20px;font-weight:600;margin:0">Scheduled agents: states at 1440</h1>'
      '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:900px">Every state of the settings section, the agent page, the form, the dialogs and chat, as PROPOSAL.md specifies them.</p></div>'
      '<div class="sheetgrid">' + "".join(st(*c) for c in CELLS) + '</div>')
board("PhoneStates", 1720, 4300, '<div style="padding:36px 40px 14px"><h1 style="font-size:20px;font-weight:600;margin:0">Scheduled agents: states at 390</h1>'
      '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px">The same states at phone width: stacked choices, 44px targets, full-width cards.</p></div>'
      '<div class="sheetgrid phonesheet">' + "".join(st(*c) for c in CELLS) + '</div>')
print("written")
