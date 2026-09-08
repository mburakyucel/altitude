"""Pending conversation-first proposal; gen.py owns generation and viewer registration."""
from html import escape

CSS = r"""
.cf{height:100%;display:grid;grid-template-columns:260px minmax(0,1fr);font-size:15px}
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
.cf-phone .cf-compose-wrap{padding:8px 12px 10px}.cf-phone .cf-compose textarea{font-size:16px}.cf-phone .cf-hint{font-size:12px;margin-top:6px}
.cf-phone .cf-tabbar{height:78px;flex-shrink:0;border-top:1px solid var(--hairline);background:var(--page);display:flex;justify-content:space-around;padding:6px 0 22px}
.cf-tabbar a{min-height:44px;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:11px;color:var(--text-muted);gap:2px;min-width:64px}.cf-tabbar a.on{color:var(--accent-text)}
.cf-phone .cf-keyboard{display:block;background:var(--bubble);height:230px;padding:14px 8px 24px;flex-shrink:0}.cf-keyboard .keys{display:flex;justify-content:center;gap:5px;margin-bottom:9px}.cf-keyboard span{border-radius:5px;background:var(--card);box-shadow:0 1px 1px var(--text-muted);font-size:18px;text-align:center;padding:7px 9px}.cf-keyboard .space{width:170px;font-size:14px}
.cf-keyboard-open .cf-phone .cf-tabbar{display:none}.cf-phone .cf-empty{padding:64px 10px}.cf-phone .cf-prototype{margin-left:auto;font-size:10px}
"""

STATES = {
    "list-loading": "Loading Needs you",
    "list-error": "Needs you read failed",
    "list-offline": "Cached Needs you, offline",
    "discussion": "Needs you during discussion",
    "loading": "Loading the question",
    "read-error": "Read failed, retry",
    "cached-error": "Offline with cached discussion",
    "accepting": "Recording quick acceptance",
    "accept-error": "Acceptance failed",
    "denied": "Acceptance denied",
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
    out.joinpath("conversation-first.css").write_text("/* Generated from conversation_first.py by gen.py. */\n" + CSS)
    routes = []

    def name(scene, mobile):
        return ("Mobile" if mobile else "") + "ConversationFirst" + scene

    def url(scene, mobile, state=None):
        return name(scene, mobile) + ".html" + ("?state=" + state if state else "")

    def link(text, scene, mobile, cls="cf-link", state=None, extra=""):
        return f'<a class="{cls}" href="{url(scene, mobile, state)}" {extra}>{text}</a>'

    def user(text):
        return f'<div class="cf-user"><div class="cf-author">You</div><span>{text}</span></div>'

    def assistant(text):
        return f'<div class="cf-assistant"><div class="cf-author">L2 · Index rollout</div><div>{text}</div></div>'

    def recommendation(mobile, chat=False, state=""):
        disabled = state in ("accepting", "cached-error", "denied", "sending")
        accept = ('<button class="cf-button primary" disabled>' + ("Recording…" if state == "accepting" else "Use 7 days & resume") + '</button>') if disabled else link("Use 7 days &amp; resume", "Accepted" if chat else "Empty", mobile, "cf-button primary", extra='data-accept="true"')
        question = 'How long should we keep the old index?'
        if not chat:
            question = link(question, "Question", mobile, "")
        errors = {
            "accept-error": '<div class="cf-notice error" role="alert">Could not record your choice. Try again.</div>',
            "denied": '<div class="cf-notice" role="alert">This connection cannot send messages or decisions. Reconnect to continue.</div>',
        }
        discussion = '<p class="cf-note">Your follow-up is in the L2 chat. Still awaiting your decision.</p>' if state == "discussion" else ''
        return f'''<article class="cf-question" id="question-retention" tabindex="-1" aria-label="Open question">
          <div class="cf-meta"><span class="cf-state">Needs you</span><span>{'L2 · 10:42' if chat else 'Atlas · Index rollout · L2'}</span></div>
          <h2>{question}</h2><p class="cf-recommendation"><strong>I recommend 7 days.</strong> It gives us a rollback window without paying for a second index for a month.</p>
          <div class="cf-actions">{accept}{'' if chat else link('Open L2 chat ' + icon('chev-r'), 'Question', mobile)}</div>
          {errors.get(state, '')}{discussion}
        </article>'''

    def evidence(mobile):
        return '<details class="cf-details"><summary>Activity &amp; evidence</summary><div class="cf-evidence"><p>10:38 · L2 checked rollback coverage.</p><p>10:40 · L3: The launch plan sets no retention window. This cost and rollback choice needs you.</p><p>10:47 · Checks finished after the question was asked.</p><p>Fictional reference: <a href="https://example.com/atlas/pull/42" target="_blank" rel="noopener noreferrer">PR #42</a></p></div></details>'

    def compose(mobile, scene, state=""):
        drafts = {"Alternative": "Keep it for 14 days, then delete it. Go ahead.", "Clarify": "",
                  "send-error": "Could we roll back after day seven?", "dictated": "Keep it for 14 days, then delete it. Go ahead.",
                  "mic-denied": "Keep it for 14 days", "voice-error": "Keep it for 14 days", "simple-input": "14 days"}
        draft = drafts.get(state, drafts.get(scene, ""))
        disabled = state in ("loading", "read-error", "cached-error", "sending", "transcribing", "missing", "denied")
        target = "Accepted" if scene == "Alternative" or state == "dictated" else "Followup"
        hints = {
            "sending": "Sending…", "send-error": "Not sent. Your draft is still here. Retry with Send.",
            "waiting": "Message saved · the L2 will answer when capacity is available.",
            "mic-denied": "Microphone access denied. You can keep typing.",
            "voice-error": "Could not transcribe. Your typed draft is still here.",
            "voice-unavailable": "Voice is unavailable. You can keep typing.",
            "cached-error": "Offline · showing saved discussion. Reconnect to send.",
            "transcribing": "Transcribing…", "listening": "Listening · 0:08", "loading": "Loading the conversation…",
            "denied": "Read only until this connection is authorized again.",
        }
        hint = hints.get(state, "Message the L2" if scene in ("Accepted", "Stale") else "Ask a question or say how to proceed.")
        row = f'<button class="cf-icon" type="button" aria-label="Use microphone" data-voice {"disabled" if disabled or state == "voice-unavailable" else ""}>{icon("mic")}</button>'
        if state == "listening":
            body = '<div class="cf-wave" aria-label="Audio waveform">▂▅▃▇▅▂▆▃▅</div>'
            row = link('Cancel', 'Question', mobile, extra='data-voice-cancel') + link('Stop', 'States', mobile, state='transcribing', extra='data-voice-stop')
        elif state == "transcribing":
            body = '<div class="cf-meta" style="min-height:44px">Turning audio into text…</div>'
        else:
            body = f'<textarea aria-label="Message the L2" placeholder="Message the L2" rows="2" {"disabled" if disabled else ""}>{escape(draft)}</textarea>'
        return f'''<div class="cf-compose-wrap"><form class="cf-compose" data-target="{url(target, mobile)}" data-scene="{scene}">
          {body}<div class="cf-compose-row">{row}<button class="cf-icon send" aria-label="Send" {"disabled" if disabled else ""}>{icon('up')}</button></div>
          </form><div class="cf-hint" role="status">{hint}</div></div>'''

    def frame(scene, mobile, state=""):
        listing = scene in ("NeedsYou", "Empty") or state.startswith('list-') or state == 'discussion'
        resolved = scene in ("Accepted", "Stale", "Empty") or state in ("archived", "accepted-waiting")
        count = "" if resolved else '<span class="badge">1</span>'
        if state in ('list-loading', 'list-error'):
            count = ''
        rail = f'''<aside class="rail"><div class="brand">{icon('chat')}Altitude</div>
          {link('Needs you ' + count, 'NeedsYou', mobile, 'ri' + (' sel' if listing else ''))}
          <div class="rsec">Projects</div><div class="ri{' sel' if not listing else ''}"><span class="dot"></span>Atlas{count}</div>
          <div class="ri"><span class="dot idle"></span>Meadow</div><div class="cf-rail-bottom">Engine · connected<br><br>Monitor<br><br>Operator</div></aside>'''
        top = 'Altitude' if listing else link(icon('chev-l') + ' Needs you', 'Empty' if resolved else 'NeedsYou', mobile, '', extra='data-back')
        top += '<span class="cf-meta">Atlas / Index rollout</span>' if not listing else ''
        top += '<span class="cf-prototype">Design proposal</span>'
        title = 'Needs you' if listing else 'Index rollout'
        status = 'All caught up' if scene == 'Empty' else '1 decision across your projects' if listing else 'Work resumed' if scene == 'Accepted' else 'Resolved · work is running' if scene == 'Stale' else 'Archived · read only' if state == 'archived' else 'Awaiting your decision'
        if state in ('list-loading', 'list-error'):
            status = 'Loading decisions…' if state == 'list-loading' else 'Decision count unavailable'
        content = ''
        if scene == "NeedsYou":
            content = recommendation(mobile) + '<p class="cf-note">A quick answer here, or a conversation with the task owner.</p>'
        elif scene == "Empty":
            content = f'<div class="cf-notice" role="status"><span data-empty-choice>7 days accepted · work will resume.</span> {link("View conversation", "Accepted", mobile)}</div><div class="cf-empty"><div class="cf-icon">{icon("check")}</div><h2>Nothing needs your decision.</h2><p class="muted">We’ll bring the next question here.</p></div>'
        elif scene == "Question":
            content = assistant('The new index is ready. The old one is only needed if we roll back.') + recommendation(mobile, True) + '<div class="cf-summary"><span class="cf-author">L3 · escalation context</span><br>The rollout plan leaves retention open. I agree with 7 days; the cost and rollback tradeoff needs your call.</div>' + evidence(mobile)
        elif scene == "Followup":
            content = recommendation(mobile, True) + user('Could we roll back after day seven?') + assistant('We could rebuild from the snapshot, but it would take about two hours. Keeping the old index for 14 days would preserve a fast rollback for another week.') + '<div class="cf-system">Question still open · work waits for your decision</div>'
        elif scene == "Alternative":
            content = '<div class="cf-summary">Open question · old index retention<br>Recommended: 7 days · ' + link('View question', 'Question', mobile) + '</div>' + assistant('Keeping it for 14 days gives us another week of fast rollback. That adds one more week of storage cost.')
        elif scene == "Clarify":
            content = '<div class="cf-summary">Open question · old index retention<br>Recommended: 7 days</div>' + user('Maybe two weeks, but I’m unsure about cost.') + assistant('That means another week of storage cost. Would you like me to use 14 days, or keep comparing?') + '<div class="cf-system">Question still open</div>' + link('View the original recommendation', 'Question', mobile)
        elif scene == "Accepted":
            content = '<div class="cf-summary">Resolved question · old index retention<br><span data-resolution>7 days approved by you · 10:49</span></div>' + user('<span data-answer>Use 7 days and resume.</span>') + '<div class="cf-system success" role="status">' + icon('check') + ' Decision recorded</div>' + assistant('<span data-ack>I’ll keep the old index for 7 days, then delete it. I’m continuing with that plan.</span>') + '<div class="cf-system">' + icon('pulse') + ' Work resumed</div>' + evidence(mobile)
        elif scene == "Stale":
            content = '<div class="cf-notice" role="status">This question was resolved in another conversation.</div><div class="cf-summary">Old index retention · 14 days<br>Approved by you in the project chat · 10:49</div><div class="cf-summary"><span class="cf-author">L3 · relayed your decision</span><br>Keep the old index for 14 days, then delete it. Go ahead.</div>' + assistant('Your 14-day choice is recorded. Work has resumed with that plan.') + evidence(mobile)
        elif state in ("list-loading", "list-error", "list-offline", "discussion"):
            if state == 'list-loading':
                content = '<div role="status">Loading decisions…</div><div class="cf-question"><div class="cf-skeleton short"></div><div class="cf-skeleton"></div><div class="cf-skeleton"></div></div>'
            elif state == 'list-error':
                content = '<div class="cf-notice" role="alert">Could not load Needs you.</div>' + link('Retry', 'NeedsYou', mobile, 'cf-button')
            else:
                content = recommendation(mobile, state='discussion' if state == 'discussion' else 'cached-error')
                if state == 'list-offline':
                    content += '<div class="cf-notice">Offline · showing saved decisions.</div>' + link('Retry', 'NeedsYou', mobile)
        elif state == "accepted-waiting":
            status = 'Decision recorded · waiting to resume'
            content = '<div class="cf-summary">Resolved question · old index retention<br>7 days approved by you · 10:49</div>' + user('Use 7 days and resume.') + '<div class="cf-system success">' + icon('check') + ' Decision recorded</div><div class="cf-notice">Waiting for capacity to resume the L2. Your choice is saved.</div>'
        elif state == "no-recommendation":
            content = assistant('The two retention windows have different cost and rollback tradeoffs. Which matters more for this launch?') + '<article class="cf-question"><span class="cf-state">Needs you</span><h2>How long should we keep the old index?</h2><p class="cf-recommendation">No recommendation yet. Discuss the tradeoff with the L2 here.</p></article>'
        elif state == "reply-error":
            content = recommendation(mobile, True) + user('Could we roll back after day seven?') + '<div class="cf-notice" role="alert">The L2 could not answer. Your message is saved; the question is still open.</div>' + link('Retry reply', 'Followup', mobile, 'cf-button')
        elif state == "loading":
            content = '<div role="status">Loading the question…</div><div class="cf-question"><div class="cf-skeleton short"></div><div class="cf-skeleton"></div><div class="cf-skeleton"></div></div><div class="cf-skeleton short"></div>'
        elif state in ("read-error", "missing"):
            text = 'Could not load this conversation.' if state == 'read-error' else 'This question is no longer available.'
            content = f'<div class="cf-notice" role="alert">{text}</div>' + link('Retry' if state == 'read-error' else 'Open task conversation', 'Question', mobile, 'cf-button')
        elif state == "revised":
            content = '<div class="cf-notice" role="status">The question changed while this page was open.</div><div class="cf-summary">Earlier recommendation · keep the old index for 7 days</div>' + assistant('The rollback drill now needs 14 days. My updated recommendation is to keep the old index until the drill ends.') + '<article class="cf-question"><span class="cf-state">Needs you · updated</span><h2>Keep the index until the rollback drill ends?</h2><p class="cf-recommendation"><strong>I recommend 14 days.</strong> It covers the full drill.</p>' + link('Use 14 days &amp; resume', 'Accepted', mobile, 'cf-button primary', extra='data-alternative') + '</article>'
        elif state == "archived":
            content = '<div class="cf-summary">Resolved · 7 days approved by you</div>' + assistant('The rollout is complete. The old index was retired after 7 days.') + '<div class="cf-notice">This task is complete. Its conversation stays readable.</div>' + evidence(mobile)
        else:
            if state == 'cached-error':
                content += '<div class="cf-notice" role="alert">Could not refresh. Showing saved discussion.</div>' + link('Retry', 'Question', mobile)
            content += recommendation(mobile, True, state)
            if state == 'new-reply':
                content += '<div class="cf-system">You’re reading the original question.</div>' + link('Latest messages · L2 replied', 'Followup', mobile, 'cf-button')
            if state in ('sending', 'waiting'):
                content += user('Could we roll back after day seven?') + '<div class="cf-system">' + ('Sending…' if state == 'sending' else 'Saved · waiting for the L2 to become available') + '</div>'
            elif state in ('listening', 'transcribing', 'dictated', 'mic-denied', 'voice-error', 'voice-unavailable'):
                content += assistant('You can choose a different retention window here.')
        composer = '' if listing or state == 'archived' else compose(mobile, scene, state)
        tabs = f'<nav class="cf-tabbar" aria-label="Main"><a href="MobileProject.html">{icon("chat")}Chat</a><a href="MobileWork.html">{icon("panel")}Work</a>{link(icon("tray") + "Needs you" + (" · 1" if count else ""), "Empty" if resolved else "NeedsYou", mobile, "on")}<a href="MobileMonitor.html">{icon("pulse")}Monitor</a></nav>'
        keyboard = ''
        if scene == 'Alternative' and mobile:
            keyboard = '<div class="cf-keyboard" aria-label="Illustrated phone keyboard"><div class="keys">' + ''.join(f'<span>{c}</span>' for c in 'qwertyuiop') + '</div><div class="keys">' + ''.join(f'<span>{c}</span>' for c in 'asdfghjkl') + '</div><div class="keys">' + ''.join(f'<span>{c}</span>' for c in 'zxcvbnm') + '</div><div class="keys"><span>123</span><span class="space">space</span><span>return</span></div></div>'
            tabs = ''
        return f'<div class="cf {"cf-phone" if mobile else ""}">{rail}<main class="cf-main"><header class="cf-top">{top}</header><div class="cf-head"><h1>{title}</h1><div class="cf-meta">{status}</div></div><div class="cf-scroll"><div class="cf-column cf-feed">{content}</div></div>{composer}{tabs}{keyboard}</main></div>'

    script = r"""<script>
const params = new URLSearchParams(location.search);
// A tiny local fiction lets Back/Forward show the outcome of this prototype's own actions.
// The production proposal instead reads the task's authoritative question record.
const canvas = document.referrer.endsWith('/design/wireframes/index.html');
const memory = {read(key) {try {return canvas ? '' : sessionStorage.getItem('cf-proposal-' + key) || '';} catch {return '';}}, write(key, value) {try {if (!canvas) sessionStorage.setItem('cf-proposal-' + key, value);} catch {}}};
if (params.has('reset')) {memory.write('decision', ''); memory.write('draft', ''); params.delete('reset'); history.replaceState(null, '', location.pathname + (params.size ? '?' + params : ''));}
const requested = params.get('state') || 'loading';
const scenes = document.querySelectorAll('template[data-state]');
if (scenes.length) {
  const selected = [...scenes].find(t => t.dataset.state === requested) || scenes[0];
  document.querySelector('[data-stage]').replaceChildren(selected.content.cloneNode(true));
}
const mobile = location.pathname.split('/').pop().startsWith('Mobile');
const dest = (scene, state) => (mobile ? 'Mobile' : '') + 'ConversationFirst' + scene + '.html' + (state ? '?state=' + state : '');
const sceneName = location.pathname.split('ConversationFirst').pop().split('.')[0];
function reconcile() {
  // Reset only on explicit entry; a restored history entry still reads the saved decision.
  const saved = memory.read('decision');
  const openScene = ['NeedsYou', 'Question', 'Followup', 'Alternative', 'Clarify'].includes(sceneName) || (sceneName === 'States' && !['archived', 'revised', 'missing', 'accepted-waiting', 'transcribing'].includes(requested));
  if (saved && openScene) location.replace(dest(sceneName === 'NeedsYou' ? 'Empty' : 'Accepted') + '?choice=' + saved + '&answer=' + encodeURIComponent(memory.read('answer')));
}
addEventListener('pageshow', reconcile);
const draft = params.get('draft') || '';
const voiced = draft ? draft + ' then delete it. Go ahead.' : 'Keep it for 14 days, then delete it. Go ahead.';
if (params.has('draft') && document.querySelector('textarea')) document.querySelector('textarea').value = requested === 'dictated' ? voiced : draft;
document.querySelectorAll('[data-voice-cancel]').forEach(a => a.href += '?draft=' + encodeURIComponent(draft));
document.querySelectorAll('[data-voice-stop]').forEach(a => a.href += '&mode=draft&draft=' + encodeURIComponent(draft));
if (requested === 'transcribing' && params.has('mode')) setTimeout(() => {
  location.href = params.get('mode') === 'send' ? dest('Accepted') + '?choice=14' : dest('States', 'dictated') + '&draft=' + encodeURIComponent(draft);
}, 600);
if (params.get('choice') === '14') {
  const set = (selector, text) => {const el = document.querySelector(selector); if (el) el.textContent = text;};
  set('[data-resolution]', '14 days approved by you · 10:49');
  set('[data-answer]', params.get('answer') || 'Keep it for 14 days, then delete it. Go ahead.');
  set('[data-ack]', 'I’ll keep the old index for 14 days, then delete it. I’m continuing with that plan.');
  set('[data-empty-choice]', '14 days accepted · work will resume.');
  if (sceneName === 'Empty') document.querySelectorAll('a[href*="Accepted"]').forEach(a => a.href += '?choice=14&answer=' + encodeURIComponent(params.get('answer') || memory.read('answer')));
}
document.querySelectorAll('[data-alternative]').forEach(a => {a.href += '?choice=14'; a.onclick = () => {memory.write('decision', '14'); memory.write('answer', 'Use 14 days and resume.'); memory.write('draft', '');};});
document.querySelectorAll('[data-accept]').forEach(a => a.addEventListener('click', () => {memory.write('decision', '7'); memory.write('answer', 'Use 7 days and resume.'); memory.write('draft', '');}));
document.querySelectorAll('a').forEach(a => {
  if (params.has('dark') && a.getAttribute('href').includes('.html')) a.href += (a.href.includes('?') ? '&' : '?') + 'dark';
});
document.querySelectorAll('[data-back]').forEach(a => a.addEventListener('click', e => {
  if (document.referrer && new URL(document.referrer).origin === location.origin && history.length > 1) {e.preventDefault(); history.back();}
}));
document.querySelectorAll('[data-voice]').forEach(b => b.addEventListener('click', () => location.href = dest('States', 'listening') + '&draft=' + encodeURIComponent(document.querySelector('textarea')?.value || '')));
document.querySelectorAll('form').forEach(form => {
  const field = form.querySelector('textarea');
  const send = form.querySelector('[aria-label="Send"]');
  if (field && !field.disabled) {
    if (!params.has('draft') && ['Question', 'Clarify'].includes(sceneName) && memory.read('draft')) field.value = memory.read('draft');
    if (field.value) memory.write('draft', field.value);
    const update = () => {send.disabled = !field.value.trim();};
    update(); field.addEventListener('input', () => {update(); memory.write('draft', field.value);});
  }
  form.addEventListener('submit', e => {
    e.preventDefault();
    if (!field) {memory.write('draft', ''); memory.write('decision', '14'); memory.write('answer', voiced); location.href = dest('States', 'transcribing') + '&mode=send&draft=' + encodeURIComponent(draft); return;}
    const text = field.value.trim();
    if (!text) return;
    // Only scripted fictional examples; this is not a production intent classifier.
    if (['14 days', 'Use 14 days', 'Keep it for 14 days, then delete it. Go ahead.'].includes(text)) {memory.write('draft', ''); memory.write('decision', '14'); memory.write('answer', text); location.href = dest('Accepted') + '?choice=14&answer=' + encodeURIComponent(text);}
    else if (text === 'Could we roll back after day seven?') {memory.write('draft', ''); location.href = dest('Followup');}
    else if (text === 'Maybe two weeks, but I’m unsure about cost.') {memory.write('draft', ''); location.href = dest('Clarify');}
    else {const hint = document.querySelector('.cf-hint'); hint.textContent = 'Prototype: try “Could we roll back after day seven?”, “14 days”, or “Maybe two weeks, but I’m unsure about cost.”';}
  });
});
if (['NeedsYou', 'Empty'].includes(sceneName)) memory.write('draft', '');
</script>"""
    for scene, label in [
        ('NeedsYou', '01 · Needs you: accept or open the L2'),
        ('Question', '02 · Land at the actual question, with L3 context'),
        ('Followup', '03 · Follow-up: discussion leaves the question open'),
        ('Alternative', '04 · Typed alternative: one conversational decision'),
        ('Clarify', '05 · Ambiguous answer: clarify in the same chat'),
        ('Accepted', '06 · Decision recorded, work resumed'),
        ('Empty', '07 · Needs you clears after acceptance'),
        ('Stale', '08 · Resolved elsewhere: read the outcome'),
        ('States', '09 · Loading, error, denied and input states'),
    ]:
        for mobile in (False, True):
            if scene == 'States':
                inner = '<div data-stage></div>' + ''.join(f'<template data-state="{state}">{frame(scene, mobile, state)}</template>' for state in STATES)
            else:
                inner = frame(scene, mobile)
            inner = '<link rel="stylesheet" href="conversation-first.css"><style>[data-stage]{height:100%}</style>' + inner + script
            board(name(scene, mobile), 390 if mobile else 1440, 844 if mobile else 900, inner)
            path = out / (name(scene, mobile) + '.html')
            path.write_text(path.read_text().replace('<meta charset="utf-8">', '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">'))
        routes.append(('Proposal · ' + label, name(scene, False), name(scene, True)))
    review = out / 'conversation-first'
    review.mkdir(exist_ok=True)
    state_links = ''.join(f'<tr><th>{label}</th><td><a href="../ConversationFirstStates.html?state={state}&amp;reset">Desktop</a> · <a href="../MobileConversationFirstStates.html?state={state}&amp;reset">Phone</a></td></tr>' for state, label in STATES.items())
    scenes = [('NeedsYou', 'Needs you'), ('Question', 'The L2 question'), ('Followup', 'Follow-up'), ('Alternative', 'Typed alternative'), ('Clarify', 'Clarification'), ('Accepted', 'Accepted & resumed'), ('Empty', 'All caught up'), ('Stale', 'Resolved elsewhere')]
    scene_links = ''.join(f'<button data-scene="{scene}">{label}</button>' for scene, label in scenes)
    review.joinpath('index.html').write_text('''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review · conversation-first decisions</title><link rel="stylesheet" href="../../../web/design/tokens.css">
<style>
*{box-sizing:border-box}body{font-family:var(--font-ui);margin:0;background:var(--page);color:var(--text-primary);line-height:1.6}main{max-width:1220px;margin:auto;padding:32px 20px}h1{font-size:32px;line-height:1.2;letter-spacing:-.6px}h2{font-size:22px}p{max-width:800px}a{color:var(--accent-text)}button{font:inherit;padding:10px 14px;border:1px solid var(--border);background:var(--card);border-radius:10px;cursor:pointer;min-height:44px;color:var(--text-primary)}button[aria-pressed=true]{border-color:var(--accent);color:var(--accent-text)}.controls{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}.frame{position:relative;overflow:hidden;max-width:1440px;background:var(--surface);border:1px solid var(--border);border-radius:12px;margin:20px auto}iframe{border:0;transform-origin:top left;position:absolute;left:0;top:0}.tag{color:var(--chip-claimed-text);background:var(--chip-claimed-bg);padding:5px 12px;border-radius:30px;font-size:13px}.small{font-size:13px;color:var(--text-muted)}table{border-collapse:collapse;width:100%;max-width:900px}th,td{text-align:left;border-bottom:1px solid var(--border);padding:10px 6px;font-size:14px}th{font-weight:500}td{white-space:nowrap}li{margin-bottom:6px}.screens{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:20px;align-items:start}.screens img{width:100%;border:1px solid var(--border);border-radius:10px}@media(max-width:700px){main{padding:24px 16px}h1{font-size:28px}.screens{grid-template-columns:1fr}.screens img:last-child{max-width:390px}}:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
</style></head><body><main>
<span class="tag">Proposal · awaiting design approval</span>
<h1>A question opens a conversation.</h1>
<p>Accept a clear recommendation from Needs you, or open the owning L2 chat to discuss it. A follow-up keeps the question open. A clear typed decision is enough to proceed.</p>
<p><a href="../CONVERSATION_FIRST.md">Read the design and architecture proposal</a> · <a href="README.md">View the captured boards</a> · <a href="../index.html">All design boards</a></p>
<h2>Try the flow</h2><p class="small">Fictional prototype. No API, microphone or provider calls. Start at Needs you, open the chat and send one of the example messages below.</p>
<div class="controls"><button data-size="desktop">Desktop · 1440 × 900</button><button data-size="phone">Phone · 390 × 844</button><button id="theme">Dark theme</button><a id="open" target="_blank" rel="noopener">Open at full size ↗</a></div>
<div class="controls">''' + scene_links + '''</div>
<div class="frame"><iframe title="Conversation-first prototype"></iframe></div>
<p class="small">Scripted messages: “Could we roll back after day seven?”, “14 days”, “Maybe two weeks, but I’m unsure about cost.” and “Keep it for 14 days, then delete it. Go ahead.” Other input stays editable with a prototype hint. The live product will use the owning L2’s judgment. Choosing a scene resets this fictional example; voice simulates the fourteen-day answer.</p>
<h2>The recommended design</h2><ul>
<li>One question and recommendation component in Needs you and the L2 chat.</li>
<li>Open the durable question with surrounding discussion, including L3’s escalation context.</li>
<li>User and L2 messages stay prominent; activity and evidence expand on demand.</li>
<li>Discussion can wake the L2 without accepting the recommendation.</li>
<li>A recorded answer clears Needs you. Work resumes through the existing provider conversation.</li></ul>
<p>Remove the separate decision page, recipient selector, optional note form and mirrored follow-up thread. Keep one pending question on the task, independently of whether its worker is answering or waiting.</p>
<h2>Question, in context</h2><div class="screens"><img src="captures/desktop-question.png" alt="Desktop: the L2 question and recommendation in a familiar conversation"><img src="captures/phone-question.png" alt="Phone: the same question with L3 escalation context and the ordinary composer"></div>
<h2 id="states">Every interaction state</h2><p>Each link opens a real rendered board. The same state inventory is walked on phone and desktop.</p><table><thead><tr><th>State</th><th>Open board</th></tr></thead><tbody>''' + state_links + '''</tbody></table>
<h2>Design checkpoint</h2><p>Approve this direction for implementation, or identify what should change. Production implementation waits for that decision; the resulting PR also stays held for the operator’s merge review.</p>
<script>
let size=innerWidth<700?'phone':'desktop', scene='NeedsYou', dark=false;
const frame=document.querySelector('iframe'), box=document.querySelector('.frame');
function fit(){const w=size==='phone'?390:1440,h=size==='phone'?844:900;box.style.maxWidth=w+'px';const scale=Math.min(1,(box.clientWidth-2)/w);frame.style.width=w+'px';frame.style.height=h+'px';frame.style.transform='scale('+scale+')';box.style.height=(h*scale+2)+'px';}
function show(){const src='../'+(size==='phone'?'Mobile':'')+'ConversationFirst'+scene+'.html?reset'+(dark?'&dark':'');frame.src=src;document.querySelector('#open').href=src;document.querySelectorAll('[data-size]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.size===size)));document.querySelectorAll('[data-scene]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.scene===scene)));fit();}
document.querySelectorAll('[data-size]').forEach(b=>b.onclick=()=>{size=b.dataset.size;show()});document.querySelectorAll('[data-scene]').forEach(b=>b.onclick=()=>{scene=b.dataset.scene;show()});document.querySelector('#theme').onclick=()=>{dark=!dark;document.querySelector('#theme').textContent=dark?'Light theme':'Dark theme';show()};new ResizeObserver(fit).observe(box);show();
</script></main></body></html>''')
    captures = [('needs-you', 'Needs you: quick acceptance or conversation'), ('question', 'Open the L2 question with surrounding discussion'), ('followup-answer', 'Follow-up: the L2 answers and the question stays open'), ('alternative-draft', 'Typed alternative, with the phone keyboard'), ('typed-decision', 'A typed decision is recorded without another confirmation'), ('simple-answer', 'A simple answer is enough; no special approval phrase'), ('resumed', 'Quick acceptance recorded, work resumed'), ('quick-acceptance', 'Needs you after acceptance'), ('resolved-elsewhere', 'A stale item resolved in another conversation')]
    gallery = '\n\n'.join(f'## {title}\n\n[Desktop](captures/desktop-{file}.png) · [Phone](captures/phone-{file}.png)\n\n![Desktop: {title}](captures/desktop-{file}.png)\n\n<img src="captures/phone-{file}.png" width="390" alt="Phone: {title}">' for file, title in captures)
    state_gallery = '\n'.join(f'| {label} | [Desktop](captures/desktop-state-{state}.png) · [Phone](captures/phone-state-{state}.png) |' for state, label in STATES.items())
    review.joinpath('README.md').write_text('# Conversation-first decisions: captured review\n\nPending design approval. Fictional content only. These captures are generated by the deterministic\nPlaywright wireframe walkthrough at 1440×900 and 390×844. They demonstrate a proposal, not live\napplication behavior.\n\n[Design and architecture](../CONVERSATION_FIRST.md) · [Interactive review](index.html)\n\n' + gallery + '\n\n## State captures\n\n| State | View |\n| --- | --- |\n' + state_gallery + '\n')
    return routes
