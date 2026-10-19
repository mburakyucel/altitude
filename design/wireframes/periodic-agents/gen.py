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
CHECK = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>'
CHEVL = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>'
CHEVR = '<svg class="i sm chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>'
DOWN = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>'
U = 'class="u" href="#"'
P = "harbor"


def board(name, w, h, body):
    (out / f"{name}.html").write_text(HEAD.format(title=name, w=w, h=h) + body + TAIL)


def rail(on="harbor", monitor=False):
    def item(name, dot):
        style = ' style="background:var(--bubble);font-weight:600"' if name == on and not monitor else ""
        return f'<div class="ri"{style}><span class="dot {dot}"></span>{name}</div>'
    mon = ' style="height:36px;background:var(--bubble);font-weight:600"' if monitor else ' style="height:36px"'
    return ('<aside class="rail"><div class="brand"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div>'
            '<div class="ri">Needs you<span class="badge">1</span></div><div class="rsec"><span>Projects</span></div>'
            + item("altitude", "idle") + item("harbor", "held") +
            '<div class="engines"><div class="erow"><div class="t"><b>Claude</b><span>38% of week</span></div><div class="meter"><i style="width:38%"></i></div></div>'
            '<div class="erow"><div class="t"><b>Codex</b><span>22% of week</span></div><div class="meter"><i style="width:22%"></i></div></div></div>'
            f'<div class="ri"{mon}>Monitor</div><div class="who"><span class="avatar">O</span>Operator</div></aside>')


def desk(inner, h=900, monitor=False):
    return (f'<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:{h}px">' + rail(monitor=monitor)
            + f'<main class="pane" style="overflow:hidden"><div class="route-content" style="max-width:760px;width:100%;overflow:visible">{inner}</div></main></div>')


def tabbar(on=None):
    tabs = [("chat", "Chat"), ("work", "Work"), ("needs", "Needs you"), ("monitor", "Monitor")]
    return '<nav class="tabbar">' + "".join(f'<div class="tab{" on" if k == on else ""}">{t}</div>' for k, t in tabs) + '</nav>'


def phone(inner, back="Settings", title=P, h=844, on=None, overlay=""):
    return (f'<div class="m" style="height:{h}px;position:relative"><div></div><header class="mh"><a class="btn" href="#" style="padding:0 8px">{CHEVL}{back}</a>'
            f'<h1 style="font-size:17px;margin:0 0 0 8px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{title}</h1></header>'
            f'<div class="route-content" style="overflow:hidden;padding:4px 16px 20px">{inner}</div><div></div>{tabbar(on)}{overlay}</div>')


def sw(on=True, busy=False):
    label = "On" if on else "Paused"
    return f'<span class="sw{" on" if on else ""}{" busy" if busy else ""}" role="switch" aria-checked="{str(on).lower()}" aria-label="{label}"><i></i></span>'


# ---- the agents ----
AGENTS = [
    ("Dependency check", 'Weekdays at 07:00 · Found something 2 h ago', True),
    ("CI health", '<span class="pulse"></span>Every day at 06:30 · Running, 4 of 20 min', True),
    ("Issue triage", 'Mondays at 08:00 · Paused', False),
    ("What landed", 'Fridays at 17:00 · Skipped today: Claude’s allowance is inside the reserve', True),
]


def arow(name, line, on, busy=False):
    return (f'<a class="arow" href="#"><span class="tx"><b>{name}</b><small>{line}</small></span>{sw(on, busy)}{CHEVR}</a>')


def section(rows=None, foot=True, state=None):
    rows = AGENTS if rows is None else rows
    head = ('<h2 id="agents" style="margin-top:24px">Scheduled agents</h2>'
            '<p class="muted" style="margin-top:-6px">Agents that run on a schedule you set and tell you what they find.</p>')
    if state == "loading":
        body = '<div class="card" style="display:flex;align-items:center;gap:10px"><span class="spin" role="status" aria-label="Loading agents…"></span></div>'
        return head + body
    if state == "failed":
        return head + '<div class="card"><p style="margin:0 0 10px">Could not load agents.</p><span class="btn">Retry</span></div>'
    items = "".join(arow(*a) if len(a) == 3 else arow(*a[:3], busy=a[3]) for a in rows)
    extra = ""
    if state == "saving":
        extra = '<div class="saving"><span class="spin"></span>Saving…</div>'
    elif state == "refused":
        extra = '<div class="saving" style="color:var(--danger)">Not saved: the agent changed in another window. <a class="u" href="#">Retry</a></div>'
    new = f'<div class="afoot"><span class="btn">New agent</span></div>'
    card = f'<div class="card alist">{items}{extra}{new}</div>'
    if not rows:
        card = '<div class="card alist"><div class="afoot" style="border-top:0"><span class="btn">New agent</span></div></div>'
    tail = f'<p class="muted" style="margin:10px 2px 0"><a {U}>2 retired</a></p>' if foot and rows else ""
    return head + card + tail


SETTINGS_TOP = ('<a class="btn setting-back" href="#">' + CHEVL + 'Settings</a><h1>harbor</h1>'
                '<p class="muted" style="margin:0 0 6px">Applies to this project only.</p>'
                '<h2 style="margin-top:20px">L3</h2><p class="muted" style="margin-top:-6px">Who answers in this project’s chat. Its model and effort are under the chat’s message box.</p>'
                '<div class="card"><p style="margin:0">Last reply reported Opus · High.</p></div>'
                '<h2 style="margin-top:20px">Routing</h2><div class="card"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center;padding:2px 0">'
                '<span><b>Tasks</b><br><small class="muted">Auto, Prefer or Only an engine.</small></span><span class="field" style="min-width:9rem;flex:none">Auto</span></div></div>')
PROJECT = ('<h2 style="margin-top:24px">Project</h2><div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px">'
           '<span><b>Setup</b><br><small class="muted">Ready</small></span></div></div>')

board("DesktopSettings", 1440, 1000, desk(SETTINGS_TOP + section() + PROJECT, h=1000))
board("PhoneSettings", 390, 844, phone(section() + PROJECT, back="Settings", title=P))

# ---- one agent ----
BRIEF = ("Check every dependency in pyproject.toml and web/package.json for security fixes and major releases. "
         "Use <code>make audit</code> if it exists. Say which packages need an update, how urgent each is, and what the change would touch.")


def status(state="idle"):
    if state == "running":
        return ('<div class="card status"><span class="spin"></span><span class="tx">Running · 4 of 20 min · <a ' + U + '>Open</a></span>'
                '<span class="btn">Stop</span></div>')
    if state == "stopping":
        return ('<div class="card status"><span class="spin"></span><span class="tx">Running · 4 of 20 min · <a ' + U + '>Open</a></span>'
                '<span class="btn" aria-disabled="true"><span class="spin"></span>Stopping…</span></div>')
    if state == "starting":
        return ('<div class="card status"><span class="tx">Next: tomorrow at 07:00</span>'
                '<span class="btn" style="min-width:86px" aria-label="Run now, starting"><span class="spin"></span>Starting…</span></div>')
    if state == "refused":
        return ('<div class="card"><div class="status"><span class="tx">Next: tomorrow at 07:00</span><span class="btn">Run now</span></div>'
                '<div class="err">Claude’s allowance is inside the reserve, so a run can’t start now.</div></div>')
    if state == "paused":
        return '<div class="card status"><span class="tx">Paused</span><span class="btn">Run now</span></div>'
    if state == "retired":
        return '<div class="card status"><span class="tx muted" style="font-size:14px">Retired Oct 19 · it doesn’t run again</span></div>'
    return '<div class="card status"><span class="tx">Next: tomorrow at 07:00</span><span class="btn">Run now</span></div>'


def days(on=(0, 1, 2, 3, 4), time="07:00"):
    letters = "MTWTFSS"
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    chips = "".join(f'<span class="{"on" if i in on else ""}" role="checkbox" aria-checked="{str(i in on).lower()}" aria-label="{names[i]}">{letters[i]}</span>' for i in range(7))
    return f'<div class="days">{chips}<span class="time field" style="flex:none;min-width:0">{time}</span></div>'


def seg(on=0):
    opts = ["When it finds something", "Every run", "Never"]
    return '<div class="seg segw">' + "".join(f'<span class="{"on" if i == on else ""}">{o}</span>' for i, o in enumerate(opts)) + '</div>'


def fields(name="Dependency check", brief=BRIEF, empty=False, dirty=False, saved=False, refused=False, examples=False, create=False, nameless=False):
    nm = ('<div class="field fld" style="color:var(--text-muted)">For example: Dependency check</div>' if empty
          else f'<div class="field fld">{name}</div>')
    ex = (f'<p class="muted ex" style="margin:16px 0 0">Start from an example: <a href="#">Nightly dependency check</a> · '
          f'<a href="#">Weekly issue triage</a> · <a href="#">Daily summary of what landed</a></p>') if examples else ""
    br = ('<div class="field ta" style="color:var(--text-muted)">For example: check whether CI got slower or flakier this week, and say which checks.</div>' if empty
          else f'<div class="field ta">{brief}</div>')
    out = (f'<label class="flab" style="margin-top:6px">Name</label>{nm}{ex}'
           f'<label class="flab">What it does</label>{br}<span class="hint">Write it as you would brief a task. It can use anything in the repository, such as a skill or script.</span>'
           f'<label class="flab">When</label>{days()}'
           f'<label class="flab">Tell me in chat</label>{seg()}'
           f'<div style="display:flex;gap:0 24px;flex-wrap:wrap;align-items:flex-end"><div><label class="flab">Model</label><span class="newtasks"><span><b>Default</b> · Default effort</span>{DOWN}</span></div>'
           f'<div><label class="flab">Each run stops after</label><span class="field" style="min-width:8rem;flex:none;color:var(--text-primary)">20 min&nbsp;{DOWN}</span></div></div>'
           '<p class="muted" style="margin:18px 0 0">Reports only: works in its own copy and can’t push, open pull requests or file issues.</p>'
           '<p class="muted" style="margin:6px 0 0">At most 5 runs a week, up to 20 minutes each, on your plan.</p>')
    if create:
        btns = '<span class="btn primary">Create agent</span><span class="btn ghost">Cancel</span>'
        if nameless:
            btns = '<span class="btn primary" aria-disabled="true" style="opacity:.45">Create agent</span><span class="btn ghost">Cancel</span>'
        out += f'<div style="display:flex;gap:8px;margin-top:18px">{btns}</div>'
        if nameless:
            out += '<div class="err">Give it a name and a brief.</div>'
    elif dirty:
        out += '<div style="display:flex;gap:8px;margin-top:18px"><span class="btn primary">Save</span><span class="btn ghost">Cancel</span></div>'
        if refused:
            out += '<div class="err">Not saved: the agent changed in another window. <a class="u" href="#">Retry</a></div>'
    elif saved:
        out += f'<div style="margin-top:14px"><span class="ok">{CHECK}Saved</span></div>'
    return f'<div class="card" style="padding-top:8px">{out}</div>'


RUNS = [
    ("found", "Today 07:00", "Found something", "6 min · 4.1M tokens", "2 packages have security fixes (requests, urllib3)."),
    ("", "Yesterday 07:00", "Nothing found", "5 min · 3.6M tokens", "Every dependency is current."),
    ("fail", "Oct 16, 07:00", "Couldn’t finish", "2 min · 0.9M tokens", "<code>make audit</code> is not a target."),
    ("skip", "Oct 15, 07:00", "Skipped", "", "Claude’s allowance was inside the reserve."),
    ("fail", "Oct 14, 07:00", "Stopped at its limit", "20 min · 11.2M tokens", "It was still checking the web dependencies."),
]


def runs(rows=RUNS, more=True):
    if not rows:
        return '<h2 style="margin-top:24px">Recent runs</h2><div class="card"><p class="muted" style="margin:0">No runs yet.</p></div>'
    items = ""
    for kind, when, outcome, cost, line in rows:
        cls = "run skip" if kind == "skip" else "run"
        d = {"found": "d found", "fail": "d fail"}.get(kind, "d")
        cost_html = f'<span>{cost}</span>' if cost else ""
        go = "" if kind == "skip" else CHEVR
        items += f'<div class="{cls}"><span class="{d}"></span><div class="tx"><div class="hd"><b>{outcome}</b><span>{when}</span>{cost_html}</div><p>{line}</p></div>{go}</div>'
    moreh = f'<p class="muted" style="margin:10px 2px 0"><a {U}>Show older</a></p>' if more else ""
    return f'<h2 style="margin-top:24px">Recent runs</h2><div class="card runs">{items}</div>{moreh}'


RETIRE = ('<h2 style="margin-top:24px">Retire agent</h2><div class="card retire"><span class="muted" style="font-size:14px">It won’t run again. Past runs stay readable.</span>'
          '<span class="btn redbtn">Retire…</span></div>')


def agent_head(phone_view=False, on=True, retired=False):
    right = "" if retired else sw(on)
    if phone_view:
        return ''
    return (f'<a class="btn setting-back" href="#">{CHEVL}{P}</a><div style="display:flex;align-items:center;gap:14px;margin:0 0 18px">'
            f'<h1 style="margin:0;flex:1">Dependency check</h1>{right}</div>')


def agent_page(state="idle", dirty=False, saved=False, refused=False):
    return status(state) + '<div style="height:16px"></div>' + fields(dirty=dirty, saved=saved, refused=refused) + runs() + RETIRE


board("DesktopAgent", 1440, 1640, desk(agent_head() + agent_page(), h=1640))


def phone_agent_title(on=True):
    return f'<span style="display:flex;align-items:center;gap:10px;width:100%"><span style="flex:1;overflow:hidden;text-overflow:ellipsis">Dependency check</span>{sw(on)}</span>'


board("PhoneAgent", 390, 1960, phone(agent_page("running"), back=P, title=phone_agent_title(), h=1960))

# ---- new agent ----
NEW_HEAD = (f'<a class="btn setting-back" href="#">{CHEVL}{P}</a><h1>New agent</h1>')
board("DesktopNew", 1440, 1040, desk(NEW_HEAD + fields(empty=True, examples=True, create=True), h=1040))
board("PhoneNew", 390, 1160, phone(fields(empty=True, examples=True, create=True, nameless=True), back=P, title="New agent", h=1160))


# ---- chat ----
def sysa(name, text, kind="found", link="Open"):
    dd = {"found": "dd found", "fail": "dd fail"}.get(kind, "dd")
    ln = f' <a href="#">{link}</a>' if link else ""
    return f'<div class="sysa"><span class="ln"></span><span class="{dd}"></span><span class="t"><b>{name}</b> {text}{ln}</span><span class="ln"></span></div>'


CHAT = ('<div class="day">Today</div>'
        '<div class="me">Can you set up a check every weekday morning for dependencies with security fixes? Only tell me when there is one.</div>'
        '<div class="l3"><p>Done. It runs weekdays at 07:00, reports only, and stops after 20 minutes. It uses the default model; say if you want a smaller one.</p></div>'
        + sysa("Created Dependency check:", "weekdays at 07:00 · tells you when it finds something ·", "plain", "Edit")
        + '<div class="day">Tomorrow</div>'
        + sysa("Dependency check", "found something: 2 packages have security fixes (requests, urllib3).")
        + sysa("CI health", "couldn’t finish: <code>make ci-report</code> is not a target.", "fail")
        + '<div class="me">Pause the CI one until I fix the make target.</div>'
        + '<div class="l3"><p>Paused. Its schedule resumes when you turn it back on.</p></div>'
        + sysa("Paused CI health ·", "", "plain", "Resume"))


def phone_chat(inner):
    return ('<div class="m">' '<div></div><header class="mh"><div><span class="name">harbor</span><div class="subl" style="padding-left:4px;margin-top:-6px">L3 · Ready</div></div><span class="sp"></span></header>'
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
    rows = [("harbor · CI health", "Running 4 of 20 min", "Nothing found · yesterday · 7 min · 3.9M tokens"),
            ("harbor · Dependency check", "Next 07:00", "Found something · 2 h ago · 6 min · 4.1M tokens"),
            ("harbor · What landed", "Next Fri 17:00", "Skipped today: Claude’s allowance is inside the reserve"),
            ("altitude · Flaky checks", "Paused", "Nothing found · Oct 17 · 9 min · 5.2M tokens")]
    items = "".join(f'<a class="agl" href="#" style="color:inherit;text-decoration:none"><div class="monitor-row"><b>{a}</b><span class="muted">{b}</span></div>'
                    f'<p class="muted" style="margin:0">{c}</p></a>' for a, b, c in rows)
    return f'<h2>Scheduled agents (4)</h2><div class="card" style="padding:4px 18px">{items}</div>'


SESSIONS = ('<h2>Sessions (2)</h2><div class="sessionrow"><div class="monitor-row"><span class="chip">L3</span><b>harbor</b><span class="muted">1 min ago</span></div>'
            '<p class="muted">Claude Code · Fable · context 12%</p><div class="meter"><i style="width:12%"></i></div></div>'
            '<div class="sessionrow"><div class="monitor-row"><span class="chip">L2</span><b>harbor / CI health · scheduled</b><span class="muted">just now</span></div>'
            '<p class="muted">Claude Code · Sonnet · context 6%</p><div class="meter"><i style="width:6%"></i></div></div>')
board("DesktopMonitor", 1440, 900, desk('<h1>Monitor</h1><p class="muted" style="margin:-14px 0 0">Seats and Routing now above, unchanged.</p>' + SESSIONS + mon_agents()))
board("PhoneMonitor", 390, 844,
      '<div class="m"><div></div><header class="mh"><div class="brand" style="margin:0;padding:0 4px;height:40px"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div><span class="sp"></span></header>'
      '<div class="route-content" style="overflow:hidden;padding:4px 16px"><p class="muted" style="margin:0">Seats and Routing now above, unchanged.</p>' + SESSIONS + mon_agents() + '</div><div></div>' + tabbar("monitor") + '</div>')

# ---- retire dialog ----
DLG = ('<div class="scrim"></div><div class="dlg" role="dialog"><h2>Retire Dependency check?</h2>'
       '<p>It won’t run again, from its schedule or Run now. Its past runs stay readable under Retired.</p>'
       '<div class="bt"><span class="btn" style="outline:2px solid var(--accent);outline-offset:2px">Cancel</span><span class="btn redfill">Retire</span></div></div>')
board("PhoneRetire", 390, 844, phone(agent_page(), back=P, title=phone_agent_title(), overlay=DLG))


# ---- states ----
def st(label, note, body):
    return f'<div class="st"><div class="lab">{label}<span>{note}</span></div>{body}</div>'


cells = [
    st("1 · Section loading", "a spinner named Loading agents…", section(state="loading")),
    st("2 · Section read failed", "other settings stay usable", section(state="failed")),
    st("3 · No agents yet", "only the line and New agent", section(rows=[])),
    st("4 · Switch saving", "the switch moves at once; the other rows stay usable",
       section(rows=[("Issue triage", "Mondays at 08:00 · Paused", True, True)], foot=False, state="saving")),
    st("5 · Switch refused", "the switch returns to its saved value",
       section(rows=[("Issue triage", "Mondays at 08:00 · Paused", False)], foot=False, state="refused")),
    st("6 · Run now starting", "held until the run is read; same width", status("starting")),
    st("7 · Run now refused", "back to Run now with its reason", status("refused")),
    st("8 · Running, then Stop pressed", "Open goes to the run’s page", status("running") + '<div style="height:10px"></div>' + status("stopping")),
    st("9 · Paused", "Run now stays available", status("paused")),
    st("10 · Edited, not saved", "Save and Cancel appear once a field differs", fields(dirty=True)),
    st("11 · Save refused", "the edit stays in the fields", fields(dirty=True, refused=True)),
    st("12 · Saved", "a check named Saved", fields(saved=True)),
    st("13 · New agent without a name", "pressed Create agent focuses the first empty field",
       fields(empty=True, create=True, nameless=True)),
    st("14 · No runs yet", "", runs(rows=[], more=False)),
    st("15 · Every kind of run", "skips have no page, so no chevron", runs(more=False)),
    st("16 · Retired agent", "read-only; no switch or Retire", status("retired") + runs(rows=RUNS[:2], more=False)),
    st("17 · Chat lines", "quiet system lines; never an alert or a Needs you count",
       '<div style="display:flex;flex-direction:column;gap:14px;padding:6px 0">'
       + sysa("Dependency check", "found something: 2 packages have security fixes (requests, urllib3).")
       + sysa("Dependency check", "found nothing: every dependency is current.", "plain")
       + sysa("Dependency check", "couldn’t finish: <code>make audit</code> is not a target.", "fail")
       + sysa("Dependency check", "stopped at its 20-minute limit.", "fail")
       + sysa("Created Dependency check:", "weekdays at 07:00 · tells you when it finds something ·", "plain", "Edit")
       + sysa("Retired Dependency check", "", "plain", "") + '</div>'),
    st("18 · Retire pending", "the dialog holds until the agent is read as retired",
       '<div class="dlg" style="position:static;box-shadow:none;border:1px solid var(--hairline)"><h2>Retire Dependency check?</h2>'
       '<p>It won’t run again, from its schedule or Run now. Its past runs stay readable under Retired.</p>'
       '<div class="bt"><span class="btn" aria-disabled="true">Cancel</span><span class="btn redfill" aria-disabled="true"><span class="spin"></span>Retiring…</span></div></div>'),
]
board("States", 1200, 3900, '<div style="padding:36px 40px 14px"><h1 style="font-size:20px;font-weight:600;margin:0">Scheduled agents: states</h1>'
      '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:900px">Every state of the settings section, the agent page, the form and chat, as PROPOSAL.md specifies them.</p></div>'
      '<div class="sheet">' + "".join(cells) + '</div>')
print("written")
