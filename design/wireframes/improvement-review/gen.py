"""Write the improvement suggestions boards beside this file: python3 design/wireframes/improvement-review/gen.py"""
import pathlib
out = pathlib.Path(__file__).resolve().parent
HEAD = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Improvement review: {title}</title>
<link rel="stylesheet" href="../wireframes.css"><link rel="stylesheet" href="card.css"></head>
<body><div class="root" data-theme="light" style="width:{w}px;height:{h}px">
'''
TAIL = '''</div><script>if(location.search.indexOf('dark')>=0)document.querySelector('.root').dataset.theme='dark'</script></body></html>
'''
CHECK = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>'

def sg(chip, title, body, seen, measure=None, acts="normal", extra=""):
    chipc = "chip wf" if chip == "How agents work" else "chip proj"
    seen_html = " · ".join(f'<a href="#">{s}</a>' for s in seen[:3])
    more = f' · <a href="#">{len(seen)-3} more</a>' if len(seen) > 3 else ""
    m = f'<div class="meta">{measure}</div>' if measure else ""
    if acts == "normal":
        a = '<div class="acts"><span class="btn" style="color:var(--accent);border-color:var(--accent)">Make a task</span><span class="btn ghost">Dismiss</span></div>'
    elif acts == "pending":
        a = '<div class="acts"><span class="btn" style="width:104px;color:var(--accent);border-color:var(--accent)" aria-label="Make a task, saving"><span class="spin"></span></span><span class="btn ghost" style="opacity:.45">Dismiss</span></div>'
    elif acts == "accepted":
        a = f'<div class="ok">{CHECK}Accepted · L3 is opening a task</div>'
    elif acts == "task":
        a = '<div class="tcard"><span class="dot"></span><div><div class="tt">Stop re-asking merge approval after a routine rebase</div><div class="tm">Running · Opus on Claude · 2 min</div></div><span class="go"><svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg></span></div>'
    elif acts == "failed":
        a = '<div class="acts"><span class="btn" style="color:var(--accent);border-color:var(--accent)">Make a task</span><span class="btn ghost">Dismiss</span></div><div class="err">Not saved. Try again.</div>'
    else:
        a = ""
    return (f'<div class="sg"><div><span class="{chipc}">{chip}</span></div><div class="t">{title}</div>'
            f'<p>{body}</p>{extra}<div class="meta">Seen in&nbsp; {seen_html}{more}</div>{m}{a}</div>')

def dismissed(chip, title):
    chipc = "chip wf" if chip == "How agents work" else "chip proj"
    return f'<div class="sg dim"><span class="{chipc}">{chip}</span><span class="t">{title}</span><span class="meta">Dismissed · <a href="#" style="text-decoration:underline;color:var(--accent);opacity:1">Undo</a></span></div>'

def lapsed(chip, title):
    chipc = "chip wf" if chip == "How agents work" else "chip proj"
    return f'<div class="sg dim"><span class="{chipc}">{chip}</span><span class="t">{title}</span><span class="meta">Not decided</span></div>'

S1 = ("How agents work", "1. Owners re-ask merge approval after a routine rebase",
      "In 3 of 14 tasks the owner re-asked you to approve a merge when only a rebase onto main had changed the PR. One sentence in the owner instructions would stop it.",
      ["Release notes for 0.4", "Tidy the settings page", "Add search to the task list", "Fix voice on Safari"],
      "Questions re-asked with no reply in between in 3 of 14 tasks")
S2 = ("altitude", "2. The phone swipe walkthrough fails the web shard about once a week",
      "Two landings waited on a rerun of the same browser walkthrough. Making its tab-switch step wait for the view to settle would remove the reruns.",
      ["Tidy the settings page", "Release notes for 0.4"],
      "CI rechecks in 4 of 14 tasks")
S3 = ("How agents work", "3. Review rounds repeat on unchanged findings",
      "One task ran nine implementation reviews, most re-reporting findings already assessed. Passing earlier assessments to the reviewer would let it skip them.",
      ["Add search to the task list"],
      "Review rounds beyond the second in 11 of 14 tasks")

def card(sgs, since="", footer=True, details=False, time="Oct 12, 09:40", win="14 tasks from Oct 5–12"):
    det = ('<div class="det"><dl><dt>Read</dt><dd>14 tasks that finished Oct 5–12; their questions, reviews, landings and conversations; project chat Oct 5–12</dd>'
           '<dt>Left out</dt><dd>2 tasks listed by title only: over the size limit</dd><dt>L3 dropped</dt><dd>Suggestion 2: already owned by “Fix voice on Safari”</dd><dt>Model</dt><dd>Claude Sonnet 5.5</dd><dt>Usage</dt><dd>24,800 of its 86,000-token limit · L3 check 21,400 tokens</dd><dt>Ran</dt><dd>Oct 12, 09:38–09:40</dd></dl></div>') if details else ""
    ft = (f'<div class="ft"><a href="#">{"Hide details" if details else "Details"}</a></div>{det}') if footer else ""
    return (f'<div class="irc"><div class="hd"><b>Improvement suggestions</b><time>{time}</time></div>'
            f'<div class="win">{win}</div>{since}{"".join(sgs)}{ft}</div>')

def sysline(text, dot="d", show=True):
    return f'<div class="sys"><span class="ln"></span><span class="{dot}"></span><span>{text}</span>{"<a href=\"#\">Show</a>" if show else ""}<span class="ln"></span></div>'

def st(label, note, body):
    return f'<div class="st"><div class="lab">{label}<span>{note}</span></div>{body}</div>'

RUN = '<div class="sys"><span class="ln"></span><span class="d run"></span><span>Improvement suggestions · reading 14 tasks from Oct 5–12</span><a href="#" style="text-decoration:underline">Stop</a><span class="ln"></span></div>'
SINCE = ('<div class="since"><b>Since earlier changes</b>'
         '<div>Questions re-asked with no reply in between: 3 of 14 tasks before, 0 of 11 since · <a href="#">Stop re-asking merge approval after a routine rebase</a></div></div>')
SEEN = '<div class="meta">Seen again after <a href="#">Pass earlier assessments to the reviewer</a></div>'

# Desktop sheet
w, h = 1200, 4100
body = ['<div style="padding:36px 40px 10px"><h1 style="font-size:20px;font-weight:600;margin:0">Improvement suggestions in project chat</h1>'
        '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:900px">Each run posts one card in project chat, and the same card under Suggestions in Needs you while any suggestion is undecided, never counted and never alerting a device. Each suggestion names its target, what keeps happening, where it was seen and what it would be measured by. Make a task hands it to L3; Dismiss records it.</p></div><div class="sheetgrid">']
body.append(st("1 · Asked for, running, then L3 checking", "Stop is on the running line; the same line then says L3 is checking",
    '<div style="display:flex;flex-direction:column;gap:14px"><div class="me" style="font-size:14px">Get improvement suggestions.</div>'
    '<div class="l3" style="font-size:14px"><p>Started. It reads the 14 tasks that finished since Oct 5; the suggestions will appear here.</p></div>'
    + RUN + '<div class="sys"><span class="ln"></span><span class="d run"></span><span>Improvement suggestions · L3 is checking 3</span><a href="#" style="text-decoration:underline">Stop</a><span class="ln"></span></div>' + '</div>'))
body.append(st("2 · Finished with suggestions", "one card, up to three suggestions; the footer opens Details",
    card([sg(*S1), sg(*S2), sg(*S3)])))
body.append(st("3 · Make a task pressed, then accepted", "spinner in place, same width; then a check and who acts next",
    card([sg(*S1, acts="pending"), sg(*S2, acts="accepted")], footer=False)))
body.append(st("4 · Task opened, one dismissed", "the accepted line becomes the ordinary task card; a dismissed suggestion folds",
    card([sg(*S1, acts="task"), dismissed(S2[0], S2[1]), sg(*S3)], footer=False)))
body.append(st("5 · Save failed", "the buttons come back with the reason under them",
    card([sg(*S3, acts="failed")], footer=False)))
body.append(st("6 · Details open", "what was read, what was left out, when it ran",
    card([sg(*S2)], details=True)))
body.append(st("7 · Later run", "measured accepted changes lead the card; a recurring pattern says so",
    card([sg("How agents work", "1. Owners still re-run reviews after an unchanged rebase",
             "Two tasks requested a fresh implementation review when only main had moved. The review rules could treat a clean rebase like the approval rule does.",
             ["Export the work list", "Fix voice on Safari"], "Review rounds beyond the second in 5 of 11 tasks", extra=SEEN)], since=SINCE, time="Oct 19, 10:02", win="11 tasks from Oct 12–19")))
body.append(st("8 · Other endings", "system lines; Show opens the details or the reason",
    '<div style="display:flex;flex-direction:column;gap:16px;padding-top:6px">'
    + sysline("Improvement suggestions · 14 tasks from Oct 5–12 · nothing to suggest")
    + sysline("Improvement suggestions failed · it went over its usage limit", "d fault")
    + sysline("Improvement suggestions failed · it could not run isolated", "d fault")
    + sysline("Improvement suggestions failed · Claude Sonnet 5.5 is unavailable", "d fault")
    + sysline("Improvement suggestions failed · L3 did not finish checking", "d fault")
    + sysline("Improvement suggestions stopped by you", "d", False)
    + '<div class="me" style="font-size:14px">Get improvement suggestions.</div><div class="l3" style="font-size:14px"><p>Only 3 tasks have finished since the last suggestions on Oct 12; it needs 5.</p></div></div>'))
body.append(st("9 · Lapsed, and dismissed with Undo", "seven days after posting, and only then; the card leaves Needs you once nothing on it waits",
    card([sg(*S1, acts="task"), lapsed(S2[0], S2[1]), dismissed(S3[0], S3[1])], footer=False)))
body.append(st("10 · In Needs you, after a question", "a Suggestions section after everything that needs you, grouped by project; not counted; the header opens the card in chat",
    '<div style="font-weight:600;font-size:15px;margin-bottom:8px">altitude</div><div class="irc" style="padding:14px 16px;margin-bottom:12px"><div style="font-size:12px;color:var(--text-muted)">L3 asks · 10 min</div><div style="font-weight:600;margin:4px 0"><a href="#" style="text-decoration:underline;color:var(--accent)">Export the work list</a></div><div>Should the export include archived tasks?</div></div><div style="font-size:13px;color:var(--text-muted);margin:0 0 8px">Suggestions</div><div style="font-weight:600;font-size:15px;margin-bottom:8px">altitude</div>' + card([sg(*S2)], footer=False).replace('<b>Improvement suggestions</b>', '<b><a href="#" style="text-decoration:underline;color:var(--accent)">Improvement suggestions</a></b>', 1)))
body.append(st("11 · Accepted, but no task yet", "L3's turn failed or ended without a task; Try again queues the same single turn",
    card([sg(*S1, acts="none") .replace('<div class="meta">Questions re-asked', '<div class="meta">Questions re-asked') + '<div class="err" style="color:var(--text)">Accepted · no task yet · <a href="#" style="text-decoration:underline;color:var(--accent)">Try again</a></div>'], footer=False)))
body.append('</div>')
(out / "States.html").write_text(HEAD.format(title="States", w=w, h=h) + "".join(body) + TAIL)

def phone(name, inner):
    top = ('<div class="m"><div></div><header class="mh"><div><span class="name">altitude</span>'
           '<div class="subl" style="padding-left:4px;margin-top:-6px">L3 · Ready</div></div><span class="sp"></span></header>'
           f'<div class="mbody"><div class="mcol">{inner}</div></div>'
           '<div class="mcomp"><div class="composer"><div class="ph2">Message L3</div></div></div>'
           '<nav class="tabbar"><div class="tab on">Chat</div><div class="tab">Work</div><div class="tab">Needs you</div><div class="tab">Monitor</div></nav></div>')
    (out / f"{name}.html").write_text(HEAD.format(title=name, w=390, h=844) + top + TAIL)

phone("Phone", '<div class="me">Get improvement suggestions.</div><div class="l3"><p>Started. It reads the 14 tasks that finished since Oct 5; the suggestions will appear here.</p></div>'
      + card([sg(*S1), sg(*S2)], footer=False))
phone("PhoneDecided", card([sg(*S1, acts="task"), dismissed(S2[0], S2[1]), sg(*S3, acts="pending")], footer=False))
phone("PhoneRunning", '<div class="me">Get improvement suggestions.</div><div class="l3"><p>Started. It reads the 14 tasks that finished since Oct 5; the suggestions will appear here.</p></div>' + RUN
      + '<div class="me">Stop.</div>' + sysline("Improvement suggestions stopped by you", "d", False)
      + '<div class="sys"><span class="ln"></span><span class="d run"></span><span>Improvement suggestions · reading 9 tasks from Oct 12–16</span><span class="spin" style="width:10px;height:10px;color:var(--text-muted)"></span><span class="ln"></span></div>'
      + '<div class="sys"><span class="ln"></span><span class="d run"></span><span>Improvement suggestions · reading 9 tasks from Oct 12–16</span><a href="#" style="text-decoration:underline">Stop</a><span class="ln"></span></div><div style="text-align:center;font-size:12px;color:var(--danger)">Not stopped. Try again.</div>')
phone("PhoneEndings", card([sg(*S1, acts="task"), lapsed(S2[0], S2[1])], footer=False)
      + sysline("Improvement suggestions · 9 tasks from Oct 12–19 · nothing to suggest")
      + sysline("Improvement suggestions failed · it went over its usage limit", "d fault")
      + sysline("Improvement suggestions failed · L3 did not finish checking", "d fault"))
FIRST = ('<div class="me">Get improvement suggestions.</div><div class="l3"><p>Started. It reads the 14 tasks that finished since Oct 5; say “stop” to stop it.</p></div>'
      '<div class="l3"><p><b>Improvement suggestions of Oct 12</b> · 14 tasks from Oct 5–12</p>'
      '<p><b>1. Owners re-ask merge approval after a routine rebase</b> · How agents work<br>In 3 of 14 tasks the owner re-asked you to approve a merge when only a rebase onto main had changed the PR. One sentence in the owner instructions would stop it.<br>Seen in <a href="#" style="text-decoration:underline">Release notes for 0.4</a>, <a href="#" style="text-decoration:underline">Tidy the settings page</a>, <a href="#" style="text-decoration:underline">Add search to the task list</a> and <a href="#" style="text-decoration:underline">Fix voice on Safari</a></p>'
      '<p><b>3. Review rounds repeat on unchanged findings</b> · How agents work<br>One task ran nine implementation reviews, most re-reporting findings already assessed. Passing earlier assessments to the reviewer would let it skip them.<br>Seen in <a href="#" style="text-decoration:underline">Add search to the task list</a></p>'
      '<p>Dropped 2: “Fix voice on Safari” already covers it.</p></div>')
phone("PhoneFirst", FIRST[FIRST.index('<div class="l3"><p><b>'):]
      + '<div class="me">Do 1, but only the owner instructions. Skip 3.</div>'
      + '<div class="l3"><p>Opened a task for 1, limited to the owner instructions, and recorded 3 as dismissed.</p><div class="tcard"><span class="dot"></span><div><div class="tt">Stop re-asking merge approval after a routine rebase</div><div class="tm">Queued · Opus on Claude</div></div></div></div>')
phone("PhoneFirstEndings", '<div class="me">Get improvement suggestions.</div><div class="l3"><p>Started. It reads the 9 tasks that finished since Oct 12; say “stop” to stop it.</p></div><div class="me">Stop.</div><div class="l3"><p>Improvement suggestions stopped by you.</p></div>'
      + '<div class="l3"><p>Improvement suggestions of Oct 16 · 9 tasks from Oct 12–16 · nothing to suggest.</p></div>'
      + '<div class="l3"><p>Improvement suggestions failed · it went over its usage limit.</p></div>'
      + '<div class="l3"><p>Improvement suggestions failed · L3 did not finish checking.</p></div>'
      + '<div class="me">Accept 2.</div><div class="l3"><p>Suggestion 2 of the Oct 12 review was dropped, so there is nothing to accept. 1 and 3 were offered.</p></div>')
print("written")

desk = ('<div style="display:grid;grid-template-columns:240px 1fr;height:900px;background:var(--surface)">'
        '<aside style="border-right:1px solid var(--hairline);padding:20px 16px;font-size:14px;display:flex;flex-direction:column;gap:6px;background:var(--page)">'
        '<b style="font-size:13px;color:var(--text-muted);font-weight:500;margin-bottom:6px">Projects</b>'
        '<div style="padding:8px 10px;border-radius:8px;background:var(--bubble);font-weight:600">altitude</div><div style="padding:8px 10px">demo</div><div style="padding:8px 10px">harbor</div></aside>'
        '<main style="display:grid;grid-template-rows:64px minmax(0,1fr) auto;min-height:0">'
        '<div class="ph"><div><h1>altitude</h1><div class="sub">L3 · Ready</div></div></div>'
        '<div style="overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end;padding:0 28px">'
        '<div style="max-width:760px;width:100%;margin:0 auto;display:flex;flex-direction:column;gap:16px;padding-bottom:16px">'
        '<div class="me">Get improvement suggestions.</div><div class="l3"><p>Started. It reads the 14 tasks that finished since Oct 5; the suggestions will appear here.</p></div>'
        + card([sg(*S1), sg(*S2, acts="accepted")]) + '</div></div>'
        '<div style="padding:12px 28px 20px"><div class="composer" style="max-width:760px;margin:0 auto"><div class="ph2">Message L3</div></div></div></main></div>')
print("desktop")

deskf = desk.replace('<div class="me">Get improvement suggestions.</div><div class="l3"><p>Started. It reads the 14 tasks that finished since Oct 5; the suggestions will appear here.</p></div>'
        + card([sg(*S1), sg(*S2, acts="accepted")]), eval(FIRST.__repr__()))
assert deskf != desk
(out / "DesktopFirst.html").write_text(HEAD.format(title="DesktopFirst", w=1440, h=900) + deskf + TAIL)
print("desktop first")

pf = out / "PhoneFirst.html"
pf.write_text(pf.read_text().replace('<div class="mbody">','<div class="mbody" style="display:flex;flex-direction:column;justify-content:flex-end;overflow:hidden;margin-top:10px">',1))

# ---- settings, consent sheet, Needs you ----
CHEVL = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>'
U = 'style="text-decoration:underline;color:var(--accent)"'
def switch(on, dim=False):
    bg = "var(--accent)" if on else "var(--hairline)"
    knob = "22px" if on else "2px"
    return (f'<span role="switch" aria-checked="{str(on).lower()}" style="position:relative;display:inline-block;width:44px;height:24px;border-radius:12px;background:{bg};flex-shrink:0;{"opacity:.55" if dim else ""}">'
            f'<span style="position:absolute;top:2px;left:{knob};width:20px;height:20px;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.25)"></span></span>')
SEP = 'border-top:1px solid var(--hairline);padding-top:12px;margin-top:10px'
def ir_section(on=True, line="Next: Monday, Oct 19.", now="Reads the 6 tasks finished since Oct 12.",
               now_state="ok", past=True, status="", reason=""):
    body = (f'<div style="display:flex;justify-content:space-between;gap:12px;align-items:center;padding:4px 0">'
            f'<span><b>Weekly</b><br><small class="muted">{line}</small></span>{switch(on, status.startswith("Saving"))}</div>')
    if now_state == "running":
        body += f'<div style="{SEP}"><small><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--accent);margin-right:6px"></span>Reading 9 tasks · <a href="#" {U}>Stop</a></small></div>'
    else:
        dis = now_state == "disabled"
        body += (f'<div style="display:flex;align-items:center;gap:12px;{SEP}"><span class="btn" style="flex-shrink:0;{"opacity:.45" if dis else ""}">Get suggestions now</span>'
                 f'<small class="muted" style="flex:1">{"" if dis else now}</small></div>')
        if reason:
            body += f'<p style="margin:8px 0 0;font-size:13px">{reason}</p>'
    body += f'<div style="{SEP}"><small class="muted">Counts and short chat quotes · Claude Sonnet 5.5 on your plan · capped per run, plus one L3 check</small></div>'
    if past:
        body += f'<div style="{SEP}"><small class="muted">4 runs · 7 suggestions · 3 became tasks · 2 dismissed · <a href="#" {U}>Latest</a></small></div>'
    st_html = ""
    if status.startswith("Saving"):
        st_html = f'<p class="muted" style="margin:6px 0 0;font-size:13px">{status}</p>'
    elif status:
        st_html = f'<p style="margin:6px 0 0;font-size:13px;color:var(--danger)">{status}</p>'
    return ('<h2 style="margin-top:20px">Improvement suggestions</h2><p class="muted" style="margin-top:-6px">Finds friction that keeps coming back in this project\'s tasks and suggests fixes.</p>'
            f'<div class="card" style="{"opacity:.6" if status.startswith("Saving") else ""}">{body}</div>{st_html}')

ROUTING = ('<h2 style="margin-top:20px">Routing</h2><p class="muted" style="margin-top:-6px">The order Auto tries: Claude and Codex share work by weekly headroom.</p>'
           '<div class="card"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center;padding:6px 0"><span><b>Tasks</b><br><small class="muted">Auto, Prefer or Only an engine.</small></span><span class="field" style="min-width:9rem">Prefer Codex</span></div></div>')
PROJECT = ('<h2 style="margin-top:20px">Project</h2><div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px"><span><b>Setup</b><br><small class="muted">Ready</small></span></div></div>')

def settings_phone(name, inner, overlay=""):
    html = ('<div class="m"><div></div><header class="mh"><a class="btn" href="#" style="padding:0 8px">' + CHEVL + 'Settings</a><h1 style="font-size:17px;margin:0 0 0 12px">altitude</h1></header>'
            f'<div class="route-content" style="overflow:hidden;position:relative">{inner}</div><div></div>'
            '<nav class="tabbar"><div class="tab">Chat</div><div class="tab">Work</div><div class="tab">Needs you</div><div class="tab">Monitor</div></nav>' + overlay + '</div>')
    (out / f"{name}.html").write_text(HEAD.format(title=name, w=390, h=844) + html + TAIL)

SHEET_TITLE = 'Turn on improvement suggestions for altitude?'
SHEET_BODY = ('Claude Sonnet 5.5 reads counts from this project\'s tasks and short quotes from its chats (up to 300 characters each). '
              'It runs on your Claude plan, capped at far less per run than an ordinary task; one ordinary L3 turn then checks what it finds. With Weekly on, this happens Monday mornings once 5 tasks have finished. '
              'Nothing is published, and nothing becomes work until you choose it.')
OFF_LINE = "Every Monday morning, when at least 5 tasks have finished."
settings_phone("PhoneSettings", ROUTING + ir_section() + PROJECT)
sheet = ('<div style="position:absolute;inset:0;background:rgba(15,18,30,.38)"></div>'
         '<div style="position:absolute;left:0;right:0;bottom:0;background:var(--surface);border-radius:16px 16px 0 0;padding:22px 20px 28px;box-shadow:0 -6px 24px rgba(0,0,0,.12)">'
         '<div style="width:36px;height:4px;border-radius:2px;background:var(--hairline);margin:-8px auto 14px"></div>'
         f'<h2 style="margin:0 0 10px;font-size:18px">{SHEET_TITLE}</h2>'
         f'<p style="margin:0 0 18px;line-height:1.5">{SHEET_BODY}</p>'
         '<span class="btn primary" style="display:flex;justify-content:center;height:44px;margin-bottom:10px">Turn on</span>'
         '<span class="btn" style="display:flex;justify-content:center;height:44px">Cancel</span></div>')
settings_phone("PhoneConsent", ROUTING + ir_section(on=False, line=OFF_LINE, now="Reads the 6 tasks finished since Oct 12.", past=False) + PROJECT,
               overlay=f'<div style="position:absolute;inset:0;width:390px;height:844px">{sheet}</div>')

dialog = ('<div style="border:1px solid var(--hairline);border-radius:14px;padding:22px 24px;background:var(--surface);box-shadow:0 10px 30px rgba(0,0,0,.12);max-width:460px">'
          f'<h2 style="margin:0 0 10px;font-size:18px">{SHEET_TITLE}</h2>'
          f'<p style="margin:0 0 18px;line-height:1.5">{SHEET_BODY}</p>'
          '<div style="display:flex;justify-content:flex-end;gap:10px"><span class="btn">Cancel</span><span class="btn primary">Turn on</span></div></div>')
cells = [
 ("1 · Off (the default)", "nothing has been agreed; either control opens the sheet first", ir_section(on=False, line=OFF_LINE, past=False)),
 ("2 · The sheet", "from Weekly or Get suggestions now; nothing saves or runs until Turn on", dialog),
 ("3 · Weekly on", "when the next run happens and on what condition", ir_section()),
 ("4 · Weekly on, last Monday skipped", "too few tasks had finished; the skip is only said here", ir_section(line="Skipped Oct 12: 3 tasks had finished. Next: Monday, Oct 19, if 2 more tasks finish.", now="Reads the 3 tasks finished since Oct 5.", now_state="disabled")),
 ("5 · Get suggestions now pressed while unavailable", "the reason appears under it on press and is its accessible description", ir_section(line="Skipped Oct 12: 3 tasks had finished. Next: Monday, Oct 19, if 2 more tasks finish.", now_state="disabled", reason="Needs 5 finished tasks since Oct 12; 3 so far.")),
 ("6 · Running", "the running line replaces the button; Stop is there and in chat", ir_section(now_state="running")),
 ("7 · Weekly off, agreed earlier", "Get suggestions now runs without the sheet", ir_section(on=False, line=OFF_LINE)),
 ("8 · Off because the terms changed", "turning Weekly on shows the new sheet", ir_section(on=False, line="Off: the terms changed. Turn on to see them.")),
 ("9 · Saving", "controls disabled while saving", ir_section(status="Saving…")),
 ("10 · Not saved", "the switch returns to the saved value; the reason in red", ir_section(on=False, line=OFF_LINE, status="Not saved: the settings changed on another device. Reload settings")),
]
sb = ['<div style="padding:36px 40px 10px"><h1 style="font-size:20px;font-weight:600;margin:0">Improvement suggestions in the project\'s settings</h1>'
      '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:900px">A new section between Routing and Project in /settings/projects/&lt;name&gt;. Off until the operator agrees on the sheet; only the operator can switch or agree.</p></div><div class="sheetgrid">']
for lab, note, body in cells:
    sb.append(st(lab, note, f'<div class="route-content" style="padding:0;max-width:520px">{body}</div>'))
sb.append('</div>')
(out / "SettingsStates.html").write_text(HEAD.format(title="SettingsStates", w=1200, h=2150) + "".join(sb) + TAIL)

ny = ('<div class="m"><div></div><header class="mh"><div><span class="name">Needs you</span></div><span class="sp"></span></header>'
      '<div class="mbody" style="overflow:hidden"><div class="mcol" style="margin-top:0;padding-top:12px">'
      '<p style="margin:2px 0 4px;font-size:15px">Nothing needs you.</p>'
      '<div style="font-size:13px;color:var(--text-muted);margin:6px 0 -6px">Suggestions</div>'
      '<div style="font-weight:600;font-size:15px;display:flex;align-items:center;gap:6px"><svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>altitude</div>'
      + card([sg(*S1), sg(*S2)], footer=False).replace('<b>Improvement suggestions</b>', '<b><a href="#" ' + U + '>Improvement suggestions</a></b>', 1)
      + '</div></div><div></div><nav class="tabbar"><div class="tab">Chat</div><div class="tab">Work</div><div class="tab on">Needs you</div><div class="tab">Monitor</div></nav></div>')
(out / "PhoneNeedsYou.html").write_text(HEAD.format(title="PhoneNeedsYou", w=390, h=844) + ny + TAIL)
print("settings boards")
