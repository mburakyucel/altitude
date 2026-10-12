"""Write the show-files boards beside this file: python3 design/wireframes/show-files/gen.py

PROPOSAL.md outranks these boards. All content is fictional: a small log viewer called Lumen.
Boards: PhoneConversation, PhoneReader, PhoneStates, DesktopSplit, DesktopSplitEarlier, DesktopQuestion.
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
BOARDS = {}


def write(name, w, h, body):
    BOARDS[name] = (w, h)
    (out / f"{name}.html").write_text(HEAD.format(title=name, w=w, h=h) + body + TAIL)


# ---------- icons ----------
def ic(path, cls="ic"):
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true">{path}</svg>'


P = {
    "back": '<path d="M15 6l-6 6 6 6"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "chev": '<path d="M9 6l6 6-6 6"/>',
    "doc": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/>',
    "docx": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9.5 12.5l5 5M14.5 12.5l-5 5"/>',
    "img": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M20.5 15.5l-5-5-8 9"/>',
    "imgx": '<path d="M20.5 13V6.5a2 2 0 0 0-2-2h-13a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2H12"/><circle cx="9" cy="10" r="1.6"/><path d="M3.5 17l5-5 3.5 3.5"/><path d="M15.5 15.5l5 5M20.5 15.5l-5 5"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/><path d="M12 14.5v2.5"/>',
    "retry": '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.5 4v4h4"/>',
    "expand": '<path d="M14 4h6v6M10 20H4v-6M20 4l-6.5 6.5M4 20l6.5-6.5"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
    "send": '<path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>',
    "dots": ''.join(f'<circle cx="{x}" cy="12" r="1.2" fill="currentColor" stroke="none"/>' for x in (6, 12, 18)),
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "chat": '<path d="M20 14.5a2 2 0 0 1-2 2H9l-4.5 4v-4H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/>',
    "work": '<rect x="3.5" y="5.5" width="17" height="14" rx="2"/><path d="M8 5.5V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v1.5"/><path d="M3.5 11h17"/>',
    "needs": '<path d="M3 13l2.6-7.2A1 1 0 0 1 6.5 5h11a1 1 0 0 1 .9.8L21 13v5.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/><path d="M3 13h5l1.4 2.5h5.2L16 13h5"/>',
    "monitor": '<path d="M3 12h3.5l2.5-6.5 4 13 2.5-6.5H21"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
}
TABBAR = '<nav class="tabbar">' + ''.join(
    f'<div class="tab{" on" if n == "Work" else ""}">{ic(P[k], "i lg")}{n}{"<span class=badge>1</span>" if n == "Needs you" else ""}</div>'
    for n, k in [("Chat", "chat"), ("Work", "work"), ("Needs you", "needs"), ("Monitor", "monitor")]) + '</nav>'
TASK = "Rewrite the README"

# ---------- fictional images, drawn inline so the boards stay self-contained ----------
FONT = "IBM Plex Sans,Segoe UI,sans-serif"
MONO = "IBM Plex Mono,ui-monospace,monospace"


def hero(uid, cover=False):
    rays = ''.join(
        f'<line x1="{150 + 62 * c:.1f}" y1="{130 + 62 * s:.1f}" x2="{150 + 78 * c:.1f}" y2="{130 + 78 * s:.1f}"/>'
        for c, s in [(1, 0), (.707, .707), (0, 1), (-.707, .707), (-1, 0), (-.707, -.707), (0, -1), (.707, -.707)])
    par = ' preserveAspectRatio="xMidYMid slice"' if cover else ''
    return (f'<svg viewBox="0 0 640 260"{par} role="img" aria-label="Lumen"><defs><linearGradient id="{uid}" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#1e1b4b"/><stop offset="1" stop-color="#4338ca"/></linearGradient></defs>'
            f'<rect width="640" height="260" {"" if cover else "rx=22 "}fill="url(#{uid})"/>'
            f'<g stroke="#fde68a" stroke-width="7" stroke-linecap="round">{rays}</g><circle cx="150" cy="130" r="44" fill="#fde68a"/>'
            f'<text x="262" y="138" font-family="{FONT}" font-size="78" font-weight="600" fill="#ffffff">Lumen</text>'
            f'<text x="266" y="184" font-family="{FONT}" font-size="26" fill="#c7d2fe">logs you can read</text></svg>')


def terminal(lines, h=330, title="lumen tail app.log", w=640, cover=False):
    colors = {"i": "#93c5fd", "w": "#fcd34d", "e": "#fca5a5", "t": "#94a3b8", "p": "#e2e8f0", "g": "#86efac"}
    rows, y = [], 82
    for line in lines:
        parts = [f'<tspan fill="{colors[k]}">{t}</tspan>' for k, t in line]
        rows.append(f'<text x="28" y="{y}" font-family="{MONO}" font-size="19" xml:space="preserve">{"".join(parts)}</text>')
        y += 34
    par = ' preserveAspectRatio="xMinYMin slice"' if cover else ''
    return (f'<svg viewBox="0 0 {w} {h}"{par} role="img" aria-label="{title}"><rect width="{w}" height="{h}" fill="#0f172a"/>'
            f'<rect width="{w}" height="40" fill="#1e293b"/>'
            '<circle cx="24" cy="20" r="6" fill="#f87171"/><circle cx="44" cy="20" r="6" fill="#fbbf24"/><circle cx="64" cy="20" r="6" fill="#4ade80"/>'
            f'<text x="{w // 2}" y="26" text-anchor="middle" font-family="{FONT}" font-size="15" fill="#94a3b8">{title}</text>'
            + ''.join(rows) + '</svg>')


def ui_mock(w, h, dark=False, cover=False, title="Timeline"):
    """A phone or browser screenshot of Lumen's exported timeline page."""
    bg, card, line, txt, mut = ("#0f172a", "#1e293b", "#334155", "#e2e8f0", "#64748b") if dark else ("#f8fafc", "#ffffff", "#e2e8f0", "#0f172a", "#94a3b8")
    dots = ["#4f46e5", "#d97706", "#dc2626", "#4f46e5", "#16a34a", "#4f46e5", "#d97706"]
    rows = ''.join(
        f'<rect x="{w * .06:.0f}" y="{90 + i * 62}" width="{w * .88:.0f}" height="50" rx="10" fill="{card}" stroke="{line}"/>'
        f'<circle cx="{w * .06 + 22:.0f}" cy="{115 + i * 62}" r="7" fill="{d}"/>'
        f'<rect x="{w * .06 + 42:.0f}" y="{106 + i * 62}" width="{w * (.55 - (i % 3) * .1):.0f}" height="8" rx="4" fill="{txt}" opacity=".8"/>'
        f'<rect x="{w * .06 + 42:.0f}" y="{120 + i * 62}" width="{w * .3:.0f}" height="6" rx="3" fill="{mut}" opacity=".7"/>'
        for i, d in enumerate(dots) if 90 + i * 62 + 50 < h)
    par = ' preserveAspectRatio="xMidYMin slice"' if cover else ''
    return (f'<svg viewBox="0 0 {w} {h}"{par} role="img" aria-label="{title}"><rect width="{w}" height="{h}" fill="{bg}"/>'
            f'<rect width="{w}" height="64" fill="#4338ca"/><circle cx="{w * .06 + 14:.0f}" cy="32" r="12" fill="#fde68a"/>'
            f'<text x="{w * .06 + 36:.0f}" y="40" font-family="{FONT}" font-size="22" font-weight="600" fill="#fff">{title}</text>'
            + rows + '</svg>')


def proposal_shot(cover=False):
    """The proposal's first screenshot: the docs page on desktop."""
    par = ' preserveAspectRatio="xMidYMin slice"' if cover else ''
    nav = ''.join(f'<rect x="28" y="{110 + i * 30}" width="{[110, 90, 130, 70, 100][i]}" height="10" rx="5" fill="{"#4f46e5" if i == 1 else "#cbd5e1"}"/>' for i in range(5))
    body = ''.join(f'<rect x="230" y="{180 + i * 22}" width="{[520, 480, 540, 300][i % 4]}" height="9" rx="4.5" fill="#cbd5e1"/>' for i in range(8))
    return (f'<svg viewBox="0 0 840 420"{par} role="img" aria-label="Docs page"><rect width="840" height="420" fill="#ffffff"/>'
            '<rect width="840" height="56" fill="#f8fafc"/><rect y="56" width="840" height="1" fill="#e2e8f0"/>'
            f'<circle cx="38" cy="28" r="11" fill="#fde68a"/><text x="58" y="35" font-family="{FONT}" font-size="19" font-weight="600" fill="#1e1b4b">Lumen docs</text>'
            '<rect x="0" y="57" width="200" height="363" fill="#f8fafc"/><rect x="200" y="57" width="1" height="363" fill="#e2e8f0"/>' + nav
            + f'<text x="230" y="128" font-family="{FONT}" font-size="30" font-weight="600" fill="#0f172a">Command line</text>'
            '<rect x="230" y="146" width="580" height="1" fill="#e2e8f0"/>' + body
            + '<rect x="230" y="365" width="580" height="40" rx="8" fill="#f1f5f9" stroke="#e2e8f0"/>'
            f'<text x="246" y="390" font-family="{MONO}" font-size="15" fill="#334155">lumen tail app.log --since 10m</text></svg>')


TAIL_LINES = [
    [("p", "$ lumen tail app.log --since 10m")],
    [("t", "09:41:02 "), ("i", "INFO  "), ("p", "server started on port 8080")],
    [("t", "09:41:05 "), ("i", "INFO  "), ("p", "3 workers ready")],
    [("t", "09:43:17 "), ("w", "WARN  "), ("p", "slow request /export 2.4 s")],
    [("t", "09:44:51 "), ("e", "ERROR "), ("p", "upstream timeout after 30 s")],
    [("t", "09:44:52 "), ("i", "INFO  "), ("p", "retry 1 of 3 succeeded")],
    [("t", "09:46:08 "), ("i", "INFO  "), ("p", "1,204 requests in 5 min")],
]

# ---------- conversation pieces ----------
EXCERPT = "Lumen — Lumen turns noisy logs into a timeline you can read. It runs wherever Python 3.10 does and keeps no state of its own."
CLI_EXCERPT = "Lumen command line — Every command reads files or standard input and writes to standard output. Nothing on disk changes."
PROP_EXCERPT = "Docs layout — The README keeps a five-line quick start and every command moves to docs/CLI.md, with one page per command group."


def card(name, excerpt, cover="", state="ready", cls=""):
    """The page card: one control with cover, document icon, bold name, chevron and two-line excerpt."""
    if state == "gone":
        return (f'<div class="pc gone" role="group" aria-label="{name}"><div class="pb"><div class="pt">{ic(P["docx"], "ic doc")}<b>{name}</b></div>'
                '<p class="ex">Preview unavailable</p></div></div>')
    if state == "loading":
        cov = '<div class="cover blank"></div>'
    elif state == "failed":
        cov = ''  # a failed cover leaves the card without its strip; it still opens
    else:
        cov = f'<div class="cover">{cover}</div>' if cover else ''
    tag = '<span class="tag">Earlier</span>' if "earlier" in cls else ''
    return (f'<a class="pc {cls}" href="#" aria-label="Open {name}">{cov}<div class="pb"><div class="pt">{ic(P["doc"], "ic doc")}'
            f'<b>{name}</b>{ic(P["chev"], "ic chev")}</div><p class="ex">{excerpt}</p></div>{tag}</a>')


def thumbs(tiles):
    """tiles: (width, svg | 'loading' | 'failed', more) — 96 px tall on phone (CSS makes desktop 120)."""
    out_ = []
    for w, body, more in tiles:
        if body == "loading":
            out_.append(f'<div class="th blank" style="width:{w}px"></div>')
        elif body == "failed":
            out_.append(f'<button class="th broke" type="button" aria-label="Retry image export-phone.png" style="width:{w}px">{ic(P["imgx"])}</button>')
        else:
            m = f'<span class="more">+{more}</span>' if more else ''
            out_.append(f'<button class="th" type="button" style="width:{w}px;padding:0">{body}{m}</button>')
    return '<div class="thumbs">' + ''.join(out_) + '</div>'


THUMBS = [(128, terminal(TAIL_LINES, 330, cover=True), 0), (72, ui_mock(300, 400, cover=True), 0),
          (54, ui_mock(270, 480, dark=True, cover=True), 0), (72, ui_mock(300, 400, cover=True, title="Export"), 2)]


def reply(text, extra=""):
    return f'<div class="l3"><p>{text}</p>{extra}</div>'


def composer(draft=None, phone=True):
    body = f'<div class="draft">{draft}<span class="caret"></span></div>' if draft else '<div class="ph2">Message the L2</div>'
    send = "icb send" if draft else "icb send disabled"
    return (f'<div class="composer">{body}<div class="crow"><span style="flex:1"></span><span class="icb">{ic(P["mic"], "i lg")}</span>'
            f'<span class="{send}" aria-label="Send">{ic(P["send"], "i lg")}</span></div></div>')


def phone_task(inner, draft, shift=0):
    return ('<div class="m task-phone"><header class="mh">'
            f'<a class="xb" href="#" aria-label="Back" style="margin-left:-12px">{ic(P["back"], "i")}</a>'
            f'<div style="flex:1;min-width:0"><div style="font-size:17px;font-weight:600">{TASK}</div><div class="subl">L2 · Running</div></div>'
            f'<span class="xb" aria-label="Task details">{ic(P["dots"], "i")}</span></header>'
            '<div class="task-tabs"><span class="on">Conversation</span><span>Live session</span></div>'
            f'<div class="mbody end" style="overflow:hidden"><div class="mcol" style="transform:translateY({shift}px)">{inner}</div></div>'
            f'<div class="mcomp">{composer(draft)}</div>{TABBAR}</div>')


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
        f'<li><span class="cb{" on" if done else ""}">{ic(P["check"], "x") if done else ""}</span><span>{t}</span></li>' for done, t in items) + '</ul>'


def rel(path):
    return f'<span class="rel" title="{path}">{path}</span>'


def pic(name, svg, width=None, state="ready"):
    """A copied image in the document: a button with an expand badge."""
    st = f' style="width:{width}px"' if width else ''
    if state == "failed":
        return (f'<div class="fail">{ic(P["imgx"], "ic lg")}<b>Image unavailable</b>'
                f'<span class="btn44">{ic(P["retry"], "ic sm")}Retry</span></div>')
    return (f'<button class="pic" type="button" aria-label="Open image {name}"{st}>{svg}'
            f'<span class="xp">{ic(P["expand"], "ic sm")}</span></button>')


def remote(alt):
    return f'<a class="remote" href="#">{ic(P["img"], "ic sm")}{alt}</a>'


def not_shown(path):
    return f'<div class="ns">{ic(P["imgx"])}<code>{path}</code></div>'


def readme(uid, hero_state="ready", tour_state="ready", short=False):
    top = [
        '<h1>Lumen</h1>',
        f'<p class="center">{pic("hero.png", hero(uid), 300, hero_state)}</p>',
        f'<p class="center">{remote("CI")}{remote("License: MIT")}{remote("Release 0.9")}</p>',
        '<p>Lumen turns noisy logs into a timeline you can read. It runs wherever Python 3.10 does and keeps no state of its own.</p>',
        '<p class="center"><a href="#install">Install</a> · <a href="#tour">Two-minute tour</a> · <a href="#config">Configuration</a></p>',
        '<h2 id="tour">Two-minute tour</h2>',
        '<p>Follow a file, then narrow it to what <em>broke</em>:</p>',
        code(['<span class="c"># the last ten minutes, then new lines</span>', 'lumen tail app.log --since 10m',
              '', '<span class="c"># every timeout, with context</span>', 'lumen grep timeout --context 3']),
        f'<p>{pic("tour.png", terminal(TAIL_LINES), None, tour_state)}</p>',
    ]
    if short:
        return ''.join(top)
    rest = [
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
    ]
    return ''.join(top + rest)


CLI = ''.join([
    '<h1>Lumen command line</h1>',
    '<p>Every command reads files or standard input and writes to standard output. Nothing on disk changes.</p>',
    '<h2>Commands</h2>',
    table(['Command', 'What it does', 'Flags'],
          [['<code>lumen tail FILE</code>', 'Follow a file as a timeline', '<code>--since</code>'],
           ['<code>lumen grep PATTERN</code>', 'Matching lines with context', '<code>--context</code>'],
           ['<code>lumen export FILE</code>', 'One standalone HTML file', '<code>--out</code>']],
          nowrap=(0, 2)),
    '<h2>Examples</h2>',
    code(['<span class="c"># errors from the last hour</span>',
          'lumen grep --level error --since 1h app.log', '',
          '<span class="c"># a timeline for a bug report</span>',
          'lumen export app.log --out timeline.html']),
    '<h2>Exit codes</h2>',
    '<ul><li><code>0</code> the command finished</li><li><code>1</code> nothing matched</li><li><code>2</code> a file could not be read</li></ul>',
])


# ---------- phone reader ----------
def reader(title, body, newer=False):
    pill = '<a class="newer" href="#">Newer</a>' if newer else ''
    return (f'<div class="rd"><header class="rh"><a class="xb" href="#" aria-label="Close">{ic(P["x"], "i")}</a>'
            f'<b>{title}</b>{pill}</header><main class="rb">{body}</main></div>')


def md(html):
    return f'<article class="md">{html}</article>'


SKELETON = ('<div class="sk" role="status" aria-label="Loading README.md"><i class="h"></i><i style="width:96%"></i><i style="width:88%"></i>'
            '<i style="width:92%"></i><i style="width:70%"></i><i style="width:80%"></i><i class="img"></i></div>')


def gone_page(icon, text):
    return (f'<div class="gone-page" role="alert">{ic(P[icon], "ic xl")}<h2>Preview unavailable</h2>'
            f'<p>{text}</p><span class="btn44">{ic(P["retry"], "ic sm")}Retry</span></div>')


UNAV = "It could not be loaded. Try again, or ask in the conversation."
DENIED = "This device has no access right now."

# 2. PhoneReader
write("PhoneReader", 390, 2020, reader("README.md", md(readme("hero-r"))))

# 1. PhoneConversation
convo = (reply("Screens from the new export page.", thumbs(THUMBS))
         + reply("First pass at the README.", card("README.md", EXCERPT, hero("hero-c1", cover=True), cls="earlier"))
         + '<div class="me">Lead with the tour.</div>'
         + reply("The README draft is ready to read. It now opens with the two-minute tour.",
                 card("README.md", EXCERPT, hero("hero-c2", cover=True))))
DRAFT = "Reads well. Could the tour also show the config file"
two = ''.join(f'<div class="frame"><div class="lab">{lab}<span>{note}</span></div>{phone_task(convo, DRAFT, shift)}</div>'
              for lab, note, shift in [("At the latest reply", "", 0), ("Scrolled up", "the earlier card dims and says so", 262)])
write("PhoneConversation", 892, 960,
      '<div style="padding:28px 40px 18px"><h1 style="font-size:20px;font-weight:600;margin:0">Shown files in the conversation</h1></div>'
      f'<div class="frames" style="grid-template-columns:repeat(2,390px)">{two}</div>')

# 3. PhoneStates
def gallery(name, count, img):
    return (f'<div class="iv" role="dialog" aria-label="Image {name}"><div class="ivh"><span>{name}</span><span class="cnt">{count}</span>'
            '<span class="btn">Zoom image</span><span class="btn">Close image</span></div>'
            f'<div class="ivc">{img}<span class="nav l" role="button" aria-label="Previous image">{ic(P["back"], "i")}</span>'
            f'<span class="nav r" role="button" aria-label="Next image">{ic(P["chev"], "i")}</span></div></div>')


viewer_frame = ('<div class="rd">' + reader("README.md", md(readme("hero-v", short=True)))[len('<div class="rd">'):-len('</div>')]
                + gallery("tour.png", "2 / 5", terminal(TAIL_LINES)) + '</div>')
card_loading = phone_task(
    reply("Screens from the new export page.", thumbs([(128, "loading", 0), (72, "failed", 0), (54, "loading", 0), (72, "loading", 0)]))
    + reply("The README draft is ready to read. It now opens with the two-minute tour.", card("README.md", EXCERPT, state="loading")), None)
card_failed = phone_task(
    reply("The CLI page now has examples.", card("CLI.md", CLI_EXCERPT, state="failed"))
    + reply("The docs layout from yesterday.", card("PROPOSAL.md", "", state="gone")), None)
frames = [
    ("Reader loading", "a skeleton of the page", reader("README.md", SKELETON)),
    ("Reader, earlier version", "Newer opens the latest in place", reader("README.md", md(readme("hero-e", short=True)), newer=True)),
    ("Reader, image failed", "in the image's place", reader("README.md", md(readme("hero-f", hero_state="failed", short=True)))),
    ("Gallery", "an image tapped in the reader; swipe or the side arrows", viewer_frame),
    ("Reader unavailable", "× still closes", reader("README.md", gone_page("docx", UNAV))),
    ("Reader denied", "", reader("README.md", gone_page("lock", DENIED))),
    ("Thumbnails and cover loading", "one thumbnail failed; tapping it retries", card_loading),
    ("Cover failed, saved files missing", "the first card still opens", card_failed),
]
cells = ''.join(f'<div class="frame"><div class="lab">{i}. {lab}<span>{note}</span></div>{body}</div>'
                for i, (lab, note, body) in enumerate(frames, 1))
write("PhoneStates", 1736, 1960,
      '<div style="padding:32px 40px 22px"><h1 style="font-size:20px;font-weight:600;margin:0">Reader and card states on phone</h1></div>'
      f'<div class="frames">{cells}</div>')

# ---------- desktop ----------
RAIL = ('<aside class="rail"><div class="brand"><span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>Altitude</div>'
        f'<div class="ri">{ic(P["needs"], "i")}Needs you<span class="badge">1</span></div>'
        f'<div class="rsec"><span>Projects</span><span class="plus">{ic(P["plus"], "i sm")}</span></div>'
        '<div class="ri sel"><span class="dot"></span>lumen</div><div class="ri"><span class="dot idle"></span>field-notes</div>'
        f'<div style="margin-top:auto"></div><div class="ri" style="height:36px">{ic(P["monitor"], "i")}Monitor</div></aside>')


def task_header():
    return ('<header class="tkh">'
            f'<div class="crumb">{ic(P["back"], "i sm")}lumen</div>'
            f'<h1><span class="dot"></span>{TASK}</h1>'
            '<div style="display:flex;gap:6px;align-items:center;font-size:13px;color:var(--text-muted)"><span class="chip">Running</span>attempt 1 · started 40 min ago</div></header>')


def desk_conversation(inner, draft):
    hint = '' if draft else '<div class="hint routine">Reaches the L2 at its next checkpoint.</div>'
    return (f'<main class="pane">{task_header()}<div class="convo"><div class="col">{inner}</div>'
            f'{composer(draft, phone=False)}{hint if hint else "<div style=height:20px></div>"}</div></main>')


def panel(title, body, newer=False):
    pill = '<a class="newer" href="#">Newer</a>' if newer else ''
    return (f'<aside class="panel" aria-label="{title}"><header class="ph2r">{ic(P["doc"], "ic doc")}<b>{title}</b>{pill}'
            f'<a class="xb" href="#" aria-label="Close">{ic(P["x"], "i")}</a></header><div class="pbody">{body}</div></aside>')


def desk(main):
    return f'<div class="dk">{RAIL}{main}</div>'


# 4. DesktopSplit (hero board)
split_convo = ('<div class="day">Today</div>'
               + '<div class="me">Rewrite the README so a newcomer gets a first result in two minutes.</div>'
               + reply("Plan: lead with a two-minute tour, then install, then configuration.")
               + '<div class="me">Sounds right. Keep the badges.</div>'
               + reply("The README draft is ready to read. It now opens with the two-minute tour.",
                       card("README.md", EXCERPT, hero("hero-d1", cover=True), cls="sel")))
write("DesktopSplit", 1280, 800, desk(
    '<div class="split">' + desk_conversation(split_convo, "Lovely. Can the tour also show the config fi")
    + panel("README.md", md(readme("hero-d2"))) + '</div>'))

# 5. DesktopSplitEarlier
earlier_convo = (reply("The command reference now lives in its own page.",
                       card("CLI.md", CLI_EXCERPT, terminal(TAIL_LINES, cover=True), cls="earlier sel"))
                 + reply("Exit codes are in, with an example for each.", card("CLI.md", CLI_EXCERPT, terminal(TAIL_LINES, cover=True))))
write("DesktopSplitEarlier", 1280, 800, desk(
    '<div class="split">' + desk_conversation(earlier_convo, None)
    + panel("CLI.md", f'<button class="extra" type="button" aria-label="Open image overview.png" style="padding:0;width:100%">{terminal(TAIL_LINES, 300, w=760)}</button>' + md(CLI), newer=True)
    + '</div>'))

# 6. DesktopQuestion
QUESTION = "Move the command reference out of the README into docs/CLI.md?"
open_q = ('<div class="card" style="display:flex;flex-direction:column;gap:12px;padding:16px 18px">'
          '<div class="kind"><span><b>Your turn</b> · 1 question</span></div>'
          f'<p class="q" style="margin:0;font-size:15px">{QUESTION}</p>'
          + card("PROPOSAL.md", PROP_EXCERPT, proposal_shot(cover=True))
          + '<p style="margin:0;font-size:14px;color:var(--text-secondary)"><b style="color:var(--text-primary)">Recommended:</b> Move it; the README keeps a five-line quick start.</p>'
          '<div class="opts" style="margin-top:0"><span class="btn">Move it ★</span><span class="btn">Keep it in the README</span><span class="btn">Other…</span></div>'
          '<div style="border-top:1px solid var(--hairline);padding-top:12px"><span class="btn primary" style="opacity:.6">Send answers</span></div></div>')
q_convo = ('<div class="day">Today</div>'
           + reply("Plan: lead with a two-minute tour, then install, then configuration. One layout question first.")
           + open_q)
write("DesktopQuestion", 1280, 800, desk(desk_conversation(q_convo, None)))

# 7. DesktopStates: the panel's states and the gallery, 1280x800 frames at three-quarter size
SPLIT_LEFT = desk_conversation(split_convo, None)


def split_with(body, title="README.md"):
    return desk('<div class="split">' + SPLIT_LEFT + panel(title, body) + '</div>')


DESK_FAILED = md(readme("hero-s3", hero_state="failed", short=True))
gallery_desk = ('<div class="dz">' + split_with(md(readme("hero-s4")))
                + '<div class="dlg">' + gallery("overview.png", "3 / 5", ui_mock(840, 620, title="Timeline")) + '</div></div>')
dframes = [
    ("Panel loading", "the conversation stays live beside it", '<div class="dz">' + split_with(SKELETON.replace('class="sk"', 'class="sk" style="max-width:520px"')) + '</div>'),
    ("Panel unavailable", "× still closes", '<div class="dz">' + split_with(gone_page("docx", UNAV)) + '</div>'),
    ("Panel image failed", "Retry in the image's place", '<div class="dz">' + split_with(DESK_FAILED) + '</div>'),
    ("Gallery on desktop", "side arrows and the keyboard arrows; hidden at the ends", gallery_desk),
]
dcells = ''.join(f'<div class="frame"><div class="lab">{i}. {lab}<span>{note}</span></div><div class="dframe">{body}</div></div>'
                 for i, (lab, note, body) in enumerate(dframes, 1))
write("DesktopStates", 2032, 1460,
      '<div style="padding:32px 40px 4px"><h1 style="font-size:20px;font-weight:600;margin:0">Reader panel and gallery on desktop</h1>'
      '<p class="muted" style="margin:4px 0 18px">1280 × 800 frames at three-quarter size</p></div>'
      f'<div class="frames d">{dcells}</div>')

print("written:", ", ".join(f"{n}.html {w}x{h}" for n, (w, h) in BOARDS.items()))
