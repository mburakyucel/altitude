"""Write the repair boards beside this file: python3 design/wireframes/repair/gen.py"""
import pathlib

out = pathlib.Path(__file__).resolve().parent
HEAD = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Repair: {title}</title>
<link rel="stylesheet" href="../wireframes.css"><link rel="stylesheet" href="repair.css"></head>
<body><div class="root" data-theme="light" style="width:{w}px;height:{h}px">
'''
TAIL = '''</div><script>if(location.search.indexOf('dark')>=0)document.querySelector('.root').dataset.theme='dark'</script></body></html>
'''
CHECK = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>'
CROSS = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>'
CHEVL = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>'
CHEVR = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>'
U = 'class="u"'
CAUSE = "the download failed its certificate check"


def board(name, w, h, body):
    (out / f"{name}.html").write_text(HEAD.format(title=name, w=w, h=h) + body + TAIL)


def tabbar(on="chat"):
    tabs = [("chat", "Chat"), ("work", "Work"), ("needs", "Needs you"), ("monitor", "Monitor")]
    return '<nav class="tabbar">' + "".join(f'<div class="tab{" on" if k == on else ""}">{t}</div>' for k, t in tabs) + '</nav>'


def sysline(text, fault=False):
    return f'<div class="sys"><span class="ln"></span><span class="d{" fault" if fault else ""}"></span><span>{text}</span><span class="ln"></span></div>'


# ---- the failed-update notice ----
def notice(state="failed", phone=True):
    text = f'The update to 0.9.0 did not finish: {CAUSE}. Altitude 0.8.0 keeps running.'
    if state == "failed":
        acts = '<span class="btn primary">Repair</span><span class="btn">Try again</span>'
    elif state == "passing":
        text = 'The update to 0.9.0 did not finish: the download was cut off. Altitude 0.8.0 keeps running.'
        acts = '<span class="btn primary">Try again</span><span class="btn">Repair</span>'
    elif state == "terminal":
        acts = '<span class="rrun">A repair is running in a terminal on this computer</span>'
    else:
        acts = f'<span class="rrun"><span class="spin"></span>Repair running · <a href="#" {U}>Open</a></span>'
    cls = "rnotice phone" if phone else "rnotice"
    return (f'<div class="{cls}"><p>{text}</p><div class="acts">{acts}</div>'
            f'<span class="x" aria-label="Dismiss update notice">{CROSS}</span></div>')


def phone_chat(top, inner, overlay=""):
    return ('<div class="m nm">' + top +
            '<header class="mh"><div><span class="name">atlas</span><div class="subl" style="padding-left:4px;margin-top:-6px">L3 · Ready</div></div><span class="sp"></span></header>'
            f'<div class="mbody"><div class="mcol">{inner}</div></div>'
            '<div class="mcomp"><div class="composer"><div class="ph2">Message L3</div></div></div>'
            + tabbar("chat") + overlay + '</div>')


CHAT = ('<div class="day">Today</div><div class="me">Is the export ready for review?</div>'
        '<div class="l3"><p>Yes. The export task opened its PR an hour ago; checks passed and it waits for your review in Needs you.</p></div>')

board("PhoneNotice", 390, 844, phone_chat(notice(), CHAT))


# ---- the start sheet ----
def sheet_body(state="update"):
    if state in ("empty", "settings"):
        field = ('<div class="field ta ph">For example: the page doesn’t load on my phone</div>' if state == "empty"
                 else '<div class="field ta">The page doesn’t load on my phone</div>')
    else:
        field = f'<div class="field ta">The update to 0.9.0 failed: {CAUSE}.</div>'
    if state == "starting":
        btns = '<span class="btn primary wide" aria-label="Start repair, starting"><span class="spin"></span>Starting…</span><span class="btn ghost wide" aria-disabled="true">Cancel</span>'
    elif state == "empty":
        btns = '<span class="btn primary wide" aria-disabled="true" style="opacity:.45">Start repair</span><span class="btn ghost wide">Cancel</span>'
    else:
        btns = '<span class="btn primary wide">Start repair</span><span class="btn ghost wide">Cancel</span>'
    err = ""
    if state == "busy":
        err = f'<p class="rerr">A repair is already running. <a href="#" {U}>Open it</a></p>'
    elif state == "agent":
        err = '<p class="rerr">Repairs start only from your own browser or terminal.</p>'
    elif state == "terminal":
        err = '<p class="rerr">A repair is running in a terminal on this computer.</p>'
    elif state == "noengine":
        err = '<p class="rerr">No coding agent can run now: Claude’s weekly allowance is used. Run <code>alt repair</code> in a terminal instead.</p>'
    return ('<h2>Start a repair?</h2><p>A repair agent gets <b>full access to this computer as you</b> to find out what’s wrong and fix it. '
            'It works until it reports, you stop it, or 2 hours pass.</p>'
            '<p>Its rules are instructions it follows, not limits Altitude enforces. It can read any of your files, and what it reads goes to Claude. '
            'Each step it takes is shown here and kept.</p>'
            '<label class="flab">What’s wrong?</label>' + field +
            '<p class="muted eng">Claude · uses your plan like a small task</p>'
            f'<div class="sbtns">{btns}</div>{err}')


board("PhoneSheet", 390, 844, phone_chat(notice(), CHAT, '<div class="scrimx"></div><div class="bsheet"><div class="grab"></div>' + sheet_body() + '</div>'))


# ---- the repair page ----
def ran(cmd, state="ok"):
    if state == "running":
        return f'<div class="cmd"><span class="spin"></span><span class="ct">Running: <code>{cmd}</code></span></div>'
    bad = state in ("bad", "cut")
    lead = {"ok": "Ran:", "bad": '<span class="ex bad">Failed (exit 1):</span>', "cut": '<span class="ex bad">Cut off by the end of the session:</span>',
            "changed": "Changed:", "fetched": "Fetched:"}[state]
    return f'<div class="cmd"><span class="dotc{" bad" if bad else ""}"></span><span class="ct">{lead} <code>{cmd}</code></span><a href="#" {U}>Show</a></div>'


def repair_header(status="Working", age="4 min", stop="Stop", phone=True):
    chipc = {"Working": "chip run", "Waiting for you": "chip wait", "Repaired": "chip ok", "Confirm yourself": "chip",
             "Not repaired": "chip bad", "Stopped": "chip"}[status]
    btn = {"Stop": '<span class="btn">Stop</span>', "": "",
           "Stopping": '<span class="btn" aria-disabled="true"><span class="spin"></span>Stopping…</span>'}[stop]
    title = "Repair · Failed update to 0.9.0"
    if phone:
        return ('<header class="mh rh"><a class="btn" href="#" style="padding:0 8px">' + CHEVL + '</a>'
                f'<div class="rt"><div class="tt">{title}</div>'
                f'<div class="tm"><span class="{chipc}">{status}</span> Claude Opus · {age}</div></div>{btn}</header>')
    return (f'<header class="ph rph"><div><h1>{title}</h1>'
            f'<div class="sub"><span class="{chipc}">{status}</span> Claude Opus · {age}</div></div>{btn}</header>')


def access(state="left", left="1 h 48 min"):
    text = {"left": f"Full access · {left} left",
            "low": f'Full access · 14 min left · <a href="#" {U}>Keep going</a>',
            "ended": f'Time’s up · the session ended · <a href="#" {U}>Keep going</a>',
            "failed": f'Time’s up · the session ended · <a href="#" {U}>Keep going</a> <span class="danger">Couldn’t resume: Claude’s allowance is used.</span>',
            "done": "Session ended"}[state]
    return f'<div class="perm{" low" if state in ("low", "ended", "failed") else ""}">{text}</div>'


def phone_repair(status, inner, acc, age="4 min", stop="Stop", ended=False):
    comp = (CONTINUE if ended else '<div class="composer"><div class="ph2">Message the repair</div></div>')
    return ('<div class="m rpage"><div></div>' + repair_header(status, age=age, stop=stop) + acc +
            f'<div class="mbody"><div class="mcol">{inner}</div></div>'
            f'<div class="mcomp">{comp}</div>' + tabbar("none") + '</div>')


CONTINUE = '<div style="display:flex;justify-content:center"><span class="btn">Continue repair…</span></div>'


START = sysline("Started by you · full access for 2 hours")
ASKED = '<div class="me" style="max-width:92%">The update to 0.9.0 failed: the download failed its certificate check.</div>'
FIND = ('<div class="l3"><p>Python on this computer has lost its certificate store: a Homebrew upgrade replaced OpenSSL '
        'without its certificates, so every download from GitHub fails its check. Altitude itself is fine.</p></div>')


def ask(state="ask"):
    head = ('<div class="dq"><div class="muted" style="font-size:12.5px">Asked by the repair</div><div class="dqh">Go ahead?</div>'
            '<p>Reinstalling OpenSSL restores its certificates. It changes a program outside Altitude.</p>'
            '<pre><code>brew reinstall openssl@3</code></pre>')
    tails = {
        "ask": '<div class="acts"><span class="btn primary">Go ahead</span><span class="btn">Don’t</span></div>',
        "saving": '<div class="acts"><span class="btn primary" aria-label="Go ahead, sending"><span class="spin"></span></span><span class="btn" aria-disabled="true">Don’t</span></div>',
        "yes": f'<div class="ok">{CHECK}Went ahead · by you</div>',
        "no": f'<div class="no">{CROSS}Not done · declined by you</div>',
        "expired": '<div class="no">Expired · the session ended</div>',
    }
    return head + tails[state] + '</div>'


def yourstep(state="ask"):
    head = ('<div class="dq"><div class="dqh">Your step: install the certificate tool</div>'
            '<p>This needs your password, so it runs in your terminal.</p>'
            '<pre><code>sudo apt install ca-certificates</code></pre>')
    tails = {
        "ask": f'<div class="acts"><span class="btn">Open in terminal</span><a href="#" {U} class="lk">Copy</a><a href="#" {U} class="lk">I ran it</a></div>',
        "done": f'<div class="ok">{CHECK}Ran by you · the repair is checking it</div>',
    }
    return head + tails[state] + '</div>'


RUN_INNER = (START + ASKED + ran("alt doctor") + ran("alt service logs") +
             '<div class="l3"><p>The update’s log shows a certificate error from GitHub. Checking Python’s certificate store.</p></div>'
             + ran("python3.12 -c 'import ssl; print(ssl.get_default_verify_paths())'") + ran("ls $(brew --prefix)/etc/openssl@3/cert.pem", "bad")
             + ran("brew info openssl@3", "running"))
board("PhoneRunning", 390, 844, phone_repair("Working", RUN_INNER, access()))

ASK_INNER = (ran("python3.12 -c 'import ssl; print(ssl.get_default_verify_paths())'") + ran("ls $(brew --prefix)/etc/openssl@3/cert.pem", "bad") +
             ran("brew info openssl@3") + FIND + ask())
board("PhoneAsk", 390, 844, phone_repair("Waiting for you", ASK_INNER, access(left="1 h 41 min"), age="7 min"))


def report(kind="ok"):
    if kind == "ok":
        return (f'<div class="rep rok"><div class="rh2">{CHECK}<b>Repaired</b></div>'
                '<h4>What was wrong</h4><p>A Homebrew upgrade left OpenSSL without its certificates, so the download of 0.9.0 failed its check.</p>'
                '<h4>What changed</h4><p>Reinstalled OpenSSL (you agreed), then installed 0.9.0. Altitude restarted and answers again.</p>'
                '<h4>What remains</h4><p>Nothing.</p>'
                '<div class="chk">Altitude checked: version 0.9.0 · matches its release · service running · all checks pass</div>'
                '<h4>Left running</h4><p>Nothing new</p></div>')
    if kind == "pass":
        return (f'<div class="rep rneu"><div class="rh2"><b>Confirm yourself</b></div>'
                '<h4>What was wrong</h4><p>Other-device access was off after the address changed.</p>'
                '<h4>What changed</h4><p>Turned other-device access back on for the new address and restarted Altitude.</p>'
                '<h4>What remains</h4><p>Nothing that Altitude can check.</p>'
                '<p><b>Try:</b> open Altitude on your phone.</p>'
                '<div class="chk">Altitude checked: service running · address answers · all checks pass</div>'
                '<h4>Left running</h4><p>Nothing new</p></div>')
    if kind == "bad":
        return (f'<div class="rep rbad"><div class="rh2">{CROSS}<b>Not repaired</b></div>'
                '<h4>What was wrong</h4><p>A proxy on this network rewrites downloads from GitHub, so 0.9.0 fails its checksum.</p>'
                '<h4>What changed</h4><p>Nothing. Altitude 0.8.0 keeps running.</p>'
                '<h4>What remains</h4><p>Update on a network without that proxy, or copy the release archive here and run <code>alt update --archive</code>.</p>'
                '<div class="chk">Altitude checked: version 0.8.0 · service running · <span class="danger">update to 0.9.0 not installed</span></div>'
                '<h4>Left running</h4><p>A user service <code>proxy-test.service</code>, started 3:12 · <a href="#" class="u">Show</a></p></div>')
    if kind == "stopped":
        return sysline("Stopped by you · the agent and its processes were stopped")
    if kind == "stopped_update":
        return sysline("Stopped by you · the update it started continues; its result appears here")
    if kind == "unconfirmed":
        return sysline("Stop unconfirmed · The repair may still be running.", True) + '<div style="text-align:center"><span class="btn">Check status</span></div>'
    if kind == "hand":
        return (f'<div class="rep rneu"><div class="rh2"><b>Confirm yourself · the installation was changed by hand</b></div>'
                '<p>Version 0.9.0 is running, but 3 installed files differ from its release.</p>'
                '<div class="chk">Altitude checked: version 0.9.0 · <span class="danger">3 files differ from its release</span> · service running</div></div>')
    if kind == "terminal":
        return (sysline("Started from a terminal · Oct 3, 21:40") + '<div class="muted" style="text-align:center;font-size:13px">Its steps are in that terminal’s history.</div>'
                + sysline("Ended · Oct 3, 22:05"))
    return sysline("The repair ended without a report", True) + '<div style="text-align:center"><span class="btn">Repair again</span></div>'


REPORT_INNER = (ran("alt update --version 0.9.0") + sysline("Altitude is restarting · the repair continues")
                + sysline("Altitude is back") + ran("alt doctor") + report("ok"))
board("PhoneReport", 390, 844, phone_repair("Repaired", REPORT_INNER, access("done"), age="16 min", stop="", ended=True))


# ---- Settings → About ----
def about(state="idle"):
    act = {"running": f'<span class="rrun"><span class="spin"></span>Running · <a href="#" {U}>Open</a></span>',
           "terminal": '<span class="rrun">Running in a terminal</span>'}.get(state, '<span class="btn">Repair…</span>')
    recent = ('<div class="recent">'
              f'<a href="#" class="rr"><span>Oct 19 · Failed update to 0.9.0</span><span class="chip ok">Repaired</span>{CHEVR}</a>'
              f'<a href="#" class="rr"><span>Oct 12 · Page doesn’t load on my phone</span><span class="chip">Confirm yourself</span>{CHEVR}</a>'
              f'<a href="#" class="rr"><span>Oct 3 · Service didn’t start after login <small class="muted">· From a terminal</small></span><span class="chip bad">Not repaired</span>{CHEVR}</a>'
              f'<a href="#" {U}>All repairs</a></div>')
    return ('<h2 class="sgh">About</h2><div class="card about">'
            '<div class="arow"><span><b>Version</b><br><small class="muted">0.9.0 · up to date</small></span></div>'
            '<div class="arow"><span><b>Check for new versions</b></span><span class="sw on"><i></i></span></div>'
            '<div class="arow"><span><b>Automatic updates</b></span><span class="sw on"><i></i></span></div>'
            f'<div class="arow"><span><b>Repair</b><br><small class="muted">Something not working? A repair agent finds out why and fixes it, with full access while it works.</small></span>{act}</div>'
            f'{recent}</div>')


board("PhoneAbout", 390, 844,
      '<div class="m"><div></div><header class="mh"><a class="btn" href="#" style="padding:0 8px">' + CHEVL + 'Settings</a></header>'
      f'<div class="route-content" style="overflow:hidden">{about()}</div><div></div>' + tabbar("chat") + '</div>')


# ---- desktop ----
RAIL = ('<aside class="drail"><b class="muted">Projects</b><div class="rp">atlas</div><div class="rp">harbor</div>'
        '<div class="rp">demo</div><div style="flex:1"></div><div class="rp">Settings</div></aside>')
desk_inner = (START + ASKED + ran("alt doctor") + ran("alt service logs") +
              ran("python3.12 -c 'import ssl; print(ssl.get_default_verify_paths())'") + ran("ls $(brew --prefix)/etc/openssl@3/cert.pem", "bad")
              + ran("brew info openssl@3") + FIND + ask())
board("Desktop", 1440, 900,
      '<div class="dgrid">' + RAIL + '<main class="dmain">' + repair_header("Waiting for you", age="7 min", phone=False)
      + access(left="1 h 41 min")
      + f'<div class="dconvo"><div class="dcol">{desk_inner}</div></div>'
      '<div class="dcomp"><div class="composer"><div class="ph2">Message the repair</div></div></div></main></div>')


# ---- every state ----
def st(label, note, body):
    return f'<div class="st"><div class="lab">{label}<span>{note}</span></div><div class="statebox">{body}</div></div>'


def dlg(state):
    return '<div class="dlg">' + sheet_body(state) + '</div>'


STOPQ = ('<div class="dlg"><h2>Stop the repair?</h2><p>A command running now is cut off.</p>'
         '<div class="sbtns"><span class="btn danger">Stop repair</span><span class="btn ghost">Keep running</span></div></div>')
NEEDS = ('<div class="nyh">Altitude</div><div class="dq"><div class="muted" style="font-size:12px">Repair asks · 2 min</div>'
         f'<div class="dqh"><a href="#" {U}>Repair · Failed update to 0.9.0</a></div><p>Go ahead? Reinstall OpenSSL: <code>brew reinstall openssl@3</code></p>'
         '<div class="acts"><span class="btn primary">Go ahead</span><span class="btn">Don’t</span></div></div>'
         '<div class="nyh" style="margin-top:14px">atlas</div><div class="muted">Export the work list · waits for your review</div>')
AFTER = report("ok") + CONTINUE
states = [
    ("1 · Failed update, other cause", "Repair leads; Try again repeats the update; × dismisses", notice(phone=False)),
    ("2 · Failed update, passing cause", "Try again leads; Repair beside it", notice("passing", phone=False)),
    ("3 · A repair is running", "the notice links to it; a terminal repair has no page to open", notice("running", phone=False) + notice("terminal", phone=False)),
    ("4 · Sheet from the notice", "the problem filled in; says plainly what full access means", dlg("update")),
    ("5 · Sheet from Settings", "Start repair waits for what's wrong", dlg("empty") + dlg("settings")),
    ("6 · Starting, refused", "spinner in place; Altitude's reason under the buttons", dlg("starting") + dlg("busy") + dlg("terminal") + dlg("noengine") + dlg("agent")),
    ("7 · Full access line", "time left; Keep going from 15 minutes before the end; time's up; resume failed; ended", access() + access("low") + access("ended") + access("failed") + access("done")),
    ("8 · Step lines", "running, ran, failed, file changed, page fetched, cut off; one line each, Show at the end",
     ran("alt update --version 0.9.0", "running") + ran("alt doctor") + ran("ls $(brew --prefix)/etc/openssl@3/cert.pem", "bad")
     + ran("~/.config/altitude/altitude.env", "changed") + ran("github.com/…/releases/0.9.0", "fetched") + ran("brew upgrade", "cut")),
    ("9 · Altitude restarting or not answering", "the repair runs outside the service and continues", sysline("Altitude is restarting · the repair continues") + sysline("Altitude is back")
     + '<div class="perm low">Altitude is not answering · the repair continues and this page catches up when it is back</div>'),
    ("10 · Asks before a big change", "deleting, installing or changing things outside Altitude", ask() + ask("saving")),
    ("11 · Answered, expired", "no buttons once settled", ask("yes") + ask("no") + ask("expired")),
    ("12 · Your step", "needs your password; Open in terminal, or Copy and I ran it", yourstep() + yourstep("done")),
    ("13 · Repaired", "only when Altitude finds 0.9.0 installed and its checks pass", report("ok")),
    ("14 · Confirm yourself", "a described problem, or installed files that differ from the release", report("pass") + report("hand")),
    ("15 · Not repaired", "what remains; the failing check in red", report("bad")),
    ("16 · Stop", "asks first; Stop ends the agent and its job's processes; an update it started finishes",
     STOPQ + repair_header("Working", stop="Stopping", phone=False) + report("stopped") + report("stopped_update") + report("unconfirmed")),
    ("17 · Ended without a report", "Repair again opens the sheet", report("ended")),
    ("18 · After the report", "no composer; Continue repair… opens the sheet", AFTER),
    ("19 · Settings → About", "recent repairs; terminal repairs marked", about()),
    ("20 · Settings → About, running", "Open in place of Repair…; a terminal repair has no link", about("running") + about("terminal")),
    ("20b · A terminal repair in the app", "brief, start and end, the report and Altitude's check", report("terminal") + report("bad")),
    ("21 · Needs you", "a repair's question in an Altitude section before projects", NEEDS),
]
board("States", 1200, 7800,
      '<div style="padding:28px 40px 6px"><h1 style="font-size:20px;margin:0">Repair: every state</h1>'
      '<p class="muted" style="max-width:900px">Phone uses the same content full width with 44px controls; the sheet is a bottom sheet there.</p></div>'
      '<div class="sheetgrid">' + "".join(st(*s) for s in states) + '</div>')


# ---- terminal route ----
TERM = '''<span class="p">$</span> alt repair
Altitude 0.8.0 · service running · the update to 0.9.0 failed 2 hours ago
Starting a repair with Claude in this terminal. It acts as you and asks before each step, as your settings allow.
Its record appears in Settings → About once Altitude runs.

<span class="dim">╭──────────────────────────────────────────────────────────╮
│ Claude Code                                              │
╰──────────────────────────────────────────────────────────╯</span>
<span class="pr">&gt;</span> Repair Altitude on this computer. The brief is in ./BRIEF.md.

● Reading the brief, the update record and the service log.

<span class="p">$</span> alt repair          <span class="dim"># with the service stopped</span>
Altitude's service is not answering. Its last log line: address already in use.
Starting a repair with Claude in this terminal. It acts as you and asks before each step, as your settings allow.

<span class="p">$</span> alt repair          <span class="dim"># with no usable coding agent</span>
Sign in to Claude first: run claude in this terminal.

<span class="p">$</span> alt repair          <span class="dim"># while a repair runs</span>
A repair is already running. Stop it there first.

<span class="p">$</span> alt repair          <span class="dim"># under an operator grant</span>
Only you can start a repair, from your own terminal.

<span class="p">$</span> python3.12 install.py --repair      <span class="dim"># when alt itself cannot start</span>'''
board("Terminal", 900, 740, f'<div class="term"><pre>{TERM}</pre></div>')
print("written")
