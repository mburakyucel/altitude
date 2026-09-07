#!/usr/bin/env python3
"""Generate the wireframe boards, wireframes.css, and boards.js beside this script.

The boards are static HTML: markup here, styles in wireframes.css, tokens from the build's
web/design/tokens.css, the one token file the boards and the build share (SPEC.md §6). Run it after
editing and commit the output with it: `python3 design/wireframes/gen.py`. SPEC.md is the authority
on behaviour; a board shows one moment of it.
"""
import pathlib

OUT = pathlib.Path(__file__).parent

FONT_LINK = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&amp;family=IBM+Plex+Mono:wght@400;500&amp;display=swap">'

TOKENS = r"""
@import url("../../web/design/tokens.css");
"""

# Board styles. Class names are shared with the markup below; SPEC.md §3 names the components.
CSS = r"""
*{box-sizing:border-box}
body{margin:0}
.root{font-family:var(--font-ui);color:var(--text-primary);background:var(--surface);-webkit-font-smoothing:antialiased;font-size:14px;line-height:1.5;position:relative;overflow:hidden}
a{color:var(--accent-text);text-decoration:none}a:hover{color:var(--accent-hover)}
svg.i{width:18px;height:18px;flex:none;fill:none;stroke:currentColor;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round}
svg.i.lg{width:24px;height:24px}
svg.i.sm{width:14px;height:14px}
/* rail */
.rail{background:var(--page);border-right:1px solid var(--hairline);display:flex;flex-direction:column;padding:14px 12px 12px}
.brand{display:flex;align-items:center;gap:10px;height:40px;padding:0 8px;margin-bottom:10px;font-weight:600;font-size:15px;color:var(--text-primary)}
.mark{width:26px;height:26px;border-radius:8px;background:var(--accent);display:inline-flex;align-items:center;justify-content:center;color:var(--on-accent)}
.mark svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
.ri{display:flex;align-items:center;gap:10px;height:38px;padding:0 10px;border-radius:var(--radius-control);color:var(--text-secondary);font-weight:500;font-size:14px}
.ri.sel{background:var(--accent-tint);color:var(--text-primary)}
.ri .badge{margin-left:auto}
.badge{min-width:20px;height:20px;padding:0 6px;border-radius:999px;background:var(--accent);color:var(--on-accent);font-size:11px;font-weight:600;display:inline-flex;align-items:center;justify-content:center;line-height:1}
.badge.quiet{background:var(--bubble);color:var(--text-secondary)}
.rsec{display:flex;align-items:center;justify-content:space-between;height:32px;padding:0 10px;margin-top:14px;font-size:12px;font-weight:500;color:var(--text-muted)}
.rsec .plus{width:24px;height:24px;border-radius:6px;display:inline-flex;align-items:center;justify-content:center;color:var(--text-muted)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--accent);display:inline-block;flex:none}
.dot.held{background:var(--data-claimed)}.dot.q{background:var(--border)}.dot.idle{background:var(--text-muted);opacity:.45}
.engines{display:flex;flex-direction:column;gap:10px;padding:12px 10px 8px;margin-top:auto;border-top:1px solid var(--hairline)}
.erow{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--text-muted)}
.erow b{color:var(--text-secondary);font-weight:500}
.erow .t{display:flex;justify-content:space-between}
.meter{height:4px;border-radius:2px;background:var(--data-track);overflow:hidden}.meter i{display:block;height:100%;background:var(--accent);border-radius:2px}.meter.hot i{background:var(--danger)}
.who{display:flex;align-items:center;gap:10px;height:40px;padding:0 10px;color:var(--text-secondary);font-size:13px;font-weight:500}
.avatar{width:26px;height:26px;border-radius:50%;background:var(--bubble);color:var(--text-primary);display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:600}
/* pane */
.pane{display:flex;flex-direction:column;min-width:0;background:var(--surface)}
.ph{display:flex;align-items:center;justify-content:space-between;height:64px;padding:0 28px;flex:none}
.ph h1{font-size:18px;font-weight:600;margin:0;line-height:1.3}
.ph .sub{font-size:13px;color:var(--text-muted);margin-top:1px}
.ph .acts{display:flex;align-items:center;gap:4px}
.ib{width:36px;height:36px;border-radius:var(--radius-control);display:inline-flex;align-items:center;justify-content:center;color:var(--text-secondary)}
.ib.on{background:var(--accent-tint);color:var(--accent-text)}
.crumb{font-size:13px;color:var(--text-muted);display:flex;align-items:center;gap:6px}
/* conversation */
.convo{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:flex-end;padding:0 28px}
.col{width:100%;max-width:720px;margin:0 auto;display:flex;flex-direction:column;gap:22px}
.day{text-align:center;font-size:12px;color:var(--text-muted)}
.me{align-self:flex-end;max-width:76%;background:var(--bubble);border-radius:var(--radius-bubble);padding:10px 16px;font-size:15px;line-height:1.55}
.l3{font-size:15px;line-height:1.65;color:var(--text-primary);padding:2px 0;display:flex;flex-direction:column;gap:12px}
.l3 p{margin:0}
.sys{display:flex;justify-content:center;align-items:center;gap:8px;font-size:12px;color:var(--text-muted)}
.sys .ln{flex:1;height:1px;background:var(--hairline)}
.tcard{display:flex;align-items:center;gap:12px;padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-card);background:var(--card);max-width:480px;color:var(--text-primary)}
.tcard .tt{font-weight:600;font-size:14px;line-height:1.3}.tcard .tm{font-size:12px;color:var(--text-muted);margin-top:2px}
.tcard .go{margin-left:auto;color:var(--text-muted)}
.composer{border:1px solid var(--border);border-radius:var(--radius-composer);background:var(--card);box-shadow:var(--shadow);padding:14px 12px 10px 18px;width:100%;max-width:720px;margin:0 auto}
.composer .ph2{color:var(--text-muted);font-size:15px;min-height:24px;line-height:24px}
.composer .draft{color:var(--text-primary);font-size:15px;min-height:24px;line-height:24px}
.crow{display:flex;align-items:center;gap:6px;margin-top:8px}
.pillbtn{display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 10px 0 12px;border-radius:999px;color:var(--text-secondary);font-size:13px;font-weight:500;border:1px solid transparent}
.icb{width:36px;height:36px;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;color:var(--text-secondary);flex:none}
.icb.send{background:var(--accent);color:var(--on-accent)}
.icb.rec{background:var(--danger);color:var(--on-accent)}
.icb.dim{color:var(--border)}
.hint{text-align:center;font-size:12px;color:var(--text-muted);padding:10px 0 14px}
/* work panel */
.work{background:var(--page);border-left:1px solid var(--hairline);display:flex;flex-direction:column;padding:18px 18px 16px;gap:18px;overflow:hidden}
.wh{display:flex;align-items:baseline;justify-content:space-between;padding:0 4px;height:28px}
.wh h2{font-size:15px;font-weight:600;margin:0}.wh span{font-size:12px;color:var(--text-muted)}
.sh{display:flex;align-items:baseline;gap:8px;font-size:13px;font-weight:600;color:var(--text-primary);margin:0 0 10px;padding:0 4px}
.sh span{color:var(--text-muted);font-weight:500}.sh a{margin-left:auto;font-size:12px;font-weight:500}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-card);padding:16px 18px}
.card.tight{padding:14px 16px}
.kind{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-muted)}
.kind b{color:var(--accent-text);font-weight:600;white-space:nowrap}.kind b.h{color:var(--chip-claimed-text)}
.kind .age{margin-left:auto;white-space:nowrap}
.q{font-size:14px;font-weight:600;line-height:1.45;margin:8px 0 6px;color:var(--text-primary)}
.q.big{font-size:17px;margin:10px 0 8px}
.why{font-size:13px;color:var(--text-secondary);line-height:1.5;margin:0}
.opts{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;height:34px;padding:0 13px;border-radius:var(--radius-control);border:1px solid var(--border);background:var(--card);font-weight:500;font-size:13px;color:var(--text-primary);white-space:nowrap}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--on-accent);font-weight:600}
.btn.ghost{border-color:transparent;background:transparent;color:var(--text-secondary)}
.btn.lg{height:40px;padding:0 16px;font-size:14px}
.foot{display:flex;justify-content:space-between;align-items:center;margin-top:12px;font-size:12px;color:var(--text-muted)}
.foot a{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--text-muted)}
.trow{display:flex;gap:10px;padding:10px 4px;border-top:1px solid var(--hairline);align-items:flex-start}
.trow:first-of-type{border-top:0}
.trow .dot{margin-top:7px}
.trow .tt{font-size:13px;font-weight:500;line-height:1.4;color:var(--text-primary)}
.trow .tm{font-size:12px;color:var(--text-muted);margin-top:2px}
.fold{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--text-muted);padding:8px 4px}
.chip{display:inline-flex;align-items:center;height:22px;padding:0 8px;border-radius:999px;font-size:12px;font-weight:500;border:1px solid var(--border);color:var(--text-secondary);background:var(--card);white-space:nowrap}
.chip.ok{background:var(--success-bg);color:var(--success-text);border-color:transparent}
.chip.held{background:var(--chip-claimed-bg);color:var(--chip-claimed-text);border-color:var(--chip-claimed-border)}
.chip.proj{background:var(--bubble);border-color:transparent;color:var(--text-secondary)}
.tabs{display:flex;gap:4px;padding:0 28px;border-bottom:1px solid var(--hairline);flex:none}
.tab2{height:40px;display:inline-flex;align-items:center;gap:6px;padding:0 12px;font-size:14px;font-weight:500;color:var(--text-muted);border-bottom:2px solid transparent;margin-bottom:-1px}
.tab2.on{color:var(--text-primary);border-bottom-color:var(--text-primary)}
.seg{display:inline-flex;background:var(--bubble);border-radius:var(--radius-control);padding:3px;gap:2px}
.seg span{height:30px;padding:0 14px;border-radius:8px;display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:500;color:var(--text-secondary)}
.seg span.on{background:var(--card);color:var(--text-primary);box-shadow:0 1px 2px rgba(15,23,42,.08)}
/* live session panel */
.live{background:var(--surface);border-left:1px solid var(--hairline);display:flex;flex-direction:column;min-width:0;overflow:hidden}
.lh{display:flex;align-items:center;justify-content:space-between;height:64px;padding:0 20px;flex:none}
.lh h2{font-size:15px;font-weight:600;margin:0;display:flex;align-items:center;gap:8px}
.lh .pulse{width:8px;height:8px;border-radius:50%;background:var(--success-text)}
.lbody{padding:0 20px 20px;display:flex;flex-direction:column;gap:8px;font-size:13px}
.sep{display:flex;align-items:center;gap:10px;font-size:12px;color:var(--text-muted);padding:6px 0}
.sep .ln{flex:1;height:1px;background:var(--hairline)}
.prompt{background:var(--accent-tint);border:1px solid var(--accent-tint-border);border-radius:var(--radius-control);padding:10px 12px;font-size:13px;color:var(--text-primary)}
.prompt b{color:var(--accent-text);font-weight:600;display:block;margin-bottom:2px;font-size:12px}
.prose{padding:2px 0;color:var(--text-primary);line-height:1.55}
.tool{display:flex;align-items:center;gap:10px;height:36px;padding:0 12px;border:1px solid var(--border);border-radius:var(--radius-control);background:var(--card);font-family:var(--font-mono);font-size:12.5px;color:var(--text-primary)}
.tool b{font-family:var(--font-ui);font-weight:600;font-size:12px;color:var(--text-secondary);width:42px}
.tool .n{margin-left:auto;color:var(--text-muted);font-family:var(--font-ui);font-size:12px;display:inline-flex;align-items:center;gap:4px}
.tool .diff{margin-left:auto;font-size:12px}.tool .diff b{width:auto;color:var(--success-text)}.tool .diff i{font-style:normal;color:var(--danger)}
/* needs-you list */
.list{width:100%;max-width:760px;margin:0 auto;display:flex;flex-direction:column;gap:14px}
.calm{text-align:center;color:var(--text-muted);font-size:13px;padding:28px 0}
.calm b{display:block;color:var(--text-secondary);font-weight:500;font-size:14px;margin-bottom:2px}
/* mobile */
.m{width:390px;height:844px;display:grid;grid-template-rows:54px 56px 1fr auto 84px;background:var(--surface)}
.mh{display:flex;align-items:center;padding:0 10px 0 16px;gap:8px}
.mh .name{display:inline-flex;align-items:center;gap:4px;height:40px;padding:0 10px 0 4px;border-radius:var(--radius-control);font-size:17px;font-weight:600;color:var(--text-primary)}
.mh .name svg{color:var(--text-muted)}
.mh .subl{font-size:12px;color:var(--text-muted);line-height:1.2}
.mh .sp{flex:1}
.mbody{min-height:0;overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end;padding:0 16px}
.mbody.top{justify-content:flex-start;padding-top:6px;gap:14px}
.mcol{display:flex;flex-direction:column;gap:16px}
.m .me{max-width:82%;font-size:16px;padding:10px 14px}
.m .l3{font-size:16px;line-height:1.6}
.m .composer{border-radius:22px;padding:12px 10px 8px 16px;max-width:none}
.m .composer .ph2,.m .composer .draft{font-size:16px}
.m .icb{width:40px;height:40px}
.mcomp{padding:10px 12px 8px}
.tabbar{border-top:1px solid var(--hairline);background:var(--page);display:grid;grid-template-columns:repeat(4,minmax(0,1fr));padding-bottom:26px}
.tab{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;font-size:11px;font-weight:500;color:var(--text-muted);position:relative}
.tab.on{color:var(--accent-text)}
.tab .badge{position:absolute;top:6px;left:calc(50% + 6px);height:18px;min-width:18px;font-size:10px}
.m .card{border-radius:14px;padding:16px}
.m .btn{height:44px;font-size:14px;padding:0 16px;border-radius:var(--radius-card)}
.m .q{font-size:16px;margin:8px 0 6px}
.m .why{font-size:14px}
.m .trow .tt{font-size:15px}.m .trow .tm{font-size:13px}
.mtitle{font-size:24px;font-weight:600;margin:6px 0 0;line-height:1.2}
.msub{font-size:14px;color:var(--text-muted);margin-top:2px}
.sheet{position:absolute;left:0;right:0;bottom:0;background:var(--card);border-radius:20px 20px 0 0;padding:8px 16px 40px;box-shadow:var(--shadow)}
.handle{width:36px;height:5px;border-radius:3px;background:var(--border);margin:0 auto 14px}
.srow{display:flex;align-items:center;gap:12px;min-height:56px;padding:8px 8px;border-radius:var(--radius-card)}
.srow.sel{background:var(--accent-tint)}
.srow .tt{font-size:16px;font-weight:600;color:var(--text-primary);line-height:1.3}.srow .tm{font-size:13px;color:var(--text-muted);margin-top:1px}
.srow .r{margin-left:auto;display:flex;align-items:center;gap:8px}
.scrim{position:absolute;inset:0;background:var(--scrim)}
/* composer states sheet */
.sheetgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:28px 32px;padding:0 40px 40px}
.st{display:flex;flex-direction:column;gap:10px}
.st .lab{font-size:13px;font-weight:600;color:var(--text-primary)}
.st .lab span{color:var(--text-muted);font-weight:400;margin-left:6px}
.st .composer{max-width:none;box-shadow:none}
.wave{display:flex;align-items:center;gap:3px;height:24px;flex:1;padding:0 6px}
.wave i{display:block;width:3px;border-radius:2px;background:var(--accent)}
.status{font-size:13px;color:var(--text-muted);display:inline-flex;align-items:center;gap:6px}
.undo{display:inline-flex;align-items:center;gap:4px;height:24px;padding:0 8px;border-radius:999px;background:var(--accent-tint);color:var(--accent-text);font-size:12px;font-weight:500}
.inl{font-size:13px;color:var(--danger);display:inline-flex;align-items:center;gap:6px}
/* decision page */
.card.sel{border-color:var(--accent)}
.dsh{font-size:13px;font-weight:600;color:var(--text-muted);margin:0 0 8px}
.dp{font-size:14px;color:var(--text-secondary);line-height:1.6;margin:0}
.dp+.dp{margin-top:8px}
.field{height:36px;border:1px solid var(--border);border-radius:var(--radius-control);padding:0 12px;display:flex;align-items:center;color:var(--text-muted);font-size:13px;flex:1;min-width:200px;background:var(--card)}
.tl{display:flex;flex-direction:column}
.tli{display:grid;grid-template-columns:44px 12px minmax(0,1fr);gap:0 12px;padding:6px 0}
.tli .t{font-size:12px;color:var(--text-muted);font-family:var(--font-mono);padding-top:3px}
.tli .mk{position:relative;display:flex;justify-content:center}
.tli .mk i{width:8px;height:8px;border-radius:50%;background:var(--border);margin-top:7px}
.tli .mk i.you{background:var(--accent)}
.tli .mk:after{content:"";position:absolute;top:19px;bottom:-8px;left:5.5px;width:1px;background:var(--hairline)}
.tli:last-child .mk:after{display:none}
.tli .b{font-size:14px;color:var(--text-primary);line-height:1.5}.tli .b b{font-weight:600}
.tli .b q{display:block;color:var(--text-secondary);margin-top:3px;padding-left:10px;border-left:2px solid var(--border)}
.tli .b q:before,.tli .b q:after{content:""}
.links{display:flex;flex-wrap:wrap;gap:8px}
.lnk{display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 12px;border-radius:999px;border:1px solid var(--border);font-size:13px;color:var(--text-secondary);background:var(--card);white-space:nowrap}
.fu{display:flex;gap:8px;padding:10px 12px;border-radius:var(--radius-control);background:var(--accent-tint);font-size:13px;line-height:1.45;color:var(--text-primary)}
.fu b{color:var(--accent-text);font-weight:600;white-space:nowrap}
.fu.wait{background:var(--bubble);color:var(--text-secondary)}
.m .fu{font-size:14px}
.stgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px 28px;padding:0 40px 40px}
/* system turns in chat */
.sys .d{width:6px;height:6px;border-radius:50%;background:var(--text-muted);flex:none}
.sys .d.fault{background:var(--danger)}
.sys .d.live{background:var(--accent)}
.sysx{background:var(--page);border:1px solid var(--hairline);border-radius:var(--radius-card);padding:12px 14px;font-size:13px;color:var(--text-secondary);line-height:1.5}
.sysx .hd{display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--text-muted);margin-bottom:8px}
.sysx .lbl{font-size:12px;font-weight:600;color:var(--text-primary);margin:10px 0 4px}
.sysx .kv{display:grid;grid-template-columns:auto minmax(0,1fr);gap:2px 12px;font-size:12.5px}
.sysx .kv b{font-weight:500;color:var(--text-muted)}
.sysx .rp{color:var(--text-primary);margin:0}
.sysx .ft{display:flex;gap:14px;margin-top:10px;font-size:12px}
.sysl{display:flex;flex-direction:column;gap:6px;padding:4px 0 0 18px;font-size:12.5px;color:var(--text-muted)}
.sysl div{display:flex;gap:8px;align-items:center}
"""

# ---------- icons ----------
def I(name, cls="i"):
    p = {
        "tray": '<path d="M3 13l2.6-7.2A1 1 0 0 1 6.5 5h11a1 1 0 0 1 .9.8L21 13v5.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/><path d="M3 13h5l1.4 2.5h5.2L16 13h5"/>',
        "chat": '<path d="M20 14.5a2 2 0 0 1-2 2H9l-4.5 4v-4H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/>',
        "work": '<rect x="3.5" y="5.5" width="17" height="14" rx="2"/><path d="M8 5.5V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v1.5"/><path d="M3.5 11h17"/>',
        "pulse": '<path d="M3 12h3.5l2.5-6.5 4 13 2.5-6.5H21"/>',
        "chev-d": '<path d="M6 9l6 6 6-6"/>',
        "chev-r": '<path d="M9 6l6 6-6 6"/>',
        "chev-l": '<path d="M15 6l-6 6 6 6"/>',
        "plus": '<path d="M12 5v14M5 12h14"/>',
        "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
        "mic-off": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/><path d="M4 4l16 16"/>',
        "up": '<path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>',
        "stop": '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
        "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
        "x": '<path d="M6 6l12 12M18 6L6 18"/>',
        "panel": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M14.5 4.5v15"/>',
        "more": '<circle cx="6" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="18" cy="12" r="1.2" fill="currentColor" stroke="none"/>',
        "folder": '<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h4l2 2.5h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
        "ext": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
        "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
        "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
        "spin": '<path d="M12 3a9 9 0 0 1 9 9"/>',
    }[name]
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true">{p}</svg>'

MARK = '<span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17l6-10 4 6 2-3 4 7"/></svg></span>'

# ---------- rail ----------
def rail(selected, needs=2, first_run=False, count_alt="1", count_vt="1"):
    sel = lambda k: " sel" if selected == k else ""
    needs_badge = f'<span class="badge">{needs}</span>' if needs else ""
    if first_run:
        projects = (
            '<div class="rsec"><span>Projects</span><span class="plus">' + I("plus", "i sm") + '</span></div>'
            '<div style="padding:6px 10px 0;font-size:13px;color:var(--text-muted);line-height:1.5">None yet. Start with a folder on the right.</div>'
        )
        engines = (
            '<div class="engines">'
            '<div class="erow"><div class="t"><b>Claude</b><span>connected</span></div><div class="meter"><i style="width:6%"></i></div></div>'
            '<div class="erow"><div class="t"><b>Codex</b><span>not configured</span></div><div class="meter"><i style="width:0"></i></div></div>'
            '</div>'
        )
    else:
        projects = (
            '<div class="rsec"><span>Projects</span><span class="plus">' + I("plus", "i sm") + '</span></div>'
            f'<div class="ri{sel("altitude")}"><span class="dot held"></span>altitude<span class="badge quiet">{count_alt}</span></div>'
            f'<div class="ri{sel("voice-tutor")}"><span class="dot idle"></span>voice-tutor<span class="badge quiet">{count_vt}</span></div>'
            '<div class="ri" style="color:var(--text-muted);font-weight:400;font-size:13px">' + I("folder") + '2 folders not managed</div>'
        )
        engines = (
            '<div class="engines">'
            '<div class="erow"><div class="t"><b>Claude</b><span>21% of week</span></div><div class="meter"><i style="width:21%"></i></div></div>'
            '<div class="erow"><div class="t"><b>Codex</b><span>71% of week</span></div><div class="meter hot"><i style="width:71%"></i></div></div>'
            '</div>'
        )
    return (
        '<aside class="rail">'
        f'<div class="brand">{MARK}Altitude</div>'
        f'<div class="ri{sel("needs")}">{I("tray")}Needs you{needs_badge}</div>'
        + projects + engines +
        f'<div class="ri{sel("monitor")}" style="height:36px">{I("pulse")}Monitor</div>'
        '<div class="who"><span class="avatar">B</span>Burak<span style="margin-left:auto;color:var(--text-muted)">' + I("gear", "i sm") + '</span></div>'
        '</aside>'
    )

# ---------- shared pieces ----------
def composer(placeholder="Message L3 about altitude", engine=True, mobile=False, draft=None, hint=None, to=None):
    eng = f'<span class="pillbtn">Auto{I("chev-d","i sm")}</span>' if engine else (f'<span class="pillbtn">To {to}{I("chev-d","i sm")}</span>' if to else '<span style="flex:1"></span>')
    body = f'<div class="draft">{draft}</div>' if draft else f'<div class="ph2">{placeholder}</div>'
    h = f'<div class="hint">{hint}</div>' if hint else ""
    return (
        f'<div class="composer">{body}'
        f'<div class="crow">{eng}<span style="flex:1"></span>'
        f'<span class="icb">{I("mic","i lg")}</span>'
        f'<span class="icb send">{I("up","i lg")}</span></div></div>{h}'
    )

def tcard(title, meta, dot="dot"):
    return (
        f'<div class="tcard"><span class="{dot}"></span><div><div class="tt">{title}</div>'
        f'<div class="tm">{meta}</div></div><span class="go">{I("chev-r","i sm")}</span></div>'
    )

def decision_card(kind, kind_cls, task, age, q, why, b1, b2, disc, big=False, project=None, mobile=False):
    proj = f'<span class="chip proj">{project}</span>' if project else ""
    return (
        f'<article class="card"><div class="kind">{proj}<b class="{kind_cls}">{kind}</b>'
        f'<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{task}</span><span class="age">{age}</span></div>'
        f'<h3 class="q{" big" if big else ""}">{q}</h3><p class="why">{why}</p>'
        f'<div class="opts"><span class="btn primary{" lg" if big else ""}">{b1}</span><span class="btn{" lg" if big else ""}">{b2}</span></div>'
        f'<div class="foot"><a href="#" style="color:var(--accent-text)">More context{I("chev-r","i sm")}</a><span style="display:flex;gap:14px"><a href="#">Ask a follow-up</a><a href="#">Open task</a></span></div></article>'
    )

def work_panel(selected=None):
    s1 = " sel" if selected == 1 else ""
    s2 = " sel" if selected == 2 else ""
    return (
        '<aside class="work">'
        '<div class="wh"><h2>Work</h2><span>3 active · 4 done this week</span></div>'
        '<div><div class="sh">Needs you <span>2</span></div><div style="display:flex;flex-direction:column;gap:10px">'
        f'<article class="card tight{s1}"><div class="kind"><b>L3 asks</b><span class="age">25 min</span></div>'
        '<h3 class="q">Fast-forward Altitude’s own checkout at dispatch, or keep failing closed?</h3>'
        '<p class="why">L3 recommends fast-forwarding; this race blocked three dispatches this week.</p>'
        '<div class="opts"><span class="btn primary">Fast-forward it</span><span class="btn">Keep failing closed</span></div>'
        f'<div class="foot" style="margin-top:10px"><a href="#" style="color:var(--accent-text)">More context{I("chev-r","i sm")}</a></div></article>'
        f'<article class="card tight{s2}"><div class="kind"><b class="h">Ready for review</b><span class="age">8 min</span></div>'
        '<h3 class="q">PR #176 is green and held: the wireframe set, desktop and iPhone.</h3>'
        '<div class="opts"><span class="btn primary">Open PR #176</span><span class="btn">Ask the L2</span></div>'
        f'<div class="foot" style="margin-top:10px"><a href="#" style="color:var(--accent-text)">More context{I("chev-r","i sm")}</a></div></article>'
        '</div></div>'
        '<div><div class="sh">Active <span>3</span></div>'
        '<div class="trow"><span class="dot held"></span><div><div class="tt">Design wireframes for the simplified product</div><div class="tm">Fable on Claude · PR #176 green · held for you</div></div></div>'
        '<div class="trow"><span class="dot"></span><div><div class="tt">Fast-forward a self-deploy checkout at dispatch</div><div class="tm">Opus on Claude · waiting on your answer</div></div></div>'
        '<div class="trow"><span class="dot q"></span><div><div class="tt">Persist paths when L3 resumes a task</div><div class="tm">Queued · waits for a lease</div></div></div>'
        f'<div class="fold">{I("chev-r","i sm")}Done this week · 4</div></div>'
        '</aside>'
    )

def convo_altitude(mobile=False):
    return (
        '<div class="day">Today</div>'
        '<div class="me">Morning. Anything I should look at?</div>'
        '<div class="l3"><p>Nothing waits on you. One task is running on its own and its PR should open within the hour.</p></div>'
        '<div class="me">Where are we on the chat scrollbar fix?</div>'
        '<div class="l3"><p>Merged an hour ago as PR #175. It shows after the next restart; nothing waits on you.</p></div>'
        '<div class="me">Address issue #166. Use Fable at L2 for this one as well; design is important.</div>'
        '<div class="l3"><p>Created one task for it. It started a minute ago with Fable on Claude, and its PR will be held for your review before anything merges.</p>'
        + tcard("Design wireframes for the simplified product", "Running · Fable on Claude · started 1 min ago") +
        '</div>'
        '<div class="sys"><span class="ln"></span><span>L3 handled two system events</span><a href="#">Show</a><span class="ln"></span></div>'
        '<div class="me">Not only desktop first. Both mobile and desktop need to land.</div>'
        '<div class="l3"><p>Passed that to the task’s L2. Every board will ship in a desktop and an iPhone version in the same PR.</p></div>'
    )

# ---------- board file ----------
BOARDS = []  # (file, width, height) in generation order; boards.js is written from it

THEME_SCRIPT = "<script>if(location.search.indexOf('dark')>=0)document.querySelector('.root').dataset.theme='dark'</script>"

def board(name, w, h, inner):
    """Write <name>.html: one static board at its native size. `?dark` in the URL shows the dark theme."""
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Altitude wireframe: {name}</title>
{FONT_LINK}
<link rel="stylesheet" href="wireframes.css">
</head>
<body>
<div class="root" data-theme="light" style="width:{w}px;height:{h}px">
{inner}
</div>
{THEME_SCRIPT}
</body>
</html>
"""
    (OUT / f"{name}.html").write_text(html)
    BOARDS.append((name, w, h))

# =====================================================================
# Desktop 1: project view (Main)
def desktop_project(with_panel=True):
    cols = "260px minmax(0,1fr) 340px" if with_panel else "260px minmax(0,1fr)"
    header_right = (
        f'<div class="acts"><span class="ib{" on" if with_panel else ""}">{I("panel")}</span><span class="ib">{I("more")}</span></div>'
    )
    tabs = ""
    if not with_panel:
        tabs = ('<div style="display:flex;justify-content:center;padding:0 28px 6px;flex:none">'
                f'<span class="seg"><span class="on">{I("chat","i sm")}Chat</span><span>{I("work","i sm")}Work <span class="badge quiet" style="height:18px;min-width:18px;font-size:10px">3</span></span><span>Done</span></span></div>')
    inner = (
        f'<div style="display:grid;grid-template-columns:{cols};height:100%">'
        + rail("altitude") +
        '<main class="pane">'
        '<header class="ph"><div><h1>altitude</h1><div class="sub">L3 answered 12 min ago on Claude · 3 tasks in flight · 1 waits for your review</div></div>'
        + header_right + '</header>' + tabs +
        '<div class="convo"><div class="col">' + convo_altitude() + '</div>'
        '<div style="height:22px"></div>'
        + composer(hint="L3 answers or creates one task. Shift + Enter for a new line.") +
        '</div></main>'
        + (work_panel() if with_panel else "") +
        '</div>'
    )
    return inner

board("Project", 1440, 900, desktop_project(True))

# Desktop 2: Needs you (cross-project)
needs_inner = (
    '<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:100%">'
    + rail("needs") +
    '<main class="pane">'
    '<header class="ph" style="height:auto;padding-top:34px;padding-bottom:10px"><div style="width:100%;max-width:760px;margin:0 auto">'
    '<h1 style="font-size:24px">Needs you</h1><div class="sub" style="font-size:14px">Three things wait on you across two projects. Everything else runs on its own.</div></div></header>'
    '<div style="padding:18px 28px 0"><div class="list">'
    + decision_card("L3 asks", "", "Fast-forward a self-deploy checkout at dispatch", "25 min",
                    "When Altitude’s own checkout is behind origin/main at dispatch, should it fast-forward itself or keep failing closed?",
                    "L3 recommends fast-forwarding: the checkout is Altitude’s, the move is a pure fast-forward, and this race has blocked three dispatches this week.",
                    "Fast-forward it", "Keep failing closed", "Why L3 recommends this", big=True, project="altitude")
    + decision_card("Ready for your review", "h", "Design wireframes for the simplified product", "8 min",
                    "PR #176 is green and held: 16 boards, desktop and iPhone.",
                    "Held because the boards are a design draft and taste is your call. The L2 reports nothing else needs a decision.",
                    "Open PR #176", "Ask the L2", "What the L2 assumed", big=True, project="altitude")
    + decision_card("L3 asks", "", "Score pronunciation per phoneme", "yesterday",
                    "Score pronunciation per phoneme, or keep the per-word score and ship the tutor loop first?",
                    "L3 recommends per-word first: it is what the current lesson flow uses, and per-phoneme scoring needs a new alignment model.",
                    "Per-word first", "Per-phoneme now", "Why L3 recommends this", big=True, project="voice-tutor")
    + '<div class="calm"><b>That is everything.</b>Running work stays in each project. FYIs from L3 appear in that project’s chat.</div>'
    '</div></div></main></div>'
)
board("NeedsYou", 1440, 900, needs_inner)

# Desktop 3: task page — L2 conversation beside the live session
live = (
    '<aside class="live">'
    f'<div class="lh"><h2><span class="pulse"></span>Live session</h2><div style="display:flex;gap:4px"><span class="btn ghost" style="height:32px">Pause</span><span class="btn ghost" style="height:32px">Raw events</span></div></div>'
    '<div class="lbody">'
    '<div class="sep"><span class="ln"></span>Session started 09:00 · attempt 1 · Fable on Claude<span class="ln"></span></div>'
    '<div class="prompt"><b>Brief</b>Design wireframes for the simplified product (#166): the request, the L2 persona, and the lease. 2,300 words</div>'
    '<div class="prose">I’ll start by orienting: the issue with the comments, the docs, the live routes, the tokens, and the design skill.</div>'
    f'<div class="tool"><b>Bash</b>gh issue view 166 --comments<span class="n">38 lines{I("chev-r","i sm")}</span></div>'
    f'<div class="tool"><b>Read</b>docs/ARCHITECTURE.md<span class="n">121 lines{I("chev-r","i sm")}</span></div>'
    f'<div class="tool"><b>Read</b>web/design/tokens.css<span class="n">72 lines{I("chev-r","i sm")}</span></div>'
    '<div class="prose">Fonts load in headless Chrome, so the boards can use the build’s IBM Plex faces. Writing the shared stylesheet next.</div>'
    '<div class="tool"><b>Write</b>design/wireframes/wireframes.css<span class="diff"><b>+297</b> <i>−0</i></span></div>'
    '<div class="sep"><span class="ln"></span>Turn ended 09:31 · context 9% used<span class="ln"></span></div>'
    '<div class="prompt"><b>You, via the task inbox</b>Not only desktop first. Both mobile and desktop need to land.</div>'
    '<div class="prose">Understood. Every board ships at both sizes in this PR; the mobile boards respect the iPhone safe areas.</div>'
    f'<div class="tool"><b>Write</b>design/wireframes/MobileInbox.html<span class="diff"><b>+84</b> <i>−0</i></span></div>'
    '<div class="sep" style="color:var(--success-text)"><span class="ln"></span>Following live · new steps appear at the bottom<span class="ln"></span></div>'
    '</div></aside>'
)
task_inner = (
    '<div style="display:grid;grid-template-columns:260px minmax(0,1fr) 480px;height:100%">'
    + rail("altitude") +
    '<main class="pane">'
    '<header style="padding:16px 28px 14px;display:flex;flex-direction:column;gap:6px;flex:none">'
    f'<div style="display:flex;align-items:center;justify-content:space-between"><div class="crumb">{I("chev-l","i sm")}altitude</div>'
    f'<div class="acts"><span class="btn ghost">Stop</span><span class="btn ghost">Reject…</span><span class="ib on">{I("panel")}</span></div></div>'
    '<h1 style="display:flex;align-items:center;gap:10px;margin:0;font-size:18px;font-weight:600;line-height:1.3"><span class="dot held"></span>Design wireframes for the simplified product</h1>'
    '<div class="sub" style="font-size:13px;color:var(--text-muted)">Fable on Claude · attempt 1 · 42 min · 13% of its context used</div>'
    '<div style="display:flex;align-items:center;gap:6px;margin-top:2px"><span class="chip ok">PR #176 checks passed</span><span class="chip held">held for your review</span></div>'
    '</header>'
    '<div class="convo"><div class="col">'
    '<div class="day">Today</div>'
    '<div class="l3"><p>I read #166 and both of your comments. Plan: one shared stylesheet on the build’s tokens, sixteen boards, numbered callouts on each. I’ll render with headless Chrome to check clipping at both sizes.</p></div>'
    '<div class="me">Not only desktop first. Both mobile and desktop need to land.</div>'
    '<div class="l3"><p>Understood: every board ships at both sizes in this PR. The mobile boards respect the iPhone safe areas and keep the composer at 16px.</p></div>'
    '<div class="l3"><p>PR #176 is open and green. It is held for your review as the brief says; the README lists the three assumptions I made where the record was silent.</p>'
    + tcard("PR #176 · Design wireframes for the simplified product", "Checks passed · held for your review · open on GitHub", dot="dot held") +
    '</div></div><div style="height:22px"></div>'
    + composer("Ask or steer this task’s L2", engine=False, hint="Messages reach the L2 at its next checkpoint. Stop aborts the worker.") +
    '</div></main>' + live + '</div>'
)
board("Task", 1440, 900, task_inner)

# Desktop 4: first run
first_inner = (
    '<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:100%">'
    + rail(None, needs=0, first_run=True) +
    '<main class="pane" style="justify-content:center;align-items:center">'
    '<div style="width:100%;max-width:560px;display:flex;flex-direction:column;gap:20px;margin-top:-60px">'
    f'<div style="display:flex;justify-content:center">{MARK.replace("mark", "mark", 1).replace("width:26px;height:26px", "")}</div>'
    '<div style="text-align:center"><h1 style="font-size:24px;font-weight:600;margin:0">Point Altitude at a project</h1>'
    '<p style="font-size:15px;color:var(--text-secondary);margin:8px 0 0;line-height:1.55">Altitude runs an L3 for each project you manage. Pick a folder and it starts a conversation about that project; work it creates lands as reviewed pull requests.</p></div>'
    '<div class="card" style="padding:6px 8px">'
    f'<div class="srow"><span class="ib" style="color:var(--text-muted)">{I("folder")}</span><div><div class="tt" style="font-size:15px">career-platform</div><div class="tm">~/Projects · git repository · 214 commits</div></div><div class="r"><span class="btn primary">Start L3</span></div></div>'
    f'<div class="srow"><span class="ib" style="color:var(--text-muted)">{I("folder")}</span><div><div class="tt" style="font-size:15px">job-search-assistant</div><div class="tm">~/Projects · git repository · 61 commits</div></div><div class="r"><span class="btn primary">Start L3</span></div></div>'
    f'<div class="srow" style="min-height:48px"><span class="ib" style="color:var(--text-muted)">{I("plus")}</span><div class="tt" style="font-size:14px;font-weight:500;color:var(--text-secondary)">Choose another folder…</div></div>'
    '</div>'
    '<p style="text-align:center;font-size:13px;color:var(--text-muted);margin:0">One engine is connected: Claude Code. Altitude works with one engine alone; add Codex later under Monitor.</p>'
    '</div></main></div>'
)
board("FirstRun", 1440, 900, first_inner)

# =====================================================================
# Mobile
def mheader_project(sub="L3 answered 12 min ago"):
    return (
        '<header class="mh"><div><span class="name">altitude' + I("chev-d","i sm") + '</span>'
        f'<div class="subl" style="padding-left:4px;margin-top:-6px">{sub}</div></div><span class="sp"></span>'
        f'<span class="ib">{I("more")}</span></header>'
    )

def mheader_global(title="Altitude"):
    return (
        f'<header class="mh"><div class="brand" style="margin:0;padding:0 4px;height:40px">{MARK}{title}</div><span class="sp"></span>'
        f'<span class="ib">{I("more")}</span></header>'
    )

def tabbar(on):
    def t(k, icon, label, badge=None):
        b = f'<span class="badge">{badge}</span>' if badge else ""
        return f'<div class="tab{" on" if on == k else ""}">{I(icon,"i lg")}{label}{b}</div>'
    return ('<nav class="tabbar">' + t("chat","chat","Chat") + t("work","work","Work") +
            t("needs","tray","Needs you", 2) + t("monitor","pulse","Monitor") + '</nav>')

mobile_convo = (
    '<div class="me">Address issue #166. Use Fable at L2 for this one as well; design is important.</div>'
    '<div class="l3"><p>Created one task for it. It started a minute ago with Fable on Claude, and its PR will be held for your review before anything merges.</p>'
    + tcard("Design wireframes for the simplified product", "Running · Fable on Claude · 1 min") +
    '</div>'
    '<div class="sys"><span class="ln"></span><span>Two system events</span><a href="#">Show</a><span class="ln"></span></div>'
    '<div class="me">Not only desktop first. Both mobile and desktop need to land.</div>'
    '<div class="l3"><p>Passed that to the task’s L2. Every board will ship in a desktop and an iPhone version in the same PR.</p></div>'
)

def mobile_chat(sheet=False):
    inner = (
        '<div class="m"><div></div>' + mheader_project() +
        '<div class="mbody"><div class="mcol">' + mobile_convo + '</div></div>'
        '<div class="mcomp">' + composer("Message L3", mobile=True) + '</div>'
        + tabbar("chat") + '</div>'
    )
    if sheet:
        inner += (
            '<div class="scrim"></div>'
            '<div class="sheet"><div class="handle"></div>'
            '<div class="sh" style="padding:0 8px;margin-bottom:6px">Projects <span>2 managed</span></div>'
            f'<div class="srow sel"><span class="dot held"></span><div><div class="tt">altitude</div><div class="tm">1 waits for your review · 3 in flight</div></div><div class="r">{I("check")}</div></div>'
            f'<div class="srow"><span class="dot idle"></span><div><div class="tt">voice-tutor</div><div class="tm">1 question for you · L3 idle since yesterday</div></div></div>'
            '<div style="height:1px;background:var(--hairline);margin:8px 8px"></div>'
            f'<div class="srow"><span class="ib" style="color:var(--text-muted);width:auto">{I("folder")}</span><div><div class="tt" style="font-size:15px;font-weight:500">career-platform</div><div class="tm">not managed</div></div><div class="r"><span class="btn" style="height:36px;font-size:13px">Start L3</span></div></div>'
            f'<div class="srow"><span class="ib" style="color:var(--text-muted);width:auto">{I("folder")}</span><div><div class="tt" style="font-size:15px;font-weight:500">job-search-assistant</div><div class="tm">not managed</div></div><div class="r"><span class="btn" style="height:36px;font-size:13px">Start L3</span></div></div>'
            '</div>'
        )
    return inner

board("MobileProject", 390, 844, mobile_chat(False))
board("MobileSwitcher", 390, 844, mobile_chat(True))

mobile_work = (
    '<div class="m"><div></div>' + mheader_project("3 in flight · 4 done this week") +
    '<div class="mbody top">'
    '<div><div class="sh">Needs you <span>2</span></div>'
    '<article class="card"><div class="kind"><b class="h">Ready for your review</b><span class="age">8 min</span></div>'
    '<h3 class="q">PR #176 is green and held: the wireframe set, desktop and iPhone.</h3>'
    '<div class="opts"><span class="btn primary">Open PR #176</span><span class="btn">Ask the L2</span></div>'
    f'<div class="foot"><a href="#" style="color:var(--accent-text)">More context{I("chev-r","i sm")}</a><a href="#">Open task</a></div></article>'
    '<article class="card" style="margin-top:10px"><div class="kind"><b>L3 asks</b><span class="age">25 min</span></div>'
    '<h3 class="q">Fast-forward Altitude’s own checkout at dispatch, or keep failing closed?</h3>'
    '<div class="opts"><span class="btn primary">Fast-forward it</span><span class="btn">Keep failing closed</span></div>'
    f'<div class="foot"><a href="#" style="color:var(--accent-text)">More context{I("chev-r","i sm")}</a><a href="#">Open task</a></div></article></div>'
    '<div><div class="sh">Active <span>3</span></div>'
    '<div class="trow"><span class="dot held"></span><div><div class="tt">Design wireframes for the simplified product</div><div class="tm">Fable on Claude · PR #176 green · held for you</div></div></div>'
    '<div class="trow"><span class="dot"></span><div><div class="tt">Fast-forward a self-deploy checkout at dispatch</div><div class="tm">Opus on Claude · waiting on your answer</div></div></div>'
    '<div class="trow"><span class="dot q"></span><div><div class="tt">Persist paths when L3 resumes a task</div><div class="tm">Queued · waits for a lease</div></div></div>'
    f'<div class="fold">{I("chev-r","i sm")}Done this week · 4</div></div>'
    '</div><div></div>' + tabbar("work") + '</div>'
)
board("MobileWork", 390, 844, mobile_work)

mobile_needs = (
    '<div class="m"><div></div>' + mheader_global() +
    '<div class="mbody top">'
    '<div><h1 class="mtitle">Needs you</h1><div class="msub">Three things wait on you. Everything else runs on its own.</div></div>'
    '<article class="card"><div class="kind"><span class="chip proj">altitude</span><b>L3 asks</b><span class="age">25 min</span></div>'
    '<h3 class="q">Fast-forward Altitude’s own checkout at dispatch, or keep failing closed?</h3>'
    '<p class="why">L3 recommends fast-forwarding: the move is a pure fast-forward, and this race blocked three dispatches this week.</p>'
    '<div class="opts"><span class="btn primary">Fast-forward it</span><span class="btn">Keep failing closed</span></div>'
    f'<div class="foot"><a href="#" style="color:var(--accent-text)">More context{I("chev-r","i sm")}</a><a href="#">Open task</a></div></article>'
    '<article class="card"><div class="kind"><span class="chip proj">altitude</span><b class="h">Ready for review</b><span class="age">8 min</span></div>'
    '<h3 class="q">PR #176 is green and held: the wireframe set, desktop and iPhone.</h3>'
    '<div class="opts"><span class="btn primary">Open PR #176</span><span class="btn">Ask the L2</span></div>'
    f'<div class="foot"><a href="#" style="color:var(--accent-text)">More context{I("chev-r","i sm")}</a><a href="#">Open task</a></div></article>'
    '<article class="card"><div class="kind"><span class="chip proj">voice-tutor</span><b>L3 asks</b><span class="age">yesterday</span></div>'
    '<h3 class="q">Score pronunciation per phoneme, or keep per-word and ship the tutor loop first?</h3></article>'
    '</div><div></div>' + tabbar("needs") + '</div>'
)
board("MobileNeedsYou", 390, 844, mobile_needs)

# =====================================================================
# Composer states sheet
def wave(heights):
    return '<span class="wave">' + "".join(f'<i style="height:{h}px"></i>' for h in heights) + '</span>'

def state(label, note, body):
    return f'<div class="st"><div class="lab">{label}<span>{note}</span></div>{body}</div>'

def comp_custom(top, row):
    return f'<div class="composer">{top}<div class="crow">{row}</div></div>'

W = [6,10,16,22,14,8,12,20,24,18,10,6,9,15,21,17,11,7,13,19,23,16,9,6,12,18,14,8]
states = (
    state("Idle", "mic and send inside the field; engine pin is a quiet pill",
          comp_custom('<div class="ph2">Message L3 about altitude</div>',
                      f'<span class="pillbtn">Auto{I("chev-d","i sm")}</span><span style="flex:1"></span><span class="icb">{I("mic","i lg")}</span><span class="icb dim">{I("up","i lg")}</span>'))
    + state("Listening", "one tap; the field becomes the recording surface",
          comp_custom('', f'<span class="icb">{I("x","i lg")}</span>{wave(W)}<span class="status" style="font-family:var(--font-mono)">0:07</span><span class="icb rec">{I("stop","i lg")}</span>'))
    + state("Transcribing", "waveform freezes; nothing else changes",
          comp_custom('', f'<span class="icb dim">{I("x","i lg")}</span>{wave([4]*28)}<span class="status">{I("spin","i sm")}Transcribing…</span><span class="icb dim">{I("stop","i lg")}</span>'))
    + state("Transcript landed", "text is the draft; one undo, no review panel",
          comp_custom('<div class="draft">Address issue 166 and use Fable at L2 for this one as well, design is important.</div>',
                      f'<span class="pillbtn">Auto{I("chev-d","i sm")}</span><span class="undo">{I("undo","i sm")}Undo transcript</span><span style="flex:1"></span><span class="icb">{I("mic","i lg")}</span><span class="icb send">{I("up","i lg")}</span>'))
    + state("L3 is busy", "send becomes Queue; the message runs at the next turn",
          comp_custom('<div class="draft">Not only desktop first. Both mobile and desktop need to land.</div>',
                      f'<span class="pillbtn">Auto{I("chev-d","i sm")}</span><span class="status">L3 is mid-turn · queued messages run in order</span><span style="flex:1"></span><span class="icb">{I("mic","i lg")}</span><span class="btn primary" style="border-radius:999px;height:36px">Queue</span>'))
    + state("Voice unavailable", "denied, plain HTTP, or no speech service: one line, typing unchanged",
          comp_custom('<div class="ph2">Message L3 about altitude</div>',
                      f'<span class="pillbtn">Auto{I("chev-d","i sm")}</span><span class="inl">{I("mic-off","i sm")}Microphone needs the secure address · typing works</span><span style="flex:1"></span><span class="icb dim">{I("up","i lg")}</span>'))
)
sheet_inner = (
    '<div style="padding:36px 40px 10px"><h1 style="font-size:20px;font-weight:600;margin:0">Composer states</h1>'
    '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:760px">One composer for Chat, the task conversation, and the project quick message. Every state lives inside the field; no status lines or review panels below it. Mobile uses the same states at 16px with 44px targets.</p></div>'
    f'<div class="sheetgrid">{states}</div>'
)
board("ComposerStates", 1200, 760, sheet_inner)


# =====================================================================
# Decision page (desktop, in the main pane; work panel stays with the card selected)
def decision_body(mobile=False):
    big = "" if mobile else " big"
    opts = (
        '<div class="opts" style="margin-top:0">'
        f'<span class="btn primary{"" if mobile else " lg"}"' + (' style="flex:1"' if mobile else '') + '>Fast-forward it</span>'
        f'<span class="btn{"" if mobile else " lg"}"' + (' style="flex:1"' if mobile else '') + '>Keep failing closed</span>'
        + ('' if mobile else '<span class="field">Add a note for the L2 (optional)</span>') +
        '</div>'
        + ('<div class="field" style="margin-top:8px;height:44px;border-radius:var(--radius-card);font-size:14px">Add a note for the L2 (optional)</div>' if mobile else '')
    )
    return (
        '<div class="kind"><span class="chip proj">altitude</span><b>L3 asks</b><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Fast-forward a self-deploy checkout at dispatch</span><span class="age">25 min</span></div>'
        f'<h1 class="q" style="font-size:{"20px" if mobile else "22px"};line-height:1.35;margin:10px 0 14px">When Altitude’s own checkout is behind origin/main at dispatch, should it fast-forward itself or keep failing closed?</h1>'
        + opts +
        '<div><div class="dsh">Why L3 recommends fast-forwarding</div>'
        '<p class="dp">The checkout is Altitude’s own deployment copy, so the move is a pure fast-forward with nothing local to lose. The fail-closed rule protects checkouts Altitude does not own; this one it owns.</p>'
        '<p class="dp">The race has blocked three dispatches this week, each cleared by a manual restart. Fast-forwarding removes that step and changes nothing for other projects.</p></div>'
        '<div><div class="dsh">Where this came from</div><div class="tl">'
        '<div class="tli"><span class="t">09:16</span><span class="mk"><i></i></span><div class="b"><b>The L2</b> (Opus on Claude) asked L3'
        '<q>Dispatch found the deployment checkout two commits behind origin/main. The brief says fail closed. Should I fast-forward instead?</q></div></div>'
        '<div class="tli"><span class="t">09:17</span><span class="mk"><i></i></span><div class="b"><b>L3</b> checked the record: no decision covers Altitude’s own checkout, so it escalated to you with a recommendation.</div></div>'
        '<div class="tli"><span class="t">now</span><span class="mk"><i class="you"></i></span><div class="b">The task is blocked until you choose. Nothing else waits on it.</div></div>'
        '</div></div>'
        '<div><div class="dsh">Evidence</div><div class="links">'
        f'<span class="lnk">{I("chat","i sm")}Task conversation</span>'
        f'<span class="lnk">{I("pulse","i sm")}Live session at the failing step</span>'
        f'<span class="lnk">{I("work","i sm")}The three blocked dispatches</span>'
        f'<span class="lnk">{I("ext","i sm")}Decision 36 in the record</span>'
        '</div></div>'
    )

decision_desktop = (
    '<div style="display:grid;grid-template-columns:260px minmax(0,1fr) 340px;height:100%">'
    + rail("altitude") +
    '<main class="pane">'
    f'<header class="ph" style="height:auto;padding:16px 28px 0"><div class="crumb">{I("chev-l","i sm")}altitude</div>'
    f'<div class="acts"><span class="btn ghost">Open task</span><span class="ib on">{I("panel")}</span></div></header>'
    '<div style="flex:1;min-height:0;padding:10px 28px 0"><div class="col" style="gap:22px">' + decision_body() + '</div></div>'
    '<div style="padding:0 28px">'
    + composer("Ask a follow-up before you decide", engine=False, to="L3",
               hint="Your question and the answer appear here and on the card. The L2 stays blocked until you choose.") +
    '</div></main>' + work_panel(selected=1) + '</div>'
)
board("Decision", 1440, 900, decision_desktop)

decision_mobile = (
    '<div class="m"><div></div>'
    f'<header class="mh"><span class="name" style="font-size:15px;font-weight:500;color:var(--accent-text);padding-left:0">{I("chev-l")}Needs you</span><span class="sp"></span>'
    f'<span class="btn ghost" style="height:40px">Open task</span></header>'
    '<div class="mbody top" style="gap:18px">' + decision_body(mobile=True) + '</div>'
    '<div class="mcomp">' + composer("Ask a follow-up before you decide", engine=False, to="L3", mobile=True) + '</div>'
    + tabbar("needs") + '</div>'
)
board("MobileDecision", 390, 844, decision_mobile)

# Decision card states (what appears and disappears)
def dcard(kind, kind_cls, age, q, extra="", opts=True, foot=True):
    o = '<div class="opts"><span class="btn primary">Fast-forward it</span><span class="btn">Keep failing closed</span></div>' if opts else ''
    f = f'<div class="foot" style="margin-top:10px"><a href="#" style="color:var(--accent-text)">More context{I("chev-r","i sm")}</a></div>' if foot else ''
    return (f'<article class="card tight" style="box-shadow:none"><div class="kind"><b class="{kind_cls}">{kind}</b><span class="age">{age}</span></div>'
            f'<h3 class="q">{q}</h3>{extra}{o}{f}</article>')

Q = "Fast-forward Altitude’s own checkout at dispatch, or keep failing closed?"
dstates = (
    state("Waiting on you", "the card as it lands; the options are the whole action",
          dcard("L3 asks", "", "25 min", Q, '<p class="why">L3 recommends fast-forwarding; this race blocked three dispatches this week.</p>'))
    + state("Follow-up sent", "your question is quoted on the card; the options stay",
          dcard("L3 asks", "", "26 min", Q,
                '<div class="fu" style="margin:4px 0 10px"><b>You asked L3</b><span>Which three dispatches, and were they all this task?</span></div>'
                f'<div class="status" style="margin-bottom:10px">{I("spin","i sm")}L3 is answering</div>'))
    + state("Answer arrived", "the answer sits under your question; decide from here",
          dcard("L3 asks", "", "28 min", Q,
                '<div class="fu wait" style="margin:4px 0 6px"><b style="color:var(--text-secondary)">You</b><span>Which three dispatches, and were they all this task?</span></div>'
                '<div class="fu" style="margin:0 0 10px"><b>L3</b><span>All three were this task’s dispatch at 09:02, 09:14 and 09:20. Nothing else was affected.</span></div>'))
    + state("Asked by the L2 directly", "the recipient follows the asker; the follow-up goes to the L2",
          dcard("L2 asks", "", "4 min", "Rename the redirect route or keep the old path and add the new one beside it?",
                '<p class="why">Design wireframes for the simplified product · Fable on Claude. The brief lets the L2 flag you directly on naming.</p>'
                ).replace("Fast-forward it", "Keep both paths").replace("Keep failing closed", "Rename it"))
    + state("Decided", "the card leaves Needs you; the chat and the task row carry the outcome",
          '<div style="display:flex;flex-direction:column;gap:14px">'
          '<div class="sys"><span class="ln"></span><span>You chose Fast-forward it · the L2 resumed</span><a href="#">Show</a><span class="ln"></span></div>'
          '<div class="card tight" style="box-shadow:none;padding:6px 12px"><div class="trow"><span class="dot"></span><div><div class="tt">Fast-forward a self-deploy checkout at dispatch</div><div class="tm">Opus on Claude · resumed after your answer · 1 min</div></div></div></div>'
          '</div>')
)
dstates_inner = (
    '<div style="padding:36px 40px 10px"><h1 style="font-size:20px;font-weight:600;margin:0">Decision card states</h1>'
    '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:820px">A decision is one card wherever it appears: the work panel, Needs you, and the phone. Tapping the question or More context opens its page with the reasoning, where it came from, the evidence, and a follow-up composer addressed to whoever asked. Follow-ups and answers accumulate on the card so the decision can be made without leaving it.</p></div>'
    f'<div class="stgrid">{dstates}</div>'
)
board("DecisionStates", 1200, 880, dstates_inner)


# =====================================================================
# System turns in chat: folded by default, one line each
def sysline(text, dot="", show="Show"):
    d = f'<span class="d{(" " + dot) if dot else ""}"></span>'
    return f'<div class="sys"><span class="ln"></span>{d}<span>{text}</span><a href="#">{show}</a><span class="ln"></span></div>'

sys_states = (
    state("One system turn, folded", "the line is L3’s two-sentence reply, not altd’s prompt",
          '<div style="display:flex;flex-direction:column;gap:14px;padding:6px 0">'
          '<div class="l3" style="font-size:14px">Passed that to the task’s L2. Every board will ship in both sizes.</div>'
          + sysline("Persist paths when L3 resumes a task is done: PR #178 merged, one review finding fixed. Nothing waits on you.")
          + '<div class="me" style="font-size:14px">Good. What is left this week?</div></div>')
    + state("Several in a row, grouped", "consecutive system turns become one line; Show lists them",
          '<div style="display:flex;flex-direction:column;gap:10px;padding:6px 0">'
          + sysline("L3 handled three system events between your messages")
          + '<div class="sysl"><div><span class="d" style="width:6px;height:6px;border-radius:50%;background:var(--text-muted)"></span>09:14 · Report landed for Persist paths: closed as done.</div>'
          '<div><span class="d" style="width:6px;height:6px;border-radius:50%;background:var(--danger)"></span>09:20 · Dispatch fault on Fast-forward a self-deploy checkout: L3 repaired it and resumed the task.</div>'
          '<div><span class="d" style="width:6px;height:6px;border-radius:50%;background:var(--text-muted)"></span>09:31 · Altitude restarted on the new main; both running tasks kept their workers.</div></div></div>')
    + state("Expanded", "what altd sent, what L3 did, one link into the task",
          '<div class="sysx"><div class="hd"><span>Report landed · Persist paths when L3 resumes a task · 09:14</span><a href="#">Hide</a></div>'
          '<div class="lbl">What altd sent L3</div>'
          '<div class="kv"><b>Verdict</b><span>ok</span><b>Problems</b><span>none</span><b>Signals</b><span>one post-mortem note: a flaky test was retried</span><b>PRs</b><span>#178 merged</span><b>Spend</b><span>14 turns, 2 subagent launches</span></div>'
          '<div class="lbl">L3 replied</div>'
          '<p class="rp">Closed the task as done and recorded the flaky test in the digest. Nothing waits on you.</p>'
          f'<div class="ft"><a href="#">Open task</a><a href="#">Full report</a><a href="#">Digest</a></div></div>')
    + state("In progress", "the line appears when the turn starts; your Send becomes Queue meanwhile",
          '<div style="display:flex;flex-direction:column;gap:14px;padding:6px 0">'
          + sysline("L3 is handling a landed report for Persist paths when L3 resumes a task", dot="live", show="")
          + '<div class="composer" style="max-width:none;box-shadow:none"><div class="draft">Good. What is left this week?</div>'
          f'<div class="crow"><span class="pillbtn">Auto{I("chev-d","i sm")}</span><span class="status">L3 is mid-turn · runs next</span><span style="flex:1"></span><span class="icb">{I("mic","i lg")}</span><span class="btn primary" style="border-radius:999px;height:36px">Queue</span></div></div></div>')
    + state("Fault repaired", "faults carry a red dot; the incident stays in the task record",
          '<div style="padding:6px 0">' + sysline("A dispatch fault blocked Fast-forward a self-deploy checkout; L3 repaired it and resumed the task.", dot="fault") + '</div>')
    + state("FYI from L3", "FYIs live here now, not in Needs you",
          '<div style="padding:6px 0">' + sysline("FYI: Codex’s sandbox kept a worktree read-only; PR #171 fixed it, so no repair task was needed.") + '</div>')
)
sys_inner = (
    '<div style="padding:36px 40px 10px"><h1 style="font-size:20px;font-weight:600;margin:0">System turns in chat</h1>'
    '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:860px">Every L3 turn the operator did not start (a landed report, a fault, a restart, an FYI) stays in the one conversation in its real order, but renders as a single muted line: L3’s short reply, with Show for the full prompt and reply. The chat log already records the trigger on every row, so this is a rendering rule, not a second conversation.</p></div>'
    f'<div class="sheetgrid">{sys_states}</div>'
)
board("SystemTurnStates", 1200, 820, sys_inner)

# =====================================================================
# Outputs beside the boards
(OUT / "wireframes.css").write_text(
    "/* Generated by gen.py: the build's tokens, then the board styles.\n"
    " * Edit gen.py, not this file. wireframes.css imports web/design/tokens.css from two levels up,\n"
    " * which is why serve.sh and altd serve the repository tree rather than this folder. */\n"
    + TOKENS + CSS)

# The viewer's board list: one row per route, desktop beside phone, in the order of README.md.
ROUTES = [
    ("Project: chat with L3, work panel beside it", "Project", "MobileProject"),
    ("Project switcher (phone)", None, "MobileSwitcher"),
    ("Project work (phone)", None, "MobileWork"),
    ("Needs you, across projects", "NeedsYou", "MobileNeedsYou"),
    ("Decision page", "Decision", "MobileDecision"),
    ("Task page: L2 conversation and live session", "Task", None),
    ("First run", "FirstRun", None),
    ("Composer states, voice included", "ComposerStates", None),
    ("Decision card states", "DecisionStates", None),
    ("System turns in chat: reports, faults, FYIs", "SystemTurnStates", None),
]
sizes = {name: (w, h) for name, w, h in BOARDS}
listed = {n for _, d, m in ROUTES for n in (d, m) if n}
assert listed == set(sizes), sorted(listed ^ set(sizes))
rows = []
for label, desk, mob in ROUTES:
    parts = [f"label: {label!r}"]
    if desk:
        parts.append(f"desktop: '{desk}.html'")
        if sizes[desk] != (1440, 900):
            parts.append("desktopSize: { w: %d, h: %d, name: 'Sheet' }" % sizes[desk])
    if mob:
        parts.append(f"mobile: '{mob}.html'")
    rows.append("  { " + ", ".join(parts) + " },")
(OUT / "boards.js").write_text("""/* boards.js: the boards the viewer (index.html) lays out, one row per route, desktop beside phone.
 * Generated by gen.py from its ROUTES table; edit that, not this. The viewer reads this file through
 * a <script> tag because Chrome blocks fetch() on file:// URLs and the viewer opens from disk.
 * A listed file that is missing renders as a visible warning tile, never a blank one. */
window.WIREFRAME_BOARDS = [
%s
];

/* Native sizes, matching shots.sh (which reads each board's own width and height). A row's
 * desktopSize / mobileSize ({ w, h, name }) overrides these for that row. */
window.WIREFRAME_SIZES = {
  desktop: { w: 1440, h: 900, name: 'Desktop' },
  mobile:  { w: 390,  h: 844, name: 'iPhone' },
};
""" % "\n".join(rows))
print("wrote", len(BOARDS), "boards, wireframes.css, boards.js")
