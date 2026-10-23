"""Write the chat boards beside this file: python3 design/wireframes/chats/gen.py

Fictional data. Phone 390×844, desktop 1440×900. Review-only captures go to the ignored shots/ folder.
"""
import pathlib

out = pathlib.Path(__file__).resolve().parent
HEAD = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Chats: {title}</title>
<link rel="stylesheet" href="../wireframes.css"><link rel="stylesheet" href="../repair/repair.css"><link rel="stylesheet" href="chats.css"></head>
<body><div class="root" data-theme="light" style="width:{w}px;height:{h}px">
'''
TAIL = '''</div><script>if(location.search.indexOf('dark')>=0)document.querySelector('.root').dataset.theme='dark'</script></body></html>
'''


def icon(path, cls="i"):
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true">{path}</svg>'


CHEVL = icon('<path d="M15 6l-6 6 6 6"/>')
CHEVR = icon('<path d="M9 6l6 6-6 6"/>', "i sm")
CHEVD = icon('<path d="M6 9l6 6 6-6"/>', "i sm")
PLUS = icon('<path d="M12 5v14M5 12h14"/>', "i sm")
MIC = icon('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>', "i lg")
UP = icon('<path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>', "i lg")
IMG = icon('<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="M20.5 16l-5-5-8 8.5"/>', "i lg")
PANEL = icon('<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M14.5 4.5v15"/>')
MORE = icon('<circle cx="6" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="18" cy="12" r="1.2" fill="currentColor" stroke="none"/>')
X = icon('<path d="M6 6l12 12M18 6L6 18"/>', "i sm")
TABS = {"chat": ("Chat", '<path d="M20 14.5a2 2 0 0 1-2 2H9l-4.5 4v-4H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/>'),
        "work": ("Work", '<rect x="3.5" y="5.5" width="17" height="14" rx="2"/><path d="M8 5.5V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v1.5"/><path d="M3.5 11h17"/>'),
        "needs": ("Needs you", '<path d="M3 13l2.6-7.2A1 1 0 0 1 6.5 5h11a1 1 0 0 1 .9.8L21 13v5.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/><path d="M3 13h5l1.4 2.5h5.2L16 13h5"/>'),
        "monitor": ("Monitor", '<path d="M3 12h3.5l2.5-6.5 4 13 2.5-6.5H21"/>')}
U = 'class="u"'
NAME = "Why is the export slow?"


def board(name, w, h, body):
    (out / f"{name}.html").write_text(HEAD.format(title=name, w=w, h=h) + body + TAIL)


def tabbar(on="work", needs=2):
    def tab(key):
        label, path = TABS[key]
        badge = f'<span class="badge">{needs}</span>' if key == "needs" and needs else ""
        return f'<div class="tab{" on" if key == on else ""}">{icon(path, "i lg")}{label}{badge}</div>'
    return '<nav class="tabbar">' + "".join(tab(key) for key in TABS) + '</nav>'


def composer(placeholder="Message the agent", model="Opus · High", text=""):
    field = f'<div class="draft">{text}</div>' if text else f'<div class="ph2">{placeholder}</div>'
    send = "icb send" if text else "icb send disabled"
    return (f'<div class="composer">{field}<div class="crow"><span class="icb">{IMG}</span><span class="l3pill">{model}{CHEVD}</span>'
            f'<span style="flex:1"></span><span class="icb">{MIC}</span><span class="{send}" aria-label="Send">{UP}</span></div></div>')


def sysline(text, at="", fault=False):
    stamp = f'<span class="muted">{at}</span>' if at else ""
    return f'<div class="sys"><span class="ln"></span><span class="d{" fault" if fault else ""}"></span>{stamp}<span>{text}</span><span class="ln"></span></div>'


# ---- Work: the Chats section ----
def trow(title, meta, dot="dot", muted=False):
    style = ' style="opacity:.7"' if muted else ""
    return (f'<a class="trow" href="#"{style}><span class="{dot}"></span><div style="flex:1;min-width:0">'
            f'<div class="tt one">{title}</div><div class="tm">{meta}</div></div>{CHEVR}</a>')


CHAT_ROWS = {
    "answering": trow(NAME, "Answering · Opus on Claude"),
    "replied": trow("Try the new index on a copy of prod data", "Replied 12 min ago", "dot idle"),
    "access": trow("Tidy my downloads and caches", "<span class=\"amber\">Computer access</span> · replied 2 h ago", "dot idle"),
    "failed": trow("Sketch the beta stage’s config", "Couldn’t answer · 1 h ago", "dot bad"),
    "idle": trow("Flaky pagination test", "You wrote yesterday", "dot idle"),
    "starting": trow("New chat", "Starting · Opus on Claude"),
}


def chats_section(*rows, archived=0, empty=False):
    head = f'<div class="sh">Agent chats{f" <span>{len(rows)}</span>" if rows else ""}<a href="#" class="newchat">{PLUS}New chat</a></div>'
    if empty:
        return f'<div class="chats">{head}</div>'
    fold = f'<div class="fold">{CHEVR}Archived · {archived}</div>' if archived else ""
    return f'<div class="chats">{head}' + "".join(CHAT_ROWS[row] for row in rows) + fold + '</div>'


def current():
    return ('<div><div class="sh">Current <span>2</span></div>'
            + '<a class="trow" href="#"><span class="dot held"></span><div style="flex:1;min-width:0"><div class="tt">Index rollout</div>'
              '<div class="tm turn">Your turn · 2 questions</div><div class="tm">L2 waiting · Opus on Claude · started 40 min ago</div></div>' + CHEVR + '</a>'
            + trow("Keep pagination working", "L2 working · Opus on Claude · started 32 min ago") + '</div>')


def work_rows(chats):
    return (current() + f'<div class="fold">{CHEVR}Done this week · 2</div>' + chats
            + f'<a class="trow" href="#" style="border-top:0"><div style="flex:1"><div class="tt">Routines · 3</div></div>{CHEVR}</a>')


PHONE_WORK_HEAD = ('<header class="mh"><div><span class="name">atlas' + CHEVD + '</span>'
                   '<div class="subl" style="padding-left:4px;margin-top:-6px">L3 · Ready</div></div><span class="sp"></span><span class="ib">' + MORE + '</span></header>')


def phone_work(chats):
    return ('<div class="m"><div></div>' + PHONE_WORK_HEAD +
            '<div class="mbody top"><div class="wh"><h2>Work</h2><span>2 current · 2 done this week</span></div>'
            f'<span class="newtasks" style="margin:0 0 4px"><span><b>Auto</b> · Default</span>{CHEVD}</span>'
            + work_rows(chats) + '</div><div></div>' + tabbar("work") + '</div>')


board("PhoneWork", 390, 844, phone_work(chats_section("answering", "replied", "access", "idle", archived=4)))
board("PhoneWorkEmpty", 390, 844, phone_work(chats_section(empty=True)))

RAIL = ('<aside class="rail"><div class="brand"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div>'
        '<div class="ri">' + icon(TABS["needs"][1]) + 'Needs you<span class="badge">2</span></div>'
        '<div class="rsec"><span>Projects</span></div>'
        '<div class="ri sel"><span class="dot held"></span>atlas</div><div class="ri"><span class="dot idle"></span>harbor</div>'
        '<div style="flex:1"></div><div class="ri" style="height:36px">' + icon(TABS["monitor"][1]) + 'Monitor</div>'
        '<div class="who"><span class="avatar">Y</span>You</div></aside>')
PROJECT_CHAT = ('<div class="day">Today</div><div class="me">What remains on the migration?</div>'
                '<div class="l3"><p>The index rollout waits for your two answers; pagination is running and should open its PR within the hour.</p></div>')
board("DesktopWork", 1440, 900,
      '<div style="display:grid;grid-template-columns:260px minmax(0,1fr) 340px;height:100%">' + RAIL +
      '<main class="pane"><header class="ph"><div><h1>atlas</h1><div class="sub">L3 answered 5 min ago on Claude · 2 tasks in flight · 2 questions need you</div></div>'
      f'<div class="acts"><span class="ib on">{PANEL}</span><span class="ib">{MORE}</span></div></header>'
      f'<div class="convo"><div class="col">{PROJECT_CHAT}</div><div style="height:22px"></div>'
      + composer("Message L3 about atlas", "L3 · <b>Auto</b> · Default effort") + '<div style="height:20px"></div></div></main>'
      '<aside class="work"><div class="wh"><h2>Work</h2><span>2 current · 2 done this week</span></div>'
      + work_rows(chats_section("answering", "replied", "access", "failed", "idle", archived=4)) + '</aside></div>')


# ---- a chat ----
def tool(name, call, hint, bad=False):
    return (f'<div class="tool"{" data-error" if bad else ""} data-kind="{"command" if name == "$" else "file"}"><b>{name}</b>'
            f'<span class="call">{call}</span><span class="n{" bad" if bad else ""}">{hint}</span></div>')


def worked(summary, *rows, open_=False):
    """A turn's steps, folded to one line under your message; open shows Live session's rows."""
    body = '<div class="steps">' + "".join(rows) + '</div>' if open_ and rows else ""
    return f'<div class="worked{" open" if open_ else ""}"><div class="wl">{CHEVR}<span>{summary}</span></div>{body}</div>'


def working(summary, latest):
    return (f'<div class="worked live"><div class="wl"><span class="spin"></span><span>{summary}</span></div>'
            f'<div class="steps">{latest}</div></div>')


ASK1 = '<div class="me">Why does the CSV export take 40 seconds on the big workspace? Find out, don’t change anything yet.</div>'
STEPS1 = worked("Worked 3 min · 14 steps")
REPLY1 = ('<div class="l3"><p>The export loads every row’s comments one query at a time: 18,000 queries for the big workspace. '
          'The time is in <code>export/rows.py</code>, in <code>comments_for(row)</code> inside the loop.</p>'
          '<p>Loading comments for all rows in one query should bring it under two seconds. Want me to try it here and time it?</p></div>')
ASK2 = '<div class="me">Yes, try it and run the export tests.</div>'
LIVE2 = working("Working · 2 min · 6 steps", tool("$", "pytest tests/test_export.py -q", "running · 40 s"))
STREAM = '<div class="l3"><p>The single query works: the big workspace exports in 1.6 seconds. Running the rest of the export tests now<span class="caret"></span></p></div>'


def phone_header(name=NAME, sub="Opus on Claude", dot="dot idle", action="more"):
    act = {"more": f'<span class="ib">{MORE}</span>', "stop": '<span class="btn">Stop</span>',
           "stopping": '<span class="btn" aria-disabled="true"><span class="spin"></span>Stopping…</span>', "": ""}[action]
    return (f'<header class="mh rh"><a href="#" aria-label="Back" class="back">{CHEVL}</a>'
            f'<div class="rt"><div class="tt">{name}</div><div class="tm"><span class="{dot}"></span>{sub}</div></div>{act}</header>')


def phone_chat(inner, header=None, foot=None, overlay=""):
    return ('<div class="m rpage" style="position:relative"><div></div>' + (header or phone_header()) +
            f'<div class="mbody"><div class="mcol">{inner}</div></div>'
            f'<div class="mcomp">{foot if foot is not None else composer()}</div>' + tabbar("work") + overlay + '</div>')


DAY = '<div class="day">Today</div>'
STARTED = sysline("Started · Opus on Claude · its own copy of main", "10:02")
board("PhoneChat", 390, 844, phone_chat(DAY + STARTED + ASK1 + STEPS1 + REPLY1 + ASK2 + LIVE2 + STREAM,
                                         phone_header(sub="Answering · Opus on Claude", dot="dot", action="stop")))

NEW_EMPTY = '<p class="empty">Works in its own copy of atlas’s code, made from main when you send.</p>'
board("PhoneNew", 390, 844, phone_chat(NEW_EMPTY, phone_header("New chat", "Opus on Claude"),
                                        composer(text="Why does the CSV export take 40 seconds on the big workspace?")))

STEPS_OPEN = worked("Worked 3 min · 14 steps",
                    tool("Read", "export/rows.py", "212 lines"),
                    tool("$", "python -m cProfile -s cumtime scripts/export.py big", "48 lines"),
                    tool("$", "grep -n comments_for export/*.py", "3 lines"),
                    tool("Read", "models/comment.py", "90 lines"), open_=True)
REPLY2 = ('<div class="l3"><p>Done. One query for all comments: the big workspace exports in 1.6 seconds, and the 31 export tests pass. '
          'The change is in this chat’s copy: <code>export/rows.py</code>, 1 file.</p></div>')
QUEUED = ('<div class="me queued">Also check the PDF export<div class="qline">Waits for the reply · <span class="u">Remove</span></div></div>')


# ---- menu, sheets ----
def menu(access=False):
    acc = ("Computer access is on · <b>Turn off</b>" if access else "Computer access…")
    return ('<div class="scrimx"></div><div class="bsheet menu"><div class="grab"></div>'
            '<div class="mi">Rename</div>'
            '<div class="mi">Make a task…</div>'
            f'<div class="mi">{acc}</div>'
            '<div class="mi">Archive</div>'
            '<p class="muted mmeta">Opus on Claude · started Oct 21 · its copy: 1 changed file</p></div>')


def access_sheet(state="ask"):
    btns = {"ask": '<span class="btn primary wide">Turn on computer access</span><span class="btn ghost wide">Cancel</span>',
            "saving": '<span class="btn primary wide" aria-disabled="true"><span class="spin"></span>Turning on…</span><span class="btn ghost wide" aria-disabled="true">Cancel</span>',
            "refused": '<span class="btn primary wide">Turn on computer access</span><span class="btn ghost wide">Cancel</span><p class="rerr">Computer access isn’t available in the container installation.</p>'}[state]
    return ('<h2>Let this chat work on your computer?</h2>'
            '<p>From your next message until you turn it off, its agent can <b>read and change the files in your home folder</b> '
            'and run your programs, as well as its own copy of the code. What it reads goes to Claude, and a web page or file it reads '
            'could try to steer it.</p>'
            '<p>Your sign-ins and keys, Altitude’s records, your other projects and other tasks’ copies stay hidden from it, '
            'and your services stay out of reach.</p>'
            f'<div class="sbtns">{btns}</div>')


def task_sheet(state="ask"):
    field = ('<div class="field ta">Make the CSV and PDF exports load comments in one query, with a test for the big workspace.</div>'
             if state != "empty" else '<div class="field ta ph">For example: ship the faster export with a test</div>')
    btn = {"ask": '<span class="btn primary wide">Make task</span>',
           "empty": '<span class="btn primary wide" aria-disabled="true" style="opacity:.45">Make task</span>',
           "saving": '<span class="btn primary wide" aria-disabled="true"><span class="spin"></span>Making…</span>'}[state]
    return ('<h2>Make a task from this chat</h2>'
            '<label class="flab">Title</label><div class="field">Load export comments in one query</div>'
            '<label class="flab" style="margin-top:12px">What should it deliver?</label>' + field +
            '<p class="muted">Its changes (1 file) move to the task, with this conversation as its context. '
            'The chat goes on in a fresh copy of main.</p>'
            f'<div class="sbtns">{btn}<span class="btn ghost wide">Cancel</span></div>')


def archive_sheet():
    return ('<h2>Archive this chat?</h2>'
            '<p>Its copy of the code has changes no task has: <b>1 changed file</b>. Archiving deletes them. '
            'The conversation stays, and Reopen starts a fresh copy from main.</p>'
            '<div class="sbtns"><span class="btn wide">Make a task first</span><span class="btn danger wide">Archive and delete changes</span>'
            '<span class="btn ghost wide">Cancel</span></div>')


def sheet(body):
    return f'<div class="scrimx"></div><div class="bsheet"><div class="grab"></div>{body}</div>'


DONE_INNER = DAY + ASK1 + STEPS1 + REPLY1 + ASK2 + worked("Worked 6 min · 22 steps") + REPLY2
board("PhoneMenu", 390, 844, phone_chat(DONE_INNER, overlay=menu()))
board("PhoneAccess", 390, 844, phone_chat(DONE_INNER, overlay=sheet(access_sheet())))
board("PhoneTask", 390, 844, phone_chat(DONE_INNER, overlay=sheet(task_sheet())))
board("PhoneArchive", 390, 844, phone_chat(DONE_INNER, overlay=sheet(archive_sheet())))

TASK_CARD = ('<div class="tcard"><span class="dot q"></span><div style="flex:1;min-width:0"><div class="tt">Load export comments in one query</div>'
             '<div class="tm">Queued · waits for a free task slot</div></div>' + CHEVR + '</div>')
ASKED_TASK = sysline("Task made by you · its changes moved to the task; a fresh copy of main", "10:31")
ACCESS_ON = sysline("Computer access on · by you", "10:40")
ASK3 = '<div class="me">Now clear atlas’s export cache and the test exports in my Downloads.</div>'
REPLY3 = ('<div class="l3"><p>Cleared <code>~/.cache/atlas/exports</code> (2.1 GB) and removed 38 <code>atlas-export-*.csv</code> '
          'files from <code>~/Downloads</code> (410 MB). Nothing else in Downloads was touched.</p></div>')

# ---- desktop ----
def desk_header(name=NAME, chips='<span class="chip">Opus on Claude</span>', dot="dot idle", act="more"):
    acts = {"more": f'<span class="ib">{MORE}</span>', "stop": f'<span class="btn hbtn">Stop<span class="esc">Esc</span></span><span class="ib">{MORE}</span>'}[act]
    return ('<header class="thead"><div class="trow1"><span class="crumb">' + CHEVL.replace('class="i"', 'class="i sm"') + 'Work</span>'
            f'<h1><span class="{dot}"></span>{name}</h1><div class="acts">{acts}</div></div>'
            f'<div class="chips">{chips}</div></header>')


ACCESS_CHIP = '<span class="chip held">Computer access</span>'


def desktop(header, inner, foot=None):
    return ('<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:100%">' + RAIL +
            '<main class="pane">' + header +
            f'<div class="convo"><div class="col">{inner}</div><div style="height:16px"></div>{foot if foot is not None else composer()}<div style="height:20px"></div></div></main></div>')


DESK_INNER = (DAY + ASK2 + worked("Worked 6 min · 22 steps",
                                 tool("Edit", "export/rows.py", "changed"),
                                 tool("$", "python scripts/export.py big --time", "1.6 s"),
                                 tool("$", "pytest tests/test_export.py -q", "31 passed"), open_=True) + REPLY2
              + ASKED_TASK + TASK_CARD + ACCESS_ON + ASK3 + worked("Worked 1 min · 4 steps") + REPLY3)
board("Desktop", 1440, 900, desktop(desk_header(chips='<span class="chip">Opus on Claude</span>' + ACCESS_CHIP), DESK_INNER))


# ---- every state ----
def st(label, note, body):
    return f'<div class="st"><div class="lab">{label}<span>{note}</span></div><div class="statebox">{body}</div></div>'


def work_box(body):
    return f'<div class="work wbox">{body}</div>'


def dlg(body):
    return f'<div class="dlg">{body}</div>'


FAILED = ('<p class="cstatus bad"><span class="dot bad"></span><span>Couldn’t answer: Claude’s weekly allowance is used. '
          f'Choose another model below, or <a href="#" {U}>Retry</a> after it resets.</span></p>')
CUT = f'<p class="cstatus bad"><span class="dot bad"></span><span>The reply was cut off: Altitude stopped while it ran. <a href="#" {U}>Retry</a></span></p>'
NEWSESSION = sysline("New session on Codex · it was given this conversation so far", "11:05")
STOPPED = sysline("Stopped by you · the command it was running was cut off", "10:20")
ARCHIVED_DOCK = '<div class="adock"><span class="muted">Archived · Oct 22</span><span class="btn">Reopen</span><span class="btn ghost">Delete…</span></div>'
DELETE = ('<div class="dlg"><h2>Delete this chat?</h2><p>Its conversation and session are removed for good.</p>'
          '<div class="sbtns"><span class="btn danger">Delete chat</span><span class="btn ghost">Cancel</span></div></div>')
RENAME = ('<header class="thead"><div class="trow1"><span class="crumb">' + CHEVL.replace('class="i"', 'class="i sm"') + 'Work</span>'
          '<div class="field rename">Why is the export slow?</div><span class="btn primary">Save</span><span class="btn ghost">Cancel</span></div></header>')
MONITOR = ('<div class="mrow"><span class="dot"></span><div style="flex:1"><div class="tt">Chat · Why is the export slow?</div>'
           '<div class="tm">atlas · Opus on Claude · answering for 2 min</div></div><div class="ctx"><div class="meter"><i style="width:34%"></i></div><small>34% context · 8 s ago</small></div></div>')
states = [
    ("1 · Work, no chats", "Agent chats after Done this week, before Routines; just New chat", work_box(chats_section(empty=True))),
    ("2 · Work, chats", "newest activity first; the running dot while it answers; Archived folds the rest",
     work_box(chats_section("starting", "answering", "replied", "access", "failed", "idle", archived=4))),
    ("3 · Archived opened", "muted rows, each opens its chat read-only",
     work_box('<div class="chats"><div class="sh">Agent chats <span>1</span><a href="#" class="newchat">' + PLUS + 'New chat</a></div>' + CHAT_ROWS["replied"]
              + f'<div class="fold">{CHEVD}Archived · 2</div>' + trow("Bisect the slow build", "Archived Oct 22", "dot idle", True)
              + trow("Read the auth flow with me", "Archived Oct 14", "dot idle", True) + '</div>')),
    ("4 · New chat", "New chat opens an empty chat; the first message makes its copy and names it; nothing is kept until you send",
     desk_header("New chat") + NEW_EMPTY),
    ("5 · Starting", "the copy is made and the session starts", sysline("Making its copy of main…") + ASK1 + working("Starting · Opus on Claude", "")),
    ("6 · Answering", "steps fold to one line with the latest step under it; the reply streams; Stop in the header",
     desk_header(chips='<span class="chip run">Answering</span><span class="chip">Opus on Claude</span>', dot="dot", act="stop") + ASK2 + LIVE2 + STREAM),
    ("7 · Replied", "the steps fold to one line; it opens to Live session's rows", STEPS_OPEN + REPLY2),
    ("8 · A message while it answers", "waits and goes with the next turn; Remove takes it back", LIVE2 + QUEUED),
    ("9 · Stop", "ends the turn at once; what it wrote so far stays", STREAM + STOPPED),
    ("10 · Couldn't answer", "the engine's reason; the model pill switches engine", FAILED + CUT),
    ("11 · A new session", "when the old session can't resume, or you pick another engine", NEWSESSION),
    ("12 · Altitude restarts", "the turn runs in its own job, so the reply arrives once Altitude is back",
     sysline("Altitude restarted · the reply continued", "10:14")),
    ("13 · Menu", "Rename, Make a task…, Computer access…, Archive; on desktop the ⋯ button opens the same list; on the phone Stop takes ⋯'s place while it answers", '<div class="dlg menu-d">' + menu().split('<div class="grab"></div>')[1]),
    ("14 · Rename", "in place; Enter saves, Escape cancels; 1–80 characters", RENAME),
    ("15 · Computer access", "asks first; on, the chip and a line until you turn it off; off applies to the next message", dlg(access_sheet()) + dlg(access_sheet("saving")) + dlg(access_sheet("refused"))
     + desk_header(chips='<span class="chip">Opus on Claude</span>' + ACCESS_CHIP) + ACCESS_ON + sysline("Computer access off · by you", "11:30")),
    ("16 · Make a task", "Altitude makes the task from your words and the chat's pinned changes; its card joins the chat", dlg(task_sheet()) + dlg(task_sheet("empty")) + ASKED_TASK + TASK_CARD),
    ("17 · Archive", "asks only when the copy has changes no task has; otherwise archives at once", dlg(archive_sheet())),
    ("18 · Archived chat", "read-only; Reopen makes a fresh copy and continues the same session; Delete removes it for good", REPLY2 + sysline("Archived by you", "Oct 22") + ARCHIVED_DOCK + DELETE),
    ("19 · Monitor", "a chat's running turn is a session row, like a routine run", MONITOR),
    ("20 · Loading, failed read", "a skeleton in the conversation's shape; one sentence and Retry",
     '<div class="skel"></div><div class="skel short"></div>' + f'<p class="muted">Couldn’t load this chat. <a href="#" {U}>Retry</a></p>'),
]
board("States", 1200, 4800,
      '<div style="padding:28px 40px 6px"><h1 style="font-size:20px;margin:0">Agent chats: every state</h1>'
      '<p class="muted" style="max-width:900px">Phone uses the same content full width with 44px controls; sheets are bottom sheets there and centered dialogs on desktop.</p></div>'
      '<div class="sheetgrid">' + "".join(st(*s) for s in states) + '</div>')
