"""Write the repair boards beside this file: python3 design/wireframes/repair/gen.py

Review-only alternatives go to the ignored captures/ folder beside it; the boards here are the chosen design.
"""
import pathlib

out = pathlib.Path(__file__).resolve().parent
HEAD = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Repair: {title}</title>
<link rel="stylesheet" href="{up}wireframes.css"><link rel="stylesheet" href="{here}repair.css"></head>
<body><div class="root" data-theme="light" style="width:{w}px;height:{h}px">
'''
TAIL = '''</div><script>if(location.search.indexOf('dark')>=0)document.querySelector('.root').dataset.theme='dark'</script></body></html>
'''


def icon(path, cls="i"):
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true">{path}</svg>'


CHECK = icon('<path d="M5 12l5 5 9-10"/>', "i sm")
CROSS = icon('<path d="M6 6l12 12M18 6L6 18"/>', "i sm")
CHEVL = icon('<path d="M15 6l-6 6 6 6"/>')
CHEVR = icon('<path d="M9 6l6 6-6 6"/>', "i sm")
CHEVD = icon('<path d="M6 9l6 6 6-6"/>', "i sm")
MIC = icon('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>', "i lg")
UP = icon('<path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>', "i lg")
PANEL = icon('<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M14.5 4.5v15"/>')
MORE = icon('<circle cx="6" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="18" cy="12" r="1.2" fill="currentColor" stroke="none"/>')
TABS = {"chat": ("Chat", '<path d="M20 14.5a2 2 0 0 1-2 2H9l-4.5 4v-4H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/>'),
        "work": ("Work", '<rect x="3.5" y="5.5" width="17" height="14" rx="2"/><path d="M8 5.5V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v1.5"/><path d="M3.5 11h17"/>'),
        "needs": ("Needs you", '<path d="M3 13l2.6-7.2A1 1 0 0 1 6.5 5h11a1 1 0 0 1 .9.8L21 13v5.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/><path d="M3 13h5l1.4 2.5h5.2L16 13h5"/>'),
        "monitor": ("Monitor", '<path d="M3 12h3.5l2.5-6.5 4 13 2.5-6.5H21"/>')}
U = 'class="u"'
CAUSE = "the download failed its certificate check"
TITLE = "Failed update to 0.9.0"


def board(name, w, h, body, folder=out):
    up = "../" if folder == out else "../../"
    here = "" if folder == out else "../"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.html").write_text(HEAD.format(title=name, w=w, h=h, up=up, here=here) + body + TAIL)


def tabbar(on="chat", needs=0):
    def tab(key):
        label, path = TABS[key]
        badge = f'<span class="badge">{needs}</span>' if key == "needs" and needs else ""
        return f'<div class="tab{" on" if key == on else ""}">{icon(path, "i lg")}{label}{badge}</div>'
    return '<nav class="tabbar">' + "".join(tab(key) for key in TABS) + '</nav>'


def composer(placeholder, model=None, phone=True):
    pill = f'<span class="l3pill">{model}{CHEVD}</span>' if model else ""
    return (f'<div class="composer"><div class="ph2">{placeholder}</div><div class="crow">{pill}<span style="flex:1"></span>'
            f'<span class="icb">{MIC}</span><span class="icb send disabled" aria-label="Send">{UP}</span></div></div>')


def sysline(text, fault=False, at=""):
    stamp = f'<span class="muted">{at}</span>' if at else ""
    return f'<div class="sys"><span class="ln"></span><span class="d{" fault" if fault else ""}"></span>{stamp}<span>{text}</span><span class="ln"></span></div>'


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
            '<div class="mcomp">' + composer("Message L3") + '</div>'
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
    err = {"busy": f'<p class="rerr">A repair is already running. <a href="#" {U}>Open it</a></p>',
           "agent": '<p class="rerr">Repairs start only from your own browser or terminal.</p>',
           "terminal": '<p class="rerr">A repair is running in a terminal on this computer.</p>',
           "noengine": '<p class="rerr">No coding agent can run now: Claude’s weekly allowance is used. Run <code>alt repair</code> in a terminal instead.</p>'}.get(state, "")
    return ('<h2>Start a repair?</h2><p>A repair agent gets <b>full access to this computer as you</b> to find out what’s wrong and fix it. '
            'It works until it reports, you stop it, or 2 hours pass.</p>'
            '<p>Its rules are instructions it follows, not limits Altitude enforces. It can read any of your files, and what it reads goes to Claude. '
            'Each step it takes is shown here and kept.</p>'
            '<label class="flab">What’s wrong?</label>' + field +
            '<p class="muted eng">Claude · uses your plan like a small task</p>'
            f'<div class="sbtns">{btns}</div>{err}')


board("PhoneSheet", 390, 844, phone_chat(notice(), CHAT, '<div class="scrimx"></div><div class="bsheet"><div class="grab"></div>' + sheet_body() + '</div>'))


# ---- Work: the Repairs section ----
def trow(title, meta, dot="dot", attention=""):
    turn = f'<div class="tm turn">{attention}</div>' if attention else ""
    return (f'<a class="trow" href="#"><span class="{dot}"></span><div style="flex:1;min-width:0">'
            f'<div class="tt">{title}</div>{turn}<div class="tm">{meta}</div></div>{CHEVR}</a>')


REPAIR_ROWS = {
    "working": trow(TITLE, "Repair working · started 12 min ago"),
    "terminal": trow("Service didn’t start after login", "Repair working in a terminal · started 5 min ago"),
    "waiting": trow(TITLE, "Repair waiting · started 7 min ago", "dot held", "Your turn · the repair asks"),
    "checking": trow(TITLE, "Checking the result · ended just now"),
    "repaired": trow(TITLE, "✓ Repaired · ended yesterday", "dot idle"),
    "confirm": trow("The page doesn’t load on my phone", "Confirm yourself · ended 2d ago", "dot idle"),
    "terminal_bad": trow("Service didn’t start after login", "✕ Not repaired · from a terminal · ended 2d ago", "dot bad"),
    "stopped": trow("Voice input stopped working", "Stopped · ended 3h ago", "dot idle"),
}


def repairs_section(*rows):
    return f'<div><div class="sh">Altitude repairs <span>{len(rows)}</span></div>' + "".join(REPAIR_ROWS[row] for row in rows) + '</div>'


def work_rows(repairs=""):
    return ('<div><div class="sh">Current <span>3</span></div>'
            + trow("Index rollout", "Running · Opus on Claude · started 40 min ago", "dot held", "Your turn · 2 questions")
            + trow("Keep pagination working", "L2 working · Opus on Claude · started 32 min ago")
            + trow("Add the beta stage", "Queued · waits for a free task slot", "dot q")
            + f'</div><div class="fold">{CHEVR}Done this week · 2</div>{repairs}')


board("PhoneWork", 390, 844,
      '<div class="m"><div></div><header class="mh"><div><span class="name">atlas' + CHEVD + '</span>'
      '<div class="subl" style="padding-left:4px;margin-top:-6px">L3 · Ready</div></div><span class="sp"></span><span class="ib">' + MORE + '</span></header>'
      '<div class="mbody top"><div class="wh"><h2>Work</h2><span>3 current · 2 done this week</span></div>'
      f'<span class="muted" style="display:block;font-size:11px;margin:0 0 4px">New tasks</span><span class="newtasks" style="margin:0 0 4px"><span><b>Auto</b> · Default</span>{CHEVD}</span>'
      + work_rows(repairs_section("working", "confirm", "terminal_bad")) + '</div><div></div>' + tabbar("work", 2) + '</div>')


RAIL = ('<aside class="rail"><div class="brand"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div>'
        '<div class="ri">' + icon(TABS["needs"][1]) + 'Needs you<span class="badge">{needs}</span></div>'
        '<div class="rsec"><span>Projects</span></div>'
        '<div class="ri{sel}"><span class="dot held"></span>atlas</div><div class="ri"><span class="dot idle"></span>harbor</div>'
        '<div style="flex:1"></div><div class="ri" style="height:36px">' + icon(TABS["monitor"][1]) + 'Monitor</div>'
        '<div class="who"><span class="avatar">Y</span>You</div></aside>')


def rail(selected=True, needs=3):
    return RAIL.format(sel=" sel" if selected else "", needs=needs)


PROJECT_CHAT = ('<div class="day">Today</div><div class="me">What remains on the migration?</div>'
                '<div class="l3"><p>The index rollout waits for your two answers; pagination is running and should open its PR within the hour.</p></div>')
board("DesktopWork", 1440, 900,
      '<div style="display:grid;grid-template-columns:260px minmax(0,1fr) 340px;height:100%">' + rail() +
      '<main class="pane"><header class="ph"><div><h1>atlas</h1><div class="sub">L3 answered 5 min ago on Claude · 3 tasks in flight · 2 questions need you</div></div>'
      f'<div class="acts"><span class="ib on">{PANEL}</span><span class="ib">{MORE}</span></div></header>'
      f'<div class="convo"><div class="col">{PROJECT_CHAT}</div><div style="height:22px"></div>'
      + composer("Message L3 about atlas", "L3 · <b>Auto</b> · Default effort") + '<div style="height:20px"></div></div></main>'
      '<aside class="work"><div class="wh"><h2>Work</h2><span>3 current · 2 done this week</span></div>'
      + work_rows(repairs_section("waiting", "repaired")) + '</aside></div>')


# ---- the repair page, like a task ----
def tool(name, call, hint, bad=False, at=""):
    stamp = f'<span class="at">{at}</span>' if at else ""
    return (f'<div class="tool"{" data-error" if bad else ""} data-kind="{"command" if name == "$" else "file"}"><b>{name}</b>'
            f'<span class="call">{call}</span><span class="n{" bad" if bad else ""}">{hint}{CHEVR if hint not in ("running · 1 min",) else ""}</span>{stamp}</div>')


BRIEF = ('<details class="prompt rbrief"><summary><span><b>Brief</b>What you wrote, the rules, how this installation is laid out and '
         'Altitude’s records</span>' + CHEVR + '</summary></details>')
STARTED = sysline("Started by you · full access for 2 hours", at="09:12")
ASKED = '<div class="me">The update to 0.9.0 failed: the download failed its certificate check.</div>'
LOOK = '<div class="l3"><p>The update’s log shows a certificate error from GitHub. Checking Python’s certificate store.</p></div>'
FIND = ('<div class="l3"><p>Python on this computer has lost its certificate store: a Homebrew upgrade replaced OpenSSL '
        'without its certificates, so every download from GitHub fails its check. Altitude itself is fine.</p></div>')
def steps(*rows):
    """Consecutive steps sit together, as in a task's live session."""
    return '<div class="steps">' + "".join(rows) + '</div>'


STEPS_A = steps(tool("$", "alt doctor", "24 lines", at="09:12"), tool("$", "alt service logs", "80 lines", at="09:13"))
STEP_B = (tool("$", "python3.12 -c 'import ssl; print(ssl.get_default_verify_paths())'", "1 line", at="09:14")
          + tool("$", "ls $(brew --prefix)/etc/openssl@3/cert.pem", "exit 1", True, at="09:14"))
STEPS_B = steps(STEP_B)
RUNNING = tool("$", "brew info openssl@3", "running · 1 min", at="09:15")
STEPS_C = tool("$", "brew info openssl@3", "12 lines", at="09:15")


def question(state="ask"):
    head = ('<div class="conversation-question"><p class="turnl">Your turn · the repair asks</p><div class="dq"><div class="dqh">Go ahead?</div>'
            '<p>Reinstalling OpenSSL restores its certificates. It changes a program outside Altitude.</p>'
            '<pre><code>brew reinstall openssl@3</code></pre>')
    tails = {
        "ask": '<div class="acts"><span class="btn primary">Go ahead</span><span class="btn">Don’t</span></div>',
        "saving": '<div class="acts"><span class="btn primary" aria-label="Go ahead, sending"><span class="spin"></span>Sending…</span><span class="btn" aria-disabled="true">Don’t</span></div>',
        "yes": f'<div class="ok">{CHECK}Went ahead · by you</div>',
        "no": f'<div class="no">{CROSS}Not done · declined by you</div>',
        "expired": '<div class="no">Expired · the session ended</div>',
    }
    return head + tails[state] + '</div></div>'


def yourstep(state="ask"):
    head = ('<div class="conversation-question"><p class="turnl">Your turn · a step for your terminal</p><div class="dq"><div class="dqh">Your step: install the certificate tool</div>'
            '<p>This needs your password, so it runs in your terminal.</p>'
            '<pre><code>sudo apt install ca-certificates</code></pre>')
    tails = {"ask": '<div class="acts"><span class="btn">Copy</span><span class="btn primary">I ran it</span></div>',
             "done": f'<div class="ok">{CHECK}Done · you ran it</div>'}
    return head + tails[state] + '</div></div>'


def report(kind="ok"):
    if kind == "ok":
        return (f'<div class="rep rok"><div class="rh2">{CHECK}<b>Repaired</b></div>'
                '<h4>What was wrong</h4><p>A Homebrew upgrade left OpenSSL without its certificates, so the download of 0.9.0 failed its check.</p>'
                '<h4>What changed</h4><p>Reinstalled OpenSSL (you agreed), then installed 0.9.0. Altitude restarted and answers again.</p>'
                '<h4>What remains</h4><p>Nothing.</p>'
                '<div class="chk">Altitude checked: version 0.9.0 · matches its release · service running · all checks pass</div>'
                '<h4>Left running</h4><p>Nothing new</p></div>')
    if kind == "pass":
        return ('<div class="rep rneu"><div class="rh2"><b>Confirm yourself</b></div>'
                '<h4>What was wrong</h4><p>Other-device access was off after the address changed.</p>'
                '<h4>What changed</h4><p>Turned other-device access back on for the new address and restarted Altitude.</p>'
                '<h4>What remains</h4><p>Nothing that Altitude can check.</p>'
                '<p><b>Try:</b> open Altitude on your phone.</p>'
                '<div class="chk">Altitude checked: service running · all checks pass</div>'
                '<h4>Left running</h4><p>Nothing new</p></div>')
    if kind == "bad":
        return (f'<div class="rep rbad"><div class="rh2">{CROSS}<b>Not repaired</b></div>'
                '<h4>What was wrong</h4><p>A proxy on this network rewrites downloads from GitHub, so 0.9.0 fails its checksum.</p>'
                '<h4>What changed</h4><p>Nothing. Altitude 0.8.0 keeps running.</p>'
                '<h4>What remains</h4><p>Update on a network without that proxy, or copy the release archive here and run <code>alt update --archive</code>.</p>'
                '<div class="chk">Altitude checked: version 0.8.0 · service running · <span class="danger">update to 0.9.0 not installed</span></div>'
                '<h4>Left running</h4><ul><li>A user service <code>proxy-test.service</code></li></ul></div>')
    if kind == "checking":
        return ('<div class="rep"><div class="rh2"><b>Report</b></div><h4>What was wrong</h4><p>A Homebrew upgrade left OpenSSL without its certificates.</p>'
                '<h4>What changed</h4><p>Reinstalled OpenSSL, then installed 0.9.0.</p><h4>What remains</h4><p>Nothing.</p>'
                '<div class="chk"><span class="spin"></span> Altitude is checking the result…</div></div>')
    if kind == "hand":
        return ('<div class="rep rneu"><div class="rh2"><b>Confirm yourself · the installation was changed by hand</b></div>'
                '<p>Version 0.9.0 is running, but its installed files differ from its release.</p>'
                '<div class="chk">Altitude checked: version 0.9.0 · <span class="danger">its files differ from the release</span> · service running · all checks pass</div></div>')
    if kind == "stopped":
        return sysline("Stopped by you · the agent and its processes were stopped", at="09:20")
    if kind == "stopped_update":
        return sysline("Stopped by you · the update it started continues; its result appears here", at="09:20")
    if kind == "terminal":
        return (sysline("Started from a terminal · Oct 17, 21:40") + '<div class="muted" style="text-align:center;font-size:13px">Its steps are in that terminal’s history.</div>'
                + sysline("Ended · Oct 17, 22:05"))
    return sysline("The repair ended without a report", True, at="09:40")


def status_line(kind):
    """The conversation's closing status line, as a task's: time running low, time up, Keep going refused."""
    keep = f'<a href="#" {U}>Keep going</a>'
    text = {"low": f"14 min of full access left · {keep}",
            "up": f"Time’s up · the session ended · {keep}",
            "failed": f'Time’s up · the session ended · {keep} <span class="danger">Couldn’t resume: Claude’s allowance is used.</span>'}[kind]
    return f'<p class="cstatus"><span class="dot held"></span>{text}</p>'


CHIP = {"working": ("Repair working", "chip run", "dot"), "waiting": ("Repair waiting for you", "chip wait", "dot held"),
        "checking": ("Checking the result", "chip", "dot"), "repaired": ("✓ Repaired", "chip ok", "dot idle"),
        "confirm": ("Confirm yourself", "chip", "dot idle"), "bad": ("✕ Not repaired", "chip bad", "dot bad"),
        "stopped": ("Stopped", "chip", "dot idle")}


def access_chip(left):
    if left is None:
        return ""
    if left == "low":
        return '<span class="chip held">Full access · 14 min left</span>'
    return f'<span class="chip">Full access · {left} left</span>'


def desk_header(status="working", left="1 h 48 min", action="Stop", terminal=False):
    label, chip, dot = CHIP[status]
    act = {"Stop": '<span class="btn hbtn">Stop<span class="esc">Esc</span></span>', "": "",
           "Stopping": '<span class="btn hbtn" aria-disabled="true"><span class="spin"></span>Stopping…</span>',
           "Check status": '<span class="btn hbtn">Check status</span>'}[action]
    chips = f'<span class="{chip}">{label}</span><span class="chip">Opus on Claude</span>' + access_chip(left)
    if terminal:
        chips = f'<span class="{chip}">{label}</span><span class="chip">From a terminal</span>'
    return ('<header class="thead"><div class="trow1"><span class="crumb">' + CHEVL.replace('class="i"', 'class="i sm"') + 'Work</span>'
            f'<h1><span class="{dot}"></span>{TITLE}</h1><div class="acts">{act}</div></div>'
            f'<div class="chips">{chips}</div></header>')


def phone_header(status="working", left="1 h 48 min left", action="Stop"):
    label, _, dot = CHIP[status]
    tone = ' style="color:var(--chip-claimed-text)"' if left and left.startswith("14") else ""
    act = {"Stop": '<span class="btn">Stop</span>', "": "", "Stopping": '<span class="btn" aria-disabled="true"><span class="spin"></span>Stopping…</span>',
           "Check status": '<span class="btn">Check status</span>'}[action]
    sub = f'<span class="{dot}"></span>{label.lstrip("✓✕ ")}' + (f'<span{tone}> · {left}</span>' if left else "")
    return (f'<header class="mh rh"><a href="#" aria-label="Back" class="back">{CHEVL}</a>'
            f'<div class="rt"><div class="tt">{TITLE}</div><div class="tm">{sub}</div></div>{act}</header>')


def dock(kind, phone=True):
    if kind == "composer":
        return composer("Message the repair")
    label = "Continue repair…" if kind == "continue" else "Repair again"
    return f'<div style="display:flex;justify-content:center;padding:4px 0 8px"><span class="btn">{label}</span></div>'


def phone_repair(status, inner, left="1 h 48 min left", action="Stop", foot="composer", needs=0):
    return ('<div class="m rpage"><div></div>' + phone_header(status, left, action) +
            f'<div class="mbody"><div class="mcol">{inner}</div></div>'
            f'<div class="mcomp">{dock(foot)}</div>' + tabbar("work", needs) + '</div>')


RUN_INNER = BRIEF + '<div class="day">Today</div>' + STARTED + ASKED + STEPS_A + LOOK + steps(STEP_B, RUNNING)
board("PhoneRunning", 390, 844, phone_repair("working", RUN_INNER))

ASK_INNER = steps(STEP_B, STEPS_C) + FIND + question()
board("PhoneAsk", 390, 844, phone_repair("waiting", ASK_INNER, "1 h 41 min left", needs=1))

REPORT_INNER = (steps(tool("$", "alt update --version 0.9.0", "16 lines", at="09:19"))
                + sysline("Altitude is restarting · the repair continues", at="09:19") + sysline("Altitude is back", at="09:20")
                + report("ok"))
board("PhoneReport", 390, 844, phone_repair("repaired", REPORT_INNER, None, "", "continue"))


# ---- Settings → About keeps the start ----
def about(state="idle"):
    act = {"running": f'<span class="rrun"><span class="spin"></span>Running · <a href="#" {U}>Open</a></span>',
           "terminal": '<span class="rrun">Running in a terminal</span>'}.get(state, '<span class="btn">Repair…</span>')
    return ('<h2 class="sgh">About</h2><div class="card about">'
            '<div class="arow"><span><b>Version</b><br><small class="muted">0.9.0 · up to date</small></span></div>'
            '<div class="arow"><span><b>Check for new versions</b></span><span class="sw on"><i></i></span></div>'
            '<div class="arow"><span><b>Automatic updates</b></span><span class="sw on"><i></i></span></div>'
            f'<div class="arow"><span><b>Repair</b><br><small class="muted">Something not working? A repair agent finds out why and fixes it, with full access while it works.</small></span>{act}</div>'
            f'<a class="arow" href="#"><span><b>All repairs</b></span>{CHEVR}</a></div>')


board("PhoneAbout", 390, 844,
      '<div class="m"><div></div><header class="mh"><a class="btn" href="#" style="padding:0 8px">' + CHEVL + 'Settings</a></header>'
      f'<div class="route-content" style="overflow:hidden">{about()}</div><div></div>' + tabbar("chat") + '</div>')


# ---- desktop ----
def desktop(header, inner, foot="composer", needs=1):
    return ('<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:100%">' + rail(True, needs) +
            '<main class="pane">' + header +
            f'<div class="convo"><div class="col">{inner}</div><div style="height:16px"></div>{dock(foot, False)}<div style="height:20px"></div></div></main></div>')


DESK_INNER = BRIEF + '<div class="day">Today</div>' + STARTED + ASKED + STEPS_A + steps(STEP_B, STEPS_C) + FIND + question()
board("Desktop", 1440, 900, desktop(desk_header("waiting", "1 h 41 min"), DESK_INNER))


# ---- every state ----
def st(label, note, body):
    return f'<div class="st"><div class="lab">{label}<span>{note}</span></div><div class="statebox">{body}</div></div>'


def dlg(state):
    return '<div class="dlg">' + sheet_body(state) + '</div>'


def work_box(body):
    return f'<div class="work wbox">{body}</div>'


STOPQ = ('<div class="dlg"><h2>Stop the repair?</h2><p>A command running now is cut off.</p>'
         '<div class="sbtns"><span class="btn danger">Stop repair</span><span class="btn ghost">Keep running</span></div></div>')
NEEDS = ('<div class="nyh">Altitude</div><div class="dq"><div class="muted" style="font-size:12px">Asked by the repair · '
         f'<a href="#" {U}>{TITLE}</a></div><div class="dqh">Go ahead?</div><p>Reinstalling OpenSSL restores its certificates. It changes a program outside Altitude.</p>'
         '<pre><code>brew reinstall openssl@3</code></pre><div class="acts"><span class="btn primary">Go ahead</span><span class="btn">Don’t</span></div></div>'
         '<div class="nyh" style="margin-top:14px">atlas</div><div class="muted">Index rollout · 2 questions</div>')
NOTANSWERING = '<p class="cnotice">Altitude is not answering · the repair continues and this page catches up when it is back</p>'
states = [
    ("1 · Work, no repair to show", "the section is absent, also while loading and when its read fails; nothing else in Work changes; saved rows stay through a failed refresh", work_box('<div class="wh"><h2>Work</h2><span>3 current · 2 done this week</span></div>' + work_rows())),
    ("2 · Work, repair rows", "last in Work, after Done this week and Routines, in every project's Work; newest first; a finished one leaves 3 days after it ended",
     work_box(repairs_section("working", "waiting", "checking", "terminal") + repairs_section("repaired", "confirm", "terminal_bad", "stopped"))),
    ("3 · Failed update, other cause", "Repair leads; Try again repeats the update; × dismisses", notice(phone=False)),
    ("4 · Failed update, passing cause", "Try again leads; Repair beside it", notice("passing", phone=False)),
    ("5 · A repair is running", "the notice links to it; a terminal repair has no page to open", notice("running", phone=False) + notice("terminal", phone=False)),
    ("6 · Sheet from the notice", "the problem filled in; says plainly what full access means", dlg("update")),
    ("7 · Sheet from Settings", "Start repair waits for what's wrong", dlg("empty") + dlg("settings")),
    ("8 · Starting, refused", "spinner in place; Altitude's reason under the buttons", dlg("starting") + dlg("busy") + dlg("terminal") + dlg("noengine") + dlg("agent")),
    ("9 · Page header", "a task's header: Work crumb, state dot and title, Stop; chips for status, model and time left",
     desk_header() + desk_header("waiting", "low") + desk_header("working", "1 h 48 min", "Stopping") + desk_header("working", None, "Check status")
     + desk_header("repaired", None, "") + desk_header("confirm", None, "", terminal=True)),
    ("10 · Phone header", "back, title, the status line with time left, Stop", phone_header() + phone_header("waiting", "14 min left") + phone_header("bad", None, "")),
    ("11 · Steps", "Live session's rows: running, done, failed, file changed, page fetched, cut off; each opens its command and output",
     steps(RUNNING, tool("$", "alt doctor", "24 lines") + tool("$", "ls $(brew --prefix)/etc/openssl@3/cert.pem", "exit 1", True)
     + tool("Edit", "~/.config/altitude/altitude.env", "changed") + tool("Fetch", "github.com/…/releases/0.9.0", "fetched")
     , tool("$", "brew upgrade", "cut off", True))),
    ("12 · Brief", "folded at the top, as a task's brief in its live session", BRIEF),
    ("13 · Altitude restarting or not answering", "the repair runs outside the service and continues",
     sysline("Altitude is restarting · the repair continues", at="09:19") + sysline("Altitude is back", at="09:20") + NOTANSWERING),
    ("14 · Asks before a big change", "under Your turn, as a task's question", question() + question("saving")),
    ("15 · Answered, expired", "no buttons once settled", question("yes") + question("no") + question("expired")),
    ("16 · Your step", "needs your password; Copy and I ran it", yourstep() + yourstep("done")),
    ("17 · Time", "the conversation's closing line from 15 minutes before the end; time's up; resume refused",
     status_line("low") + status_line("up") + status_line("failed")),
    ("18 · Report", "Altitude checks; Repaired only when it finds 0.9.0 installed and its checks pass", report("checking") + report("ok")),
    ("19 · Confirm yourself", "a described problem, or installed files that differ from the release", report("pass") + report("hand")),
    ("20 · Not repaired", "what remains; the failing check in red; Left running", report("bad")),
    ("21 · Stop", "asks first; Stop ends the agent and its job's processes; an update it started finishes",
     STOPQ + report("stopped") + report("stopped_update")),
    ("22 · Ended without a report", "Repair again opens the sheet", report("ended") + dock("again")),
    ("23 · After the report", "no composer; Continue repair… opens the sheet", dock("continue")),
    ("24 · A terminal repair", "start and end, the report and Altitude's check; its steps stay in the terminal", report("terminal") + report("pass")),
    ("25 · Settings → About", "the start and All repairs; Open while one runs, no link for a terminal repair", about() + about("running") + about("terminal")),
    ("26 · Needs you", "a repair's question in an Altitude section before projects", NEEDS),
]
board("States", 1200, 8200,
      '<div style="padding:28px 40px 6px"><h1 style="font-size:20px;margin:0">Repair: every state</h1>'
      '<p class="muted" style="max-width:900px">Phone uses the same content full width with 44px controls; sheets are bottom sheets there.</p></div>'
      '<div class="sheetgrid">' + "".join(st(*s) for s in states) + '</div>')


# ---- terminal route ----
TERM = '''<span class="p">$</span> alt repair
Altitude 0.8.0 · service running · the update to 0.9.0 failed 2 hours ago
Starting a repair with Claude in this terminal. It acts as you and asks before each step, as your settings allow.
Its record appears in Work once Altitude runs.

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


# ---- review-only alternative: two panes, exactly a task's layout (ignored captures/) ----
alt = out / "captures"
STEPS_PANEL = ('<aside class="live"><div class="lh"><h2><span class="pulse"></span>Steps</h2></div><div class="lbody">'
               + BRIEF.replace("details", "div").replace(CHEVR, "")
               + STARTED + STEPS_A + LOOK + steps(STEP_B, STEPS_C) + '</div></aside>')
board("AltDesktop", 1440, 900,
      '<div style="display:grid;grid-template-columns:260px minmax(0,1fr) 480px;height:100%">' + rail(True, 1) +
      '<main class="pane">' + desk_header("waiting", "1 h 41 min").replace('<div class="acts">', f'<div class="acts"><span class="ib on">{PANEL}</span>') +
      '<div class="convo"><div class="col"><div class="day">Today</div>' + ASKED + FIND + question() + '</div><div style="height:16px"></div>'
      + composer("Message the repair") + '<div style="height:20px"></div></div></main>' + STEPS_PANEL + '</div>', alt)
board("AltPhone", 390, 844,
      '<div class="m task-phone">' + phone_header("waiting", "1 h 41 min left")
      + '<div class="task-tabs"><span class="on">Conversation</span><span>Steps</span></div>'
      + '<div class="mbody"><div class="mcol">' + ASKED + FIND + question() + '</div></div>'
      + '<div class="mcomp">' + composer("Message the repair") + '</div>' + tabbar("work", 1) + '</div>', alt)
print("written")
