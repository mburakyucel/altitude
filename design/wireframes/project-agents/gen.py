"""Write the routines boards beside this file: python3 design/wireframes/project-agents/gen.py"""
import pathlib

out = pathlib.Path(__file__).resolve().parent
HEAD = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Routines: {title}</title>
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


def rail(monitor=False, on=P, needs=False):
    def item(name, dot):
        style = ' style="background:var(--bubble);font-weight:600"' if name == on and not monitor else ""
        return f'<div class="ri"{style}><span class="dot {dot}"></span>{name}</div>'
    mon = ' style="height:36px;background:var(--bubble);font-weight:600"' if monitor else ' style="height:36px"'
    return ('<aside class="rail"><div class="brand"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div>'
            + ('<div class="ri" style="background:var(--bubble);font-weight:600">Needs you<span class="badge">2</span></div>' if needs
               else '<div class="ri">Needs you<span class="badge">1</span></div>') + '<div class="rsec"><span>Projects</span></div>'
            + item("altitude", "idle") + item(P, "held") +
            '<div class="engines"><div class="erow"><div class="t"><b>Claude</b><span>38% of week</span></div><div class="meter"><i style="width:38%"></i></div></div>'
            '<div class="erow"><div class="t"><b>Codex</b><span>22% of week</span></div><div class="meter"><i style="width:22%"></i></div></div></div>'
            f'<div class="ri"{mon}>Monitor</div><div class="who"><span class="avatar">O</span>Operator</div></aside>')


def desk(inner, h=900, monitor=False, on=P, needs=False):
    return (f'<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:{h}px">' + rail(monitor, on, needs)
            + f'<main class="pane" style="overflow:hidden"><div class="route-content" style="max-width:760px;width:100%;overflow:visible">{inner}</div></main></div>')


def tabbar(on=None):
    tabs = [("chat", "Chat"), ("work", "Work"), ("needs", "Needs you"), ("monitor", "Monitor")]
    return '<nav class="tabbar">' + "".join(f'<div class="tab{" on" if k == on else ""}">{t}</div>' for k, t in tabs) + '</nav>'


def phone(inner, back=P, title="Routines", h=844, overlay="", tab="work"):
    return (f'<div class="m" style="height:{h}px;position:relative"><div></div><header class="mh"><a class="btn" href="#" style="padding:0 8px">{CHEVL}{back}</a>'
            f'<h1 style="font-size:17px;margin:0 0 0 8px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{title}</h1></header>'
            f'<div class="route-content" style="overflow:hidden;padding:4px 16px 20px">{inner}</div><div></div>{tabbar(tab)}{overlay}</div>')


# ---- the routines page ----
AGENTS = [
    ("Dependency check", "Weekdays at 07:00 · Found something 2 h ago"),
    ("Review each merge", PULSE + "When it’s sent an event · Running, 2 of 20 min"),
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
        used = '<small style="color:var(--chip-claimed-text)">2 h used this week · routines wait until Monday</small>'
    return (f'<div class="afoot budget"><span class="tx"><b>Routines may use up to</b>{used}</span>'
            f'<span class="field" style="min-width:9rem;flex:none;color:var(--text-primary)">{sel}&nbsp;{DOWN}</span></div>{extra}')


BUILTIN = [("Build health", "Mondays at 08:00 · Runs the project’s checks on a clean copy and says what broke")]
BUILTIN_ALL = [("Dependency check", "Weekdays at 07:00 · Looks for security fixes and major releases of what the project uses"),
               ("Build health", BUILTIN[0][1]),
               ("What landed", "Fridays at 17:00 · Sums up what merged this week and anything left unfinished")]
WEEKLY_ROW = ("Weekly improvement review", "Mondays at 08:00 · Suggests fixes for friction that keeps coming back in this project’s tasks")


def brow(name, line, state=None, refused="the routines changed in another window."):
    btn = '<span class="btn">Turn on</span>'
    if state == "saving":
        btn = '<span class="btn" style="min-width:76px" aria-label="Turn on, saving"><span class="spin"></span></span>'
    err = f'<div class="err" style="margin-top:6px">Not turned on: {refused}</div>' if state == "refused" else ""
    return f'<div class="arow brow"><span class="tx"><b>{name}</b><small>{line}</small>{err}</span>{btn}</div>'


def builtin(rows=BUILTIN, state=None):
    items = "".join(brow(n, l, state if i == 0 else None) for i, (n, l) in enumerate(rows))
    return ('<h2 style="margin-top:24px">Built in</h2><p class="muted" style="margin:-4px 0 10px;font-size:13.5px">Ready to turn on; each becomes yours to change.</p>'
            f'<div class="card alist">{items}</div>')


def section(rows=None, state=None, bstate=None, retired=True, top=True, built=BUILTIN, bistate=None):
    rows = AGENTS if rows is None else rows
    head = (f'<a class="btn setting-back" href="#">{CHEVL}{P}</a><h1 style="margin-bottom:4px">Routines</h1>'
            '<p class="muted" style="margin:0 0 14px">Each one runs a brief when its trigger fires, and tells you what it found.</p>')
    if not top:
        head = head.split("</h1>")[1]
    if state == "loading":
        return head + '<div class="card"><span class="spin" role="status" aria-label="Loading routines…"></span></div>'
    if state == "failed":
        return head + '<div class="card"><p style="margin:0 0 10px">Could not load routines.</p><span class="btn">Retry</span></div>'
    items = "".join(arow(*a) for a in rows)
    card = f'<div class="card alist">{items}{budget(bstate)}<div class="afoot"><span class="btn">New routine</span></div></div>'
    tail = f'<p class="muted" style="margin:10px 2px 0"><a {U}>2 retired</a></p>' if retired and rows else ""
    return head + card + tail + (builtin(built, bistate) if built else "")


board("DesktopAgents", 1440, 900, desk(section()))
board("PhoneAgents", 390, 1000, phone(section(top=False), h=1000))

# ---- one routine ----
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
        "events": ("Runs when it’s sent an event", run_now + pause, ""),
        "events-none": ('<span>Runs when it’s sent an event<small>No event yet, so Run now runs without one.</small></span>',
                        run_now + pause, ""),
        "running-pending": ('<span class="spin"></span><span>Running · 4 of 20 min · <a ' + U + '>Open</a><small>3 events wait for the next run.</small></span>',
                            '<span class="btn">Stop</span>' + pause, ""),
        "new": ("Not on yet", run_now + '<span class="btn">Turn on</span>', ""),
        "starting": ("Next: tomorrow at 07:00", '<span class="btn" style="min-width:86px" aria-label="Run now, starting"><span class="spin"></span>Starting…</span>'
                     '<span class="btn" aria-disabled="true">Pause</span>', ""),
        "refused": ("Next: tomorrow at 07:00", run_now + pause, '<div class="err">Only 12 min of this week’s budget is left.</div>'),
        "paused": (PAUSE + "Paused", '<span class="btn">Resume</span>' + run_now, ""),
        "self-paused": (PAUSE + "Paused itself after 3 failed runs", '<span class="btn">Resume</span>' + run_now, ""),
        "pause-refused": ("Next: tomorrow at 07:00", run_now + pause,
                          '<div class="err">Not saved: the routine changed in another window. <a class="u" href="#">Retry</a></div>'),
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


RETIRE = ('<h2 style="margin-top:24px">Retire routine</h2><div class="card retire"><span class="muted" style="font-size:14px">It won’t run again. Past runs stay readable.</span>'
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


EVENT_LINES = '<div class="evl"><p>10:02 · PR #810 merged</p><p>10:02 · PR #811 merged</p><p>10:04 · PR #812 merged</p></div>'


def run_page(state="ended"):
    meta = '<p class="muted" style="margin:0 0 16px;font-size:13.5px">On its schedule · Sonnet{}</p>'
    if state == "events":
        return ('<p class="muted" style="margin:0 0 4px;font-size:13.5px">For “PR #810 merged” and 2 more · Sonnet · 7 min · 4.4M tokens</p>'
                + EVENT_LINES + '<div class="card"><div class="runhead"><span class="d found"></span><b>Found something</b></div>'
                  '<p style="margin:6px 0 0">#811 raised the retry limit in <code>uploader.py</code> without a test; the test would go in '
                  '<code>tests/test_uploader.py</code> beside the other retry cases.</p></div>' + FOLDS)
    if state == "replay":
        return ('<p class="muted" style="margin:0 0 16px;font-size:13.5px">Run now by you · For “PR #812 merged”, the latest · Sonnet · 3 min · 1.1M tokens</p>'
                '<div class="card"><div class="runhead"><span class="d"></span><b>Nothing found</b></div>'
                '<p style="margin:6px 0 0">Every change in PR #812 has a test.</p></div>' + FOLDS)
    if state == "no-event":
        return ('<p class="muted" style="margin:0 0 16px;font-size:13.5px">Run now by you · No event yet · Sonnet · 1 min · 0.4M tokens</p>'
                '<div class="card"><div class="runhead"><span class="d"></span><b>Nothing found</b></div>'
                '<p style="margin:6px 0 0">No merged pull request was named, so there was nothing to check.</p></div>' + FOLDS)
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


# ---- the Weekly improvement review as a built-in routine (Altitude's own project, step 2) ----
WEEKLY_RUNS = [
    ("found", "Oct 19, 08:00", "3 suggestions", "4 min", "Owners re-ask settled questions; a review box hides its reason; landing waits are unclear."),
    ("", "Oct 12, 08:00", "Nothing to suggest", "3 min", "6 tasks read."),
    ("skip", "Oct 5, 08:00", "Skipped", "", "3 tasks had finished; it needs 5."),
]


def weekly_status(state="few"):
    """The Weekly review's status card: its line, its buttons and the one line under them, if any."""
    off = '<span class="btn" aria-disabled="true" style="opacity:.45">Run now</span>'
    run_now, pause, resume = '<span class="btn">Run now</span>', '<span class="btn">Pause</span>', '<span class="btn">Resume</span>'
    reads = '<p class="muted" style="margin:8px 0 0;font-size:13px">Reads the 6 tasks finished since Oct 19.</p>'
    err = lambda text: f'<div class="err">{text}</div>'
    stop = '<span class="btn">Stop</span>'
    rows = {
        "few": ("Next: Monday, Oct 26, if 2 more tasks finish", off + pause, ""),
        "few-pressed": ("Next: Monday, Oct 26, if 2 more tasks finish", off + pause,
                        '<p class="muted" style="margin:8px 0 0;font-size:13px">Run now needs 5 finished tasks since Oct 19; 3 so far.</p>'),
        "ready": ("Next: Monday, Oct 26", run_now + pause, reads),
        "starting": ("Next: Monday, Oct 26", '<span class="btn" style="min-width:86px" aria-label="Run now, starting"><span class="spin"></span>Starting…</span>'
                     '<span class="btn" aria-disabled="true" style="opacity:.45">Pause</span>', reads),
        "reserve": ("Next: Monday, Oct 26", off + pause,
                    '<p class="muted" style="margin:8px 0 0;font-size:13px">Claude’s allowance is inside Altitude’s reserve.</p>'),
        "run-refused": ("Next: Monday, Oct 26", run_now + pause, err("Another review just ended; ask again.")),
        "pause-refused": ("Next: Monday, Oct 26", run_now + pause, err('Not saved: the terms changed while the sheet was open; read them again. <a class="u" href="#">Retry</a>')),
        "reading": ('<span class="spin"></span>Reading 6 tasks', stop, ""),
        "checking": ('<span class="spin"></span>L3 is checking 3', stop, ""),
        "stopping": ('<span class="spin"></span>Reading 6 tasks', '<span class="btn" aria-disabled="true"><span class="spin"></span>Stopping…</span>', ""),
        "stop-failed": ('<span class="spin"></span>Reading 6 tasks', stop, err("Not stopped. Try again.")),
        "paused": (PAUSE + "Paused", resume + run_now, reads),
        "terms": (PAUSE + "Paused: its terms changed", resume + run_now, ""),
    }
    line, btns, under = rows[state]
    return f'<div class="card"><div class="status"><span class="tx">{line}</span><span class="btns">{btns}</span></div>{under}</div>'


WEEKLY_ALL = [
    ("found", "Oct 19, 08:00", "3 suggestions", "4 min", "Owners re-ask settled questions; a review box hides its reason; landing waits are unclear."),
    ("", "Oct 15, 14:02", "Nothing to suggest", "3 min", "6 tasks read."),
    ("fail", "Oct 12, 08:00", "Couldn’t finish", "1 min", "It was interrupted by a restart."),
    ("", "Oct 8, 10:40", "Stopped by you", "2 min", "It was reading 7 tasks."),
    ("skip", "Oct 5, 08:00", "Skipped", "", "3 tasks had finished; it needs 5."),
]
SHEET_TEXT = ("Claude Sonnet 5.5 reads counts from this project’s tasks and short quotes from its chats (up to 300 characters each). "
              "It runs on your Claude plan, capped at far less per run than an ordinary task; one ordinary L3 turn then checks what it finds. "
              "With Weekly on, this happens Monday mornings once 5 tasks have finished. Nothing is published, and nothing becomes work until you choose it.")


def weekly_page():
    card = weekly_status()
    what = ('<h2 style="margin-top:24px">What it does</h2><div class="card">'
            '<p style="margin:0">Finds friction that keeps coming back in this project’s tasks and suggests fixes, as cards in chat you can make into tasks.</p>'
            '<p class="muted" style="margin:10px 0 0">Counts and short chat quotes · Claude Sonnet 5.5 on your plan · capped per run, plus one L3 check</p>'
            f'<p class="muted" style="margin:6px 0 0">4 runs · 7 suggestions · 3 became tasks · 2 dismissed · <a {U}>Latest</a></p></div>')
    return card + runs(rows=WEEKLY_RUNS, more=False) + what


WEEKLY_HEAD = f'<a class="btn setting-back" href="#">{CHEVL}altitude</a><h1 style="margin-bottom:16px">Weekly improvement review</h1>'
board("DesktopWeekly", 1440, 900, desk(WEEKLY_HEAD + weekly_page(), on="altitude"))
board("PhoneWeekly", 390, 1000, phone(weekly_page(), back="altitude", title="Weekly improvement review", h=1000))


# ---- the form: new and edit ----
def days(on=(0, 1, 2, 3, 4), time="07:00"):
    letters = "MTWTFSS"
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    chips = "".join(f'<span class="{"on" if i in on else ""}" role="checkbox" aria-checked="{str(i in on).lower()}" aria-label="{names[i]}">{letters[i]}</span>' for i in range(7))
    return f'<div class="days">{chips}<span class="time field" style="flex:none;min-width:0">{time}</span></div>'


CLOCK = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>'
ZAP = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M13 3L5 13h6l-1 8 8-10h-6l1-8z"/></svg>'
XM = '<span class="x" role="button" aria-label="Remove this trigger"><svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17"/></svg></span>'


EVENT = ("When it’s sent an event", "You send it from this computer; its page shows how.")


def event_row(words, line):
    return f'<div class="tr">{ZAP}<span class="tx">{words}<small>{line}</small></span>{XM}</div>'


def triggers(kind="schedule", open_schedule=True, empty=False, noday=False):
    """The Runs when list: each trigger one row with Remove; a schedule row opens its days and time."""
    if empty:
        rows = '<div class="tr"><span class="tx muted">Only when you run it</span></div>'
    else:
        none = '<div class="err" style="margin-top:8px">Pick at least one day.</div>' if noday else ""
        sched = (f'<div class="tr open"><div class="row1">{CLOCK}<span class="tx">On a schedule</span>{XM}</div>{days(on=() if noday else (0, 1, 2, 3, 4))}{none}</div>' if open_schedule
                 else f'<div class="tr">{CLOCK}<span class="tx">Weekdays at 07:00</span>{XM}</div>')
        merge = event_row(*EVENT)
        rows = {"schedule": sched, "event": merge, "both": sched + merge}[kind]
    return f'<div class="trig">{rows}<div class="add"><span class="btn">+ Add a trigger</span></div></div>'


def trigger_menu():
    """+ Add a trigger's menu, until the list has the event trigger."""
    return f'<div class="menu-mock" role="menu"><div class="hi">On a schedule</div><div>{EVENT[0]}</div></div>'


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
            f'<label class="flab">Tell me in chat</label>{seg()}<span class="hint">Suggested tasks wait in Needs you whatever you choose.</span>'
            f'<div style="display:flex;gap:0 24px;flex-wrap:wrap;align-items:flex-end"><div><label class="flab">Model</label><span class="newtasks"><span><b>Default</b> · Default effort</span>{DOWN}</span></div>'
            f'<div><label class="flab">Each run stops after</label><span class="field" style="min-width:8rem;flex:none;color:var(--text-primary)">20 min&nbsp;{DOWN}</span></div></div>')
    primary = "Create routine" if new else "Save"
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
        body += '<div class="err">Not saved: the routine changed in another window. <a class="u" href="#">Retry</a></div>'
    return f'<div class="card" style="padding-top:8px">{body}</div>'


board("DesktopEdit", 1440, 960, desk(f'<a class="btn setting-back" href="#">{CHEVL}{P}</a><h1>New routine</h1>' + form(new=True), h=960))
board("PhoneEdit", 390, 1150, phone(form(trig="event", name="Review each merge", brief=MERGE_BRIEF), back="Review each merge", title="Edit", h=1150))


# ---- suggestions (step 5) ----
CHECK = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>'
SUGS = [("Add a test for the uploader’s retry limit",
         "A case beside the other retry tests in tests/test_uploader.py that checks the limit of 5, so the next change "
         "to it fails a test."),
        ("Say the new retry limit in the upload docs",
         "One sentence in docs/uploads.md, which still says three, so the docs match what uploads do.")]


def sug(n, state="open"):
    title, text = SUGS[n - 1]
    head = f'<div class="t">{n}. {title}</div><p>{text}</p>'
    acts = {
        "open": '<div class="acts"><span class="btn acc">Make a task</span><span class="btn ghost">Dismiss</span></div>',
        "pressed": ('<div class="acts"><span class="btn acc" style="width:104px" aria-label="Make a task, saving"><span class="spin"></span></span>'
                    '<span class="btn ghost" style="opacity:.45">Dismiss</span></div>'),
        "accepted": f'<div class="ok">{CHECK}Accepted · L3 is opening a task</div>',
        "notask": f'<div class="ok">{CHECK}Accepted · no task yet · <a {U}>Try again</a></div>',
        "retrying": f'<div class="ok">{CHECK}Accepted · no task yet · <span class="spin" aria-label="Try again, saving"></span></div>',
        "retry-refused": (f'<div class="ok">{CHECK}Accepted · no task yet · <a {U}>Try again</a></div>'
                          '<div class="err">Not saved: L3 is still opening its task.</div>'),
        "undoing": ('<div class="acts"><span class="btn acc" aria-disabled="true" style="opacity:.45">Make a task</span>'
                    '<span class="btn ghost" aria-disabled="true" style="opacity:.45">Dismiss</span><span class="spin" aria-label="Undo, saving"></span></div>'),
        "task": ('<div class="tcard"><span class="dot"></span><div><div class="tt">Add a test for the uploader’s retry limit</div>'
                 '<div class="tm">Running · Opus on Claude · 2 min · from Review each merge</div></div>'
                 '<span class="go"><svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg></span></div>'),
        "refused": ('<div class="acts"><span class="btn acc">Make a task</span><span class="btn ghost">Dismiss</span></div>'
                    '<div class="err">Not saved. Try again.</div>'),
    }
    if state == "dismissing":
        return f'<div class="sg dim"><span class="t">{n}. {title}</span><span class="meta">Dismissed · <span class="spin" aria-label="Dismiss, saving"></span></span></div>'
    if state == "dismiss-refused":
        return (f'<div class="sg">{head}<div class="acts"><span class="btn acc">Make a task</span><span class="btn ghost">Dismiss</span></div>'
                f'<div class="err">Not saved: Altitude could not be reached. <a {U}>Retry</a></div></div>')
    if state == "dismissed":
        return f'<div class="sg dim"><span class="t">{n}. {title}</span><span class="meta">Dismissed · <a href="#">Undo</a></span></div>'
    if state == "lapsed":
        return f'<div class="sg dim"><span class="t">{n}. {title}</span><span class="meta">Not decided</span></div>'
    return f'<div class="sg">{head}{acts[state]}</div>'


LINE5 = "For “PR #810 merged” and 2 more"


def scard(states=("open", "open"), time="Today 10:19", footer=True):
    """One card per run with kept suggestions, in Needs you under Suggestions."""
    sgs = "".join(sug(n, st) for n, st in enumerate(states, 1))
    ft = '<div class="ft"><a href="#">Open the run</a></div>' if footer else ""
    return (f'<div class="scard"><div class="hd"><b>Review each merge</b><time>{time}</time></div>'
            f'<div class="win">{LINE5}</div>{sgs}{ft}</div>')


ASIDE = [(3, "Raise the CLI’s retry limit to match", "the CLI reads the uploader’s limit, so there is nothing to change")]
UNFINISHED = "L3 did not finish checking"
NONE_KEPT = [(1, SUGS[0][0], "#809 already adds this test"), (2, SUGS[1][0], "#214 already covers the docs")]


def aside(items, open_=False, first=False, why=None):
    """What L3 set aside, folded under the kept suggestions; open, each title with L3's reason. One reason for all
    (an unfinished check) is said once, on the fold."""
    chev = CHEVR.replace("chev", "")
    head = f'<div class="fold" style="padding:10px 0 0">{chev}Set aside · {len(items)}{f" · {why}" if why else ""}</div>'
    line = "margin-top:4px" if first else "border-top:1px solid var(--hairline);margin-top:12px"
    if not open_:
        return f'<div class="sg bare" style="{line};padding-top:0">{head}</div>'
    rows = "".join(f'<div class="sg dim" style="border-top:0;margin-top:6px;padding-top:0;flex-direction:column;align-items:flex-start;gap:2px">'
                   f'<span class="t">{n}. {t}</span>{f"<span class=meta>{r}</span>" if r else ""}</div>' for n, t, r in items)
    return f'<div class="sg bare" style="{line};padding-top:0">{head.replace("i sm", "i sm open")}{rows}</div>'


def suggested(states=("open", "open"), set_aside=ASIDE, open_=False, checking=0, why=None):
    """The run page's Suggested tasks card, under the report: L3's check, then what it kept and set aside."""
    if checking:
        body = (f'<div class="ok" style="margin-top:10px;color:var(--text-muted)"><span class="spin"></span>'
                f'L3 is checking {checking} suggestions</div>')
    else:
        body = "".join(sug(n, st) for n, st in enumerate(states, 1)) + (aside(set_aside, open_, not states, why) if set_aside else "")
    return f'<div class="scard" style="margin-top:12px"><div class="hd"><b>Suggested tasks</b></div>{body}</div>'


def run_with_suggestions():
    return ('<p class="muted" style="margin:0 0 4px;font-size:13.5px">For “PR #810 merged” and 2 more · Sonnet · 7 min · 4.4M tokens</p>'
            + EVENT_LINES + '<div class="card"><div class="runhead"><span class="d found"></span><b>Found something</b></div>'
              '<p style="margin:6px 0 0">#811 raised the retry limit in <code>uploader.py</code> from 3 to 5 without a test, and '
              '<code>docs/uploads.md</code> still says three.</p></div>' + suggested() + FOLDS)


# ---- chat ----
def sysa(name, text, kind="plain", link="Open"):
    dd = {"found": "dd found", "fail": "dd fail"}.get(kind, "dd")
    ln = f' <a href="#">{link}</a>' if link else ""
    return f'<div class="sysa"><span class="ln"></span><span class="t"><span class="{dd}"></span><b>{name}</b> {text}{ln}</span><span class="ln"></span></div>'


def offer(state="shown"):
    """L3's offer card: Altitude draws it from the stored definition; only the operator's press saves it."""
    btns = '<span class="btn primary">Create routine</span><span class="btn">Edit first</span>'
    foot = ""
    if state == "saving":
        btns = ('<span class="btn primary" style="min-width:112px" aria-label="Create routine, saving"><span class="spin"></span></span>'
                '<span class="btn" aria-disabled="true">Edit first</span>')
    elif state == "created":
        btns, foot = "", f'<p class="ok" style="margin:10px 0 0">Created · <a {U}>Open</a></p>'
    elif state == "refused":
        btns, foot = "", '<div class="err">Not saved: Dependency check changed since this offer.</div>'
    elif state == "lapsed":
        btns, foot = "", '<p class="muted" style="margin:10px 0 0;font-size:13px">This offer lapsed.</p>'
    row = f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">{btns}</div>' if btns else ""
    return ('<div class="acard"><small class="muted">New routine</small><b>Dependency check</b>'
            '<small class="muted">Weekdays at 07:00 · Tells you when it finds something · On when created</small>'
            f'<p class="clamp">{BRIEF}</p><a {U} style="font-size:13px">Show all</a>{row}{foot}</div>')


CHAT = ('<div class="day">Today</div>'
        '<div class="me">Can you set up a check every weekday morning for dependencies with security fixes? Only tell me when there is one.</div>'
        '<div class="l3"><p>Here it is. Say if you’d like it on a smaller model.</p>' + offer() + '</div>'
        + '<div class="day">Tomorrow</div>'
        + sysa("Dependency check", "found something: 2 packages have security fixes.", "found")
        + sysa("Review each merge", "found something for “PR #810 merged” and 2 more: #811 changed the retry limit without a test.", "found")
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
MON_ROWS = [("altitude / Weekly improvement review", "Next Monday at 08:00", "Last: 3 suggestions · 2 d ago · 4 min · 1.2M tokens"),
            ("harbor / Review each merge", PULSE + "Running", "Last: Found something · yesterday · 6 min · 3.9M tokens"),
            ("harbor / Dependency check", "Next tomorrow at 07:00", "Last: Nothing found · 2 h ago · 6 min · 4.1M tokens"),
            ("harbor / Upload timing", "Waiting", "Last: Couldn’t finish · 7 d ago · 1 min"),
            ("harbor / What landed", PAUSE + "Paused", "Last: Found something · 3 d ago · 9 min · 5.2M tokens")]


def mon_agents(rows=MON_ROWS, head=True):
    items = "".join(f'<a class="agl" href="#"><div class="tx"><div class="monitor-row"><b>{a}</b><span class="nx">{b}</span></div>'
                    f'<p class="muted" style="margin:0">{c}</p></div>{CHEVR}</a>' for a, b, c in rows)
    return (f'<h2>Routines ({len(rows)})</h2>' if head else '') + f'<div class="card" style="padding:4px 18px">{items}</div>'


def session_row(chip, title, meta, ago, meter=None, stale=False):
    bar = f'<div class="meter"{" style=opacity:.5" if stale else ""}><i style="width:{meter}%"></i></div>' if meter is not None else ''
    age = f'<span class="muted">{ago}</span>' if ago else ''
    return (f'<div class="sessionrow"><div class="monitor-row"><span class="chip">{chip}</span><b>{title}</b>{age}</div>'
            f'<p class="muted">{meta}</p>{bar}</div>')


def seat(name, week, five):
    return (f'<div class="card"><div class="monitor-row"><h3 style="margin:0">{name}</h3><span class="muted">reading 3m old</span></div>'
            f'<div class="monitor-row" style="margin-top:8px"><span>5-hour</span><span>{five}%</span></div><div class="meter"><i style="width:{five}%"></i></div>'
            f'<div class="monitor-row" style="margin-top:8px"><span>7-day</span><span>{week}%</span></div><div class="meter"><i style="width:{week}%"></i></div></div>')


UPDATE = '<h2>Altitude update</h2><p class="muted" style="margin:0 0 4px">No update pending.</p>'
SEATS = ('<h2>Seats</h2><p class="muted" style="margin:0 0 6px">Past the mark at 90%, or without a current reading, Altitude skips optional work such as routine runs and the Weekly review.</p>'
         '<div class="monseats">' + seat("Claude Code", 38, 12) + seat("Codex", 22, 4) + '</div>')
ROUTING = ('<h2>Routing now</h2><div class="card"><div class="monitor-row"><span>L3 · harbor · Auto</span><b style="flex:none">Claude Code</b></div>'
           '<p class="muted" style="margin:2px 0 10px">Most weekly headroom</p><div class="monitor-row"><span>L2 · new task</span><b style="flex:none">Claude Code</b></div>'
           '<p class="muted" style="margin:2px 0 0">Project harbor: most weekly headroom</p></div>')
AGENT_SESSION = session_row("Routine", "harbor / Review each merge", "Claude Code · Sonnet · context 6% · 4 of 20 min", "just now", 6)
SESSIONS = ('<h2>Sessions (3)</h2>' + session_row("L3", "harbor", "Claude Code · Fable · context 12%", "1 min ago", 12)
            + session_row("L2", "harbor / fix-upload-retry", "Claude Code · Opus · context 31% · edits 14", "2 min ago", 31)
            + AGENT_SESSION)
board("DesktopMonitor", 1440, 1640, desk('<h1>Monitor</h1>' + UPDATE + SEATS + ROUTING + SESSIONS + mon_agents(), h=1640, monitor=True))
board("PhoneMonitor", 390, 1720,
      '<div class="m" style="height:1720px"><div></div><header class="mh"><div class="brand" style="margin:0;padding:0 4px;height:40px"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div><span class="sp"></span></header>'
      '<div class="route-content" style="overflow:hidden;padding:4px 16px"><h1>Monitor</h1>' + UPDATE + SEATS + ROUTING + SESSIONS + mon_agents()
      + '</div><div></div>' + tabbar("monitor") + '</div>')


# ---- work: the project's task list ----
def trow(title, status, dot="dot"):
    return (f'<a class="trow" href="#"><span class="{dot}"></span><div style="flex:1">'
            f'<div class="tt">{title}</div><div class="tm">{status}</div></div>{CHEVR}</a>')


def work_rows():
    fold = f'<div class="fold">{CHEVR.replace("chev", "")}Done this week · 2</div>'
    agents = f'<a class="fold agentsrow" href="#">Routines · 4{CHEVR}</a>'
    return ('<div><div class="sh">Current <span>3</span></div>'
            + trow("Review each merge", "Routine run · “PR #812 merged” · 2 of 20 min")
            + trow("Add a test for the uploader’s retry limit", "Running · PR #811 green · from Review each merge")
            + trow("Upload timing", "Routine run · waiting for Review each merge", "dot q")
            + '</div>' + fold + agents)


board("DesktopWork", 1440, 900,
      '<div style="display:grid;grid-template-columns:260px minmax(0,1fr) 340px;height:900px">' + rail() +
      '<main style="display:grid;grid-template-rows:64px minmax(0,1fr) auto;min-height:0;background:var(--surface)">'
      '<div class="ph"><div><h1>harbor</h1><div class="sub">L3 answered 2 h ago on Claude · 1 task in flight</div></div></div>'
      '<div style="overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end;padding:0 28px">'
      '<div style="max-width:760px;width:100%;margin:0 auto;display:flex;flex-direction:column;gap:16px;padding-bottom:16px">'
      + sysa("Dependency check", "found something: 2 packages have security fixes.", "found")
      + sysa("Review each merge", "found something for “PR #809 merged”: the upload limit moved without its test.", "found")
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


# ---- sending events to a routine (step 4) ----
AGENT_ID = "3f9a2c1b0e4d"
SEND = f'alt --project {P} agent send {AGENT_ID} "what happened"'
PATHLINE = 'PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"'
RECIPES = [
    ("Each merged pull request: a script for cron every 10 minutes",
     f"""# crontab: */10 * * * * sh ~/bin/{P}-merges.sh
{PATHLINE}
for n in $(gh pr list -R acme/{P} --state merged --search sort:updated-desc -L 10 --json number -q '.[].number'); do
  alt --project {P} agent send {AGENT_ID} "PR #$n merged" --key "pr-$n" >/dev/null
done"""),
    ("Each new issue: a script for cron every 15 minutes",
     f"""# crontab: */15 * * * * sh ~/bin/{P}-issues.sh
{PATHLINE}
for n in $(gh issue list -R acme/{P} --state all -L 10 --json number -q '.[].number'); do
  alt --project {P} agent send {AGENT_ID} "Issue #$n opened" --key "issue-$n" >/dev/null
done"""),
    ("When a command of yours fails",
     f'make bench >/dev/null 2>&1 || alt --project {P} agent send {AGENT_ID} "Benchmarks failed on $(git rev-parse --short HEAD)"'),
]


def code(text, copy=True):
    import html
    btn = '<span class="btn sm">Copy</span>' if copy else ""
    return f'<div class="codeb"><code>{html.escape(text)}</code>{btn}</div>'


def recipe(what, cmd):
    return (f'<div class="rhead"><span class="muted">{what}</span><span class="btn sm">Copy</span></div>'
            f'<div class="codeb pre"><code>{__import__("html").escape(cmd)}</code></div>')


def send_section(examples=False):
    fold = f'<div class="fold" style="margin-top:8px">{(DOWN if examples else CHEVR.replace("chev", ""))}Examples</div>'
    body = "".join(recipe(*r) for r in RECIPES) if examples else ""
    return ('<div class="card"><p style="margin:0 0 2px;font-weight:600;font-size:14px">Send it an event</p>'
            '<p class="muted" style="margin:0 0 6px;font-size:13px">From a script or your terminal on this computer:</p>'
            f'{code(SEND)}'
            '<p class="muted" style="margin:6px 0 0;font-size:12.5px">To try it first, use <code>agent run</code> in place of <code>agent send</code>: it starts a run now, even before the routine is on.</p>'
            f'{fold}{body}'
            '<p class="muted" style="margin:10px 0 0;font-size:12.5px">Send numbers and your own words, not text anyone else can write: a run has your code and the network.</p></div>')


# ---- needs you: a routine that paused itself (step 4) ----
def needs_card(state="shown"):
    """Counted with the project's questions, never alerting a device; Resume presses the routine's resume change."""
    resume = {"resuming": '<span class="btn" style="min-width:86px" aria-label="Resume, saving"><span class="spin"></span></span>'}.get(state, '<span class="btn">Resume</span>')
    err = '<div class="err">Not resumed: Flaky tests is retired.</div>' if state == "refused" else ""
    return ('<div class="card ncard"><div class="nk"><span class="bad">Routine paused itself</span><span class="muted">2 h ago</span></div>'
            f'<a class="nt" href="#">Flaky tests{CHEVR}</a>'
            '<p>3 runs in a row couldn’t finish. The last: <code>make test-e2e</code> is not a target.</p>'
            f'<div class="nb">{resume}<a {U}>Open the last run</a></div>{err}</div>')


QUESTION = ('<div class="card ncard"><div class="nk"><span style="color:var(--accent-text)">The L2 asks</span><span class="muted">20 min ago</span></div>'
            '<a class="nt" href="#">Move uploads to the new bucket</a>'
            '<p>Keep the old bucket readable for a week after the move?</p>'
            '<div class="nb"><span class="btn primary">Keep it a week</span><span class="btn">Remove it at once</span></div></div>')


def needs_page():
    return ('<h1 style="margin-bottom:2px">Needs you</h1><p class="muted" style="margin:0 0 18px">1 question · 1 paused routine in harbor</p>'
            f'<h2 style="margin:0 0 10px">{P}</h2><div style="display:flex;flex-direction:column;gap:12px">{QUESTION}{needs_card()}</div>'
            f'<div class="nsec">Suggestions</div><h2 style="margin:0 0 10px">{P}</h2>{scard()}')


board("DesktopNeeds", 1440, 900, desk(needs_page(), needs=True, on=None))
board("PhoneNeeds", 390, 844, '<div class="m"><div></div><header class="mh"><div class="brand" style="margin:0;padding:0 4px;height:40px"><span class="mark">'
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div><span class="sp"></span></header>'
      f'<div class="route-content" style="overflow:hidden;padding:4px 16px">{needs_page()}</div><div></div>' + tabbar("needs") + '</div>')


# ---- states ----
def st(label, note, body):
    return f'<div class="st"><div class="lab">{label}<span>{note}</span></div>{body}</div>'


RETIRED_LIST = ('<h2 style="margin-top:0">Retired routines</h2><div class="card alist">'
                + arow("Issue labels", "Retired Oct 19 · 23 runs") + arow("Old digest", "Retired Sep 30 · 41 runs") + '</div>')
RETIRED_PAGE = ('<h1 style="margin:0 0 4px">Issue labels</h1><p class="muted" style="margin:0 0 4px">Retired Oct 19</p>'
                + runs(rows=RUNS[:2], more=False) + definition(edit=False))
CELLS = [
    ("1 · Section loading", "a spinner named Loading routines…", section(state="loading", top=False)),
    ("2 · Section read failed", "other settings stay usable", section(state="failed", top=False)),
    ("3 · No routines of your own yet", "the budget, New routine and every built-in routine", section(rows=[], top=False, built=BUILTIN_ALL)),
    ("4 · Budget saving", "the choice shows at once", section(rows=AGENTS[:1], bstate="saving", retired=False, top=False, built=None)),
    ("5 · Budget refused", "back to the saved value", section(rows=AGENTS[:1], bstate="refused", retired=False, top=False, built=None)),
    ("6 · Budget spent", "routines wait until Monday", section(rows=[AGENTS[3]], bstate="spent", retired=False, top=False, built=None)),
    ("7 · Run now starting", "held until the run is read; same width", status("starting")),
    ("8 · Run now refused", "Run now again, with its reason", status("refused")),
    ("9 · Waiting", "for a task slot, or for the project’s other routine run", status("waiting") + '<div style="height:10px"></div>' + status("waiting-agent")),
    ("10 · Running, then Stop pressed", "Open goes to the run; Pause lets this run finish", status("running") + '<div style="height:10px"></div>' + status("stopping")),
    ("11 · Paused", "Resume answers at once", status("paused")),
    ("12 · Pause refused", "the line and button return", status("pause-refused")),
    ("13 · Every kind of run", "skips have no page, so no chevron", runs(more=False)),
    ("14 · No runs yet", "", runs(rows=[], more=False)),
    ("15 · Edit saving", "Cancel waits", form(saving=True)),
    ("16 · Edit refused", "the edit stays in the fields", form(refused=True)),
    ("17 · New routine without a name", "pressed Create routine focuses the first empty field", form(new=True, nameless=True)),
    ("18 · Leaving with unsaved changes", "Keep editing has focus",
     dialog("Discard your changes?", "Your edits to Dependency check haven’t been saved.",
            f'<span class="btn"{FOCUS}>Keep editing</span><span class="btn redbtn">Discard</span>', static=True)),
    ("19 · Retire pending", "held until the routine reads as retired",
     dialog("Retire Dependency check?", RETIRE_TEXT,
            '<span class="btn" aria-disabled="true">Cancel</span><span class="btn redfill" aria-disabled="true"><span class="spin"></span>Retiring…</span>', static=True)),
    ("20 · Retire refused", "Cancel works",
     dialog("Retire Dependency check?", RETIRE_TEXT, f'<span class="btn"{FOCUS}>Cancel</span><span class="btn redfill">Retire</span>',
            '<div class="err">Not retired: the routine changed in another window.</div>', static=True)),
    ("21 · Retired routines", "from “2 retired” under the section", RETIRED_LIST),
    ("22 · A retired routine", "runs and what it did, read-only", RETIRED_PAGE),
    ("23 · Chat lines", "quiet system lines; never an alert or a Needs you count",
     '<div style="display:flex;flex-direction:column;gap:14px;padding:6px 0">'
     + sysa("Dependency check", "found something: 2 packages have security fixes.", "found")
     + sysa("Dependency check", "found nothing.")
     + sysa("Dependency check", "couldn’t finish: <code>make audit</code> is not a target.", "fail")
     + sysa("Dependency check", "stopped at its 20-minute limit.", "fail")
     + sysa("Dependency check", "stopped by you.")
     + sysa("Retired Dependency check", "", link="") + '</div>'),
    ("24 · Add a trigger", "the menu under the button; Escape returns focus to it",
     triggers("schedule", open_schedule=False) + '<div style="margin:-8px 0 0 12px;position:relative">' + trigger_menu() + '</div>'),
    ("25 · No trigger", "the routine runs only when you press Run now", triggers(empty=True)),
    ("26 · One run for several events", "events during a run wait and go together",
     runs(rows=[("found", "Today 10:12", "Found something", "7 min · 4.4M tokens", "For “PR #810 merged” and 2 more: #811 changed the retry limit without a test."),
                ("", "Today 09:40", "Nothing found", "4 min · 2.1M tokens", "For “PR #809 merged”: every change has a test.")], more=False)),
    ("27 · Paused after repeated failures", "three runs in a row couldn’t finish",
     status("self-paused") + '<div style="height:12px"></div>'
     + sysa("Dependency check", "paused itself: 3 runs in a row couldn’t finish.", "fail", link="Open")),
    ("28 · A late run, a run that waited too long", "one catch-up after Altitude was off; a wait ends at six hours",
     runs(rows=[("", "Oct 13, 07:00, ran at 09:14", "Nothing found", "5 min · 3.4M tokens", "Every dependency is current."),
                ("skip", "Oct 12, 07:00", "Skipped", "", "No free slot for 6 hours.")], more=False)),
    ("29 · A new routine", "saved paused; try it, then turn it on", status("new")),
    ("30 · A schedule with no day", "Save waits for a day", triggers(noday=True)),
    ("31 · Offer in chat", "drawn by Altitude from the stored definition; only your press saves it", offer()),
    ("32 · Offer saving, then created", "the card stays where it was", offer("saving") + '<div style="height:12px"></div>' + offer("created")),
    ("33 · Offer refused, offer lapsed", "no buttons once it cannot be saved", offer("refused") + '<div style="height:12px"></div>' + offer("lapsed")),
    ("34 · Run page loading, read failed", "", run_page("loading") + '<div style="height:12px"></div>' + run_page("failed")),
    ("35 · A run that couldn’t finish", "its reason in the report’s place", run_page("fail")),
    ("36 · The project menu", "Routines… is always there, after Project settings…",
     '<div class="menu-mock" role="menu" style="position:static"><div class="hi">Project settings…</div><div>Routines…</div>'
     '<div>Setup… <span class="muted">Ready</span></div><div>Reset L3 conversation…</div><div>All settings…</div></div>'),
    ("37 · Turning a built-in routine on", "it joins your routines, on, with its schedule", builtin(state="saving")),
    ("38 · Turn on refused", "the button returns with the reason", builtin(state="refused")),
    ("39 · Altitude’s own project", "the Weekly review is built in there (step 2); its Turn on opens its terms first",
     builtin(rows=[WEEKLY_ROW] + BUILTIN_ALL[:2])),
    ("40 · Weekly: its terms", "centered at 1440, Cancel focused; at 390 the bottom sheet of PhoneWeeklyTerms",
     dialog("Turn on the Weekly improvement review for altitude?", SHEET_TEXT,
            f'<span class="btn"{FOCUS}>Cancel</span><span class="btn primary">Turn on</span>', static=True)),
    ("41 · Weekly: turning on, refused", "the spinner until the list reads it as yours; stale terms return the button",
     '<div class="card alist">' + brow(*WEEKLY_ROW, state="saving") + '</div><div style="height:10px"></div>'
     + '<div class="card alist">' + brow(*WEEKLY_ROW, state="refused", refused="the terms changed while the sheet was open; read them again.") + '</div>'),
    ("42 · Weekly: yours", "first among your routines; its runs don’t count toward the budget",
     '<div class="card alist">' + arow("Weekly improvement review", "Mondays at 08:00 · 3 suggestions 2 d ago")
     + arow("Weekly improvement review", PULSE + "Mondays at 08:00 · Reading 6 tasks")
     + arow("Weekly improvement review", PAUSE + "Mondays at 08:00 · Paused: its terms changed") + '</div>'),
    ("43 · Weekly page: loading, read failed", "",
     '<div class="card"><span class="spin" role="status" aria-label="Loading Weekly improvement review…"></span></div><div style="height:10px"></div>'
     '<div class="card"><p style="margin:0 0 10px">Could not load the Weekly improvement review.</p><span class="btn">Retry</span></div>'),
    ("44 · Weekly: too few tasks, then Run now pressed", "the reason shows under the card on press; it is also Run now’s description",
     weekly_status("few") + '<div style="height:10px"></div>' + weekly_status("few-pressed")),
    ("45 · Weekly: ready, starting, reserve", "Run now’s line, or its reason when it can’t run",
     weekly_status("ready") + '<div style="height:10px"></div>' + weekly_status("starting")
     + '<div style="height:10px"></div>' + weekly_status("reserve")),
    ("46 · Weekly: Run now refused, Pause refused", "the buttons return; the reason in red",
     weekly_status("run-refused") + '<div style="height:10px"></div>' + weekly_status("pause-refused")),
    ("47 · Weekly: running, Stop pressed, Stop failed", "only Stop while it runs; Pause returns when it ends",
     weekly_status("reading") + '<div style="height:10px"></div>' + weekly_status("checking")
     + '<div style="height:10px"></div>' + weekly_status("stopping") + '<div style="height:10px"></div>' + weekly_status("stop-failed")),
    ("48 · Weekly: paused, terms out of date", "whenever its agreement is out of date, Resume and Run now open the terms first",
     weekly_status("paused") + '<div style="height:10px"></div>' + weekly_status("terms")),
    ("49 · Weekly: every kind of run", "each opens its card or line in chat; a skip has none",
     runs(rows=WEEKLY_ALL, more=False)),
    ("50 · Weekly: no runs yet", "the totals line appears after the first ended run", runs(rows=[], more=False)),
    ("51 · Weekly: terms changed, in chat", "a quiet line; Open goes to its page",
     sysa("Weekly improvement review", "paused: its terms changed.")),
    ("52 · Monitor: loading, read failed", "both sections share Monitor’s one read (step 3)",
     '<div class="skel" style="height:72px"></div><div style="height:10px"></div>'
     '<p style="margin:0;color:var(--danger)">Could not read the monitor. <a class="u" href="#">Retry</a></p>'),
    ("53 · Monitor: a routine’s session", "a run with a reading; before its first reading; a reading gone stale",
     AGENT_SESSION
     + session_row("Routine", "atlas / Release notes", "Claude Code · Opus · context unknown · 0 of 30 min", "")
     + session_row("Routine", "harbor / Dependency check", "Claude Code · Sonnet · context 41% · 18 of 20 min", "6 min ago", 41, stale=True)),
    ("54 · Monitor: what comes next", "the first that applies, in the routine page’s order; Last: is the latest run that ran",
     mon_agents([("harbor / Review each merge", PULSE + "Running", "No runs yet."),
                 ("harbor / Upload timing", "Waiting", "Last: Nothing found · 2 h ago · 5 min · 2.8M tokens"),
                 ("atlas / Docs drift", "Not on yet", "Last: Found something · 2 d ago · 7 min · 3.1M tokens"),
                 ("harbor / Flaky tests", PAUSE + "Paused itself", "Last: Couldn’t finish · yesterday · 1 min"),
                 ("harbor / What landed", PAUSE + "Paused", "Last: Found something · 3 d ago · 9 min · 5.2M tokens"),
                 ("altitude / Weekly improvement review", PAUSE + "Paused: its terms changed", "Last: Nothing to suggest · 6 d ago · 3 min"),
                 ("atlas / Release notes", "Waits until Monday", "Last: Found something · 4 d ago · 12 min · 6.0M tokens"),
                 ("harbor / Dependency check", "Next tomorrow at 07:00 Istanbul", "Last: Stopped at its limit · 2 h ago · 20 min · 9.4M tokens"),
                 ("harbor / Issue triage", "When it’s sent an event", "Last: Found something · 1 d ago · 8 min · 3.0M tokens"),
                 ("atlas / Licence audit", "Only when you run it", "Last: Nothing found · 6 d ago · 3 min · 0.9M tokens")],
               head=False)),
    ("55 · Monitor: no routines", "no Routines section and no heading; Sessions ends the page",
     '<h2 style="margin-top:0">Sessions (1)</h2>' + session_row("L3", "harbor", "Claude Code · Fable · context 12%", "1 min ago", 12)),
    ("56 · The event trigger", "its row says where events come from; the hint follows the list (step 4)",
     '<div>' + triggers("both", open_schedule=False) + EVHINT + '</div>'),
    ("57 · Event trigger added", "+ Add a trigger now adds a schedule directly, with no menu",
     triggers("both", open_schedule=True)),
    ("58 · Events only; none yet; events waiting", "the status names the trigger; events during a run wait for the next one",
     status("events") + '<div style="height:10px"></div>' + status("events-none") + '<div style="height:10px"></div>' + status("running-pending")),
    ("59 · Event runs", "a replay of the latest event; a skip names its events",
     runs(rows=[("", "Today 11:02", "Nothing found", "3 min · 1.1M tokens", "For “PR #812 merged”: every change has a test."),
                ("found", "Today 10:12", "Found something", "7 min · 4.4M tokens", "For “PR #810 merged” and 2 more: #811 changed the retry limit without a test."),
                ("skip", "Oct 18, 09:30", "Skipped", "", "For “PR #806 merged” · No free slot for 6 hours.")], more=False)),
    ("60 · A run for several events", "each event on its own muted line under what fired it", run_page("events")),
    ("61 · Run now on an event routine", "it replays the latest event; with none yet it runs without one",
     run_page("replay") + '<div style="height:12px"></div>' + run_page("no-event")),
    ("64 · Send it an event", "on the routine page, in What it does; Examples folded", send_section()),
    ("65 · Send it an event: examples", "three recipes to copy; the routine’s id and repository filled in", send_section(examples=True)),
    ("62 · Needs you: paused itself", "counted with the questions, never alerting a device", needs_card()),
    ("63 · Needs you: resuming, refused", "the card leaves once the routine reads as on", needs_card("resuming") + '<div style="height:12px"></div>' + needs_card("refused")),
    ("66 · The run page while L3 checks; none kept; check unfinished", "nothing in Needs you until L3 posts or two hours pass; what it sets aside rests folded here, with its reason",
     suggested(checking=3) + suggested((), set_aside=NONE_KEPT) + suggested((), set_aside=NONE_KEPT, open_=True)
     + suggested((), set_aside=[(n, SUGS[n - 1][0], None) for n in (1, 2)], why=UNFINISHED)
     + suggested((), set_aside=[(n, SUGS[n - 1][0], None) for n in (1, 2)], open_=True, why=UNFINISHED)),
    ("67 · Needs you: Suggestions", "once L3 has kept them; after what needs you, uncounted, never alerting; shown whatever Tell me says; at most three waiting per routine",
     f'<div class="nsec" style="margin-top:0">Suggestions</div><h2 style="margin:0 0 10px">{P}</h2>' + scard()),
    ("68 · Make a task pressed, then accepted", "a spinner in place, same width; then the words hold until L3’s outcome is read",
     scard(("pressed", "open")) + '<div style="height:12px"></div>' + scard(("accepted", "open"))),
    ("69 · Task opened", "the line becomes the task’s card", scard(("task", "open"))),
    ("70 · No task yet: Try again, pressed, refused", "the same single turn once more",
     scard(("notask", "open")) + '<div style="height:12px"></div>' + scard(("retrying", "open"))
     + '<div style="height:12px"></div>' + scard(("retry-refused", "open"))),
    ("71 · Dismiss pressed, dismissed, not saved", "folds at the press; an unsaved press comes back with Retry; one refused because it changed elsewhere shows where it now stands",
     scard(("dismissing", "open")) + '<div style="height:12px"></div>' + scard(("dismissed", "open"))
     + '<div style="height:12px"></div>' + scard(("dismiss-refused", "open"))),
    ("72 · Undo pressed; not decided; Make a task not saved", "Undo until it lapses seven days after L3 kept it",
     scard(("undoing", "open")) + '<div style="height:12px"></div>' + scard(("lapsed", "lapsed"))
     + '<div style="height:12px"></div>' + scard(("refused", "open"))),
    ("73 · The run page", "Suggested tasks under the report: what L3 kept, with the same buttons and states, then what it set aside, folded",
     run_with_suggestions() + suggested(open_=True)),
    ("74 · The form’s Tell me", "one muted line under the choices", f'<label class="flab" style="margin-top:0">Tell me in chat</label>{seg()}<span class="hint">Suggested tasks wait in Needs you whatever you choose.</span>'),
    ("75 · Work: a task from a suggestion", "its card’s muted line names the routine; the Weekly review’s read “from Weekly improvement review”",
     '<div class="work" style="position:static;padding:0">' + trow("Add a test for the uploader’s retry limit", "Running · PR #811 green · from Review each merge")
     + trow("Stop re-asking merge approval after a rebase", "Done · from Weekly improvement review", "dot ok") + '</div>'),
]
board("States", 1200, 21410, '<div style="padding:36px 40px 14px"><h1 style="font-size:20px;font-weight:600;margin:0">Routines: states at 1440</h1>'
      '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:900px">Every state of the routines page, the routine page, the form, the dialogs and chat, as PROPOSAL.md specifies them.</p></div>'
      '<div class="sheetgrid">' + "".join(st(*c) for c in CELLS) + '</div>')
board("PhoneStates", 1720, 15440, '<div style="padding:36px 40px 14px"><h1 style="font-size:20px;font-weight:600;margin:0">Routines: states at 390</h1>'
      '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px">The same states at phone width: stacked choices, 44px targets, full-width cards.</p></div>'
      '<div class="sheetgrid phonesheet">' + "".join(st(*c) for c in CELLS) + '</div>')
board("PhoneWeeklyTerms", 390, 844, phone(weekly_page(), back="altitude", title="Weekly improvement review",
      overlay='<div class="scrim"></div><div class="dlg" role="dialog" style="top:auto;bottom:0;left:0;right:0;border-radius:16px 16px 0 0;padding-bottom:28px">'
              f'<h2>Turn on the Weekly improvement review for altitude?</h2><p>{SHEET_TEXT}</p>'
              f'<div class="bt"><span class="btn primary">Turn on</span><span class="btn"{FOCUS}>Cancel</span></div></div>'))
# ---- acting (ACTING.md, increment 1: the fence and Worktrees and branches) ----
BOX = '<span class="cb{}" role="checkbox" aria-checked="{}">{}</span>'
TICK = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>'
MAY_LINE = ("Sets aside worktrees and deletes branches nothing needs: no uncommitted change, nothing changed in seven "
            "days, every commit kept on a branch or merged, and nothing open in them. Each has Undo on the run’s page.")


def may(ticked=False):
    box = BOX.format(" on" if ticked else "", str(ticked).lower(), TICK if ticked else "")
    none = "" if ticked else '<span class="hint">Unticked, it only reports.</span>'
    return (f'<label class="flab">What it may change</label><div class="trig may"><label class="tr">{box}'
            f'<span class="tx"><b>Worktrees and branches</b><small>{MAY_LINE}</small></span></label></div>{none}')


TIDY = "Tidy worktrees and branches"
TIDY_BRIEF = ("Go through the worktrees and branches Altitude lists below and clear the ones nothing needs. Keep anything "
              "that belongs to work still under way. Say what you cleared and what you kept, and why.")


def act_form(ticked=True, saving=False, refused=False):
    html = form(name=TIDY, brief=TIDY_BRIEF, saving=saving, refused=refused).replace("Weekdays at 07:00", "Mondays at 09:00")
    return html.replace('<label class="flab">Runs when</label>', may(ticked) + '<label class="flab">Runs when</label>', 1)


FENCE_ON = '<p style="margin:6px 0 0;font-size:14px">May change worktrees and branches, with Undo</p>'
FENCE_OFF = '<p class="muted" style="margin:6px 0 0;font-size:14px">Only reports</p>'


def act_definition(fence=FENCE_ON):
    return ('<h2 style="margin-top:24px">What it does</h2><div class="card">'
            f'<p class="clamp" style="margin:0">{TIDY_BRIEF}</p><p style="margin:4px 0 0"><a {U}>Show all</a></p>'
            '<p class="muted" style="margin:10px 0 0">Mondays at 09:00 · Tells you when it finds something · Default model · Stops after 20 min</p>'
            f'{fence}<div style="margin-top:12px"><span class="btn">Edit</span></div></div>')


SUMMARY = "2 worktrees and 2 branches nothing needs."
DONE = "Set aside 2 worktrees and deleted 2 branches."
ACT_RUNS = [
    ("found", "Today 09:00", "Found something", "4 min · 1.9M tokens", f"{SUMMARY} {DONE}"),
    ("", "Oct 19, 09:00", "Nothing found", "2 min · 0.8M tokens", "Every worktree and branch is in use or has work in it."),
]


def act_agent_page():
    return status("idle").replace("tomorrow at 07:00", "Monday at 09:00") + runs(ACT_RUNS, more=False) + act_definition() + RETIRE


UNDO = '<span class="btn sm">Undo</span>'
UNDOING = '<span class="btn sm" aria-label="Undo, saving"><span class="spin"></span></span>'


def change(line, note, ctrl="", tone=""):
    return f'<div class="chg"><div class="tx"><b>{line}</b><small{tone}>{note}</small></div>{ctrl}</div>'


ASIDE1, ASIDE2 = "Set aside worktree agent-7c41e2d0", "Set aside worktree agent-91d0c3f2"
FOR_GOOD = "Removed for good Nov 9 unless you undo it."
CHANGES = {
    "worktree": change(ASIDE1, FOR_GOOD, UNDO),
    "worktree2": change(ASIDE2, FOR_GOOD, UNDO),
    "branch": change("Deleted branch upload-retry-fix", "Merged by #214.", UNDO),
    "branch-main": change("Deleted branch docs-typo-fix", "Merged into main.", UNDO),
    "gone": change(ASIDE1, "Removed for good Nov 9. Undo restores it from its branch, without ignored files.", UNDO),
    "kept": change("Kept worktree add-offline-sync-f…-the-mobile-app", "It has uncommitted changes."),
    "kept-recent": change("Kept worktree agent-3e90b1c5a7d2", "It changed 2 days ago."),
    "kept-task": change("Kept worktree add-a-billing-export-for-team-acc…", "Task “Add a billing export” is still open."),
    "kept-open": change("Kept worktree agent-5b2e8f4c1d09", "A program has it open."),
    "kept-loose": change("Kept branch retry-limit-tests", "It has 2 commits found nowhere else."),
    "kept-aside": change(ASIDE1, "Still set aside: it contains modified or untracked files, use --force to delete it"),
    "kept-undone": change("Kept branch docs-typo-fix", "You put it back Oct 19."),
    "denied": change("Not allowed: delete branch upload-retry-fix", "This routine only reports."),
    "cap": change("Not allowed: set aside worktree agent-91d0c3f27a6e", "This run used its 20 requests."),
    "failed": change("Couldn’t set aside worktree agent-5b2e8f4c1d09", "git: cannot move a locked working tree"),
    "undoing": change(ASIDE1, FOR_GOOD, UNDOING),
    "undoing2": change(ASIDE2, FOR_GOOD, UNDOING),
    "undone": change(ASIDE1 + " · Undone", "Put back Oct 26. This routine leaves it alone from now on."),
    "undo-refused": change(ASIDE1, "Not undone: a worktree is already at that path.", UNDO, ' style="color:var(--danger)"'),
}


def changed(keys, undo_all=None):
    head = '<h2 style="margin-top:20px">What it changed</h2>'
    if undo_all:
        btn = UNDOING.replace("Undo, saving", "Undo all, saving") if undo_all == "pressed" else '<span class="btn sm">Undo all</span>'
        head = f'<div class="chghead"><h2>What it changed</h2>{btn}</div>'
    return head + '<div class="card chgs">' + "".join(CHANGES[k] for k in keys) + '</div>'


ACT_REPORT = ('<p>Of the 7 worktrees under <code>.claude/worktrees</code>, 2 belong to finished work and had nothing in them; '
              'two merged branches were checked out nowhere.</p>'
              '<p>Kept <code>add-offline-sync-for-the-mobile-app</code>: it has uncommitted changes to <code>sync.py</code>.</p>')
ENDED = ["worktree", "worktree2", "branch", "branch-main", "kept"]


def act_run_page(state="ended"):
    meta = '<p class="muted" style="margin:0 0 16px;font-size:13.5px">On its schedule · Sonnet{}</p>'
    if state == "running":
        return (meta.format("") + '<div class="card"><div class="status"><span class="tx"><span class="spin"></span>Running · 3 of 20 min</span>'
                '<span class="btns"><span class="btn">Stop</span></span></div></div>' + changed(["worktree", "kept"])
                + f'<div class="card" style="margin-top:12px">{SESSION}</div>')
    return (meta.format(" · 4 min · 1.9M tokens")
            + f'<div class="card"><div class="runhead"><span class="d found"></span><b>Found something</b></div><div class="report">{ACT_REPORT}</div></div>'
            + changed(ENDED, undo_all=True) + FOLDS)


TIDY_HEAD = f'<a class="btn setting-back" href="#">{CHEVL}{P}</a><h1 style="margin-bottom:16px">{TIDY}</h1>'
TIDY_RUN_HEAD = f'<a class="btn setting-back" href="#">{CHEVL}{TIDY}</a><h1 style="margin-bottom:4px">Run · Today 09:00</h1>'
board("DesktopActEdit", 1440, 1120, desk(f'<a class="btn setting-back" href="#">{CHEVL}{TIDY}</a><h1>Edit</h1>' + act_form(), h=1120))
board("PhoneActEdit", 390, 1320, phone(act_form(), back=TIDY, title="Edit", h=1320))
board("DesktopActAgent", 1440, 1000, desk(TIDY_HEAD + act_agent_page(), h=1000))
board("PhoneActAgent", 390, 1250, phone(act_agent_page(), back=P, title=TIDY, h=1250))
board("DesktopActRun", 1440, 1000, desk(TIDY_RUN_HEAD + act_run_page(), h=1000))
board("PhoneActRun", 390, 1250, phone(act_run_page(), back="Tidy worktrees…", title="Run · Today 09:00", h=1250))

TIDY_ROW = (TIDY, "Mondays at 09:00 · Clears worktrees and branches nothing needs, with Undo")
TIDY_SHEET = ("Mondays at 09:00 it sets aside worktrees and deletes local branches of harbor that nothing needs: no "
              "uncommitted change, nothing changed in seven days, every commit kept on a branch or merged, and nothing open "
              "in them. Each is listed on its run's page with Undo. It never touches main, open tasks or GitHub.")
ACT_AGENTS = [(TIDY, "Mondays at 09:00 · Changes worktrees and branches · Found something 2 h ago")] + AGENTS


def tidy_sheet(static=False):
    style = ('position:static;box-shadow:none;border:1px solid var(--hairline)' if static
             else 'top:auto;bottom:0;left:0;right:0;border-radius:16px 16px 0 0;padding-bottom:28px')
    return (f'<div class="dlg" role="dialog" style="{style}"><h2>Turn on {TIDY} for {P}?</h2><p>{TIDY_SHEET}</p>'
            f'<div class="bt"><span class="btn primary">Turn on</span><span class="btn"{FOCUS}>Cancel</span></div></div>')


board("PhoneActTurnOn", 390, 1000, phone(section(top=False, built=[TIDY_ROW] + BUILTIN), h=1000,
                                         overlay='<div class="scrim"></div>' + tidy_sheet()))


def act_offer(line):
    return ('<div class="acard"><small class="muted">Change to ' + TIDY + f'</small><b>{TIDY}</b>'
            f'<span style="font-size:14px">{line}</span>'
            '<div style="display:flex;gap:8px;margin-top:10px"><span class="btn primary">Save change</span><span class="btn">Edit first</span></div></div>')


ACT_CELLS = [
    ("A1 · Form: unticked", "a routine that only reports; the muted line under the list",
     '<div class="card" style="padding-top:0">' + may(False) + '</div>'),
    ("A2 · Form: ticked", "the row’s own line alone", '<div class="card" style="padding-top:0">' + may(True) + '</div>'),
    ("A3 · Form: saving / refused", "Save’s spinner; refused keeps the tick with its reason and Retry",
     '<div class="card" style="padding-top:0">' + may(True)
     + '<div style="display:flex;gap:8px;margin-top:20px"><span class="btn primary" style="min-width:72px" aria-label="Save, saving"><span class="spin"></span></span>'
       '<span class="btn ghost" aria-disabled="true">Cancel</span></div>'
     + '<div class="err">Not saved: the routine changed in another window. <a class="u" href="#">Retry</a></div></div>'),
    ("A4 · Routines list", "a routine that may change things names it after its triggers; one that only reports reads as today",
     '<div class="card alist">' + "".join(arow(*a) for a in ACT_AGENTS[:3]) + '</div>'),
    ("A5 · Routine page: may change", "the line after the definition, primary tone", act_definition()),
    ("A6 · Routine page: only reports", "muted", act_definition(FENCE_OFF)),
    ("A7 · Built in: the row", "Turn on opens the sheet", '<div class="card alist">' + brow(*TIDY_ROW) + '</div>'),
    ("A8 · Built in: the sheet", "Cancel focused; Escape and the scrim change nothing; at 390 a bottom sheet", tidy_sheet(static=True)),
    ("A9 · Built in: turning on / refused", "the sheet closes and the row’s Turn on spins, as the Weekly review’s does; refused, the reason under the row",
     '<div class="card alist">' + brow(*TIDY_ROW, state="saving") + '</div><div class="card alist" style="margin-top:12px">'
     + brow(*TIDY_ROW, state="refused") + '</div>'),
    ("A10 · Run page: running with changes", "the card above the live session, growing as the run asks", changed(["worktree", "kept"])),
    ("A11 · Changes: done, with Undo all", "worktrees set aside, a branch merged by a pull request, one merged into main",
     changed(["worktree", "worktree2", "branch", "branch-main"], undo_all=True)),
    ("A12 · Changes: kept", "each reason Altitude’s own check gives",
     changed(["kept", "kept-recent", "kept-task", "kept-open", "kept-loose", "kept-undone"])),
    ("A13 · Changes: not allowed / failed / removed for good", "without the permission; past the run’s 20 requests; Git refused; after 14 days; Git refused the removal",
     changed(["denied", "cap", "failed", "gone", "kept-aside"])),
    ("A14 · Undo: pressed / done / refused", "a spinner in Undo; Undone with the day and that it sticks; the reason in the danger tone and Undo again",
     changed(["undoing", "undone", "undo-refused"])),
    ("A15 · Undo all pressed", "every row that can be undone shows its spinner, then its own outcome",
     changed(["undoing", "undoing2"], undo_all="pressed")),
    ("A16 · Chat: a run that changed things", "Altitude’s sentence follows the run’s summary",
     sysa(TIDY, f"found something: {SUMMARY} {DONE}", "found")),
    ("A17 · Offers", "a change to the permission is its own line in the primary tone",
     act_offer("May now change worktrees and branches") + '<div style="height:10px"></div>'
     + act_offer("No longer changes worktrees and branches")),
]
board("ActStates", 1200, 4300, '<div style="padding:36px 40px 14px"><h1 style="font-size:20px;font-weight:600;margin:0">Routines that act: states at 1440</h1>'
      '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:900px">Increment 1 of ACTING.md: the fence on the form, '
      'the list and the routine page, the built-in and its sheet, a run’s changes with Undo, chat and offers.</p></div>'
      '<div class="sheetgrid">' + "".join(st(*c) for c in ACT_CELLS) + '</div>')
board("PhoneActStates", 1720, 3200, '<div style="padding:36px 40px 14px"><h1 style="font-size:20px;font-weight:600;margin:0">Routines that act: states at 390</h1>'
      '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px">The same states at phone width: 44px targets, rows that wrap.</p></div>'
      '<div class="sheetgrid phonesheet">' + "".join(st(*c) for c in ACT_CELLS) + '</div>')

# ---- commands you list (COMMANDS.md, the step after increment 1) ----
CLEAN = "Clean up the machine"
CLEAN_BRIEF = ("Check the machine's disk and memory. When container images take more than 20 GB, prune them; when the "
               "build cache is over 5 GB, clear it. Post a short report of what you found and did.")
CMDS = [("prune-images", "podman image prune --all --force --filter until=168h"),
        ("post-report", "curl -fsS -X POST --data-binary @- https://hooks.example.com/harbor-team", True)]
NAME_HINT = "Lowercase letters, digits and dashes; the routine asks for it by this name."
CMD_HINT = f"Runs as you in {P}’s folder for up to 10 minutes, whenever the routine decides."
TAKES_HINT = "It reads a message from the routine on its input. Never tick it for a command that runs what it reads."
REMOVE = '<span class="x" aria-label="Remove {}"><svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></span>'


def cmd_row(name, text, takes=False):
    note = '<span class="takes">Takes the routine’s text</span>' if takes else ""
    return (f'<div class="tr cmd"><span class="tx"><b>{name}</b><code>{text}</code>{note}</span>{REMOVE.format(name)}</div>')


def cmd_open(name="", text="", errors=()):
    nm = name or '<span style="color:var(--text-muted)">For example: prune-images</span>'
    tx = text or '<span style="color:var(--text-muted)">For example: podman image prune --all --force</span>'
    e = dict(errors)
    return ('<div class="tr open">'
            f'<label class="flab" style="margin-top:0">Name</label><div class="field fld">{nm}</div>'
            + (f'<div class="err">{e["name"]}</div>' if "name" in e else f'<span class="hint">{NAME_HINT}</span>')
            + f'<label class="flab">Command</label><div class="field ta mono">{tx}</div>'
            + (f'<div class="err">{e["text"]}</div>' if "text" in e else f'<span class="hint">{CMD_HINT}</span>')
            + '<div class="may" style="margin-top:12px"><label class="tr" style="padding:0">' + BOX.format("", "false", "")
            + f'<span class="tx"><b>Takes the routine’s text</b><small>{TAKES_HINT}</small></span></label></div>'
            + '</div>')


def commands(rows=CMDS, opened=None, full=False):
    body = "".join(cmd_row(*r) for r in rows) + (opened or "")
    add = "" if full else '<div class="add"><span class="btn sm">Add a command</span></div>'
    return f'<label class="flab">Commands</label><div class="trig">{body}{add}</div>'


def cmd_form(rows=CMDS, opened=None, ticked=False):
    html = form(name=CLEAN, brief=CLEAN_BRIEF).replace("Weekdays at 07:00", "Daily at 06:00")
    return html.replace('<label class="flab">Runs when</label>',
                        may(ticked) + commands(rows, opened) + '<label class="flab">Runs when</label>', 1)


def output(text):
    return (f'<div class="fold" style="margin-top:6px">{CHEVR.replace("chev", "open")}Output</div>'
            f'<pre class="out">{text}</pre>')


OUT_PRUNE = "Deleted Images\n3f1c2a9e0b7d…\n91aa04c6e2f1…\nTotal reclaimed space: 23.4GB"
CMD_CHANGES = {
    "ran": change("Ran prune-images", "Finished in 12 s." + output(OUT_PRUNE)),
    "ran-closed": change("Ran prune-images", "Finished in 12 s." + f'<span class="fold" style="margin-top:6px">{CHEVR.replace("chev", "")}Output</span>'),
    "posted": change("Ran post-report", "Finished in 1 s."),
    "failed": change("Ran post-report", '<span style="color:var(--danger)">Exited with 1 after 3 s.</span>'
                     + output("curl: (22) The requested URL returned error: 403")),
    "timeout": change("Ran prune-images", '<span style="color:var(--danger)">Stopped after 10 min.</span>'
                      + f'<span class="fold" style="margin-top:6px">{CHEVR.replace("chev", "")}Output</span>'),
    "running": change("Running prune-images", "", '<span class="spin"></span>'),
    "nostart": change("Couldn’t run prune-images", "Altitude could not start it, so nothing ran."),
    "unknown": change("Not allowed: run clear-cache", "This routine has no command by that name."),
    "changed": change("Not allowed: run prune-images", "This command changed after the run started."),
    "notext": change("Not allowed: run prune-images", "This command does not take the routine’s text."),
    "cap": change("Not allowed: run post-report", "This run used its 20 requests."),
}


def cmd_changed(keys):
    return '<h2 style="margin-top:20px">What it changed</h2><div class="card chgs">' + "".join(CMD_CHANGES[k] for k in keys) + '</div>'


CMD_REPORT = ('<p>Container images took 31 GB, so I pruned images older than a week: 23.4 GB back. The build cache was '
              '1.2 GB, under the limit.</p><p>Posted this report to the team channel.</p>')


def cmd_run_page():
    return ('<p class="muted" style="margin:0 0 16px;font-size:13.5px">On its schedule · Sonnet · 2 min · 0.6M tokens</p>'
            f'<div class="card"><div class="runhead"><span class="d found"></span><b>Found something</b></div><div class="report">{CMD_REPORT}</div></div>'
            + cmd_changed(["ran", "posted"]) + FOLDS)


CLEAN_RUN_HEAD = f'<a class="btn setting-back" href="#">{CHEVL}{CLEAN}</a><h1 style="margin-bottom:4px">Run · Today 06:00</h1>'
board("DesktopCmdEdit", 1440, 1240, desk(f'<a class="btn setting-back" href="#">{CHEVL}{CLEAN}</a><h1>Edit</h1>' + cmd_form(), h=1240))
board("PhoneCmdEdit", 390, 1560, phone(cmd_form(), back=CLEAN, title="Edit", h=1560))
board("DesktopCmdRun", 1440, 900, desk(CLEAN_RUN_HEAD + cmd_run_page(), h=900))
board("PhoneCmdRun", 390, 1100, phone(cmd_run_page(), back="Clean up the m…", title="Run · Today 06:00", h=1100))

CMD_FENCE = ('<p style="margin:6px 0 0;font-size:14px">May run prune-images and post-report</p>')
CMD_CELLS = [
    ("C1 · Form: no commands", "only Add a command",
     '<div class="card" style="padding-top:0">' + commands(rows=[]) + '</div>'),
    ("C2 · Form: two commands", "name, its text wrapping in full, Remove; which takes the routine’s text", '<div class="card" style="padding-top:0">' + commands() + '</div>'),
    ("C3 · Form: adding", "Add a command opens two fields and the tick, unticked",
     '<div class="card" style="padding-top:0">' + commands(CMDS[:1], cmd_open()) + '</div>'),
    ("C4 · Form: errors on Save", "under their fields",
     '<div class="card" style="padding-top:0">' + commands(CMDS[:1], cmd_open("Prune images", "", (("name", "Use lowercase letters, digits and dashes."), ("text", "Write the command.")))) + '</div>'),
    ("C5 · Form: ten commands", "Add a command goes",
     '<div class="card" style="padding-top:0">' + commands([(f"step-{i}", "./scripts/step.sh") for i in range(1, 4)], full=True).replace('<div class="tr cmd">', '<div class="tr cmd">', 1) + '<span class="hint">…seven more rows; no Add a command.</span></div>'),
    ("C6 · Routines list", "what it may do after its triggers",
     '<div class="card alist">' + arow(CLEAN, "Daily at 06:00 · Runs 2 commands · Found something 2 h ago")
     + arow(TIDY, "Mondays at 09:00 · Changes worktrees and branches · Nothing found 3 d ago") + '</div>'),
    ("C7 · Routine page", "both permissions; then only commands", act_definition(FENCE_ON + CMD_FENCE).replace(TIDY_BRIEF, CLEAN_BRIEF).replace("Mondays at 09:00", "Daily at 06:00")
     + '<div style="height:10px"></div>' + act_definition(CMD_FENCE).replace(TIDY_BRIEF, CLEAN_BRIEF).replace("Mondays at 09:00", "Daily at 06:00")),
    ("C8 · Run: running a command", "a spinner until it ends", cmd_changed(["running"])),
    ("C9 · Run: done", "Output folded open and closed", cmd_changed(["ran", "ran-closed"])),
    ("C10 · Run: failed / timed out / couldn’t start", "danger tone for the command’s own failure",
     cmd_changed(["failed", "timeout", "nostart"])),
    ("C11 · Run: not allowed", "no such command; changed since the run started; text it does not take; past 20 requests",
     cmd_changed(["unknown", "changed", "notext", "cap"])),
    ("C12 · Chat", "Altitude’s sentence after the summary",
     sysa(CLEAN, "found something: Container images took 31 GB. Ran prune-images and post-report.", "found")
     + '<div style="height:10px"></div>' + sysa(CLEAN, "found something: Container images took 31 GB. Ran prune-images. post-report failed.", "found")),
]
board("CmdStates", 1200, 3700, '<div style="padding:36px 40px 14px"><h1 style="font-size:20px;font-weight:600;margin:0">Commands you list: states at 1440</h1>'
      '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:900px">COMMANDS.md: the Commands list on the form, '
      'the list and routine page lines, a run’s command rows, and chat.</p></div>'
      '<div class="sheetgrid">' + "".join(st(*c) for c in CMD_CELLS) + '</div>')
board("PhoneCmdStates", 1720, 2200, '<div style="padding:36px 40px 14px"><h1 style="font-size:20px;font-weight:600;margin:0">Commands you list: states at 390</h1>'
      '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px">The same states at phone width: 44px targets, text that wraps.</p></div>'
      '<div class="sheetgrid phonesheet">' + "".join(st(*c) for c in CMD_CELLS) + '</div>')

print("written")
