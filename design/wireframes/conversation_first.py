"""Approved conversation-first design; gen.py owns generation and viewer registration."""
from html import escape

CSS = r"""
.cf{height:100%;display:grid;grid-template-columns:260px minmax(0,1fr);font-size:15px}
.cf [hidden]{display:none!important}
.cf *{min-width:0}.cf button,.cf textarea{font:inherit}.cf a,.cf button,.cf summary{touch-action:manipulation}
.cf a:focus-visible,.cf button:focus-visible,.cf textarea:focus-visible,.cf summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.cf button{cursor:pointer}.cf button:disabled{cursor:default;opacity:.55}.cf .rail{gap:6px}
.cf .ri{min-height:44px}.cf .rail small{margin:0 10px;color:var(--text-muted)}
.cf-rail-bottom{margin-top:auto;padding:12px 10px;color:var(--text-muted);font-size:13px}
.cf-main{display:flex;flex-direction:column;overflow:hidden}.cf-top{height:70px;padding:0 32px;display:flex;align-items:center;gap:14px;border-bottom:1px solid var(--hairline);flex-shrink:0}
.cf-top a{display:inline-flex;align-items:center;min-height:44px;gap:6px}.cf-top .cf-meta{margin-left:auto}
.cf-head{padding:24px 32px 16px;flex-shrink:0}.cf h1{font-size:24px;line-height:1.25;letter-spacing:-.5px;margin:0 0 6px;font-weight:600}
.cf h2{font-size:19px;line-height:1.4;margin:8px 0}.cf p{margin:0 0 8px;line-height:1.6}
.cf-meta{font-size:13px;color:var(--text-muted);display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.cf-state{color:var(--chip-claimed-text);background:var(--chip-claimed-bg);border-radius:20px;padding:3px 10px;font-size:12px;display:inline-flex;align-items:center;gap:6px}
.cf-scroll{flex:1;overflow:auto;overscroll-behavior:contain;padding:8px 32px 24px}.cf-column{max-width:700px;margin:auto;display:flex;flex-direction:column;gap:18px}
.cf-feed{gap:20px}.cf-author{font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px}.cf-assistant{line-height:1.65}
.cf-user{align-self:flex-end;max-width:86%;background:var(--bubble);border-radius:18px;padding:12px 16px;line-height:1.6}
.cf-question{border:1px solid var(--accent-tint-border);border-radius:12px;background:var(--card);padding:20px;scroll-margin-top:16px}
.cf-question h2 a{color:var(--text-primary)}.cf-recommendation{font-size:15px;color:var(--text-secondary)}
.cf-recommendation strong{color:var(--text-primary);font-weight:600}.cf-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:14px}
.cf-button{border:1px solid var(--border);border-radius:10px;min-height:44px;padding:10px 16px;display:inline-flex;align-items:center;justify-content:center;gap:8px;background:var(--card);color:var(--text-primary);font-weight:500;font-size:14px;line-height:1.4}
.cf-button.primary{color:var(--on-accent);background:var(--accent);border-color:var(--accent)}.cf-button.primary:hover{background:var(--accent-hover);color:var(--on-accent)}
.cf-link{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:6px 10px;font-size:14px;gap:6px}
.cf-note{color:var(--text-muted);font-size:13px;margin-top:12px;line-height:1.5}.cf-note a{min-height:44px;display:inline-flex;align-items:center}
.cf-system{font-size:13px;color:var(--text-muted);display:flex;align-items:center;gap:8px;line-height:1.5}.cf-system.success{color:var(--success-text)}
.cf-summary{border-left:2px solid var(--border);padding:4px 0 4px 14px;color:var(--text-secondary);font-size:14px}
.cf-details{font-size:13px;color:var(--text-muted)}.cf-details summary{cursor:pointer;min-height:44px;display:flex;align-items:center;gap:8px}.cf-details summary::before{content:'›';font-size:20px}.cf-details[open] summary::before{content:'⌄'}
.cf-evidence{border:1px solid var(--border);background:var(--page);border-radius:10px;padding:14px;color:var(--text-secondary)}
.cf-evidence a{text-decoration:underline;text-underline-offset:2px}
.cf-compose-wrap{flex-shrink:0;padding:12px 32px 24px}.cf-compose{max-width:700px;margin:auto;background:var(--card);border:1px solid var(--border);border-radius:24px;padding:12px 12px 10px 18px;box-shadow:var(--shadow)}
.cf-compose textarea{display:block;width:100%;border:0;outline:0;background:transparent;color:var(--text-primary);resize:none;font-size:15px;line-height:1.5;min-height:44px;max-height:140px;padding:0}.cf-compose textarea::placeholder{color:var(--text-muted)}
.cf-compose-row{display:flex;align-items:center;justify-content:flex-end;gap:8px}.cf-icon{width:44px;height:44px;border:0;border-radius:50%;background:transparent;color:var(--text-secondary);display:inline-flex;align-items:center;justify-content:center}
.cf-icon.send{background:var(--accent);color:var(--on-accent)}.cf-hint{font-size:12px;color:var(--text-muted);max-width:700px;margin:8px auto 0;padding-left:8px}
.cf-tabbar{display:none}.cf-empty{padding:96px 20px;text-align:center}.cf-empty h2{font-size:20px}.cf-empty .cf-icon{background:var(--success-bg);color:var(--success-text);margin:auto auto 16px}
.cf-notice{padding:12px 16px;border-radius:10px;background:var(--bubble);font-size:14px;color:var(--text-secondary);line-height:1.5}.cf-notice.error{color:var(--danger)}
.cf-skeleton{background:var(--bubble);height:15px;border-radius:6px;margin:12px 0;width:90%}.cf-skeleton.short{width:55%}
.cf-keyboard{display:none}.cf-wave{letter-spacing:4px;color:var(--accent-text);font-size:22px;flex:1}.cf-prototype{font-size:12px;color:var(--text-muted)}
.cf-phone{grid-template-columns:minmax(0,1fr)}.cf-phone>.rail{display:none}.cf-phone .cf-top{height:54px;padding:0 16px;gap:8px;font-size:14px}.cf-phone .cf-top .cf-meta{display:none}
.cf-phone .cf-head{padding:14px 16px 12px}.cf-phone h1{font-size:21px}.cf-phone .cf-scroll{padding:4px 16px 16px}.cf-phone .cf-column{gap:16px}
.cf-phone .cf-question{padding:16px}.cf-phone h2{font-size:18px}.cf-phone .cf-user{font-size:16px;max-width:94%;padding:10px 14px}.cf-phone .cf-assistant{font-size:16px}
.cf-phone .cf-compose-wrap{padding:8px 12px}.cf-phone .cf-compose{padding:4px;display:flex;align-items:flex-end;gap:2px;border-radius:26px}.cf-phone .cf-compose textarea{font-size:16px;flex:1;min-height:44px;max-height:120px;padding:10px 8px;line-height:24px}.cf-phone .cf-compose-row{display:contents}.cf-phone .cf-icon{flex:none}.cf-phone .cf-hint{font-size:12px;margin-top:6px}.cf-phone .cf-hint.routine{display:none}
.cf-phone .cf-tabbar{height:84px;flex-shrink:0;border-top:1px solid var(--hairline);background:var(--page);display:flex;justify-content:space-around;padding:6px 0 26px}
.cf-phone .cf-compact-title{flex:1;font-size:17px;font-weight:600;line-height:1.3;margin:0;letter-spacing:0}.cf-compact-title small{display:block;font-size:12px;font-weight:400;color:var(--text-muted)}.cf-local-tabs{display:flex;gap:20px;height:44px;flex-shrink:0;padding:0 16px;border-bottom:1px solid var(--hairline);font-size:14px;align-items:center}.cf-local-tabs a{min-height:44px;display:flex;align-items:center}.cf-local-tabs .on{border-bottom:2px solid var(--accent)}
.cf-tabbar a{position:relative;min-height:44px;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:11px;color:var(--text-muted);gap:2px;min-width:64px}.cf-tabbar a.on{color:var(--accent-text)}.cf-tabbar .badge{position:absolute;top:0;left:calc(50% + 8px);height:18px;min-width:18px;font-size:10px}
.cf-phone .cf-keyboard{display:block;background:var(--bubble);height:230px;padding:14px 8px 24px;flex-shrink:0}.cf-keyboard .keys{display:flex;justify-content:center;gap:5px;margin-bottom:9px}.cf-keyboard span{border-radius:5px;background:var(--card);box-shadow:0 1px 1px var(--text-muted);font-size:18px;text-align:center;padding:7px 9px}.cf-keyboard .space{width:170px;font-size:14px}
.cf-keyboard-open .cf-phone .cf-tabbar{display:none}.cf-phone .cf-empty{padding:64px 10px}.cf-phone .cf-prototype{margin-left:auto;font-size:10px}
.cf-answer{display:block;width:100%;min-height:72px;max-height:160px;resize:vertical;padding:10px 12px;margin-top:10px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text-primary)}.cf-receipt{margin-top:10px;padding-top:10px;border-top:1px solid var(--hairline);font-size:14px}.cf-receipt p{white-space:pre-wrap}.cf-group-footer{position:sticky;bottom:0;background:var(--card);padding-bottom:12px}
.cf .cf-choices button{min-height:44px}
"""

STATES = {
    "list-loading": "Loading Needs you",
    "list-error": "Needs you read failed",
    "list-offline": "Cached Needs you, offline",
    "discussion": "Needs you during discussion",
    "loading": "Loading the question",
    "read-error": "Read failed, retry",
    "cached-error": "Offline with cached discussion",
    "accepting": "Sending question responses",
    "accept-error": "Response send failed",
    "denied": "Sending denied",
    "sending": "Sending a message",
    "send-error": "Message failed, draft retained",
    "waiting": "Message queued, L2 unavailable",
    "reply-error": "L2 could not answer",
    "accepted-waiting": "Decision recorded, waiting to resume",
    "no-recommendation": "Question without a recommendation",
    "simple-input": "A simple typed answer",
    "new-reply": "A reply arrives below the question",
    "revised": "A newer question replaced this one",
    "missing": "Question unavailable",
    "archived": "Task archived, read only",
    "listening": "Listening",
    "transcribing": "Transcribing",
    "dictated": "Dictation in editable draft",
    "mic-denied": "Microphone denied",
    "voice-error": "Transcription failed",
    "voice-unavailable": "Voice unavailable",
}


def generate(out, board, icon):
    out.joinpath("conversation-first.css").write_text("/* Generated from conversation_first.py. */\n" + CSS + """
.cf-group{padding:16px}.cf-group h2{font-size:17px;margin:3px 0 7px}.cf-group-item{padding:12px 0;border-bottom:1px solid var(--hairline)}.cf-group-item:last-of-type{border-bottom:0}.cf-group-item p{font-size:13px;margin:0 0 6px}.cf-choices{display:flex;gap:6px;flex-wrap:wrap}.cf-choices button{min-height:40px;padding:8px 12px;font-size:13px}.cf-choices [aria-pressed=true]{border-color:var(--accent);background:var(--accent-tint);color:var(--accent-text)}.cf-group-footer{display:flex;gap:8px;flex-wrap:wrap;padding-top:12px}.cf-group-note{font-size:12px;color:var(--text-muted);margin-top:8px}.cf-open-count{font-size:13px;color:var(--text-muted)}.cf-closed{font-size:14px;border-left:2px solid var(--success-text);padding-left:12px}.cf-closed p{margin:4px 0}.cf-phone .cf-group{padding:12px}.cf-phone .cf-group h2{font-size:16px}.cf-phone .cf-group-item{padding:10px 0}.cf-phone .cf-group .cf-actions{margin-top:8px}.cf-phone .cf-group-footer .cf-button{font-size:13px;padding:8px 10px}.cf-phone .cf-head{padding-top:10px;padding-bottom:8px}
""")
    routes = []
    scenes = [('NeedsYou', 'Needs you · questions upfront'), ('Question', 'One question · answer in place'),
              ('Group', 'Choose answers · send once'), ('Followup', 'Discuss without deciding'),
              ('Partial', 'Keep only what still needs an answer'), ('Accepted', 'Decisions recorded · work resumed')]
    def name(scene, mobile):
        return ('Mobile' if mobile else '') + 'ConversationFirst' + scene
    def url(scene, mobile, state=''):
        return name(scene, mobile) + '.html' + ('?state=' + state if state else '')
    def link(text, scene, mobile, cls='cf-link', state='', extra=''):
        return f'<a class="{cls}" href="{url(scene, mobile, state)}" {extra}>{text}</a>'
    def user(text):
        return f'<div class="cf-user"><div class="cf-author">You</div><span>{text}</span></div>'
    def assistant(text):
        return f'<div class="cf-assistant"><div class="cf-author">L2 · Index rollout</div>{text}</div>'
    def evidence():
        return '<details class="cf-details"><summary>Activity &amp; evidence</summary><div class="cf-evidence"><p>10:38 · Rollback coverage checked.</p><p>10:47 · Later technical output does not replace the question.</p><a href="https://example.com/atlas/pull/42" target="_blank" rel="noopener noreferrer">PR #42</a></div></details>'
    def answer_field(key, label, hidden=False, disabled=False):
        return f'<textarea class="cf-answer" data-answer-field="{key}" aria-label="{label}" placeholder="Your answer or a follow-up question…" rows="2" {"hidden" if hidden else ""} {"disabled" if disabled else ""}></textarea>'
    def footer(disabled=False):
        return f'<div class="cf-group-footer"><button class="cf-button primary" data-send-answers disabled>Send answers</button><button class="cf-button" data-recommendations {"disabled" if disabled else ""}>Use recommendations</button></div><p class="cf-group-note">Choose an answer or write your own. Follow-up questions are welcome.</p>'
    def single(mobile, plain=False, disabled=False):
        choices = ''.join(f'<button class="cf-button" data-pick="retention" data-value="{days} days" aria-pressed="false" {"disabled" if disabled else ""}>{days} days{" · recommended" if days == 7 else ""}</button>' for days in (7, 14, 30))
        choices += f'<button class="cf-button" data-other="retention" aria-pressed="false" {"disabled" if disabled else ""}>Other…</button>'
        recommendation = '<p>No recommendation yet. Write your answer or a follow-up question.</p>' if plain else '<p class="cf-recommendation"><strong>I recommend 7 days.</strong> It covers the rollout without another month of storage.</p><div class="cf-choices">' + choices + '</div>'
        return f'<article class="cf-question" aria-label="Open question" id="question-retention" tabindex="-1"><div class="cf-author">L2 · your answer</div><div data-item="retention"><h2>How long should we keep the old index?</h2>{recommendation}{answer_field("retention", "Retention answer", not plain, disabled)}</div>{footer(disabled)}</article>'
    def group(mobile, listing=False):
        context = '' if listing else '<details class="cf-details"><summary>More context</summary><p>The old index remains available for instant rollback during retention. After deletion, restoring search from the daily snapshot takes about two hours. Longer retention keeps both indexes in storage for longer.</p></details>'
        return f"""<article class="cf-question cf-group" aria-label="Index rollout questions" id="questions" tabindex="-1">
<div class="cf-meta">{'Atlas · L3 asks' if listing else 'L3 brought these questions to the L2'}<span class="cf-open-count">3 questions need you</span></div>
{'<h2>' + link('Index rollout', 'Group', mobile) + '</h2>' if listing else ''}
<div class="cf-group-item" data-item="retention"><h2>How long should we keep the old index?</h2><p>Recommended: 7 days of instant rollback. After deletion, recovery takes two hours.</p>{context}<div class="cf-choices" role="group" aria-label="Retention answer">{''.join(f'<button class="cf-button" aria-pressed="false" data-pick="retention" data-value="{d} days">{d} days</button>' for d in (7,14,30))}<button class="cf-button" data-other="retention" aria-pressed="false">Other…</button></div>{answer_field('retention', 'Retention answer', True)}</div>
<div class="cf-group-item" data-item="region"><h2>Where should the backup live?</h2><p>Recommended: West, beside the primary.</p><div class="cf-choices" role="group" aria-label="Backup region answer">{''.join(f'<button class="cf-button" aria-pressed="false" data-pick="region" data-value="{region}">{region}</button>' for region in ('West','East'))}<button class="cf-button" data-other="region" aria-pressed="false">Other…</button></div>{answer_field('region', 'Backup region answer', True)}</div>
<div class="cf-group-item" data-item="owner"><h2>Who should receive the rollout report?</h2>{answer_field('owner', 'Report recipient answer')}</div>
{footer()}
{link('Open L2 chat ' + icon('chev-r'), 'Group', mobile) if listing else ''}</article>"""
    state_text = {
        'list-loading':'Loading decisions…', 'list-error':'Could not load Needs you.', 'list-offline':'Offline · showing saved decisions.',
        'discussion':'Your follow-up is in the L2 chat. Still awaiting your decision.', 'loading':'Loading the question…',
        'read-error':'Could not load this conversation.', 'cached-error':'Could not refresh. Showing saved discussion.',
        'accepting':'Sending answers…', 'accept-error':'Could not send answers. Your responses are kept here.',
        'denied':'This connection cannot send messages or decisions. Reconnect to continue.', 'sending':'Sending…',
        'send-error':'Not sent. Your draft is still here. Retry with Send.', 'waiting':'Message saved · the L2 will answer when capacity is available.',
        'reply-error':'The L2 could not answer. Your message is saved; the question is still open.',
        'accepted-waiting':'Waiting for capacity to resume the L2. Your choice is saved.',
        'no-recommendation':'No recommendation yet. Write your answer or a follow-up question.',
        'simple-input':'Ask a question or say how to proceed.', 'new-reply':'Latest messages · L2 replied',
        'revised':'The question changed while this page was open.', 'missing':'This question is no longer available.',
        'archived':'This task is complete. Its conversation stays readable.', 'listening':'Listening · 0:08',
        'transcribing':'Transcribing…', 'dictated':'Ask a question or say how to proceed.',
        'mic-denied':'Microphone access denied. You can keep typing.', 'voice-error':'Could not transcribe. Your typed draft is still here.',
        'voice-unavailable':'Voice is unavailable. You can keep typing.',
    }
    def composer(mobile, state=''):
        disabled = state in ('loading','read-error','cached-error','denied','accepting','sending','transcribing','missing')
        draft = 'Could we roll back after day seven?' if state == 'send-error' else '14 days' if state == 'simple-input' else ''
        voice = link('Cancel', 'Group', mobile, extra='data-voice-cancel') + link('Stop', 'States', mobile, state='dictated', extra='data-voice-stop')
        body = '<div class="cf-wave" aria-label="Audio waveform">▂▅▃▇▅▂▆</div>' if state == 'listening' else f'<textarea aria-label="Message the L2" placeholder="Message the L2" rows="{1 if mobile else 2}" {"disabled" if disabled and state != "transcribing" else ""}>{draft}</textarea>'
        controls = voice if state == 'listening' else f'<button class="cf-icon" type="button" aria-label="Use microphone" data-voice {"disabled" if disabled or state == "voice-unavailable" else ""}>{icon("mic")}</button>'
        routine = state not in state_text or state in ('simple-input', 'dictated', 'waiting', 'sending')
        return f'<div class="cf-compose-wrap"><form class="cf-compose">{body}<div class="cf-compose-row">{controls}<button class="cf-icon send" aria-label="Send" {"disabled" if disabled else ""}>{icon("up")}</button></div></form><div class="cf-hint{" routine" if routine else ""}" role="status">{state_text.get(state, "Ask a question or say how to proceed.")}</div></div>'
    def frame(scene, mobile, state=''):
        listing = scene == 'NeedsYou' or state.startswith('list-') or state == 'discussion'
        top = 'Altitude' if listing else link(icon('chev-l') + ' Needs you', 'NeedsYou', mobile, '', extra='data-back')
        title = 'Needs you' if listing else 'Index rollout'
        status = '3 questions across 1 project' if listing else 'Decisions recorded · work resumed' if scene == 'Accepted' else 'Questions and discussion with the L2'
        if listing and state.startswith('list-'):
            status = 'Saved attention · refresh needed' if state == 'list-offline' else 'Attention unavailable' if state == 'list-error' else 'Loading attention…'
        if scene == 'NeedsYou': content = group(mobile, True)
        elif scene == 'Question': content = assistant('The new index is ready. The old one is only needed if we roll back.') + single(mobile) + evidence()
        elif scene == 'Group': content = group(mobile) + '<p class="cf-note">All three questions are here. Pick any quick answers and send once, or reply normally below.</p>' + evidence()
        elif scene == 'Followup': content = '<div class="cf-summary">3 questions still open · ' + link('View questions', 'Group', mobile) + '</div>' + user('Could we roll back after day seven?') + assistant('We can rebuild from a snapshot in two hours. Keeping the old index for 14 days preserves instant rollback for another week.') + '<p class="cf-system">Discussion leaves every question open.</p>' + evidence()
        elif scene == 'Partial': content = '<div class="cf-closed" data-closed><b>Recorded</b><p>Retention: 14 days.</p><p>Backup region: closed — snapshots replace the regional backup.</p></div>' + '<div data-remaining>' + group(mobile) + '</div>'
        elif scene == 'Accepted': content = '<div class="cf-summary" data-outcome>All three questions resolved.</div>' + user('<span data-answer>Keep it for 14 days. Use snapshots instead of the regional backup. Send the report to the release team.</span>') + '<p class="cf-system success" role="status">Decision recorded</p>' + assistant('<span data-ack>I’ll keep the index for 14 days, use snapshots, and send the report to the release team.</span>') + '<p class="cf-system">Work resumed</p>' + evidence()
        else:
            text = state_text[state]
            content = f'<p class="cf-notice" role="{"alert" if state in ("list-error","read-error","accept-error","denied","cached-error") else "status"}">{text}</p>'
            if state in ('listening','transcribing','dictated','mic-denied','voice-error','voice-unavailable','simple-input','sending','send-error'):
                content = ''
            if state in ('loading','list-loading'): content += '<div class="cf-skeleton"></div><div class="cf-skeleton short"></div>'
            elif state in ('read-error','list-error','missing'): content += link('Retry', 'Group', mobile, 'cf-button')
            elif state in ('accepted-waiting','archived'): content += '<p class="cf-system success">Decision recorded</p><p>Keep the old index for 14 days.</p>'
            elif state == 'revised': content += link('View current questions', 'Group', mobile)
            elif state == 'new-reply': content += single(mobile) + link('Open the reply', 'Followup', mobile)
            elif state != 'no-recommendation': content += single(mobile, disabled=state in ('cached-error','denied','accepting','list-offline','sending'))
            else: content = single(mobile, plain=True)
        count = 0 if scene == 'Accepted' or state in ('accepted-waiting', 'archived') else 1 if scene in ('Question', 'Partial') or scene == 'States' else 3
        badge = f'<span class="badge" data-attention {"hidden" if not count else ""}>{count}</span>'
        if state in ('list-loading', 'list-error', 'list-offline'):
            label, value = {'list-loading': ('Attention loading', '…'), 'list-error': ('Attention unavailable', '?'), 'list-offline': ('Saved attention · 1 question', '1')}[state]
            badge = f'<span class="badge" aria-label="{label}" title="{label}">{value}</span>'
        rail = f'<aside class="rail"><div class="brand">{icon("chat")}Altitude</div>{link("Needs you" + badge, "NeedsYou", mobile, "ri" + (" sel" if listing else ""))}<div class="rsec">Projects</div><div class="ri sel"><span class="dot held"></span>Atlas</div><div class="ri"><span class="dot idle"></span>Meadow</div><div class="cf-rail-bottom">Engine · connected<br><br>Monitor<br><br>Operator</div></aside>'
        tabs = f'<nav class="cf-tabbar" aria-label="Main"><a href="MobileProject.html">{icon("chat")}Chat</a><a href="MobileWork.html">{icon("panel")}Work</a>{link(icon("tray") + "Needs you" + badge, "NeedsYou", mobile, "on")}<a href="MobileMonitor.html">{icon("pulse")}Monitor</a></nav>'
        head = f'<header class="cf-top">{top}<span class="cf-prototype">Fictional design</span></header><div class="cf-head"><h1>{title}</h1><div class="cf-meta" data-summary>{status}</div></div>'
        if mobile and not listing:
            short_status = 'L2 · Done' if state == 'archived' else 'L2 · Running' if scene == 'Accepted' else 'L2 · Needs your answer'
            head = f'<header class="cf-top">{link(icon("chev-l"), "NeedsYou", mobile, extra="data-back aria-label=Back")}<h1 class="cf-compact-title">{title}<small data-summary>{short_status}</small></h1><details class="phone-details"><summary aria-label="Task details">{icon("more")}</summary><div class="details-panel"><h2>{title}</h2><p>{status}</p><p>Attempt 1 · task metadata and observed tokens</p><p>The original question stays in this conversation. Reading details records no decision.</p></div></details></header><nav class="cf-local-tabs" aria-label="Task views"><a class="on" href="#">Conversation</a><a href="MobileTaskLive.html">Live session</a></nav>'
        return f'<div class="cf {"cf-phone" if mobile else ""}">{rail}<main class="cf-main">{head}<div class="cf-scroll"><div class="cf-column cf-feed">{content}</div></div>{"" if listing or state == "archived" else composer(mobile,state)}{tabs}</main></div>'
    script = r"""<script>
const params=new URLSearchParams(location.search), mobile=location.pathname.split('/').pop().startsWith('Mobile');
const scene=location.pathname.split('ConversationFirst').pop().split('.')[0], state=params.get('state')||'loading';
const dest=(name,condition='')=>(mobile?'Mobile':'')+'ConversationFirst'+name+'.html'+(condition?'?state='+condition:'');
const read=key=>{try{return sessionStorage.getItem('cf-current-'+key)||''}catch{return ''}},write=(key,value)=>{try{sessionStorage.setItem('cf-current-'+key,value)}catch{}};
if(params.has('reset')){['answers','responses','draft','single','answer'].forEach(k=>write(k,''));params.delete('reset');history.replaceState(null,'',location.pathname+(params.size?'?'+params:''))}
const templates=document.querySelectorAll('template[data-state]');if(templates.length){const t=[...templates].find(t=>t.dataset.state===state)||templates[0];document.querySelector('[data-stage]').replaceChildren(t.content.cloneNode(true))}
let answers=JSON.parse(read('answers')||'{}'),responses=JSON.parse(read('responses')||'{}'),picks={};
if(scene==='Partial'&&!Object.keys(answers).length)answers={retention:'14 days',region:'Closed: snapshots replace the regional backup'};
function render(){
 if(read('single')&&(scene==='Question'||scene==='States'&&!['archived','revised','missing','accepted-waiting'].includes(state))){location.replace(dest('Accepted'));return}
 const members=document.querySelector('.cf-group')?['retention','region','owner']:['retention'];
 document.querySelectorAll('[data-item]').forEach(row=>{const id=row.dataset.item;row.hidden=Boolean(answers[id]);if(responses[id]){row.querySelectorAll('.cf-choices,.cf-answer,.cf-recommendation').forEach(e=>e.hidden=true);if(!row.querySelector('.cf-receipt')){const receipt=document.createElement('div');receipt.className='cf-receipt';receipt.innerHTML='<b>Sent to L2</b><p></p>';receipt.querySelector('p').textContent=responses[id];row.append(receipt)}}});
 const left=members.filter(id=>!answers[id]&&!responses[id]);
 if(['NeedsYou','Group','Partial','Followup'].includes(scene))document.querySelectorAll('[data-attention]').forEach(e=>{e.textContent=left.length;e.hidden=!left.length});
 if(scene==='NeedsYou'){document.querySelector('[data-summary]').textContent=left.length?left.length+' question'+(left.length===1?'':'s')+' across 1 project':'All caught up';if(!left.length){document.querySelector('.cf-column').innerHTML='<div class="cf-empty"><h2>Nothing needs you.</h2><p>The sent responses stay in the L2 conversation.</p><a class="cf-link" href="'+dest('Group')+'">View conversation</a></div>';return}}
 document.querySelectorAll('.cf-open-count').forEach(e=>e.textContent=left.length+' question'+(left.length===1?'':'s')+' need'+(left.length===1?'s':'')+' you');
 const count=Object.values(picks).filter(value=>value.trim()).length;
 document.querySelectorAll('[data-pick]').forEach(b=>b.setAttribute('aria-pressed',String(picks[b.dataset.pick]===b.dataset.value)));
 const rec=document.querySelector('[data-recommendations]');if(rec)rec.hidden=Boolean(Object.keys(picks).length||!left.some(id=>document.querySelector('[data-pick="'+id+'"]')));
 const send=document.querySelector('[data-send-answers]');if(send){send.disabled=!count;send.hidden=!left.length;send.textContent=count?'Send '+count+' answer'+(count===1?'':'s'):'Send answers'}
 const note=document.querySelector('.cf-group-note');if(note)note.textContent=count?'Only these answers will be sent. You can answer the rest later.':left.length?'Choose an answer or write your own. Follow-up questions are welcome.':'Responses sent. The L2 continues the conversation.';
 const closed=document.querySelector('[data-closed]');if(closed){closed.replaceChildren();const title=document.createElement('b');title.textContent='Recorded';closed.append(title);for(const [key,value] of Object.entries(answers)){const p=document.createElement('p');p.textContent=({retention:'Retention',region:'Backup region',owner:'Report recipient'}[key])+': '+value+'.';closed.append(p)}}
 if(!left.length&&scene!=='NeedsYou'&&document.querySelector('[data-send-answers]')&&!document.querySelector('[data-owner-example]')){const discussion=Object.values(responses).some(text=>text.includes('?'));const link=document.createElement('a');link.className='cf-link';link.dataset.ownerExample='';link.href=dest(discussion?'Followup':'Accepted');link.textContent=discussion?'Design example: later L2 reply':'Design example: later L2 decision';document.querySelector('.cf-column').append(link)}
}
render();addEventListener('pageshow',()=>{answers=JSON.parse(read('answers')||JSON.stringify(answers));responses=JSON.parse(read('responses')||JSON.stringify(responses));render()});
const save=()=>write('answers',JSON.stringify(answers));
document.querySelectorAll('[data-pick]').forEach(b=>b.onclick=()=>{const key=b.dataset.pick,value=b.dataset.value;if(picks[key]===value)delete picks[key];else picks[key]=value;const field=document.querySelector('[data-answer-field="'+key+'"]');field.hidden=true;field.value='';document.querySelector('[data-other="'+key+'"]')?.setAttribute('aria-pressed','false');render()});
document.querySelectorAll('[data-other]').forEach(b=>b.onclick=()=>{const key=b.dataset.other,field=document.querySelector('[data-answer-field="'+key+'"]');field.hidden=!field.hidden;field.value='';delete picks[key];if(!field.hidden){picks[key]='';field.focus()}b.setAttribute('aria-pressed',String(!field.hidden));render()});
document.querySelectorAll('[data-answer-field]').forEach(field=>field.oninput=()=>{picks[field.dataset.answerField]=field.value;render()});
document.querySelector('[data-send-answers]')?.addEventListener('click',()=>{Object.assign(responses,Object.fromEntries(Object.entries(picks).filter(([,value])=>value.trim())));write('responses',JSON.stringify(responses));picks={};render()});
document.querySelector('[data-recommendations]')?.addEventListener('click',()=>{for(const [key,value] of Object.entries({retention:'7 days',region:'West'}))if(!answers[key]&&!responses[key]&&document.querySelector('[data-pick="'+key+'"]'))picks[key]=value;render()});
if(scene==='Accepted'&&read('single')){document.querySelector('[data-outcome]').textContent='Old index retention resolved.';document.querySelector('[data-answer]').textContent=read('answer');document.querySelector('[data-ack]').textContent='I’ll keep the old index for '+read('single')+' days, then delete it.'}
else if(scene==='Accepted'&&Object.keys(answers).length){document.querySelector('[data-answer]').textContent=read('answer')||'Use the recorded answers.';document.querySelector('[data-ack]').textContent='I’ll keep the index for '+answers.retention+', '+(answers.region.startsWith('Closed:')?'use snapshots':'keep the backup in '+answers.region)+', and send the report to '+answers.owner+'.'}
else if(scene==='Accepted'&&Object.keys(responses).length){document.querySelector('[data-outcome]').textContent='Example: the L2 interprets the submitted answers.';document.querySelector('[data-answer]').textContent=Object.entries(responses).map(([key,value])=>key+': '+value).join('\n');document.querySelector('[data-ack]').textContent='I’ll use these answers for the rollout. A follow-up question would continue our discussion instead.'}
const field=document.querySelector('textarea[aria-label="Message the L2"]'),send=document.querySelector('form [aria-label="Send"]');
if(field&&!field.disabled){if(params.has('draft'))field.value=params.get('draft');else if(read('draft'))field.value=read('draft');if(state==='dictated')field.value=(params.get('draft')||'Keep it for 14 days,')+' then delete it. Go ahead.';const update=()=>send.disabled=!field.value.trim();update();field.addEventListener('input',()=>{write('draft',field.value);update()})}
document.querySelectorAll('[data-voice]').forEach(b=>b.onclick=()=>location.href=dest('States','listening')+'&draft='+encodeURIComponent(field?.value||''));
document.querySelectorAll('[data-voice-cancel]').forEach(a=>a.href+='?draft='+encodeURIComponent(params.get('draft')||''));
document.querySelectorAll('[data-voice-stop]').forEach(a=>a.href+='&draft='+encodeURIComponent(params.get('draft')||''));
document.querySelector('form')?.addEventListener('submit',e=>{e.preventDefault();if(!field){write('single','14');location.href=dest('Accepted');return}const text=field.value.trim();if(!text)return;
 if(text==='Could we roll back after day seven?'||text==='Maybe two weeks, but I’m unsure about cost.'){write('draft','');location.href=dest('Followup')}
 else if(text==='Keep 14 days; use snapshots so region no longer matters.'){answers={...answers,retention:'14 days',region:'Closed: snapshots replace the regional backup'};save();write('draft','');location.href=dest('Partial')}
 else if(text==='Release team'||text==='Keep 14 days, use West, and send the report to the release team.'){answers={retention:answers.retention||'14 days',region:answers.region||'West',owner:'Release team'};save();write('answer',text);write('draft','');location.href=dest('Accepted')}
 else if(['14 days','Keep it for 14 days, then delete it. Go ahead.'].includes(text)){write('answer',text);write('draft','');if(scene==='Question'||scene==='States'){write('single','14');location.href=dest('Accepted')}else{answers.retention='14 days';save();location.href=dest('Partial')}}
 else document.querySelector('.cf-hint').textContent='Prototype: try the example messages in CONVERSATION_FIRST.md.'});
document.querySelectorAll('[data-back]').forEach(a=>a.onclick=e=>{if(document.referrer&&new URL(document.referrer).origin===location.origin&&history.length>1){e.preventDefault();history.back()}});
</script>"""
    for scene,label in scenes+[('States','Shared input and recovery appendix')]:
        for mobile in (False,True):
            inner=('<div data-stage></div>'+''.join(f'<template data-state="{state}">{frame(scene,mobile,state)}</template>' for state in STATES)) if scene=='States' else frame(scene,mobile)
            board(name(scene,mobile),390 if mobile else 1440,844 if mobile else 900,'<link rel="stylesheet" href="conversation-first.css"><style>[data-stage]{height:100%}</style>'+inner+script)
            path=out/(name(scene,mobile)+'.html');path.write_text(path.read_text().replace('<meta charset="utf-8">','<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'))
        routes.append(('Conversation · '+label,name(scene,False),name(scene,True)))
    # Superseded fictional scenes are removed, rather than retained as a second review workspace.
    for old in ('Alternative','Clarify','Empty','Stale'):
        for mobile in (False,True):out.joinpath(name(old,mobile)+'.html').unlink(missing_ok=True)
    return routes
