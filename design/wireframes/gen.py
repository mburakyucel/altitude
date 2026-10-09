#!/usr/bin/env python3
"""Generate the wireframe boards, wireframes.css, and boards.js beside this script.

The boards are static HTML: markup here, styles in wireframes.css, tokens from the build's
web/design/tokens.css, the one token file the boards and the build share (SPEC.md §6). Run it after
editing and commit the output with it: `python3 design/wireframes/gen.py`. SPEC.md and these boards
are the design record together; keep their rules and visual states aligned.
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
.newtasks{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-secondary);border:1px solid var(--border);border-radius:999px;padding:5px 10px;width:max-content;max-width:100%}
.newtasks b{color:var(--text-primary);font-weight:600}.newtasks.set{background:var(--accent-tint);border-color:transparent}
.l3pill{display:inline-flex;align-items:center;gap:4px;height:32px;padding:0 12px;border:1px solid var(--border);border-radius:999px;font-size:13px;color:var(--text-secondary)}.l3pill b{color:var(--text-primary)}
.sgroup{margin-top:18px}.sgroup h2{font-size:13px;text-transform:none;color:var(--text-muted);margin:0 0 6px}
.sgroup .card{padding:0}.sgroup .setting-link{border:0;border-top:1px solid var(--hairline);border-radius:0;margin:0}.sgroup .setting-link:first-child{border-top:0}
.scols{display:grid;grid-template-columns:1fr 1fr;gap:0 28px;align-items:start}
.models{width:400px;background:var(--card);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow);padding:14px 16px;font-size:14px}
.models .tabs{display:flex;gap:4px;border-bottom:1px solid var(--hairline);margin:-2px 0 10px}.models .tabs span{padding:8px 10px;color:var(--text-muted)}.models .tabs span.on{color:var(--text-primary);box-shadow:inset 0 -2px var(--accent);font-weight:600}
.models .opt{display:flex;gap:8px;align-items:center;padding:7px 2px}.models .opt i.r{width:14px;height:14px;border-radius:50%;border:1.5px solid var(--border);display:inline-block}.models .opt i.r.on{border:4px solid var(--accent)}
.models .opt small{color:var(--text-muted);margin-left:auto}
.seg{display:flex;border:1px solid var(--border);border-radius:10px;overflow:hidden;margin:6px 0 10px}.seg span{flex:1;text-align:center;padding:6px 0;font-size:13px;border-left:1px solid var(--hairline)}.seg span:first-child{border-left:0}.seg span.on{background:var(--accent-tint);color:var(--accent);font-weight:600}
.models .act{display:flex;gap:8px;justify-content:flex-end;align-items:center;margin-top:10px}
.meter{height:4px;border-radius:2px;background:var(--data-track);overflow:hidden}.meter i{display:block;height:100%;background:var(--accent);border-radius:2px}.meter.hot i{background:var(--danger)}
.who{display:flex;align-items:center;gap:10px;height:40px;padding:0 10px;color:var(--text-secondary);font-size:13px;font-weight:500}
.avatar{width:26px;height:26px;border-radius:50%;background:var(--bubble);color:var(--text-primary);display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:600}
/* pane */
.pane{display:flex;flex-direction:column;min-width:0;min-height:0;background:var(--surface);overflow:hidden}
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
.convo>.col{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding-bottom:22px}
.convo>.col>*{flex-shrink:0}.convo>.col>:first-child{margin-top:auto}
.convo>.composer,.convo>.hint{flex-shrink:0}
.day{text-align:center;font-size:12px;color:var(--text-muted)}
.me{align-self:flex-end;max-width:76%;background:var(--bubble);border-radius:var(--radius-bubble);padding:10px 16px;font-size:15px;line-height:1.55}
.l3{font-size:15px;line-height:1.65;color:var(--text-primary);padding:2px 0;display:flex;flex-direction:column;gap:12px}
.l3 p{margin:0}
.sys{display:flex;justify-content:center;align-items:center;gap:8px;font-size:12px;color:var(--text-muted)}
.sys .ln{flex:1;height:1px;background:var(--hairline)}
.tcard{display:flex;align-items:center;gap:12px;padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-card);background:var(--card);max-width:480px;color:var(--text-primary)}
.tcard .tt{font-weight:600;font-size:14px;line-height:1.3}.tcard .tm{font-size:12px;color:var(--text-muted);margin-top:2px}
.tcard .go{margin-left:auto;color:var(--text-muted)}
.offer{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px}
.offer .ob{display:inline-flex;align-items:center;gap:6px;height:34px;padding:0 14px 0 10px;border:1px solid var(--border);border-radius:999px;background:var(--card);font-size:13px;font-weight:600;color:var(--accent-text);white-space:nowrap}
.offer .ob[aria-disabled]{color:var(--text-muted)}
.offer .ot{font-size:13px;color:var(--text-muted);min-width:0}
.offer .err{flex-basis:100%;font-size:13px;color:var(--danger)}
.dots{display:inline-flex;gap:4px;padding:6px 0}.dots i{width:6px;height:6px;border-radius:50%;background:var(--text-muted);opacity:.6}
.composer{border:1px solid var(--border);border-radius:var(--radius-composer);background:var(--card);box-shadow:var(--shadow);padding:14px 12px 10px 18px;width:100%;max-width:720px;margin:0 auto}
.composer .ph2{color:var(--text-muted);font-size:15px;min-height:24px;line-height:24px}
.composer .draft{color:var(--text-primary);font-size:15px;min-height:24px;line-height:24px}
.crow{display:flex;align-items:center;gap:6px;margin-top:8px}
.pillbtn{display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 10px 0 12px;border-radius:999px;color:var(--text-secondary);font-size:13px;font-weight:500;border:1px solid transparent}
.icb{width:36px;height:36px;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;color:var(--text-secondary);flex:none}
.icb.send{background:var(--accent);color:var(--on-accent)}
.icb.send.disabled{opacity:.4}
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
summary.fold{cursor:pointer;min-height:44px}details[open]>summary.fold .i{transform:rotate(90deg)}
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
.lbody{padding:0 20px 20px;display:flex;flex-direction:column;gap:8px;font-size:13px;min-height:0;overflow:auto;overscroll-behavior:contain}.lbody>*{flex-shrink:0}
.sep{display:flex;align-items:center;gap:10px;font-size:12px;color:var(--text-muted);padding:6px 0}
.sep .ln{flex:1;height:1px;background:var(--hairline)}
.prompt{background:var(--accent-tint);border:1px solid var(--accent-tint-border);border-radius:var(--radius-control);padding:10px 12px;font-size:13px;color:var(--text-primary)}
.prompt b{color:var(--accent-text);font-weight:600;display:block;margin-bottom:2px;font-size:12px}
.prose{padding:2px 0;color:var(--text-primary);line-height:1.55}
.tool{display:flex;align-items:center;gap:10px;min-height:36px;padding:6px 12px;border:1px solid var(--border);border-radius:var(--radius-control);background:var(--card);font-family:var(--font-mono);font-size:12.5px;color:var(--text-primary);overflow-wrap:anywhere}
.tool b{font-family:var(--font-ui);font-weight:600;font-size:12px;color:var(--text-secondary);width:42px}
.tool[data-kind="command"] b{width:auto}
.tool .n{margin-left:auto;color:var(--text-muted);font-family:var(--font-ui);font-size:12px;display:inline-flex;align-items:center;gap:4px}
/* needs-you list */
.list{width:100%;max-width:760px;margin:0 auto;display:flex;flex-direction:column;gap:14px}
.calm{text-align:center;color:var(--text-muted);font-size:13px;padding:28px 0}
.calm b{display:block;color:var(--text-secondary);font-weight:500;font-size:14px;margin-bottom:2px}
/* mobile */
.m{width:390px;height:844px;display:grid;grid-template-rows:0 54px minmax(0,1fr) auto 84px;background:var(--surface);overflow:hidden}
.mh{display:flex;align-items:center;padding:0 10px 0 16px;gap:8px}
.mh .name{display:inline-flex;align-items:center;gap:4px;height:44px;padding:0 10px 0 4px;border-radius:var(--radius-control);font-size:17px;font-weight:600;color:var(--text-primary)}
.mh .name svg{color:var(--text-muted)}
.mh .subl{font-size:12px;color:var(--text-muted);line-height:1.2}
.mh .sp{flex:1}
.mbody{min-height:0;overflow:auto;overscroll-behavior:contain;display:flex;flex-direction:column;padding:0 16px}.mbody>*{flex-shrink:0}
.mbody.top{justify-content:flex-start;padding-top:6px;gap:14px}
.mcol{display:flex;flex-direction:column;gap:16px;margin-top:auto;padding-bottom:12px}
.m .me{max-width:82%;font-size:16px;padding:10px 14px}
.m .l3{font-size:16px;line-height:1.6}
.m .composer,.compact .composer{border-radius:22px;padding:4px;max-width:none}
.m .composer .ph2,.m .composer .draft,.compact .composer .ph2,.compact .composer .draft{font-size:16px;min-height:44px;max-height:120px;overflow:auto;padding:10px 8px;line-height:24px}
.m .crow,.compact .crow{margin-top:0}
.m .icb,.compact .icb{width:44px;height:44px}.m .hint.routine,.compact .hint.routine{display:none}
.mcomp{padding:8px 12px}
.phone-details>summary{list-style:none;display:flex;align-items:center;justify-content:center;width:44px;height:44px;cursor:pointer}
.phone-details>summary::-webkit-details-marker{display:none}.phone-details[open]>summary{background:var(--accent-tint);border-radius:10px}
.phone-details .details-panel{position:absolute;top:54px;left:12px;right:12px;z-index:3;max-height:calc(100% - 70px);overflow:auto;padding:20px;background:var(--card);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow);font-size:14px;line-height:1.6}
.details-panel h2{font-size:18px;margin:0 0 14px}.details-panel h3{font-size:15px;margin:18px 0 8px}.details-panel p{margin:8px 0;overflow-wrap:anywhere}.details-panel select{font:inherit;min-height:44px;border:1px solid var(--border);border-radius:10px;background:var(--card);color:var(--text-primary)}
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
/* State examples use the same shapes as their route boards. */
.muted{color:var(--text-muted);font-size:13px}.danger{color:var(--danger)}
.skel{height:16px;border-radius:6px;background:var(--bubble);margin:10px 0}
.statebox{border:1px solid var(--hairline);border-radius:var(--radius-card);padding:16px;min-width:0}
.statebox p{margin:6px 0 12px}.statebox .hint{padding-bottom:0}
.statebox .composer{max-width:358px}.statebox .crow{flex-wrap:nowrap}
.statebox .wave{min-width:0;overflow:hidden}.statebox .status{flex-shrink:0}
.statebox .icb{width:44px;height:44px}.statebox .ph2,.statebox .draft{font-size:16px}
.statebox .lab{margin-bottom:8px}.statebox .tool{font-size:12px}
/* voice states sheet: one row per state, desktop composer beside the phone dock */
/* settings page: option rows, the host setup panel, read-only network lines, entry points */
.opt{display:flex;gap:12px;padding:12px 0;border-bottom:1px solid var(--hairline);align-items:flex-start}.opt:last-child{border-bottom:0}
.opt .radio{width:18px;height:18px;border-radius:50%;border:1.5px solid var(--border);flex:none;margin-top:2px;position:relative}.opt .radio.on{border-color:var(--accent)}.opt .radio.on::after{content:"";position:absolute;inset:3px;border-radius:50%;background:var(--accent)}
.opt b{display:block;font-size:14px;font-weight:600}.opt p{margin:2px 0 0;font-size:13px;color:var(--text-muted);line-height:1.5}
.form{display:grid;gap:10px;margin-top:10px}
.form .actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:2px}.settings-saving .opt{opacity:.55}.settings-saving .btn{pointer-events:none}
.kv{display:grid;grid-template-columns:auto minmax(0,1fr);gap:6px 18px;font-size:13px}.kv span{color:var(--text-muted)}.kv b{font-weight:500;overflow-wrap:anywhere}
.sgrid{display:grid;grid-template-columns:minmax(0,1fr) 390px;gap:22px 32px;padding:0 40px 40px;align-items:start}
.sgrid .vlab{grid-column:1/-1;font-size:13px;font-weight:600;color:var(--text-primary);margin-top:6px}.sgrid .vlab span{color:var(--text-muted);font-weight:400;margin-left:6px}
.menu-mock{width:280px;background:var(--card);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);padding:8px;font-size:14px}.menu-mock div{padding:9px 12px;border-radius:8px}.menu-mock div.hi{background:var(--accent-tint);color:var(--accent)}.menu-mock hr{border:0;border-top:1px solid var(--hairline);margin:6px 0}
.who.on{background:var(--accent-tint);border-radius:10px;color:var(--text-primary)}
.vgrid{display:grid;grid-template-columns:minmax(0,1fr) 390px;gap:22px 32px;padding:0 40px 40px;align-items:start}
.vgrid .vlab{grid-column:1/-1;font-size:13px;font-weight:600;color:var(--text-primary);margin-top:6px}
.vgrid .vlab span{color:var(--text-muted);font-weight:400;margin-left:6px}
.vdesk .composer{max-width:none;box-shadow:none}.vdesk .hint{text-align:left;padding:6px 0 0 18px}
.wave.bounded{flex:none;width:168px;padding:0}
.wave.frozen i{background:var(--text-muted)}
.spin{width:14px;height:14px;border-radius:50%;border:2px solid var(--border);border-top-color:var(--accent);display:inline-block;flex:none}
.compact .gap{flex:1}
.route-content{min-height:0;overflow:auto;overscroll-behavior:contain;padding:20px 28px 28px}
.route-content h1{font-size:18px;margin:0 0 24px;font-weight:600}
.route-content h2{font-size:14px;font-weight:600;margin:22px 0 12px}
.route-content h3{font-size:15px;margin:0;font-weight:600}
.setting-link{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px;border:1px solid var(--border);border-radius:var(--radius-card);color:var(--text-primary);background:var(--card);text-decoration:none}
.setting-link:hover,.setting-link:focus-visible{border-color:var(--accent);background:var(--accent-tint);text-decoration:none}
.setting-link b{display:block;font-weight:600}.setting-link small{display:block;margin-top:3px;color:var(--text-muted);font-size:13px}.setting-back{margin-bottom:20px}
.route-content p{margin:8px 0}.route-content ul{padding-left:20px}
.seats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.monitor-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.monitor-row .muted{margin-left:auto}
.window{margin-top:18px}.window .monitor-row{justify-content:space-between}
.reserve{position:relative;overflow:visible;margin:8px 0}.reserve:after{content:"";position:absolute;left:70%;top:-3px;bottom:-3px;width:1px;background:var(--text-secondary)}
.stale .meter{opacity:.5}.route-content .card{padding:18px}
.routing{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 18px;padding:12px 0;border-bottom:1px solid var(--hairline)}
.routing p{grid-column:1/-1;font-size:13px;color:var(--text-muted);margin:0;overflow-wrap:anywhere}
.sessionrow{padding:16px;border:1px solid var(--border);border-radius:var(--radius-card);margin-top:12px}.sessionrow b{overflow-wrap:anywhere;min-width:0}
.banner{display:flex;gap:16px;align-items:center;padding:12px 28px;background:var(--accent-tint);border-bottom:1px solid var(--accent-tint-border);font-size:13px;flex-shrink:0}
.banner p{margin:0}.banner .btn{margin-left:auto}.banner .muted{font-size:12px}
.m .route-content{padding:16px}.m .seats{grid-template-columns:minmax(0,1fr)}
.m .banner{padding:10px 16px;gap:8px}.m .banner .btn{height:44px;padding:0 10px;font-size:13px}
.task-phone{grid-template-rows:54px 44px minmax(0,1fr) auto 84px}
.task-meta{padding:8px 16px;font-size:12px}.task-meta .monitor-row{gap:6px}
.task-tabs{display:flex;align-items:center;border-bottom:1px solid var(--hairline);padding:0 16px;gap:24px;font-size:14px;min-height:44px}
.task-tabs .on{color:var(--accent-text);border-bottom:2px solid var(--accent);padding-bottom:4px}
.task-phone .live{border:0}.task-phone .lh{padding:0 16px;height:48px}.task-phone .lh h2{font-size:14px}
.task-phone .lbody{padding:0 16px 16px}.task-phone .tool .n{white-space:nowrap}
.activity{flex:none;width:100%;max-width:720px;margin:0 auto 8px;font-size:13px;line-height:1.45}
.activity .activity-words{margin:6px 0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.activity details[open] .activity-words{display:block}.activity summary{cursor:pointer;color:var(--text-muted);list-style:none}
.activity summary::-webkit-details-marker{display:none}.activity .activity-words{color:var(--text-secondary)}
.activity .collapse,.activity details[open] .expand{display:none}.activity details[open] .collapse{display:inline}
.activity-row{display:flex;align-items:center;justify-content:space-between;gap:8px}.activity-row .btn{min-height:44px}
.receipt{align-self:flex-end;font-size:12px;color:var(--text-muted);margin-top:-14px}
.worker-control{padding:8px 16px;border-top:1px solid var(--hairline);margin-top:auto;flex:none}
.m .activity .esc{display:none}
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
def rail(selected, needs=3, first_run=False):
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
            f'<div class="ri{sel("altitude")}"><span class="dot held"></span>altitude</div>'
            f'<div class="ri{sel("harbor")}"><span class="dot idle"></span>harbor</div>'
            '<div class="ri" style="color:var(--text-muted);font-weight:400;font-size:13px">' + I("folder") + '2 folders not managed</div>'
        )
        engines = (
            '<div class="engines">'
            '<div class="erow"><div class="t"><b>Claude</b><span>21% of week</span></div><div class="meter"><i style="width:21%"></i></div></div>'
            '<div class="erow"><div class="t"><b>Codex</b><span>71% of week</span></div><div class="meter hot"><i style="width:71%"></i></div></div>'
            f'<span class="newtasks">New tasks · <b>Auto</b>{I("chev-d","i sm")}</span>'
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
    eng = f'<span class="l3pill">L3 · <b>Auto</b>{I("chev-d","i sm")}</span>' if engine else (f'<span class="pillbtn">To {to}{I("chev-d","i sm")}</span>' if to else '<span style="flex:1"></span>')
    body = f'<div class="draft">{draft}</div>' if draft else f'<div class="ph2">{placeholder}</div>'
    h = f'<div class="hint{"" if "danger" in hint else " routine"}">{hint}</div>' if hint else ""
    return (
        f'<div class="composer">{body}'
        f'<div class="crow">{eng}<span style="flex:1"></span>'
        f'<span class="icb">{I("mic","i lg")}</span>'
        f'<span class="icb send{"" if draft else " disabled"}" aria-label="Send">{I("up","i lg")}</span></div></div>{h}'
    )

def tcard(title, meta, dot="dot"):
    return (
        f'<div class="tcard"><span class="{dot}"></span><div><div class="tt">{title}</div>'
        f'<div class="tm">{meta}</div></div><span class="go">{I("chev-r","i sm")}</span></div>'
    )

def work_rows(mobile=False):
    question = 'MobileConversationFirstGroup.html' if mobile else 'ConversationFirstGroup.html'
    task = 'MobileTask.html' if mobile else 'Task.html'
    def row(title, status, dot="dot", href=task):
        return (f'<a class="trow" href="{href}"><span class="{dot}"></span><div style="flex:1">'
                f'<div class="tt">{title}</div><div class="tm">{status}</div></div>{I("chev-r","i sm")}</a>')
    return (
        '<div><div class="sh">Current <span>5</span></div>'
        + row('Index rollout', 'Needs you · 3 questions · Running', 'dot held', question)
        + row('Design wireframes for the simplified product', 'Running · PR #176 green · Merge held')
        + row('Enable the merge CI gate', 'Planned · waits for parallel checks and the browser fix to land', 'dot q')
        + row('Clarify the dispatch retry policy', 'Waits for L3')
        + row('Update delivery notes', 'Report landed · waits for L3')
        + f'</div><details><summary class="fold">{I("chev-r","i sm")}Done this week · 1</summary>'
        + row('Fix the chat scrollbar', 'Done · PR #175 merged', 'dot idle') + '</details>'
    )

def work_panel():
    return '<aside class="work"><div class="wh"><h2>Work</h2><span>5 current · 1 done this week</span></div>' + work_rows() + '</aside>'

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
        '<div class="sys"><span class="ln"></span><span>L3 handled 2 system events between your messages</span><a href="#">Show</a><span class="ln"></span></div>'
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
def desktop_project(with_panel=True, convo=None):
    cols = "260px minmax(0,1fr) 340px" if with_panel else "260px minmax(0,1fr)"
    header_right = (
        f'<div class="acts"><span class="ib{" on" if with_panel else ""}">{I("panel")}</span><span class="ib">{I("more")}</span></div>'
    )
    tabs = ""
    if not with_panel:
        tabs = ('<div style="display:flex;justify-content:center;padding:0 28px 6px;flex:none">'
                f'<span class="seg"><span class="on">{I("chat","i sm")}Chat</span><span>{I("work","i sm")}Work</span><span>Done</span></span></div>')
    inner = (
        f'<div style="display:grid;grid-template-columns:{cols};height:100%">'
        + rail("altitude") +
        '<main class="pane">'
        '<header class="ph"><div><h1>altitude</h1><div class="sub">L3 answered 12 min ago on Claude · 3 tasks in flight · 3 questions need you</div></div>'
        + header_right + '</header>' + tabs +
        '<div class="convo"><div class="col">' + (convo or convo_altitude()) + '</div>'
        '<div style="height:22px"></div>'
        + composer(hint="L3 answers or creates one task. Shift + Enter for a new line.") +
        '</div></main>'
        + (work_panel() if with_panel else "") +
        '</div>'
    )
    return inner

board("Project", 1440, 900, desktop_project(True))

# Desktop 3: task page — L2 conversation beside the live session
def task_activity(live_href="Task.html"):
    return (
        '<section class="activity" aria-label="L2 activity"><div class="activity-row"><b>Latest from L2</b><span class="muted">12 sec ago</span></div>'
        '<details><summary><span class="activity-words">The composer stays above the keyboard. I’m checking the inner scroll containers next.</span><span class="expand">Expand</span><span class="collapse">Collapse</span></summary></details>'
        '<div class="activity-row"><span class="muted">Tool output observed · 8 sec ago</span><button class="btn">Stop<span class="esc"> · Esc</span></button></div>'
        f'<a href="{live_href}">View live session</a></section>'
    )

live = (
    '<aside class="live">'
    f'<div class="lh"><h2><span class="pulse"></span>Live session</h2><div style="display:flex;gap:4px"><span class="btn ghost" style="height:32px">Pause</span><span class="btn ghost" style="height:32px" title="credential-shaped keys and values are redacted; model reasoning is never shown">Raw events</span></div></div>'
    '<div class="lbody">'
    '<div class="sep"><span class="ln"></span>queued → running · altd · 09:00<span class="ln"></span></div>'
    '<div class="prompt"><b>Brief</b>Design wireframes for the simplified product (#166): the request, the L2 persona, and the lease. 2,300 words</div>'
    '<div class="prose">I’ll start by orienting: the issue with the comments, the docs, the live routes, the tokens, and the design skill.</div>'
    f'<div class="tool" data-kind="command"><b>$</b>gh issue view 166 --comments<span class="n">38 lines{I("chev-r","i sm")}</span></div>'
    f'<div class="tool"><b>Read</b>docs/ARCHITECTURE.md<span class="n">121 lines{I("chev-r","i sm")}</span></div>'
    f'<div class="tool"><b>Read</b>web/design/tokens.css<span class="n">72 lines{I("chev-r","i sm")}</span></div>'
    '<div class="prose">Fonts load in headless Chrome, so the boards can use the build’s IBM Plex faces. Writing the shared stylesheet next.</div>'
    f'<div class="tool"><b>Edit</b>design/wireframes/gen.py<span class="n">3 lines{I("chev-r","i sm")}</span></div>'
    '<div class="prompt"><b>You, via the task inbox</b>Not only desktop first. Both mobile and desktop need to land.</div>'
    '<div class="prose">Understood. Every board ships at both sizes in this PR; the mobile boards respect the iPhone safe areas.</div>'
    f'<div class="tool" data-kind="command"><b>$</b>python3 design/wireframes/gen.py<span class="n">running…</span></div>'
    '<div class="sep" style="color:var(--success-text)"><span class="ln"></span>Following live · new steps appear at the bottom<span class="ln"></span></div>'
    '</div><div class="worker-control"><button class="btn">Stop</button></div></aside>'
)
task_inner = (
    '<div style="display:grid;grid-template-columns:260px minmax(0,1fr) 480px;height:100%">'
    + rail("altitude") +
    '<main class="pane">'
    '<header style="padding:16px 28px 14px;display:flex;flex-direction:column;gap:6px;flex:none">'
    f'<div style="display:flex;align-items:center;justify-content:space-between"><div class="crumb">{I("chev-l","i sm")}altitude</div>'
    f'<div class="acts"><span class="btn ghost">Reject</span><span class="ib on">{I("panel")}</span></div></div>'
    '<h1 style="display:flex;align-items:center;gap:10px;margin:0;font-size:18px;font-weight:600;line-height:1.3"><span class="dot"></span>Design wireframes for the simplified product</h1>'
    '<div class="sub" style="font-size:13px;color:var(--text-muted)">attempt 1 · started 32 min ago · 18% of its context used</div>'
    '<div style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-top:2px"><span class="chip">Running</span><span class="chip">Fable on Claude</span><a href="#" class="chip">PR #176 open · main checks passed</a><details><summary class="chip held" style="cursor:pointer">Merge held · Details</summary><p>Keep this PR open until the operator reviews the phone and desktop interaction states and separately approves merging it.</p></details></div>'
    '</header>'
    '<div class="convo"><div class="col">'
    '<div class="day">Today</div>'
    '<div class="l3"><p>I read #166 and both of your comments. Plan: one shared stylesheet on the build’s tokens, sixteen boards, numbered callouts on each. I’ll render with headless Chrome to check clipping at both sizes.</p></div>'
    '<div class="me">Not only desktop first. Both mobile and desktop need to land.</div>'
    '<div class="receipt">Delivered to session</div>'
    '<div class="l3"><span class="muted">L3</span><p>The brief calls for both sizes. Keep the PR held for review.</p></div>'
    '<div class="l3"><p>Understood: every board ships at both sizes in this PR. The mobile boards respect the iPhone safe areas and keep the composer at 16px.</p></div>'
    '<div class="l3"><p>PR #176 is open and green. It is held for your review as the brief says; the README lists the three assumptions I made where the record was silent.</p>'
    '</div></div><div style="height:22px"></div>'
    + task_activity() + composer("Message the L2", engine=False, hint="Reaches the L2 at its next checkpoint.") +
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
    '<p style="font-size:15px;color:var(--text-secondary);margin:8px 0 0;line-height:1.55">Altitude runs an L3 for each project you manage. Pick a folder to open Setup and see configuration progress while L3 prepares its first reply. Work lands as reviewed pull requests.</p></div>'
    '<div class="card" style="padding:6px 8px">'
    f'<div class="srow"><span class="ib" style="color:var(--text-muted)">{I("folder")}</span><div><div class="tt" style="font-size:15px">atlas</div><div class="tm">~/Projects · git repository · 214 commits</div></div><div class="r"><span class="btn primary">Add project</span></div></div>'
    f'<div class="srow"><span class="ib" style="color:var(--text-muted)">{I("folder")}</span><div><div class="tt" style="font-size:15px">job-search-assistant</div><div class="tm">~/Projects · git repository · 61 commits</div></div><div class="r"><span class="btn primary">Add project</span></div></div>'
    f'<div class="srow" style="min-height:48px"><span class="ib" style="color:var(--text-muted)">{I("plus")}</span><div class="tt" style="font-size:14px;font-weight:500;color:var(--text-secondary)">Choose another folder…</div></div>'
    '</div>'
    '<p style="text-align:center;font-size:13px;color:var(--text-muted);margin:0">One engine is connected. Altitude works with one engine alone; additional engines are optional.</p>'
    '</div></main></div>'
)
board("FirstRun", 1440, 900, first_inner)

# =====================================================================
# Mobile
def mheader_project(sub="L3 · Ready"):
    return (
        '<header class="mh"><div><span class="name">altitude' + I("chev-d","i sm") + '</span>'
        f'<div class="subl" style="padding-left:4px;margin-top:-6px">{sub}</div></div><span class="sp"></span>'
        f'<details class="phone-details"><summary aria-label="More actions">{I("more")}</summary><div class="details-panel">'
        '<a href="MobileProjectSettings.html">Project settings…</a><p>Setup…</p><p>Reset L3 conversation…</p><a href="MobileSettings.html">All settings…</a><a href="index.html">Design boards ↗</a></div></details></header>'
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
            t("needs","tray","Needs you", 3) + t("monitor","pulse","Monitor") + '</nav>')

mobile_convo = (
    '<div class="me">Address issue #166. Use Fable at L2 for this one as well; design is important.</div>'
    '<div class="l3"><p>Created one task for it. It started a minute ago with Fable on Claude, and its PR will be held for your review before anything merges.</p>'
    + tcard("Design wireframes for the simplified product", "Running · Fable on Claude · 1 min") +
    '</div>'
    '<div class="sys"><span class="ln"></span><span>L3 handled 2 system events between your messages</span><a href="#">Show</a><span class="ln"></span></div>'
    '<div class="me">Not only desktop first. Both mobile and desktop need to land.</div>'
    '<div class="l3"><p>Passed that to the task’s L2. Every board will ship in a desktop and an iPhone version in the same PR.</p></div>'
)

def mobile_chat(sheet=False, convo=None):
    inner = (
        '<div class="m"><div></div>' + mheader_project() +
        '<div class="mbody"><div class="mcol">' + (convo or mobile_convo) + '</div></div>'
        '<div class="mcomp">' + composer("Message L3", mobile=True) + '</div>'
        + tabbar("chat") + '</div>'
    )
    if sheet:
        inner += (
            '<div class="scrim"></div>'
            '<div class="sheet"><div class="handle"></div>'
            '<div class="sh" style="padding:0 8px;margin-bottom:6px">Projects <span>2 managed</span></div>'
            f'<div class="srow sel"><span class="dot held"></span><div><div class="tt">altitude</div><div class="tm">L3 ready</div></div><div class="r">{I("check")}</div></div>'
            '<div class="srow"><span class="dot idle"></span><div><div class="tt">harbor</div><div class="tm">L3 idle since yesterday</div></div></div>'
            '<div style="height:1px;background:var(--hairline);margin:8px 8px"></div>'
            f'<div class="srow"><span class="ib" style="color:var(--text-muted);width:auto">{I("folder")}</span><div><div class="tt" style="font-size:15px;font-weight:500">atlas</div><div class="tm">not managed</div></div><div class="r"><span class="btn" style="height:36px;font-size:13px">Add project</span></div></div>'
            f'<div class="srow"><span class="ib" style="color:var(--text-muted);width:auto">{I("folder")}</span><div><div class="tt" style="font-size:15px;font-weight:500">job-search-assistant</div><div class="tm">not managed</div></div><div class="r"><span class="btn" style="height:36px;font-size:13px">Add project</span></div></div>'
            '</div>'
        )
    return inner

board("MobileProject", 390, 844, mobile_chat(False))
board("MobileSwitcher", 390, 844, mobile_chat(True))

mobile_work = (
    '<div class="m"><div></div>' + mheader_project() +
    '<div class="mbody top"><div class="wh"><h2>Work</h2><span>5 current · 1 done this week</span></div>'
    + f'<span class="newtasks" style="margin:0 0 10px">New tasks · <b>Auto</b>{I("chev-d","i sm")}</span>'
    + work_rows(mobile=True) + '</div><div></div>' + tabbar("work") + '</div>'
)
board("MobileWork", 390, 844, mobile_work)

# =====================================================================
# Composer states sheet
def wave(heights):
    return '<span class="wave">' + "".join(f'<i style="height:{h}px"></i>' for h in heights) + '</span>'

def state(label, note, body):
    return f'<div class="st"><div class="lab">{label}<span>{note}</span></div>{body}</div>'

def comp_custom(top, row, hint=""):
    return f'<div class="composer">{top}<div class="crow">{row}</div></div>' + (f'<div class="hint">{hint}</div>' if hint else '')

def arrow(disabled=False):
    return f'<span class="icb send{" disabled" if disabled else ""}" aria-label="Send">{I("up","i lg")}</span>'

def compose_state(phase):
    """One send control in every state; 358px examples also prove the phone's stacked field and control row."""
    draft = phase in ("Typing", "Busy", "Landed", "Failed")
    top = '<div class="draft">Both phone and desktop need to land.</div>' if draft else '<div class="ph2">Message L3 about altitude</div>'
    pin = ""
    mic = f'<span class="icb">{I("mic","i lg")}</span>'
    hint = ""
    if phase == "Listening":
        return comp_custom(top, f'<span class="icb" aria-label="Cancel">{I("x","i lg")}</span>{wave(W[:6])}<span class="status">0:07</span><span class="icb" aria-label="Stop">{I("stop","i lg")}</span>' + arrow(), "Listening · Stop to edit, or send")
    if phase == "Transcribing":
        top = '<div class="draft">Both phone and desktop need to land.</div>'
        mic = f'<span class="icb dim">{I("mic","i lg")}</span>'
        return comp_custom(top, '<span style="flex:1"></span>' + mic + arrow(True), "Transcribing…")
    elif phase == "Busy":
        hint = ""
    elif phase == "Denied":
        mic = f'<span class="icb dim">{I("mic-off","i lg")}</span>'
        hint = "Microphone blocked in the browser. Typing works."
    elif phase == "Unavailable":
        mic = ""
        hint = "Voice needs HTTPS"
    elif phase == "Failed":
        hint = '<span class="danger">Could not transcribe. Typing works.</span>'
    return comp_custom(top, pin + '<span style="flex:1"></span>' + mic + arrow(not draft), hint)

W = [6,10,16,22,14,8,12,20,24,18,10,6,9,15,21,17,11,7,13,19,23,16,9,6,12,18,14,8]
states = ''.join(state(label, note, '<div class="statebox compact">' + compose_state(label) + '</div>') for label, note in [
    ("Idle", "typing enables the arrow"),
    ("Typing", "the draft sends through the arrow"),
    ("Listening", "Cancel discards; Stop edits; arrow sends at once"),
    ("Transcribing", "editable draft; controls disabled; hint below"),
    ("Landed", "transcript appended to draft; nothing else appears"),
    ("Busy", "same arrow; message runs at the next turn"),
    ("Denied", "mic disabled; typing remains available"),
    ("Unavailable", "mic hidden; HTTPS hint below"),
    ("Failed", "draft stays; no message sent"),
]) + state("Sending refused", "bubble leaves; the draft returns", '<div class="statebox compact">' + composer(draft="Both phone and desktop need to land.", hint='<span class="danger">Not sent. Retry.</span>') + '</div>')
sheet_inner = (
    '<div style="padding:36px 40px 10px"><h1 style="font-size:20px;font-weight:600;margin:0">Composer states</h1>'
    '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:860px">One composer for project chat and task conversation. Each field below is phone-width, with 16px text and 44px controls in one row under the field. Only relevant voice and error hints add height. Busy status stays in the header; queued rows name their run order and retain Remove. The accent arrow is the only send control.</p></div>'
    f'<div class="sheetgrid">{states}</div>'
)
board("ComposerStates", 1200, 1380, sheet_inner)

# Voice states sheet: the same three controls at both widths; only the desktop waveform is bounded.
LIVE = "Keep the draft and the words appear while you speak"
def vcomposer(draft, row, hint="", phone=False, hint_danger=False):
    top = f'<div class="draft">{draft}</div>' if draft else '<div class="ph2">Message L3 about altitude</div>'
    body = f'<div class="composer">{top}<div class="crow">{row}</div></div>'
    if hint:
        body += f'<div class="hint">{"<span class=danger>" + hint + "</span>" if hint_danger else hint}</div>'
    return f'<div class="statebox compact">{body}</div>' if phone else f'<div class="vdesk">{body}</div>'

def vrow(label, note, draft, phase, hint="", danger=False):
    cancel = f'<span class="icb" aria-label="Cancel">{I("x","i lg")}</span>'
    stop = f'<span class="icb rec" aria-label="Stop">{I("stop","i lg")}</span>'
    mic = f'<span class="icb">{I("mic","i lg")}</span>'
    pill = f'<span class="l3pill">L3 · <b>Auto</b></span><span style="flex:1"></span>'
    if phase == "listening":
        desk = pill + cancel + wave(W[:14]).replace('class="wave"', 'class="wave bounded"') + '<span class="status">0:07</span>' + stop + arrow()
        phone = cancel + wave(W[:6]) + '<span class="status">0:07</span>' + stop + arrow()
    elif phase == "transcribing":
        desk = pill + cancel + wave(W[:14]).replace('class="wave"', 'class="wave bounded frozen"') + '<span class="status">0:07</span>' + f'<span class="icb dim">{I("mic","i lg")}</span>' + arrow(True)
        phone = cancel + '<span class="gap"></span>' + f'<span class="icb dim">{I("mic","i lg")}</span>' + arrow(True)
        hint = '<span class="spin"></span> ' + hint
    elif phase == "unavailable":
        desk = pill + arrow(not draft)
        phone = pill + arrow(not draft)
    else:
        desk = pill + mic + arrow(not draft)
        phone = pill + mic + arrow(not draft)
    return (f'<div class="vlab">{label}<span>{note}</span></div>'
            + vcomposer(draft, desk, hint, hint_danger=danger)
            + vcomposer(draft, phone, hint, phone=True, hint_danger=danger))

voice_rows = "".join([
    vrow("Listening · browser recognition", "words land in the field as they are recognized; the last phrase may still change", LIVE, "listening", "Listening… Stop to add text, or Send."),
    vrow("Listening · this computer", "words arrive from this computer about a second behind speech; the last words may still change", LIVE, "listening", "Listening… Stop to add text, or Send."),
    vrow("Transcribing · after Stop or Send", "this computer finishes its last words in about half a second; browser recognition waits for its last phrase; Cancel stays available; desktop waveform and timer freeze; phone hides them", LIVE, "transcribing", "Transcribing…"),
    vrow("Connection lost · this computer", "recording and the timer continue; the recording stays in page memory only", LIVE, "listening", "Connection lost — still recording. Your words will catch up."),
    vrow("Catching up · this computer", "the page reconnected and replays the recording; the words shown stay until the replay passes them", LIVE, "listening", "Catching up…"),
    vrow("Waiting for connection · this computer", "Stop or Send while offline; Cancel discards the recording and keeps the words shown", LIVE, "transcribing", "Waiting for connection…"),
    vrow("Landed · every backend", "transcript appended to the draft, cursor at the end, nothing else appears", LIVE, "landed"),
    vrow("Unavailable · this browser has no speech recognition", "mic hidden, typing unaffected", "", "unavailable", "This browser has no speech recognition. Typing works."),
    vrow("Failed · recognition error", "draft stays; nothing is sent", "Keep the draft", "failed", "Could not transcribe. Typing works.", danger=True),
    vrow("Unreached · this computer", "two minutes without a connection; the words shown stay and a voice Send returns its text unsent to its draft", LIVE, "failed", "Couldn't reach this computer: your recording's last words weren't added.", danger=True),
])
voice_inner = (
    '<div style="padding:36px 40px 10px"><h1 style="font-size:20px;font-weight:600;margin:0">Voice input states</h1>'
    '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:960px">One recording cluster at both widths: Cancel, waveform, timer, Stop and the send arrow. '
    'Desktop keeps the cluster compact beside the controls with a crisp, bounded waveform instead of stretching it across the field; phone fills its single row. '
    'Both backends show words while you speak; with this computer, a lost connection keeps recording and the words catch up. The field is read-only during voice input on every backend.</p></div>'
    f'<div class="vgrid">{voice_rows}</div>'
)
board("VoiceStates", 1200, 2700, voice_inner)

# ---------- settings overview and nested voice settings ----------
def voice_options(chosen="host", host="ready"):
    opts = [
        ("host", "This computer", "Words appear as you speak, with punctuation and capitals, in English, in every browser. Audio goes from your device to this computer over Altitude’s own connection, is transcribed here and is never stored or sent to another service. If the connection drops, recording continues and your words catch up."),
        ("browser", "Browser recognition", "No setup in supported browsers. Words appear as you speak. Your browser may send audio to its speech service; that service’s privacy policy applies."),
    ]
    panels = {
        "ready": ('Ready on this computer. While you dictate, the speech process uses about 2 GB of memory.', '<span class="btn">Remove voice (698 MB)</span>'),
        "absent": ('Needs a one-time download of about 698 MB, checked against this release.', '<span class="btn primary">Set up voice</span>'),
    }
    rows = ""
    for key, title, note in opts:
        rows += f'<div class="opt"><span class="radio{" on" if chosen == key else ""}"></span><div style="flex:1;min-width:0"><b>{title}</b><p>{note}</p>'
        if key == "host" and chosen == "host":
            text, button = panels[host]
            rows += (f'<div class="form"><p class="muted" style="font-size:12px;margin:0">{text}</p><div class="actions">{button}</div>'
                     '<p class="muted" style="font-size:12px;margin:0">Speech model: NVIDIA Parakeet TDT 0.6B v2, licensed CC-BY-4.0.</p></div>')
        rows += '</div></div>'
    return rows

def settings_content(phone=False):
    target = "MobileVoiceSettings.html" if phone else "VoiceSettings.html"
    project = "MobileProjectSettings.html" if phone else "ProjectSettings.html"
    link = lambda title, note, href="#": f'<a class="setting-link" href="{href}"><span><b>{title}</b><small>{note}</small></span>{I("chev-r")}</a>'
    row = lambda title, note, tail: f'<div class="setting-link"><span><b>{title}</b><small>{note}</small></span>{tail}</div>'
    switch = '<span class="field" style="min-width:3rem;text-align:center">On</span>'
    group = lambda title, rows: f'<div class="sgroup"><h2>{title}</h2><div class="card">{rows}</div></div>'
    left = (f'<div class="sgroup" style="margin-top:0"><div class="card">{link("Operator", "Your name on every screen")}</div></div>'
        + group("This project", link("altitude", "L3: Auto · tasks prefer Codex", project))
        + group("Models", link("New tasks", "Auto · each project's own defaults"))
        + group("Projects", link("All projects", "2 projects · folder ~/Projects")))
    right = (group("Voice", link("Voice input", "This computer", target))
        + group("Devices and access", link("Devices", "2 paired · pair another, certificate")
            + row("Terminal", "Every paired browser can run commands as you on this computer.", '<span class="field" style="min-width:3rem;text-align:center">Off</span>')
            + row("Network", "https://altitude.example.test · HTTPS on · view only", ""))
        + group("Coding agents", link("Prerequisites", "GitHub CLI sign-in, coding agents and Git")
            + row("Validation runs", "Agents test installs, containers and browsers in throwaway containers.", switch)
            + link("Incident reports", "Kept on this computer"))
        + group("About", row("Version", "1.4.0 · Up to date", "")))
    return ('' if phone else '<h1>Settings</h1>') + (left + right if phone else f'<div class="scols"><div>{left}</div><div>{right}</div></div>')

def default_row(engine, model, effort, phone):
    cols = "1fr" if phone else "9rem 1fr 1fr"
    return (f'<div style="display:grid;grid-template-columns:{cols};gap:12px;align-items:end;border-top:1px solid var(--hairline);padding-top:14px;margin-top:14px">'
            f'<b>{engine}</b><label class="muted" style="font-size:12px">Model<span class="field ph">Default: {model}</span></label>'
            f'<label class="muted" style="font-size:12px">Effort<span class="field">Default ({effort})</span></label></div>')

def project_settings_content(phone=False):
    heading = '' if phone else '<a class="btn setting-back" href="Settings.html">' + I("chev-l") + 'Settings</a><h1>altitude</h1>'
    section = lambda title, note, body: f'<h2 style="margin-top:20px">{title}</h2><p class="muted" style="margin-top:-6px">{note}</p><div class="card">{body}</div>'
    defaults = "".join(default_row(label, model, effort, phone) for label, model, effort in
        (("Tasks · Claude", "opus", "High"), ("Tasks · Codex", "CLI default", "High"), ("L3 · Claude", "fable", "High"), ("L3 · Codex", "CLI default", "Medium")))
    select = lambda title, note, value: (f'<div style="display:flex;justify-content:space-between;gap:12px;align-items:center;padding:6px 0">'
        f'<span><b>{title}</b><br><small class="muted">{note}</small></span><span class="field" style="min-width:9rem">{value}</span></div>')
    return (heading + '<p class="muted" style="margin:0 0 6px">Applies to this project only. A model or effort L3 sets for one task wins over these.</p>'
        + section("L3", "Who answers in this project's chat. The same choice as the button under the message box; it comes before routing.",
            '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px"><span><b>Auto</b><br><small class="muted">Project routing and defaults · last reply reported gpt-5 · High</small></span><span class="btn">Change…</span></div>')
        + section("Auto defaults", "Used under Auto and as the fallback. Started tasks keep their model and effort; L3 uses a change from its next reply.", defaults)
        + section("Routing", "The order Auto tries: Claude and Codex share work by weekly headroom.",
            select("Tasks", "Auto, Prefer or Only an engine.", "Prefer Codex") + select("L3", "Auto or Only an engine.", "Auto"))
        + section("Project", "", f'<a class="setting-link" style="border:0;margin:0;padding:0 0 10px" href="#"><span><b>Setup</b><small>Ready</small></span>{I("chev-r")}</a>'
            '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;border-top:1px solid var(--hairline);padding-top:10px">'
            '<span><b>Remove project</b><br><small class="muted">Detach L3. Files and history stay; its settings here don\'t.</small></span>'
            '<span class="btn" style="color:var(--danger);border-color:var(--danger)">Remove…</span></div>'))

def voice_settings_content(phone=False):
    heading = '' if phone else '<a class="btn setting-back" href="Settings.html">' + I("chev-l") + 'Settings</a><h1>Voice input</h1>'
    return (heading + '<p class="muted" style="margin:0 0 18px">Choose how speech becomes text. Applies to every project.</p>'
        '<div class="card"><h3>Transcription</h3>' + voice_options("host") + '</div>'
        '<p class="muted" style="font-size:12px">Changes apply to your next recording. Altitude keeps no recordings.</p>')

def rail_settings():
    return rail("settings").replace('<div class="who">', '<div class="who on">').replace('<span class="avatar">B</span>Burak', '<span class="avatar">O</span>Operator')

board("Settings", 1440, 900, '<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:100%">' + rail_settings() + '<main class="pane"><div class="route-content" style="max-width:720px;width:100%">' + settings_content() + '</div></main></div>')
settings_phone_header = '<header class="mh"><span class="btn" style="padding:0 8px">' + I("chev-l") + 'Back</span><h1 style="font-size:17px;margin:0 0 0 12px">Settings</h1></header>'
board("MobileSettings", 390, 844, '<div class="m"><div></div>' + settings_phone_header + '<div class="route-content">' + settings_content(phone=True) + '</div><div></div>' + tabbar("") + '</div>')
board("ProjectSettings", 1440, 900, '<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:100%">' + rail_settings() + '<main class="pane" style="overflow:auto"><div class="route-content" style="max-width:720px;width:100%">' + project_settings_content() + '</div></main></div>')
project_settings_phone_header = '<header class="mh"><a class="btn" href="MobileSettings.html" style="padding:0 8px">' + I("chev-l") + 'Settings</a><h1 style="font-size:17px;margin:0 0 0 12px">altitude</h1></header>'
board("MobileProjectSettings", 390, 844, '<div class="m"><div></div>' + project_settings_phone_header + '<div class="route-content" style="overflow:auto">' + project_settings_content(phone=True) + '</div><div></div>' + tabbar("") + '</div>')
board("VoiceSettings", 1440, 900, '<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:100%">' + rail_settings() + '<main class="pane"><div class="route-content" style="max-width:720px;width:100%">' + voice_settings_content() + '</div></main></div>')
voice_settings_phone_header = '<header class="mh"><a class="btn" href="MobileSettings.html" style="padding:0 8px">' + I("chev-l") + 'Settings</a><h1 style="font-size:17px;margin:0 0 0 12px">Voice input</h1></header>'
board("MobileVoiceSettings", 390, 844, '<div class="m"><div></div>' + voice_settings_phone_header + '<div class="route-content">' + voice_settings_content(phone=True) + '</div><div></div>' + tabbar("") + '</div>')

def settings_card(chosen, status="", host="ready", phone=False):
    """One Voice input card; status is the line under the choices, and a Retry/Reload link follows a failure."""
    saving = status == "Saving…"
    if chosen is None:
        body = f'<p class="muted" style="margin:6px 0 0">{status}</p>'
    else:
        body = voice_options(chosen, host)
        if status.startswith("Not saved"):
            body += f'<p style="margin:6px 0 0;color:var(--danger);font-size:13px">{status} <span style="color:var(--accent);text-decoration:underline">Reload settings</span></p>'
        elif status:
            body += f'<p class="muted" style="margin:6px 0 0">{status}</p>'
    card = '<div class="card' + (' tight' if phone else '') + (' settings-saving' if saving else '') + '"><h3>Voice input</h3>' + body + '</div>'
    return f'<div class="compact" style="width:390px">{card}</div>' if phone else f'<div class="vdesk" style="max-width:640px">{card}</div>'

def srow(label, note, chosen, status="", host="ready"):
    return (f'<div class="vlab">{label}<span>{note}</span></div>' + settings_card(chosen, status, host) + settings_card(chosen, status, host, phone=True))

entry_desktop = ('<div class="vdesk" style="display:flex;gap:28px;align-items:flex-start">'
    '<div style="width:260px"><div class="muted" style="margin-bottom:8px">Rail, operator row</div><div class="who on" style="border:1px solid var(--border)"><span class="avatar">O</span>Operator<span style="margin-left:auto;color:var(--accent)">' + I("gear", "i sm") + '</span></div></div>'
    '<div><div class="muted" style="margin-bottom:8px">Project header, three dots</div><div class="menu-mock"><div class="hi">Project settings…</div><div>Setup…</div><div>Reset L3 conversation…</div><hr><div>All settings…</div><div>Design boards ↗</div></div></div></div>')
entry_phone = ('<div class="compact" style="width:390px"><div class="muted" style="margin-bottom:8px">Project header, three dots</div><div class="menu-mock" style="width:100%"><div class="hi">Project settings…</div><div>Setup…</div><div>Reset L3 conversation…</div><hr><div>All settings…</div><div>Design boards ↗</div></div></div>')

settings_inner = (
    '<div style="padding:28px 40px 8px"><h1 style="margin:0;font-size:18px">Settings: states and entry points</h1>'
    '<p class="muted" style="margin:6px 0 18px">Settings opens an overview grouped by where each setting applies. The voice choices live inside Voice input at /settings/voice. Desktop left; phone right.</p></div>'
    '<div class="sgrid">'
    + srow("Entry points", "the rail gear and the project three dots on desktop; the project three dots on phone. The menu holds no destructive item", None).split('<div class="vdesk"')[0] + entry_desktop + entry_phone
    + srow("Loading", "no selected default or editable controls", None, "Loading settings…")
    + srow("Read failed", "Retry reads again; typing elsewhere is unaffected", None, 'Could not load settings. <span style="color:var(--accent);text-decoration:underline">Retry</span>')
    + srow("Saved host", "This computer, saved at once; its setup panel shows the current state; the overview reads This computer, with · not set up until ready", "host", "Saved.")
    + srow("Host not set up", "Set up voice starts the download; progress refreshes every second with Cancel setup", "host", host="absent")
    + srow("Saved browser", "Browser recognition, saved at once; nothing to configure", "browser", "Saved.")
    + srow("Saving", "choices disabled until the request answers", "browser", "Saving…")
    + srow("Failed/denied save", "server explanation with Retry; a choice changed elsewhere offers Reload settings; the saved choice is unchanged", "host", "Not saved: the voice choice changed elsewhere.")
    + '</div>'
)
board("SettingsStates", 1200, 3460, settings_inner)


# =====================================================================
# Decisions use the current conversation boards below; obsolete standalone forms are removed.

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
          + sysline("L3 handled 3 system events between your messages", dot="fault")
          + '</div>')
    + state("Group expanded", "each turn opens separately; Hide folds the group",
          '<div style="display:flex;flex-direction:column;gap:12px">'
          + sysline("09:14 · Persist paths is done; PR #178 merged.")
          + sysline("09:20 · L3 repaired the dispatch fault and resumed the task.", dot="fault")
          + sysline("09:31 · Altitude restarted; the running tasks kept their workers.")
          + '<a href="#" style="align-self:flex-end">Hide</a></div>')
    + state("Expanded", "what altd sent, what L3 did, one link into the task",
          '<div class="sysx"><div class="hd"><span>Report landed · Persist paths when L3 resumes a task · 09:14</span><a href="#">Hide</a></div>'
          '<div class="lbl">What altd sent L3</div>'
          '<div class="kv"><b>Verdict</b><span>ok</span><b>Problems</b><span>none</span><b>Signals</b><span>one post-mortem note: a flaky test was retried</span><b>PRs</b><span>#178 merged</span><b>Spend</b><span>14 turns, 2 subagent launches</span></div>'
          '<div class="lbl">L3 replied</div>'
          '<p class="rp">Closed the task as done and recorded the flaky test in the digest. Nothing waits on you.</p>'
          f'<div class="ft"><a href="#">Full report</a><a href="#">Digest</a></div></div>')
    + state("In progress", "the active turn stays outside the completed group",
          '<div style="display:flex;flex-direction:column;gap:14px;padding:6px 0">'
          + sysline("L3 handled 2 system events between your messages")
          + sysline("L3 is handling a landed report for Persist paths when L3 resumes a task", show="")
          + compose_state("Busy") + '</div>')
    + state("Fault repaired", "faults carry a red dot; the incident stays in the task record",
          '<div style="padding:6px 0">' + sysline("A dispatch fault blocked Fast-forward a self-deploy checkout; L3 repaired it and resumed the task.", dot="fault") + '</div>')
    + state("FYI from L3", "FYIs live here now, not in Needs you",
          '<div style="padding:6px 0">' + sysline("FYI: Codex’s sandbox kept a worktree read-only; PR #171 fixed it, so no repair task was needed.") + '</div>')
    + state("Failed report turn", "failure changes the words; the trigger keeps its muted dot",
          sysline("L3 could not handle a landed report for Persist paths when L3 resumes a task"))
)
sys_inner = (
    '<div style="padding:36px 40px 10px"><h1 style="font-size:20px;font-weight:600;margin:0">System turns in chat</h1>'
    '<p style="margin:6px 0 18px;color:var(--text-muted);font-size:14px;max-width:860px">Every L3 turn the operator did not start (a landed report, a fault, a restart, an FYI) stays in the one conversation in its real order, but renders as a single muted line: L3’s short reply, with Show for the full prompt and reply. The chat log already records the trigger on every row, so this is a rendering rule, not a second conversation.</p></div>'
    f'<div class="sheetgrid">{sys_states}</div>'
)
board("SystemTurnStates", 1200, 1100, sys_inner)

# Task on the phone: status stays visible; metadata and actions open in details.
phone_task_header = (
    f'<header class="mh"><a href="MobileWork.html" aria-label="Back" style="display:grid;place-items:center;min-width:44px;min-height:44px">{I("chev-l")}</a>'
    '<div style="flex:1;min-width:0"><div style="font-size:17px;font-weight:600">Design wireframes</div><div class="subl">L2 · Running · Merge held</div></div>'
    f'<details class="phone-details"><summary aria-label="Task details">{I("more")}</summary><div class="details-panel"><h2>Design wireframes for the simplified product</h2>'
    '<p>attempt 1 · started 32 min ago · 18% context used</p><p>Engine and model · Observed tokens</p><a href="#">PR #176 open · main checks passed</a>'
    '<h3>Merge held</h3><p>Keep this PR open until the operator reviews the phone and desktop interaction states and separately approves merging it. This restriction remains while the worker is running or blocked.</p>'
    '<p>Reject</p></div></details></header>'
)
phone_task_convo = (
    '<div class="day">Today</div>'
    '<div class="l3"><p>The boards are ready for review at both sizes.</p></div>'
    '<div class="me">Check the phone header and the keyboard too.</div>'
    '<div class="receipt">Queued · waiting for a checkpoint</div>'
    '<div class="l3"><span class="muted">L3</span><p>The brief keeps the shell fixed while the content scrolls.</p></div>'
)
for name, is_live in [("MobileTask", False), ("MobileTaskLive", True)]:
    tabs = f'<div class="task-tabs"><span class="{"" if is_live else "on"}">Conversation</span><span class="{"on" if is_live else ""}">Live session</span></div>'
    content = live if is_live else '<div class="mbody"><div class="mcol">' + phone_task_convo + '</div></div>'
    field = '<div></div>' if is_live else '<div class="mcomp">' + task_activity("MobileTaskLive.html") + composer("Message the L2", engine=False, hint="Reaches the L2 at its next checkpoint.") + '</div>'
    board(name, 390, 844, '<div class="m task-phone">' + phone_task_header + tabs + content + field + tabbar("work") + '</div>')

# The same live panel overlays the main pane from 1024 through 1279px.
board("TaskOverlay", 1100, 900,
      '<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:100%">' + rail("altitude") +
      '<main class="pane"><header class="ph"><h1>Design wireframes for the simplified product</h1></header>'
      '<div class="convo"><div class="col">' + phone_task_convo + '</div>' + composer("Message the L2", engine=False) + '</div></main></div>'
      '<div class="scrim"></div><div style="position:absolute;inset:0 0 0 auto;width:480px;display:flex">' + live + '</div>')

def state_sheet(name, title, examples, height):
    content = ''.join(state(label, note, '<div class="statebox">' + body + '</div>') for label, note, body in examples)
    board(name, 1200, height, f'<div style="padding:28px 40px"><h1 style="font-size:20px;margin:0">{title}</h1></div><div class="sheetgrid">{content}</div>')

state_sheet("TaskStates", "Task page states", [
    ("Running", "one-click Stop beside the composer and in Live session", task_activity()),
    ("Stopping", "draft editable; Send and Continue unavailable", '<button class="btn" disabled>Stopping…</button><p class="muted">Waiting for the worker to end.</p><div class="field">Keep these edits; check the phone first.</div>'),
    ("Stopped", "Continue keeps the unsent draft; correction resumes the same session", '<button class="btn">Continue session</button>' + composer("Message the L2", engine=False, draft="Check the phone first.") + '<p class="muted">Send a correction to continue this session.</p>'),
    ("Stop unconfirmed", "read status without retrying Stop", '<p class="danger">Stop unconfirmed · The worker may still be running</p><button class="btn">Check status</button><p>Draft kept; Send and Continue unavailable.</p>'),
    ("Quiet / unavailable", "old prose never becomes fresh activity", '<b>Last update · 4 min ago</b><p>No new activity for 4 min</p><p>Activity unavailable · Last known update</p><button class="btn">Retry activity</button>'),
    ("Delivery evidence", "receipt alternatives beneath the original bubble", '<div class="me">Check the phone first.</div><p class="muted">Queued · waiting for a checkpoint</p><p class="muted">Delivered to session</p><p class="muted">Delivery unconfirmed</p>'),
    ("Resumed", "old direction clears until fresh public output", '<p>Waiting to resume</p><p>Running · No public update yet.</p><p>Existing edits and saved session retained.</p>'),
    ("Reject confirmation", "optional reason accompanies the archive", '<p>Reject this task? Its worker ends and the task is archived.</p><div class="field">Reason (optional)</div><div class="opts"><span class="btn">Cancel</span><span class="btn primary">Reject</span></div>'),
    ("Waiting on L3 + merge held", "complete reasons on request", '<p>L2 · Waits for L3 · Merge held</p><details><summary>Task details</summary><h3>Waits for L3</h3><p>Which recorded decision applies? The original question stays in the conversation.</p><h3>Merge held</h3><p>Keep the PR open until the operator reviews both phone and desktop states and separately approves the merge.</p></details>'),
    ("Held until resume", "Queued replaces the blocked label", '<span class="chip">Queued</span><p>Waits for resume · the window reopens at 10:30</p><div class="hint">Delivered when Altitude resumes the L2.</div>'),
    ("Connecting", "skeleton until the session arrives", '<div class="skel" style="height:50px"></div><div class="skel" style="width:75%"></div><p class="muted">Connecting to the session…</p>'),
    ("Boundaries arrived first", "retain the recorded boundary", '<div class="sep">queued → running · altd · 09:00</div><p class="muted">Connecting to the session…</p>'),
    ("Streaming", "tool output folds under its row", '<div class="tool" data-kind="command"><b>$</b>make test<span class="n">running…</span></div><div class="tool"><b>Edit</b>gen.py<span class="n">3 lines ›</span></div><div class="tool" data-kind="command"><b>$</b>check assets<span class="n danger">error</span></div><div class="tool"><b>Read</b>SPEC.md<span class="n">no output</span></div>'),
    ("Following or paused", "Pause changes to Follow", '<span class="btn ghost">Follow</span><p class="muted">Paused · Follow to catch up</p><p class="muted">Following live · new steps appear at the bottom</p>'),
    ("Unavailable / ended", "session footer follows task state", '<p class="muted">No session file for this attempt</p><p class="muted">Session paused until the task resumes</p><p class="muted">Session ended</p>'),
    ("Empty conversations", "active and finished", '<p class="muted">No messages yet.</p><p class="muted">No messages on this task.</p><span class="chip">Done</span> <a class="chip ok" href="#">PR #178 merged · main checks passed</a><p class="muted">attempt 1 · done 2h ago</p>'),
    ("Resume by message", "blocked task composer", composer("Message the L2", engine=False, hint="Sending resumes the L2 with your message.")),
    ("Message refused", "bubble removed; editable draft returned", composer("Message the L2", engine=False, draft="Check the phone header too.", hint='<span class="danger">Not sent. Retry.</span>')),
], 2260)

state_sheet("ProjectLifecycleStates", "Remove project: detach L3", [
    ("Where it is", "Settings › project › Project; the ⋯ menu holds no destructive item", '<p><b>Setup</b> <span class="muted">Ready</span></p><p><b>Remove project</b> <span class="muted">Detach L3. Files and history stay; its settings here don\'t.</span> <span class="btn" style="color:var(--danger);border-color:var(--danger)">Remove…</span></p>'),
    ("Confirmation", "Cancel first and focused; the red action names the project", '<p><b>Remove example from Altitude?</b></p><p class="muted">L3 is detached and Altitude stops managing this folder.</p><ul class="muted" style="margin:0 0 10px;padding-left:18px"><li>Stays on disk: the repository, worktrees, history and queued messages.</li><li>Not kept: its settings here, such as models and routing.</li><li>Undo: add the same folder as example again to reattach L3 with its history.</li><li>Unfinished tasks and a running L3 reply must finish first.</li></ul><span class="btn">Cancel</span> <span class="btn" style="background:var(--danger);color:#fff;border-color:var(--danger)">Remove example</span>'),
    ("Cancelled", "Cancel, Escape, × and the scrim close it before submission", '<p class="muted">The project and its conversation remain available.</p>'),
    ("Removing", "both buttons disabled", '<p class="muted">Removing… Closing doesn\'t cancel removal.</p><span class="btn" aria-disabled="true">Cancel</span> <span class="btn" aria-disabled="true">Removing…</span>'),
    ("Refused", "the server's reason; the project stays", '<p class="danger">Finish or reject the 1 unfinished task(s) first: existing-work.</p><span class="btn">Cancel</span> <span class="btn" style="background:var(--danger);color:#fff;border-color:var(--danger)">Remove example</span>'),
    ("Response lost", "the project list is read again before saying anything", '<p class="muted">Checking whether it was removed…</p><p class="danger">Couldn\'t confirm removal; example is still in Altitude.</p><span class="btn">Cancel</span> <span class="btn" style="background:var(--danger);color:#fff;border-color:var(--danger)">Retry</span>'),
    ("List unreadable too", "Check again rereads; removal returns only once the project is known to be present", '<p class="danger">Couldn\'t confirm removal, and the project list could not be read.</p><span class="btn">Cancel</span> <span class="btn">Check again</span>'),
    ("Removed", "managed row and old views leave", '<h3>Needs you</h3><p class="muted">A remaining project is selected. With none managed, First run offers the retained folder.</p><h3>Project not managed</h3><p class="muted">Select a project or add its folder again.</p>'),
    ("Attach L3 again", "existing folder-add flow restores history", '<p>example <span class="btn primary">Add project</span></p><p class="muted">Setup shows current configuration and progress.</p><p class="danger">Registration unavailable. Try again.</p><span class="btn">Retry</span>'),
    ("Restored", "registration succeeds; saved queue resumes", '<p>Saved project history.</p><p>Queued request answered.</p>' + composer("Message L3 about example", engine=False)),
], 1400)

def models_dialog(tab="l3", chosen="Auto", effort="Default", note="", other=False, saving=False):
    tabs = '<div class="tabs"><span' + (' class="on"' if tab == "l3" else '') + '>L3 · altitude only</span><span' + (' class="on"' if tab == "tasks" else '') + '>Tasks · All projects</span></div>'
    head = ('<p class="muted" style="margin:0 0 8px">In use: <b>Auto</b> · who answers you in altitude\'s chat</p>' if tab == "l3"
            else '<p class="muted" style="margin:0 0 8px">In use: <b>Auto</b> · every project; tasks that start from now, including queued ones. Started tasks keep theirs.</p>')
    opts = "".join(f'<div class="opt"><i class="r{" on" if name == chosen else ""}"></i>{name}<small>{where}</small></div>' for name, where in
        (("Auto", "routing and defaults"), ("Fable", "Claude"), ("Opus", "Claude"), ("Sonnet", "Claude"), ("Codex default", "Codex"), ("Other model…", "")))
    if other:
        opts += '<div style="display:flex;gap:8px;margin:4px 0 0 22px"><span class="field" style="min-width:6rem">Codex</span><span class="field" style="flex:1">gpt-6-astra</span></div>'
    seg = '<div class="seg">' + "".join(f'<span{" class=on" if e == effort else ""}>{e}</span>' for e in ("Default", "Low", "Medium", "High", "Max")) + '</div>'
    use = "Saving…" if saving else ("Use for L3 in altitude" if tab == "l3" else "Use for all new tasks")
    foot = ('<p class="muted" style="font-size:12px;margin:6px 0 0">Applies from L3\'s next reply.</p>' if tab == "l3" else
            '<p class="muted" style="font-size:12px;margin:6px 0 0">harbor runs tasks only on Codex, so it keeps its Codex model. <u>Change</u></p>'
            '<p class="muted" style="font-size:12px;margin:4px 0 0">For one task, tell L3: “use Opus at Max for this”.</p>')
    return (f'<div class="models"><div style="display:flex;justify-content:space-between"><b>Models</b><span>{I("x","i sm")}</span></div>{tabs}{head}'
            f'{opts}<div style="margin-top:6px"><b style="font-size:13px">Effort</b></div>{seg}{note}'
            f'<div class="act"><span class="btn primary"' + (' aria-disabled="true"' if saving else '') + f'>{use}</span></div>{foot}</div>')

state_sheet("ModelsStates", "Models dialog: L3 and new tasks", [
    ("Closed controls", "the L3 button under the message box; New tasks beside the quota (rail, phone Monitor and Work)", '<span class="l3pill">L3 · <b>Auto</b></span> <span class="l3pill">L3 · <b>Fable · Low</b></span><p></p><span class="newtasks">New tasks · <b>Auto</b></span> <span class="newtasks set">New tasks · <b>Fable · High</b></span><p></p><span class="newtasks set">New tasks · <b>Fable unavailable · Auto meanwhile</b></span>'),
    ("L3 tab", "opens from the L3 button; focus on the current choice", models_dialog("l3")),
    ("Tasks tab", "opens from New tasks; the Only project keeps its engine", models_dialog("tasks", "Fable", "High")),
    ("Other model", "an engine and an exact model id", models_dialog("tasks", "Other model…", "High", other=True)),
    ("Saving", "choices and the other tab are locked", models_dialog("l3", "Fable", "Low", saving=True)),
    ("Changed elsewhere", "Reload drops the draft and shows the current value", models_dialog("tasks", "Opus", "Max", note='<p class="danger" style="font-size:13px">Changed in another window. <u>Reload</u></p>')),
    ("Save failed", "the server's reason and Retry; In use keeps the earlier value", models_dialog("l3", "Opus", "Default", note='<p class="danger" style="font-size:13px">Claude does not support reasoning effort minimal. <u>Retry</u></p>')),
    ("Loading and read failed", "the closed control is disabled while loading", '<p class="muted">Loading models…</p><p class="danger">Could not load models. <u>Retry</u></p>'),
], 2340)

state_sheet("ConversationStates", "Conversation and report states", [
    ("L3 never ran", "header offers Start L3", '<span class="btn primary">Start L3</span><p class="muted">L3 has not started. Start L3 to begin the conversation.</p>'),
    ("Conversation empty", "L3 has run before", '<p class="muted">Say what you want done. L3 answers or creates one task.</p>'),
    ("Conversation loading", "three prose-shaped rows", '<div class="skel" style="height:40px"></div><div class="skel" style="width:80%;height:40px"></div><div class="skel" style="width:90%;height:40px"></div>'),
    ("Conversation error", "cached rows stay visible", '<p class="danger">Could not load the conversation. <a href="#">Retry</a></p><div class="l3"><p>The task’s PR is ready for review.</p></div>'),
    ("Report loading", "heading skeleton", '<div class="skel" style="width:45%;height:24px"></div>'),
    ("Report empty / error", "Retry repeats the read", '<p class="muted">No report yet.</p><p class="danger">Could not load the report. <a href="#">Retry</a></p>'),
], 740)

# A coordinator reply that could become a task offers one action (SPEC.md §3.3 Create task).
OFFER_TITLE = "Refresh Needs you as soon as an answer is sent"

def offer(state="ready"):
    label = {"ready": "Create task", "sending": "Sending…", "failed": "Create task"}[state]
    icon = I("spin", "i sm") if state == "sending" else I("work", "i sm")
    err = '<span class="err" role="alert">Not sent. Check the connection and press it again.</span>' if state == "failed" else ""
    return (f'<div class="offer"><span class="ob"{" aria-disabled=\"true\"" if state == "sending" else ""}>{icon}{label}</span>'
            f'<span class="ot">{OFFER_TITLE}</span>{err}</div>')

offer_question = '<div class="me">The Needs you badge on my phone still said 2 after I answered both questions. Bug?</div>'
offer_reply = ('<div class="l3"><p>Yes, a small one. The badge reads the queue on its next refresh, so after an answer it '
               'can lag up to 20 seconds. Nothing is lost; it only looks wrong. Refetching the queue right after an '
               'answer is sent fixes it.</p>')
offer_pressed = f'<div class="me">Create task: {OFFER_TITLE}</div>'
offer_done = ('<div class="l3"><p>Created it. It starts as soon as a slot is free; nothing needs you meanwhile.</p>'
              + tcard(OFFER_TITLE, "Queued · starts when a slot is free", "dot q") + '</div>')
typing = '<span class="dots" role="status" aria-label="L3 is answering"><i></i><i></i><i></i></span>'

board("ReplyTask", 1440, 900, desktop_project(True, '<div class="day">Today</div>' + offer_question + offer_reply + offer() + '</div>'))
board("MobileReplyTask", 390, 844, mobile_chat(False, offer_question + offer_reply + offer() + '</div>'))

state_sheet("ReplyTaskStates", "Create task: states", [
    ("Reply without the action", "an answer, a report, or a task already created", offer_question + '<div class="l3"><p>Merged an hour ago as PR #175. It shows after the next restart; nothing waits on you.</p></div>'),
    ("Reply with the action", "L3 recommends work but has not been asked to start it", offer_question + offer_reply + offer() + '</div>'),
    ("Pressed", "the action dims for the moment the message takes to save", offer_reply + offer("sending") + '</div>'),
    ("Waiting", "the action leaves; the instruction is your next message", offer_reply + '</div>' + offer_pressed + typing),
    ("Waiting while L3 is busy", "the ordinary queued row, with Send now and Remove", offer_reply + '</div><p class="muted" style="margin:0">Create task: ' + OFFER_TITLE + ' · Queued · runs next</p>'),
    ("Task queued", "L3's answer carries the ordinary task card", offer_reply + '</div>' + offer_pressed + offer_done),
    ("Not sent", "the action stays and says why; pressing it again retries", offer_reply + offer("failed") + '</div>'),
    ("L3 could not answer", "the ordinary failed turn with Retry", offer_reply + '</div>' + offer_pressed + '<p class="muted">L3 could not answer this turn. <a href="#">Retry</a></p>'),
    ("Answered by typing", "any message you send instead retires the action", offer_reply + '</div><div class="me">Not now, after the release.</div><div class="l3"><p>Noted. I will bring it up after the release.</p></div>'),
], 1720)

report_content = (
    '<p class="muted"><a href="Task.html">← Persist paths when L3 resumes a task</a></p><h1>Report</h1>'
    '<h2>Landed</h2><ul><li>PR #178 merged · Persist task paths</li><li>Main checks success</li><li>Deploy: healthy</li></ul>'
    '<h2>Review</h2><ul><li>Resume retained a stale path · fixed · The current task record supplies the lease.</li></ul>'
    '<h2>Decisions</h2><ul><li>Use the recorded lease when the task resumes.</li></ul>'
    '<h2>FYI</h2><ul><li>The merged behavior is verified.</li></ul>'
    '<h2>Follow-ups</h2><ul><li>Review the queued-task wording with slice 3.</li></ul>'
    '<h2>Spend</h2><p>turns: 14</p>'
    '<h2>Report notes</h2><p>The task paths survive a resume and the full suite passes.</p>'
    '<h2 id="digest">Digest</h2><p>PR #178 is merged, checks pass, and the result is deployed.</p>'
)
board("Report", 1440, 900, '<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:100%">' + rail("altitude") + '<main class="pane"><div class="route-content">' + report_content + '</div></main></div>')
board("MobileReport", 390, 844, '<div class="m"><div></div>' + mheader_project() + '<div class="route-content">' + report_content + '</div><div></div>' + tabbar("work") + '</div>')

def seat(label, stale=False, empty=False):
    if empty:
        return f'<article class="card"><h3>{label}</h3><p class="muted">No reading. The seat has not been read yet.</p></article>'
    windows = ''.join(f'<div class="window"><div class="monitor-row"><span>{name}</span><span>{percent}%</span></div><div class="meter reserve"><i style="width:{percent}%"></i></div><p class="muted">{reset}</p></div>' for name, percent, reset in [
        ("5-hour", 24, "resets in 2h 10m (Mon 14:30)"), ("7-day", 32, "resets in 3d 4h (Thu 16:20)")])
    return f'<article class="card{" stale" if stale else ""}"><div class="monitor-row"><h3>{label}</h3>' + ('<span class="chip held">Stale</span>' if stale else '') + f'<span class="muted">reading {"2h" if stale else "3m"} old</span></div>{windows}</article>'

monitor_content = (
    '<h1>Monitor</h1><h2>Seats</h2><div class="seats">' + seat("Claude Code") + seat("Codex", stale=True) + '</div>'
    '<p class="muted">The mark on each meter is the 70% reserve line.</p>'
    '<h2>Routing now</h2><div class="card">'
    '<div class="routing"><span>L3 · altitude · Auto</span><b>Claude Code</b><p>More weekly headroom in the current readings.</p></div>'
    '<div class="routing"><span>L3 · harbor · pinned to Codex</span><b>Codex</b><p>Pinned for this project.</p></div>'
    '<div class="routing"><span>L2 · new task</span><b>Claude Code</b><p>More weekly headroom in the current readings.</p></div></div>'
    '<h2>Sessions (2)</h2><div class="sessionrow"><div class="monitor-row"><span class="chip">L2</span><b>altitude / design-wireframes</b><span class="muted">3 min ago</span></div><p class="muted">Claude Code · Fable · context 18%</p><div class="meter"><i style="width:18%"></i></div></div>'
    '<div class="sessionrow"><div class="monitor-row"><span class="chip">L2</span><b>altitude / persist-paths</b><span class="muted">2 days ago</span></div><p class="muted">Codex · context 9% · idle</p><div class="meter"><i style="width:9%"></i></div></div>'
)

def restart_banner(mode="idle", mobile=False):
    message = "Altitude is restarting…" if mode == "underway" else "Altitude restarts at the next quiet moment."
    if mode == "waiting":
        message += " Waiting for an L3 turn."
    action = '<span class="btn primary">Restart</span>' if mode == "idle" else ''
    if mobile:
        summary = "Restarting…" if mode == "underway" else "Update waiting" if mode == "waiting" else "Update ready"
        return '<div class="banner" style="padding:2px 12px;min-height:48px;gap:4px"><span style="flex:1">' + summary + '</span><details class="phone-details"><summary style="width:auto;padding:0 8px">Details</summary><div class="details-panel"><h2>Update details</h2><p>Changes to the web app · 4 files · landed 2h ago</p><p>' + message + '</p></div></details>' + action + '</div>'
    return '<div class="banner"><div><p>Merged changes to the web app are waiting to activate. <span class="muted" title="7 September 2026, 10:00">4 files, landed 2h ago</span></p><p>' + message + '</p></div>' + action + '</div>'

board("Monitor", 1440, 900, '<div style="display:grid;grid-template-columns:260px minmax(0,1fr);height:100%">' + rail("monitor") + '<main class="pane">' + restart_banner() + '<div class="route-content">' + monitor_content + '</div></main></div>')
board("MobileMonitor", 390, 844, '<div class="m" style="grid-template-rows:auto 54px minmax(0,1fr) 0 84px">' + restart_banner("waiting", mobile=True) + mheader_global() + '<div class="route-content">' + monitor_content.replace('<h1>Monitor</h1>', f'<h1>Monitor</h1><span class="newtasks">New tasks · <b>Auto</b>{I("chev-d","i sm")}</span>', 1) + '</div><div></div>' + tabbar("monitor") + '</div>')

state_sheet("MonitorStates", "Monitor states", [
    ("Loading", "skeleton in the page’s shape", '<div class="skel" style="width:30%"></div><div class="skel" style="height:100px"></div><div class="skel" style="height:60px"></div>'),
    ("Read failed", "one sentence and Retry", '<p class="danger">Could not read the monitor. <a href="#">Retry</a></p>'),
    ("No reading", "the API supplies what produces one", seat("Claude Code", empty=True)),
    ("Stale", "amber chip; meter at 50% opacity", seat("Codex", stale=True)),
    ("One configured engine", "one card fills the row", seat("Claude Code")),
    ("No sessions / no engine", "explicit empty readings", '<h3>Sessions (0)</h3><p class="muted">No live sessions.</p><div class="routing"><span>L2 · new task</span><b class="danger">No engine</b><p>No engine is available.</p></div>'),
], 1180)

state_sheet("RestartStates", "Restart banner states", [
    ("Pending at a quiet point", "running workers can remain", restart_banner()),
    ("Waiting", "names what holds activation", restart_banner("waiting")),
    ("Restart under way", "button leaves at once", restart_banner("underway")),
    ("New process answered", "banner leaves", '<header class="ph" style="padding:0"><h1>altitude</h1><span class="muted">L3 answered 3 min ago</span></header>'),
], 540)

# =====================================================================
# Outputs beside the boards
(OUT / "wireframes.css").write_text(
    "/* Generated by gen.py: the build's tokens, then the board styles.\n"
    " * Edit gen.py, not this file. wireframes.css imports web/design/tokens.css from two levels up,\n"
    " * which is why serve.sh and altd serve the repository tree rather than this folder. */\n"
    + TOKENS + CSS)

# The viewer's board list: one row per route, desktop beside phone, in the order of README.md.
from conversation_first import generate as conversation_first_boards

ROUTES = [
    ("Project: chat with L3, work panel beside it", "Project", "MobileProject"),
    ("Project switcher (phone)", None, "MobileSwitcher"),
    ("Project work (phone)", None, "MobileWork"),
    ("Task page: L2 conversation and live session", "Task", "MobileTask"),
    ("Task live session (phone)", None, "MobileTaskLive"),
    ("Task live panel overlay below 1280px", "TaskOverlay", None),
    ("Task page states", "TaskStates", None),
    ("Report and digest", "Report", "MobileReport"),
    ("Monitor", "Monitor", "MobileMonitor"),
    ("Monitor states", "MonitorStates", None),
    ("Restart banner states", "RestartStates", None),
    ("First run", "FirstRun", None),
    ("Composer states, voice included", "ComposerStates", None),
    ("Voice input states: desktop and phone", "VoiceStates", None),
    ("Settings", "Settings", "MobileSettings"),
    ("Settings · Voice input", "VoiceSettings", "MobileVoiceSettings"),
    ("Settings · This project", "ProjectSettings", "MobileProjectSettings"),
    ("Settings states and entry points", "SettingsStates", None),
    ("Models dialog states", "ModelsStates", None),
    ("System turns in chat: reports, faults, FYIs", "SystemTurnStates", None),
    ("Conversation and report states", "ConversationStates", None),
    ("Create task: a reply that could become a task", "ReplyTask", "MobileReplyTask"),
    ("Create task states", "ReplyTaskStates", None),
    ("Project lifecycle states", "ProjectLifecycleStates", None),
]
ROUTES = conversation_first_boards(OUT, board, I) + ROUTES
for obsolete in ("Decision", "MobileDecision", "DecisionStates", "NeedsYou", "MobileNeedsYou"):
    (OUT / (obsolete + ".html")).unlink(missing_ok=True)
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
