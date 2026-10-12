"""Write the show-files boards beside this file: python3 design/wireframes/show-files/gen.py

PROPOSAL.md outranks these boards. All content is fictional: a small log viewer called Lumen.
"""
import pathlib

out = pathlib.Path(__file__).resolve().parent
HEAD = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Show files: {title}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&amp;family=IBM+Plex+Mono:wght@400;500&amp;display=swap">
<link rel="stylesheet" href="../wireframes.css"><link rel="stylesheet" href="show.css"></head>
<body><div class="root" data-theme="light" style="width:{w}px;height:{h}px">
'''
TAIL = '''</div><script>if(location.search.indexOf('dark')>=0)document.querySelector('.root').dataset.theme='dark'</script></body></html>
'''


def write(name, w, h, body):
    (out / f"{name}.html").write_text(HEAD.format(title=name, w=w, h=h) + body + TAIL)


def icon(path, cls="i"):
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true">{path}</svg>'


BACK = icon('<path d="M15 6l-6 6 6 6"/>')
DOTS = icon(''.join(f'<circle cx="{x}" cy="12" r="1.2" fill="currentColor" stroke="none"/>' for x in (6, 12, 18)))
MIC = icon('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>', "i lg")
SEND = icon('<path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>', "i lg")
CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'
NOIMG = ('<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2"/>'
         '<circle cx="9" cy="10" r="1.6"/><path d="M20.5 15.5l-5-5-8 9"/><path d="M3 3l18 18"/></svg>')
TABS = [
    ("Chat", '<path d="M20 14.5a2 2 0 0 1-2 2H9l-4.5 4v-4H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/>'),
    ("Work", '<rect x="3.5" y="5.5" width="17" height="14" rx="2"/><path d="M8 5.5V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v1.5"/><path d="M3.5 11h17"/>'),
    ("Needs you", '<path d="M3 13l2.6-7.2A1 1 0 0 1 6.5 5h11a1 1 0 0 1 .9.8L21 13v5.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/><path d="M3 13h5l1.4 2.5h5.2L16 13h5"/>'),
    ("Monitor", '<path d="M3 12h3.5l2.5-6.5 4 13 2.5-6.5H21"/>'),
]
TABBAR = '<nav class="tabbar">' + ''.join(
    f'<div class="tab{" on" if n == "Work" else ""}">{icon(p, "i lg")}{n}{"<span class=badge>1</span>" if n == "Needs you" else ""}</div>'
    for n, p in TABS) + '</nav>'
TASK = "Rewrite the README"

# ---------- fictional images, drawn inline so the boards stay self-contained ----------


def hero(uid):
    rays = ''.join(
        f'<line x1="{150 + 62 * c:.1f}" y1="{130 + 62 * s:.1f}" x2="{150 + 78 * c:.1f}" y2="{130 + 78 * s:.1f}"/>'
        for c, s in [(1, 0), (.707, .707), (0, 1), (-.707, .707), (-1, 0), (-.707, -.707), (0, -1), (.707, -.707)])
    return (f'<svg viewBox="0 0 640 260" role="img" aria-label="Lumen"><defs><linearGradient id="{uid}" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#1e1b4b"/><stop offset="1" stop-color="#4338ca"/></linearGradient></defs>'
            f'<rect width="640" height="260" rx="22" fill="url(#{uid})"/>'
            f'<g stroke="#fde68a" stroke-width="7" stroke-linecap="round">{rays}</g><circle cx="150" cy="130" r="44" fill="#fde68a"/>'
            '<text x="262" y="138" font-family="IBM Plex Sans,Segoe UI,sans-serif" font-size="78" font-weight="600" fill="#ffffff">Lumen</text>'
            '<text x="266" y="184" font-family="IBM Plex Sans,Segoe UI,sans-serif" font-size="26" fill="#c7d2fe">logs you can read</text></svg>')


def terminal(lines, h=330, title="lumen tail app.log", w=640):
    colors = {"i": "#93c5fd", "w": "#fcd34d", "e": "#fca5a5", "t": "#94a3b8", "p": "#e2e8f0", "g": "#86efac"}
    rows = []
    y = 82
    for line in lines:
        x = 28
        parts = []
        for kind, text in line:
            parts.append(f'<tspan x="{x}" fill="{colors[kind]}">{text}</tspan>' if x == 28 else f'<tspan fill="{colors[kind]}">{text}</tspan>')
            x = None
        rows.append(f'<text y="{y}" font-family="IBM Plex Mono,ui-monospace,monospace" font-size="19">{"".join(parts)}</text>')
        y += 34
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{title}"><rect width="{w}" height="{h}" fill="#0f172a"/>'
            f'<rect width="{w}" height="40" fill="#1e293b"/>'
            '<circle cx="24" cy="20" r="6" fill="#f87171"/><circle cx="44" cy="20" r="6" fill="#fbbf24"/><circle cx="64" cy="20" r="6" fill="#4ade80"/>'
            f'<text x="{w // 2}" y="26" text-anchor="middle" font-family="IBM Plex Sans,Segoe UI,sans-serif" font-size="15" fill="#94a3b8">{title}</text>'
            + ''.join(rows) + '</svg>')


TAIL_LINES = [
    [("p", "$ lumen tail app.log --since 10m")],
    [("t", "09:41:02 "), ("i", "INFO "), ("p", " server started on port 8080")],
    [("t", "09:41:05 "), ("i", "INFO "), ("p", " 3 workers ready")],
    [("t", "09:43:17 "), ("w", "WARN "), ("p", " slow request /export took 2.4 s")],
    [("t", "09:44:51 "), ("e", "ERROR"), ("p", " upstream timeout after 30 s")],
    [("t", "09:44:52 "), ("i", "INFO "), ("p", " retry 1 of 3 succeeded")],
    [("t", "09:46:08 "), ("i", "INFO "), ("p", " 1,204 requests in the last 5 minutes")],
]
GREP_LINES = [
    [("p", "$ lumen grep timeout --context 1")],
    [("t", "09:44:50 "), ("i", "INFO "), ("p", " GET /export 200")],
    [("t", "09:44:51 "), ("e", "ERROR"), ("p", " upstream "), ("w", "timeout"), ("p", " after 30 s")],
    [("t", "09:44:52 "), ("i", "INFO "), ("p", " retry 1 of 3 succeeded")],
    [("g", "1 match in app.log")],
]
EXPORT_LINES = [
    [("p", "$ lumen export app.log --out timeline.html")],
    [("t", "reading  "), ("p", "app.log (4,812 lines)")],
    [("t", "writing  "), ("p", "timeline.html")],
    [("g", "done in 0.4 s")],
]

# ---------- rendered markdown ----------


def code(lines):
    return '<pre><code>' + '\n'.join(lines) + '</code></pre>'


def table(head, rows, nowrap=(), scroll=None):
    th = ''.join(f'<th>{h}</th>' for h in head)
    body = ''.join('<tr>' + ''.join(f'<td{" class=nw" if i in nowrap else ""}>{c}</td>' for i, c in enumerate(r)) + '</tr>' for r in rows)
    bar = f'<div class="bar"><i style="width:{scroll}%"></i></div>' if scroll else ''
    return f'<div class="tw"><div class="sc"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>{bar}</div>'


def tasks(items):
    return '<ul class="tasks">' + ''.join(
        f'<li><span class="cb{" on" if done else ""}">{CHECK if done else ""}</span><span>{t}</span></li>' for done, t in items) + '</ul>'


def rel(path):
    return f'<span class="rel" title="{path}">{path}</span>'


def not_shown(path):
    return f'<div class="ns">{NOIMG}<span>Image not shown · {path}</span></div>'


README = ''.join([
    '<h1>Lumen</h1>',
    f'<p class="center"><a class="img" style="width:300px" href="#">{hero("hero-a")}</a></p>',
    '<p class="center"><a href="#">CI</a>&nbsp;&nbsp; <a href="#">License: MIT</a>&nbsp;&nbsp; <a href="#">Release 0.9</a></p>',
    '<p>Lumen turns noisy logs into a timeline you can read. It runs wherever Python 3.10 does and keeps no state of its own.</p>',
    '<p class="center"><a href="#install">Install</a> · <a href="#tour">Two-minute tour</a> · <a href="#config">Configuration</a></p>',
    '<h2 id="tour">Two-minute tour</h2>',
    '<p>Follow a file, then narrow it to what <em>broke</em>:</p>',
    code(['<span class="c"># the last ten minutes, then new lines</span>', 'lumen tail app.log --since 10m',
          '', '<span class="c"># every timeout, with context</span>', 'lumen grep timeout --context 3']),
    '<h2 id="install">Install</h2>',
    '<ul><li><code>pipx install lumen-logs</code> (recommended)</li><li><code>brew install lumen</code> on macOS</li>'
    f'<li>From source: see {rel("CONTRIBUTING.md")}</li></ul>',
    '<h2>Status</h2>',
    tasks([(True, 'Tail and follow files'), (True, 'Search with context'), (False, 'Export a timeline to HTML'), (False, 'Windows support')]),
    '<h2 id="config">Configuration</h2>',
    '<p>Settings come from <code>lumen.toml</code>, then the environment:</p>',
    table(['Key', 'Default', 'Environment', 'What it sets'],
          [['<code>color</code>', '<code>auto</code>', '<code>LUMEN_COLOR</code>', 'Colour output: auto, always or never'],
           ['<code>since</code>', '<code>1h</code>', '<code>LUMEN_SINCE</code>', 'How far back tail starts reading'],
           ['<code>time_format</code>', '<code>iso</code>', '<code>LUMEN_TIME_FORMAT</code>', 'Timestamp style at the start of each line']],
          nowrap=(0, 1, 2, 3), scroll=52),
    '<blockquote><p><strong>Note:</strong> Lumen only reads files. It never moves, truncates or rotates them.</p></blockquote>',
    '<h2>How it works</h2>',
    '<p>Each line is parsed once and placed on a single timeline:</p>',
    not_shown('docs/images/old-diagram.png'),
    '<h2>Contributing</h2>',
    f'<p>Read {rel("CONTRIBUTING.md")} first, then open an issue on the <a href="#">issue tracker</a>.</p>',
    '<hr><p>MIT licensed.</p>',
])

README_RAW = '''# Lumen

<p align="center">
  <img src="docs/images/hero.png" alt="Lumen" width="300">
</p>

<p align="center">
  <a href="https://ci.example.org/lumen"><img src="https://ci.example.org/lumen/badge.svg" alt="CI"></a>
  <img src="https://img.example.org/license-mit.svg" alt="License: MIT">
  <img src="https://img.example.org/release.svg" alt="Release 0.9">
</p>

Lumen turns noisy logs into a timeline you can read. It runs wherever Python 3.10 does and keeps no state of its own.

[Install](#install) · [Two-minute tour](#two-minute-tour) · [Configuration](#configuration)

## Two-minute tour

Follow a file, then narrow it to what *broke*:

```sh
# the last ten minutes, then new lines
lumen tail app.log --since 10m
```'''

CLI = ''.join([
    '<h1>Lumen command line</h1>',
    '<p>Every command reads files or standard input and writes to standard output. Nothing on disk changes.</p>',
    '<h2>Commands</h2>',
    table(['Command', 'What it does', 'Common flags'],
          [['<code>lumen tail FILE</code>', 'Follow a file and print new lines as a timeline', '<code>--since</code>, <code>--level</code>'],
           ['<code>lumen grep PATTERN FILE</code>', 'Show matching lines with the lines around them', '<code>--context</code>, <code>--ignore-case</code>'],
           ['<code>lumen export FILE</code>', 'Write a timeline to one standalone HTML file', '<code>--out</code>, <code>--title</code>']],
          nowrap=(0, 2)),
    '<h2>Examples</h2>',
    '<p>Errors from the last hour, then a timeline to attach to a bug report:</p>',
    code(['<span class="c"># errors from the last hour, three lines of context</span>',
          'lumen grep --level error --since 1h --context 3 app.log', '',
          '<span class="c"># a timeline for a bug report</span>',
          'lumen export app.log --since 30m --out timeline.html']),
    '<h2>Exit codes</h2>',
    '<ul><li><code>0</code> the command finished</li><li><code>1</code> nothing matched</li><li><code>2</code> a file could not be read</li></ul>',
    '<h3>Environment</h3>',
    f'<p>Every flag has an environment variable; the table is in {rel("README.md")}. Report anything unclear on the <a href="#">issue tracker</a>.</p>',
    '<blockquote><p><strong>Tip:</strong> <code>lumen tail -</code> reads standard input, so it works at the end of any pipe.</p></blockquote>',
])

# ---------- viewer parts ----------


def meta(when, path=None):
    return '<div class="vmeta">' + (f'<code>{path}</code> · ' if path else '') + f'<span title="Oct 12, 2026, 07:40">{when}</span></div>'


def heading(title, when, path=None, earlier=False):
    stat = '<p class="vstat" role="status">Earlier version. <a class="u" href="#">Open latest</a></p>' if earlier else ''
    return f'<header class="vh"><h1>{title}</h1>{meta(when, path)}{stat}</header>'


def figure(name, img="", state="ready"):
    full = '<a class="u" href="#">Full size ↗</a>' if state == "ready" else ''
    if state == "loading":
        body = '<div class="istate" aria-busy="true"><p class="loading"><span class="spin a"></span>Loading image…</p></div>'
    elif state == "failed":
        body = '<div class="istate fail" role="alert"><p>Image unavailable.</p><span class="btn">Retry image</span></div>'
    else:
        body = f'<a class="img" href="#" aria-label="Open {name} full size">{img}</a>'
    return f'<figure class="fig"><figcaption><b>{name}</b>{full}</figcaption>{body}</figure>'


def doc(md, pressed=False):
    t = f'<span class="tgl" role="button" aria-pressed="{"true" if pressed else "false"}">Raw</span>'
    inner = f'<pre class="raw">{md}</pre>' if pressed else f'<article class="md">{md}</article>'
    return f'<section class="docwrap">{t}{inner}</section>'


def unavailable(text):
    return f'<div class="unav" role="alert"><h1>Preview unavailable</h1><p>{text}</p><span class="btn">Retry</span></div>'


UNAV = "This saved preview could not be loaded. Return to the conversation for an update, or try again."
DENIED = "Access to this preview is unavailable. Retry after access is restored."
LOADING = '<p class="loading"><span class="spin a"></span>Loading preview…</p>'


def phone_viewer(page):
    return ('<div class="mv"><header class="mh">'
            f'<a class="hb" href="#" aria-label="Back">{BACK}</a><div class="ht"><b>{TASK}</b><div class="subl">L2 · Running</div></div></header>'
            f'<main class="vw">{page}</main>{TABBAR}</div>')


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# 2. PhoneViewer: README.md, ready
write("PhoneViewer", 390, 2060, phone_viewer(heading("README.md", "shown 2 hours ago") + doc(README)))

# 3. PhoneStates
IMG3 = [("tour-tail.png", terminal(TAIL_LINES)), ("tour-grep.png", terminal(GREP_LINES, 230, "lumen grep timeout")),
        ("tour-export.png", terminal(EXPORT_LINES, 200, "lumen export app.log"))]
frames = [
    ("Loading", "no heading yet", LOADING),
    ("Raw on", "the toggle is pressed; the source starts at its top", heading("README.md", "shown 2 hours ago") + doc(esc(README_RAW), pressed=True)),
    ("Images only", "one figure per image; no Raw toggle", heading("3 images", "shown 2 hours ago") + ''.join(figure(n, s) for n, s in IMG3)),
    ("Image loading, image failed", "Full size waits for the image",
     heading("3 images", "shown 2 hours ago") + figure(*IMG3[0]) + figure(IMG3[1][0], state="loading") + figure(IMG3[2][0], state="failed")),
    ("Earlier version", "a later reply showed the same file", heading("README.md", "shown yesterday", earlier=True) + doc(README)),
    ("Unavailable", "missing or altered saved bytes, or a failed read", unavailable(UNAV)),
    ("Denied", "the device lost access", unavailable(DENIED)),
]
cells = ''.join(f'<div class="frame"><div class="lab">{i}. {lab}<span>{note}</span></div>{phone_viewer(page)}</div>'
                for i, (lab, note, page) in enumerate(frames, 1))
write("PhoneStates", 1736, 1960,
      '<div style="padding:32px 40px 22px"><h1 style="font-size:20px;font-weight:600;margin:0">Viewer states on phone</h1></div>'
      f'<div class="frames">{cells}</div>')

# 4. DesktopViewer: docs/CLI.md with one extra image

RAIL = ('<aside class="rail"><div class="brand"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div>'
        f'<div class="ri">{icon(TABS[2][1])}Needs you<span class="badge">1</span></div>'
        f'<div class="rsec"><span>Projects</span><span class="plus">{icon("<path d=\"M12 5v14M5 12h14\"/>", "i sm")}</span></div>'
        '<div class="ri sel"><span class="dot"></span>lumen</div><div class="ri"><span class="dot idle"></span>field-notes</div>'
        f'<div style="margin-top:auto"></div><div class="ri" style="height:36px">{icon(TABS[3][1])}Monitor</div></aside>')

desk_page = ('<a class="back" href="#">← Back</a>' + heading("CLI.md", "shown 5 minutes ago", path="docs/CLI.md")
             + figure("overview.png", terminal(TAIL_LINES, 320, w=1200)) + doc(CLI))
write("DesktopViewer", 1280, 1480,
      f'<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:100%">{RAIL}'
      f'<main class="pane" style="overflow:hidden"><div class="vw" style="max-width:984px">{desk_page}</div></main></div>')

# 1 and 5. Conversations

REPLY = ('<div class="l3"><p>The README draft is ready to read. It now opens with the two-minute tour.</p>'
         '<a class="view" href="PhoneViewer.html">View README.md</a></div>')
QUESTION = "Move the command reference out of the README into docs/CLI.md?"
CLOSED_Q = ('<div class="qc" data-status="resolved">'
            f'<p class="qq">{QUESTION}</p>'
            '<div class="rcpt"><b>Decision recorded</b><p>Move it; the README keeps a five-line quick start.</p><span class="muted">You · 3 hours ago</span></div>'
            '<a class="view" href="#">View PROPOSAL.md</a></div>')
OPEN_Q = ('<div class="qc"><div class="k"><span><b>Your turn</b> · 1 question</span></div>'
          f'<p class="qq">{QUESTION}</p>'
          '<a class="view" href="#">View PROPOSAL.md</a>'
          '<p class="rec"><b style="color:var(--text-primary);font-weight:600">Recommended:</b> Move it; the README keeps a five-line quick start.</p>'
          '<div class="opts"><span class="btn" aria-pressed="false">Move it ★</span><span class="btn" aria-pressed="false">Keep it in the README</span><span class="btn" aria-pressed="false">Other…</span></div>'
          '<div class="batch"><span class="btn primary off">Send answers</span></div></div>')

phone_convo = ('<div class="m task-phone"><header class="mh">'
               f'<a class="hb" href="#" aria-label="Back" style="margin-left:-6px">{BACK}</a>'
               f'<div class="ht"><b>{TASK}</b><div class="subl">L2 · Running</div></div>'
               f'<span class="hb" style="margin:0" aria-label="Task details">{DOTS}</span></header>'
               '<div class="task-tabs"><span class="on">Conversation</span><span>Live session</span></div>'
               '<div class="mbody"><div class="mcol"><div class="day">Today</div>'
               '<div class="l3"><p>Plan: lead with a two-minute tour, then install, then configuration. One question first.</p></div>'
               + CLOSED_Q + '<div class="me">Go ahead with the draft.</div>' + REPLY + '</div></div>'
               '<div class="mcomp"><div class="composer"><div class="draft">Reads well. Could the tour also show the config file<span class="caret"></span></div>'
               f'<div class="crow"><span style="flex:1"></span><span class="icb">{MIC}</span><span class="icb send" aria-label="Send">{SEND}</span></div></div></div>'
               + TABBAR + '</div>')
write("PhoneConversation", 390, 844, phone_convo)

desk_convo = (f'<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:100%">{RAIL}<main class="pane">'
              '<header style="padding:16px 28px 14px;display:flex;flex-direction:column;gap:6px;flex:none">'
              f'<div class="crumb">{icon("<path d=\"M15 6l-6 6 6 6\"/>", "i sm")}lumen</div>'
              f'<h1 style="display:flex;align-items:center;gap:10px;margin:0;font-size:18px;font-weight:600;line-height:1.3"><span class="dot"></span>{TASK}</h1>'
              '<div style="font-size:13px;color:var(--text-muted)">attempt 1 · started 40 min ago</div>'
              '<div style="display:flex;gap:6px;margin-top:2px"><span class="chip">Running</span></div></header>'
              '<div class="convo"><div class="col"><div class="day">Today</div>'
              '<div class="l3"><p>Plan: lead with a two-minute tour, then install, then configuration. I will show you the draft once it reads well.</p></div>'
              '<div class="me">Sounds right. Keep the badges.</div>'
              + REPLY.replace("PhoneViewer.html", "DesktopViewer.html") + OPEN_Q + '</div>'
              '<div class="composer"><div class="ph2">Message the L2</div>'
              f'<div class="crow"><span style="flex:1"></span><span class="icb">{MIC}</span><span class="icb send disabled" aria-label="Send">{SEND}</span></div></div>'
              '<div class="hint routine">Reaches the L2 at its next checkpoint.</div></div></main></div>')
write("DesktopConversation", 1280, 800, desk_convo)

BOARDS = {"PhoneConversation": (390, 844), "PhoneViewer": (390, 2060), "PhoneStates": (1736, 1960),
          "DesktopViewer": (1280, 1480), "DesktopConversation": (1280, 800)}
print("written:", ", ".join(f"{n}.html {w}x{h}" for n, (w, h) in BOARDS.items()))
