"""Write the image viewer boards beside this file: python3 design/wireframes/image-viewer/gen.py"""
import pathlib

out = pathlib.Path(__file__).resolve().parent
HEAD = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Image viewer: {title}</title>
<link rel="stylesheet" href="../wireframes.css"><link rel="stylesheet" href="viewer.css"></head>
<body><div class="root board" data-theme="light" style="width:{w}px;height:{h}px">
'''
TAIL = '''</div><script>if(location.search.indexOf('dark')>=0)document.querySelector('.root').dataset.theme='dark'</script></body></html>
'''


def board(name, w, h, body):
    (out / f"{name}.html").write_text(HEAD.format(title=name, w=w, h=h) + body + TAIL)


def icon(paths, cls="i"):
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true">{paths}</svg>'


CLOSE = icon('<path d="M6 6l12 12M18 6L6 18"/>')
BACK = icon('<path d="M15 6l-6 6 6 6"/>')
NEXT = icon('<path d="M9 6l6 6-6 6"/>')
ZOOM = icon('<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5M10.5 8v5"/>')
FIT = icon('<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5"/>')
COPY = icon('<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2"/>')
DOWNLOAD = icon('<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5"/><path d="M4.5 16v2.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5V16"/>')
OPEN = icon('<path d="M13.5 4.5h6v6M19.5 4.5l-8 8"/><path d="M17.5 13.5v4a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2h4"/>')
SHARE = icon('<path d="M12 3.5v11M8 7.5l4-4 4 4"/><path d="M8 10.5H6.5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2H16"/>')
EXPAND = icon('<path d="M14 4h6v6M10 20H4v-6M20 4l-6.5 6.5M4 20l6.5-6.5"/>', "i sm")
IMAGE_OFF = icon('<path d="M20.5 13V6.5a2 2 0 0 0-2-2h-13a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2H12"/><circle cx="9" cy="10" r="1.6"/>'
                 '<path d="M3.5 17l5-5 3.5 3.5"/><path d="M15.5 15.5l5 5M20.5 15.5l-5 5"/>', "i lg")
RETRY = icon('<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.5 4v4h4"/>', "i sm")
HAND = '<span class="hand" style="left:{x}px;top:{y}px"></span>'


# ---- fictional pictures: SVG stand-ins that scale like the real thing ----

def terminal(cls="pic", style=""):
    """A phone screenshot of a terminal session (390 × 844)."""
    rows = [
        ("#8b949e", "~/atlas $ make check"), ("#c9d1d9", "python  ok   412 tests  38.1 s"), ("#c9d1d9", "web     ok   611 tests  24.6 s"),
        ("#f85149", "ui      FAIL  shard 3/6"), ("#c9d1d9", "  search.pw.ts:88  results"), ("#c9d1d9", "  expected 20 rows, got 0"),
        ("#8b949e", "  at walk.state (walkthrough.ts)"), ("#c9d1d9", ""), ("#8b949e", "~/atlas $ git log -1 --oneline"),
        ("#c9d1d9", "4be1c0a Keep cursors across the"), ("#c9d1d9", "        index switch"), ("#c9d1d9", ""),
        ("#8b949e", "~/atlas $ make ui UI_ARGS=search"), ("#3fb950", "  1 passed (9.4s)"), ("#8b949e", "~/atlas $ ▍"),
    ]
    text = "".join(f'<text x="18" y="{150 + i * 30}" fill="{c}">{t}</text>' for i, (c, t) in enumerate(rows))
    return f'''<svg class="{cls}" style="{style}" viewBox="0 0 390 844" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
<rect width="390" height="844" fill="#0d1117"/><text x="28" y="34" fill="#e6edf3" font-size="15" font-weight="600" font-family="IBM Plex Sans, DejaVu Sans, sans-serif">9:41</text>
<rect x="318" y="22" width="44" height="16" rx="4" fill="none" stroke="#e6edf3" stroke-width="1.5"/><rect x="321" y="25" width="30" height="10" rx="2" fill="#e6edf3"/>
<rect x="0" y="60" width="390" height="48" fill="#161b22"/><text x="18" y="90" fill="#e6edf3" font-size="15" font-family="IBM Plex Sans, DejaVu Sans, sans-serif" font-weight="600">Terminal · atlas</text>
<g font-family="IBM Plex Mono, DejaVu Sans Mono, monospace" font-size="14.5">{text}</g>
<rect x="0" y="770" width="390" height="74" fill="#161b22"/><rect x="18" y="786" width="354" height="40" rx="20" fill="#0d1117" stroke="#30363d"/>
<rect x="128" y="832" width="134" height="5" rx="2.5" fill="#e6edf3"/></svg>'''


def dashboard(cls="pic", style=""):
    """A desktop screenshot of a search dashboard (1440 × 900)."""
    bars = "".join(f'<rect x="{560 + i * 70}" y="{700 - h}" width="44" height="{h}" rx="6" fill="#6366f1" opacity="{0.55 + i * 0.05}"/>'
                   for i, h in enumerate([180, 240, 210, 300, 270, 340, 320, 390, 360, 420, 400]))
    rows = "".join(f'<rect x="64" y="{220 + i * 64}" width="380" height="44" rx="8" fill="#f1f5f9"/><rect x="84" y="{235 + i * 64}" width="{180 + (i * 37) % 120}" height="14" rx="7" fill="#94a3b8"/>'
                   for i in range(8))
    return f'''<svg class="{cls}" style="{style}" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
<rect width="1440" height="900" fill="#ffffff"/><rect width="1440" height="72" fill="#0f172a"/>
<text x="64" y="46" fill="#ffffff" font-family="IBM Plex Sans, DejaVu Sans, sans-serif" font-size="24" font-weight="600">Atlas search · p95 latency</text>
<rect x="64" y="120" width="380" height="64" rx="10" fill="#eef2ff"/><text x="88" y="160" fill="#3730a3" font-family="IBM Plex Sans, DejaVu Sans, sans-serif" font-size="22">Index v2 · 182 ms</text>
{rows}<rect x="520" y="120" width="856" height="640" rx="14" fill="none" stroke="#e2e8f0" stroke-width="2"/>
<text x="560" y="170" fill="#0f172a" font-family="IBM Plex Sans, DejaVu Sans, sans-serif" font-size="22" font-weight="600">Requests over the cutover</text>{bars}
<path d="M560 520 C700 470 820 540 960 430 S1200 330 1330 300" fill="none" stroke="#f59e0b" stroke-width="5"/></svg>'''


def chart(cls="pic", style=""):
    """A small landscape picture (800 × 600)."""
    return f'''<svg class="{cls}" style="{style}" viewBox="0 0 800 600" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
<rect width="800" height="600" fill="#f8fafc"/><rect width="800" height="90" fill="#1e293b"/>
{"".join(f'<rect x="{60 + (i % 3) * 40}" y="{130 + i * 38}" width="{180 + (i * 53) % 260}" height="22" rx="4" fill="#e11d48"/>' for i, _ in enumerate(range(11)))}</svg>'''


# ---- the viewer ----

def phone_viewer(label, picture, *, name="terminal.png", context="You · 3 min ago", count="1 / 3", bars=True,
                 prev=False, nxt=True, action=SHARE, action_label="Share image", extra="", stage_style="", note="", disabled=False):
    top = f'''<div class="vbar top">
  <span class="vbtn" title="Close image">{CLOSE}</span>
  <span class="vtitle"><b>{name}</b><small>{context}</small></span>
  {f'<span class="vbtn{" off" if disabled else ""}" title="{action_label}">{action}</span>' if action else '<span class="vgap"></span>'}
</div>''' if bars else ""
    arrows = (f'<span class="vnav prev">{BACK}</span>' if prev and bars else "") + (f'<span class="vnav next">{NEXT}</span>' if nxt and bars else "")
    pill = f'<span class="vcount">{count}</span>' if bars and count else ""
    return f'''<figure class="frame"><figcaption>{label}</figcaption>
<div class="phone viewer" style="{stage_style}">{picture}{extra}{top}{arrows}{pill}</div>{f'<p class="note">{note}</p>' if note else ''}</figure>'''


def desktop_viewer(label, picture, *, name="dashboard.png", context="L2 · 12:04", count="2 / 4", zoomed=False, extra="",
                   pill=None, copy=True, actions=True, note="", scale=1.0):
    acts = ""
    if actions:
        acts = (f'<span class="vbtn d{" on" if zoomed else ""}" title="{"Fit image" if zoomed else "Zoom image"}">{FIT if zoomed else ZOOM}</span>'
                + (f'<span class="vbtn d" title="Copy image">{COPY}</span>' if copy else "")
                + f'<span class="vbtn d" title="Download image">{DOWNLOAD}</span><span class="vbtn d" title="Open in new tab">{OPEN}</span>'
                + '<span class="vdiv"></span>')
    tip = '<span class="tip">Copy image</span>' if "tip" in extra else ""
    return f'''<figure class="frame"><figcaption>{label}</figcaption>
<div class="desk viewer" style="zoom:{scale}">{picture}{extra.replace("tip", "")}
<div class="vbar top desk-bar"><span class="vtitle"><b>{name}</b><small>{context}</small></span>
<span class="vacts">{acts}<span class="vbtn d" title="Close image">{CLOSE}</span></span>{tip}</div>
<span class="vnav prev d">{BACK}</span><span class="vnav next d">{NEXT}</span>
<span class="vcount{" vstatus" if pill else ""}">{pill or count}</span></div>{f'<p class="note">{note}</p>' if note else ''}</figure>'''


def phone_boards():
    fit = terminal("pic pfit")
    frames = [
        phone_viewer("01 · Open from a chat thumbnail", fit,
                     note="The picture fits between the bars, so the viewer never covers the screenshot's own header. Share opens the system sheet."),
        phone_viewer("02 · Pinched or double-tapped", terminal("pic zoom", "width:1100px;height:2380px;left:-40px;top:-520px"),
                     extra=HAND.format(x=80, y=330) + HAND.format(x=230, y=470),
                     note="Zooms around the fingers, up to actual pixels on a double-tap; one finger pans under the bars and edges hold."),
        phone_viewer("03 · Swiping to the next", terminal("pic pfit", "left:-150px") + dashboard("pic pfit", "left:256px"), count="1 / 3",
                     extra=HAND.format(x=180, y=520), nxt=False,
                     note="The picture follows the finger and the next slides in 16 px behind it; past a third, it carries on."),
        phone_viewer("04 · Swipe down to close", terminal("pic pfit", "transform:translate(10px,120px) scale(.86)"), bars=False,
                     extra='<div class="under"></div>' + HAND.format(x=200, y=560),
                     note="The dark and the bars fade with the distance; past 120 px it shrinks back into its thumbnail."),
    ]
    board("Phone", 4 * 430 + 40, 1010, '<h1 class="bt">Image viewer · phone</h1><div class="frames">' + "".join(frames) + "</div>")
    states = [
        phone_viewer("05 · Loading", '<span class="vspin"></span>', name="IMG_2041.png", context="You · 3 min ago", disabled=True,
                     note="Close, arrows and count work; Share is dimmed until the picture is in hand."),
        phone_viewer("06 · Failed", f'<div class="vfail">{IMAGE_OFF}<b>Image unavailable</b><span class="vretry">Retry</span></div>',
                     name="IMG_2041.png", context="You · 3 min ago", action=None, count="2 / 3", prev=True,
                     note="Arrows and Close still work; Share is hidden."),
        phone_viewer("07 · A document's picture", chart("pic pfit"), name="phone.png", context="README.md", count="2 / 5", prev=True,
                     action=DOWNLOAD, action_label="Download image",
                     note="Context names the document. Where the browser cannot share files, Download takes Share's place."),
        phone_viewer("08 · A picked image, before sending", terminal("pic pfit"), name="IMG_2045.png", context="Not sent yet", action=None, count="",
                     nxt=False, note="Tapping a picked thumbnail checks it full size; no actions, since it is your own file."),
    ]
    board("PhoneStates", 4 * 430 + 40, 1010, '<h1 class="bt">Image viewer · phone states</h1><div class="frames">' + "".join(states) + "</div>")


def desktop_boards():
    open_ = desktop_viewer("01 · Open from an owner's shown images", dashboard("pic dfit"),
                           note="Whole screen, near-black. Name and where it came from at the left; Zoom, Copy, Download, Open in new tab and Close at the right, each with its tooltip.",
                           extra="tip")
    board("Desktop", 1500, 1080, '<h1 class="bt">Image viewer · desktop</h1>' + open_)
    zoomed = desktop_viewer("02 · Zoomed toward the pointer", dashboard("pic zoom", "width:2880px;height:1800px;left:-1200px;top:-560px"),
                            zoomed=True, scale=0.5,
                            note="Wheel, double-click or Zoom image; drag pans, edges hold.")
    failed = desktop_viewer("03 · Failed", f'<div class="vfail">{IMAGE_OFF}<b>Image unavailable</b><span class="vretry">Retry</span></div>',
                            name="overview.png", context="Before · Pictures in this PR", count="1 / 3", actions=False, scale=0.5,
                            note="Only Close and the arrows remain.")
    copied = desktop_viewer("04 · Copied", chart("pic dfit"), name="timeline.png", context="You · yesterday", pill='<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg> Image copied', scale=0.5,
                            note="The message takes the count's place for two seconds.")
    capture = desktop_viewer("05 · A validation capture", terminal("pic dfit"), name="Phone send", context="Validation run 4", count="2 / 2",
                             copy=False, scale=0.5, note="A GIF plays as it does on the page; Copy is not offered for it.")
    board("DesktopStates", 1500, 1180, '<h1 class="bt">Image viewer · desktop states</h1><div class="grid2">' + zoomed + failed + copied + capture + "</div>")


# ---- thumbnails ----

def tile(picture, name=None, w=None, h=None, state="ready", reason="Image unavailable", more=None):
    size = f"width:{w}px;height:{h}px"
    if state == "loading":
        inner = '<span class="tload"></span>'
    elif state == "failed":
        inner = f'<span class="tfail">{IMAGE_OFF.replace("i lg", "i")}<small>{reason}</small><span class="tretry">{RETRY}Retry</span></span>'
    else:
        inner = picture.replace("xMidYMid meet", "xMidYMin slice") + (f'<span class="tmore">+{more}</span>' if more else "")
    label = f'<span class="tname">{name}</span>' if name else ""
    return f'<span class="tile{" " + state}" style="width:{w}px"><span class="tpic" style="{size}">{inner}</span>{label}</span>'


def thumbnails():
    phone = f'''<div class="phone page">
<div class="ph-head"><span class="back">{BACK}</span><b>Image task</b></div>
<div class="convo-col">
<div class="me">The timer covers the result count.
  <span class="tiles one">{tile(terminal("pic cover"), "IMG_2041.png", 135, 240)}</span></div>
<div class="me">Before and after the fix
  <span class="tiles">{tile(chart("pic cover"), "before.png", 128, 96)}{tile(chart("pic cover"), "after.png", 128, 96)}</span></div>
<div class="me">Two more
  <span class="tiles">{tile(None, "IMG_2043.png", 72, 96, "loading")}{tile(None, "IMG_2044.png", 128, 96, "failed", "Access denied")}</span></div>
</div></div>'''
    desk_shown = f'''<div class="desk-col">
<p class="reply">I checked the export on both widths. Here is what it looks like now.</p>
<span class="tiles shown">{tile(dashboard("pic cover"), None, 160, 120)}{tile(chart("pic cover"), None, 160, 120)}{tile(None, None, 120, 120, "failed")}{tile(chart("pic cover"), None, 120, 120, more=2)}</span>
<p class="cap">Shown images: one row, three tiles and +N; a failed one now reads like a chat tile.</p>
<div class="doc"><h3>Phone layout</h3><p>The results list keeps its count above the fold.</p>
<span class="docpic">{terminal("pic", "width:220px;height:476px;position:static")}<span class="badge-x">{EXPAND}</span></span>
<p class="cap">A document's picture keeps its expand badge; press and focus match every thumbnail.</p></div>
<div class="captures"><b>Desktop send</b> <small>29 KiB · 1.8 s · 3 frames</small>
<span class="docpic">{dashboard("pic", "width:420px;height:262px;position:static")}<span class="badge-x">{EXPAND}</span></span>
<b>Desktop reconnect</b>
<span class="capfail">{IMAGE_OFF}<b>Capture unavailable</b><span class="vretry light">Retry</span></span>
<p class="cap">Captures open in the viewer; loading is a grey block at the saved size, failure the document's box.</p></div>
</div>'''
    body = f'''<h1 class="bt">Thumbnails that open the viewer</h1>
<div class="frames"><figure class="frame"><figcaption>Phone · chat images: one tall, a pair, loading and failed</figcaption>{phone}
<p class="note">One picture at its own aspect (9:16 to 16:9, inside 200 × 240, cropped from the top); several as 96 px tiles; names beneath; a grey block while loading; the reason and Retry on failure.</p></figure>
<figure class="frame"><figcaption>Desktop · shown images, a document picture, captures</figcaption>{desk_shown}</figure></div>'''
    board("Thumbnails", 1260, 1560, body)


phone_boards()
desktop_boards()
thumbnails()
