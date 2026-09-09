"""Approved mobile chat design. HTML/CSS examples; no application behavior or API calls."""

CSS = r'''
@import url("../../web/design/tokens.css");
*{box-sizing:border-box}body{margin:0;font:15px/1.6 var(--font-ui);color:var(--text-primary);background:var(--surface)}
button,textarea,select{font:inherit;color:inherit}button{cursor:pointer;background:none;border:0;min-width:44px;min-height:44px;border-radius:10px;padding:0 10px}button:focus-visible,textarea:focus-visible,select:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.phone{width:390px;height:844px;display:flex;flex-direction:column;overflow:hidden;position:relative}.app{height:844px;display:flex;flex-direction:column;min-height:0}.keyboard-open .app{height:510px;flex:none}
.head{height:54px;flex:none;padding:0 12px;display:flex;gap:4px;align-items:center;border-bottom:1px solid var(--hairline)}.identity{min-width:0;flex:1;text-align:left;padding:0 4px;line-height:1.3}.identity strong{display:block;font-size:17px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.identity small{display:block;font-size:13px;color:var(--text-muted)}.decisions{font-size:13px;color:var(--chip-claimed-text);white-space:nowrap}.decisions b{border-radius:99px;background:var(--chip-claimed-bg);padding:2px 6px}.more{font-size:23px}
.tabs{display:flex;flex:none;height:44px;border-bottom:1px solid var(--hairline);padding:0 12px}.tabs button{border-radius:0}.tabs .selected{border-bottom:2px solid var(--accent);color:var(--accent-text)}
.notice{display:flex;align-items:center;gap:4px;min-height:48px;flex:none;padding:2px 12px;background:var(--accent-tint);font-size:13px}.notice span{flex:1;line-height:1.45}.notice button{color:var(--accent-text);font-weight:500}.notice.error{background:var(--surface);color:var(--danger)}
.messages{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding:10px 16px;display:flex;flex-direction:column}.flow{display:flex;flex-direction:column;gap:20px;margin-top:auto;flex:none}.day{text-align:center;color:var(--text-muted);font-size:12px}.bubble{align-self:flex-end;max-width:88%;padding:10px 14px;background:var(--bubble);border-radius:18px;font-size:16px;line-height:1.55}.reply{font-size:16px;line-height:1.65}.reply p{margin:0 0 12px}.reply p:last-child{margin:0}.active{color:var(--text-muted);font-size:13px}.queued{border:1px solid var(--border);border-radius:12px;padding:8px 12px;font-size:14px;color:var(--text-secondary)}.queued small{font-size:13px}.queued button{float:right;color:var(--accent-text);margin-top:-8px}
.dock{padding:8px 12px;flex:none}.entry{display:flex;align-items:flex-end;gap:2px;padding:4px;border:1px solid var(--border);border-radius:26px;background:var(--card);box-shadow:var(--shadow)}textarea{resize:none;border:0;background:none;flex:1;min-width:0;height:44px;padding:10px 8px;line-height:24px;font-size:16px;max-height:120px;outline:none}.entry button{padding:0;width:44px;flex:none;display:grid;place-items:center;border-radius:50%}.send{background:var(--accent);color:var(--on-accent)}.send:disabled{opacity:.4}.entry svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}.hint{font-size:13px;line-height:18px;color:var(--text-muted);padding:6px 4px 0}.hint.error{color:var(--danger)}
.nav{height:84px;flex:none;background:var(--page);border-top:1px solid var(--hairline);display:flex;padding-bottom:26px}.nav button{width:25%;font-size:11px;line-height:1.4;padding:4px 0}.nav svg{display:block;margin:0 auto 2px;width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.7}.nav .selected{color:var(--accent-text)}.nav b{background:var(--accent);color:var(--on-accent);border-radius:20px;padding:1px 4px;font-size:10px}
.keyboard-open .nav{display:none}.keyboard{height:334px;flex:none;background:#d9dde3;padding:12px 6px;color:#172033;display:none}.keyboard-open .keyboard{display:block}.keyboard p{font-size:12px;text-align:center;margin:0 0 14px}.keys{display:flex;gap:5px;justify-content:center;margin-bottom:10px}.keys span{background:#fff;box-shadow:0 1px 1px #89929d;border-radius:5px;flex:1;text-align:center;padding:9px 0;line-height:22px}.keys.mid{padding:0 14px}.keys.bottom{padding:0 40px}.keys .space{flex:5}.keyboard .dismiss{display:block;margin:14px auto 0;background:#c3c9d1;font-size:13px;padding:0 15px}
.keyboard-open .scrim{bottom:334px}
.wave{display:flex;gap:3px;align-items:center;flex:1;justify-content:center;height:44px;color:var(--accent-text)}.wave i{width:3px;background:currentColor;border-radius:3px}.timer{font:13px var(--font-mono)}.entry .word{width:auto;padding:0 8px;font-size:14px;border-radius:12px}.entry .stop{background:var(--bubble)}
.identity .held{color:var(--chip-claimed-text)}.fault-notice{color:var(--danger);background:var(--surface);border-bottom:1px solid var(--hairline)}.fault-notice button{color:var(--danger)}
.question-card{border:1px solid var(--chip-claimed-border);border-radius:12px;padding:16px;background:var(--card);scroll-margin-top:20px}.question-card h2{font-size:16px;line-height:1.5;margin:0 0 10px}.question-card p{font-size:14px;margin:8px 0}.question-card small{color:var(--chip-claimed-text)}.task-jump{display:flex;justify-content:flex-end;min-height:44px}.task-jump button{color:var(--accent-text);font-size:13px}
.question-card[data-internal]{border-color:var(--border)}.question-card[data-internal] small{color:var(--text-muted)}.desktop-actions{display:none}.phone[data-desktop] .head{position:relative}.phone[data-desktop] .desktop-actions{display:flex;gap:4px;position:absolute;right:190px;top:8px}
.sheet h3{font-size:15px;margin:20px 0 8px}.sheet .reason{overflow-wrap:anywhere}.sheet .merge-reason{border-top:1px solid var(--border)}.review-rail,.desktop-meta,.desktop-live,.desktop-hint{display:none}
.phone[data-desktop]{width:1440px;height:900px;display:grid;grid-template-columns:260px minmax(0,1fr)}.phone[data-desktop] .app{height:900px}.phone[data-desktop] .review-rail{display:flex;flex-direction:column;gap:12px;padding:24px 16px;background:var(--page);border-right:1px solid var(--border)}.review-rail strong{padding:0 12px;font-size:17px}.review-rail button{text-align:left}.review-rail .selected{background:var(--accent-tint);color:var(--accent-text)}.review-rail .bottom{margin-top:auto}
.phone[data-desktop] .reply,.phone[data-desktop] .bubble,.phone[data-desktop] textarea{font-size:15px}
.phone[data-desktop] .head{height:170px;padding:8px 28px;display:grid;grid-template-columns:1fr auto 44px;gap:0 8px}.phone[data-desktop] .back{text-align:left;color:var(--text-muted);padding:0}.phone[data-desktop] .desktop-live{display:block;grid-column:2;grid-row:1}.phone[data-desktop] .more{grid-column:3;grid-row:1}.phone[data-desktop] .identity{grid-row:2;grid-column:1 / -1}.phone[data-desktop] .identity strong{font-size:24px}.phone[data-desktop] .identity small{font-size:14px;margin-top:6px}.phone[data-desktop] .desktop-meta{display:block;grid-row:3;grid-column:1 / -1;color:var(--text-muted);font-size:13px}.phone[data-desktop] .tabs,.phone[data-desktop] .nav{display:none}.phone[data-desktop] .messages{padding:12px 28px 8px}.phone[data-desktop] .flow{width:100%;max-width:720px;margin-left:auto;margin-right:auto}.phone[data-desktop] .dock{width:100%;max-width:776px;align-self:center;padding:8px 28px 12px}.phone[data-desktop] .entry{flex-wrap:wrap;padding:10px 12px;border-radius:24px}.phone[data-desktop] textarea{flex-basis:100%;padding:0 4px;height:24px!important}.phone[data-desktop] .mic{margin-left:auto}.phone[data-desktop] .desktop-hint{display:block;text-align:center;font-size:12px;color:var(--text-muted);margin-top:6px}.phone[data-desktop] .sheet{width:520px;max-height:800px;border-radius:16px;padding:20px 24px}.phone[data-desktop] .scrim{align-items:center;justify-content:center}
.scrim{position:absolute;inset:0;background:var(--scrim);z-index:2;display:flex;align-items:flex-end}.sheet{width:100%;background:var(--card);padding:16px 20px 26px;border-radius:20px 20px 0 0;max-height:calc(100% - 54px);overflow:auto}.sheet h2{font-size:18px;margin:0}.sheet .close{float:right}.sheet p{margin:12px 0;font-size:14px}.sheet label{display:flex;align-items:center;gap:12px;font-size:14px}.sheet select{height:44px;border:1px solid var(--border);border-radius:10px;background:var(--card)}.sheet .action{display:block;width:100%;text-align:left;border-top:1px solid var(--hairline);border-radius:0}.sheet .danger{color:var(--danger)}[hidden]{display:none!important}
'''

JS = r'''
const params=new URLSearchParams(location.search),scene=params.get('scene')||(location.pathname.includes('TaskStatusProposal')?'task-blocked-held':'read');
const task=scene.startsWith('task'),busy=scene==='busy',error=scene==='error',voice=scene==='voice',denied=scene==='denied',transcribing=scene==='transcribing';
const phone=document.querySelector('.phone'),field=document.querySelector('textarea'),nav=document.querySelector('.nav');
const desktop=phone.hasAttribute('data-desktop');
const keyboard=!desktop&&(['type','busy','error','multiline','task-type'].includes(scene)||params.has('keyboard'));
const mergeHeld=['task-held','task-blocked-held','task-question-held','task-fault-held'].includes(scene);
const waitingL3=['task-blocked','task-blocked-held'].includes(scene),questionHeld=scene==='task-question-held',faultHeld=scene==='task-fault-held',paused=scene==='task-paused';
const waiting=waitingL3||questionHeld||faultHeld||paused;
const mergeReason='Keep this PR open until the operator reviews the phone and desktop interaction states, checks the rollback plan, and gives separate permission to merge. Design approval alone does not release this hold.';
const blockReason='Confirm whether existing clients must keep the original pagination contract during the index migration, including retries after a saved cursor expires. The coordinator can answer from the agreed rollout constraints.';
const questionReason='How long should the original index remain available for rollback after the new index passes validation? Consider delayed client retries and the time needed to inspect recovery evidence before allowing deletion.';
const faultReason='The isolated verification browser could not start. L3 has been told and will arrange recovery.';
const pausedReason='Task stopped for operational review. Resume when the existing verification can continue.';
const state=waitingL3?'Waits for L3':questionHeld?'Needs your answer':faultHeld?'Blocked by a fault':paused?'Paused':'Running';
document.querySelector('.desktop-actions').innerHTML=((faultHeld||paused)?'<button>Resume</button>':'')+(!waiting?'<button>Stop…</button>':'')+'<button>Reject…</button>';
phone.classList.toggle('keyboard-open',keyboard);
document.querySelector('.decisions').hidden=scene!=='question';
document.querySelector('.nav b').hidden=scene!=='question';
document.querySelector('.identity strong').textContent=task?'Prepare index migration':'atlas ⌄';
document.querySelector('.identity small').textContent=task?'L2 · Running':busy?'L3 · Answering':'L3 · Ready';
if(task)document.querySelector('.identity small').innerHTML='L2 · '+state+(mergeHeld?' · <span class="held">Merge held</span>':'');
document.querySelector('.back').hidden=!task;document.querySelector('.tabs').hidden=!task;
if(desktop)document.querySelector('.back').textContent='‹ atlas';
document.querySelector('.fault-notice').hidden=!faultHeld;
const questionCard=document.querySelector('.question-card'),jump=document.querySelector('.task-jump');
questionCard.hidden=!(questionHeld||waitingL3);jump.hidden=!(questionHeld||waitingL3);
questionCard.toggleAttribute('data-internal',waitingL3);
document.querySelector('.question-card h2').textContent=waitingL3?'Waiting for L3’s answer':'Question for you';
document.querySelector('.question-card small').textContent=waitingL3?'L2 · L3 can answer':'L2 · Awaiting your answer';
document.querySelector('.question-context').textContent=waitingL3?blockReason:questionReason;
field.placeholder=task?'Message the L2':'Message L3';field.setAttribute('aria-label',field.placeholder);
field.value=keyboard?'Include the rollback steps.':'';
document.querySelector('.queued').hidden=!busy;document.querySelector('.active').hidden=!busy;
document.querySelector('.notice').hidden=!error;
const hint=document.querySelector('.hint');hint.hidden=!error&&!denied&&!voice&&!transcribing;
hint.textContent=error?'Not sent. Retry with the send arrow.':denied?'Microphone blocked in the browser. Typing works.':voice?'Listening · Stop to edit, or send when ready.':transcribing?'Transcribing… Typing works.':'';
hint.classList.toggle('error',error);document.querySelector('.mic').disabled=denied;
if(scene==='multiline')field.value='Include the rollback steps.\nKeep the old index available.\nCheck pagination before switching.\nHold the final switch for review.\nRecord how to restore traffic.\nKeep the evidence with the task.';
function grow(){field.style.height='44px';field.style.height=Math.min(120,Math.max(44,field.scrollHeight))+'px';document.querySelector('.send').disabled=!field.value||transcribing;}
field.oninput=grow;grow();
if(transcribing){document.querySelector('.mic').disabled=true;document.querySelector('.send').disabled=true;field.placeholder='Message L3';}
if(busy)document.querySelector('.send').setAttribute('aria-label','Queue');
if(voice){field.hidden=true;document.querySelector('.mic').hidden=true;document.querySelector('.voice-controls').hidden=false;document.querySelector('.send').disabled=false;}
document.querySelector('.dismiss').onpointerdown=e=>e.preventDefault();
document.querySelector('.dismiss').onclick=()=>{const start=field.selectionStart,end=field.selectionEnd;phone.classList.remove('keyboard-open');field.focus({preventScroll:true});field.setSelectionRange(start,end);};
const overlay=document.querySelector('.scrim'),sheet=document.querySelector('.sheet');let opener;
function close(){overlay.hidden=true;opener?.focus();}
function open(kind,button){opener=button;overlay.hidden=false;
let content;
if(kind==='project')content='<h2>Switch project</h2><p>atlas is selected.</p><button class="action">harbor</button>';
else if(kind==='notice')content='<h2>Update ready</h2><p>The backend and web app changed · 2 files · landed just now.</p><p>Altitude restarts at the next quiet moment. Restart is available while it is safe to activate.</p><button class="action">Restart</button>';
else if(task){
 content='<h2>Task details</h2><p>Prepare index migration</p><p>'+state+' · attempt 1</p>';
 if(waiting)content+='<section aria-label="Why this task is waiting"><h3>'+state+'</h3><p class="reason">'+(waitingL3?blockReason:questionHeld?questionReason:faultHeld?faultReason:pausedReason)+'</p>'+(questionHeld?'<button class="action view-question">View question</button>':'')+'</section>';
 if(mergeHeld)content+='<section class="merge-reason" aria-label="Merge hold"><h3>Merge held</h3><p class="reason">'+mergeReason+'</p></section>';
 content+='<p>Configured engine · model not observed</p><p>Token usage unknown · no reading available</p><button class="action">Observed token details ⌄</button>'+((faultHeld||paused)?'<button class="action">Resume</button>':'')+(!waiting?'<button class="action">Stop…</button>':'')+'<button class="action danger">Reject…</button>';
}
else content='<h2>Project details</h2><p>L3 answered just now · configured engine</p><p>2 tasks in flight · '+(scene==='question'?'2 decisions need you':'no decisions need you')+'</p><label>L3 engine <select aria-label="L3 engine"><option>Auto</option><option>Configured engine</option></select></label><p>Auto chooses an available configured engine.</p><button class="action">Design boards ↗</button><button class="action">Reset L3 conversation…</button><button class="action danger">Remove project…</button>';
sheet.innerHTML='<button class="close" aria-label="Close details">✕</button>'+content;sheet.querySelector('.close').onclick=close;sheet.querySelector('.view-question')?.addEventListener('click',()=>{close();viewQuestion();});sheet.querySelector('.close').focus();}
document.querySelector('.more').onclick=e=>open('details',e.currentTarget);
document.querySelector('.identity').onclick=e=>open(task?'details':'project',e.currentTarget);
document.querySelector('.notice-details').onclick=e=>open('notice',e.currentTarget);
document.querySelector('.fault-details').onclick=e=>open('details',e.currentTarget);
overlay.onclick=e=>{if(e.target===overlay)close();};
document.addEventListener('keydown',e=>{if(overlay.hidden)return;if(e.key==='Escape')close();if(e.key==='Tab'){const nodes=[...sheet.querySelectorAll('button,select')];const first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){last.focus();e.preventDefault();}else if(!e.shiftKey&&document.activeElement===last){first.focus();e.preventDefault();}}});
const messages=document.querySelector('.messages');
function viewQuestion(){messages.scrollTop+=questionCard.getBoundingClientRect().top-messages.getBoundingClientRect().top-20;questionCard.focus({preventScroll:true});updateJump();}
function updateJump(){if(!(questionHeld||waitingL3))return;const card=questionCard.getBoundingClientRect(),area=messages.getBoundingClientRect();const visible=card.top>=area.top&&card.top<area.bottom,atBottom=messages.scrollHeight-messages.scrollTop-messages.clientHeight<48;document.querySelector('.jump-question').hidden=visible;document.querySelector('.jump-latest').hidden=atBottom;jump.hidden=visible&&atBottom;}
document.querySelector('.jump-question').onclick=viewQuestion;document.querySelector('.jump-latest').onclick=()=>{messages.scrollTop=messages.scrollHeight;updateJump();};messages.addEventListener('scroll',updateJump);messages.scrollTop=1e6;updateJump();
if(params.has('dark'))document.body.dataset.theme='dark';
'''

def generate(out, board, icon):
    (out / 'mobile-chat.css').write_text(CSS)
    (out / 'mobile-chat.js').write_text(JS)
    arrow='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6"/></svg>'
    mic='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0m-7 0v3"/></svg>'
    content='''<div class="phone"><aside class="review-rail"><strong>Altitude</strong><button>Needs you</button><small>Projects</small><button class="selected">atlas</button><button>harbor</button><button class="bottom">Monitor</button></aside><div class="app">
<header class="head"><button class="back" aria-label="Back" hidden>‹</button><button class="identity" aria-haspopup="dialog"><strong>Atlas ⌄</strong><small>L3 · Ready</small></button><button class="decisions" aria-label="2 decisions need you">Needs you <b>2</b></button><button class="more" aria-label="Details and actions" aria-haspopup="dialog">⋯</button><div class="desktop-actions"></div><button class="desktop-live">Live session</button><div class="desktop-meta">Attempt 1 · configured engine · token usage unknown</div></header>
<div class="notice" hidden><span>Update ready</span><button class="notice-details">Details</button><button>Restart</button></div>
<div class="notice fault-notice" role="status" hidden><span>Verification browser could not start.<br>L3 has been told.</span><button class="fault-details">Details</button></div>
<div class="tabs" hidden><button class="selected">Conversation</button><button>Live session</button></div>
<div class="messages" role="region" aria-label="Conversation"><div class="flow">
<div class="day">Today</div><div class="bubble">Can we keep the existing clients working during the index change?</div>
<div class="reply"><p>Yes. Existing clients keep their current response format while the new index is built.</p><p>The backfill runs separately and resumes from its last saved checkpoint. We can inspect the results before switching traffic.</p><p>I’ll keep the compatibility and recovery work in separate tasks so each change is reviewable.</p></div>
<article class="question-card" aria-label="Pending question" tabindex="-1" hidden><small>L2 · Awaiting your answer</small><h2>Question for you</h2><p class="question-context"></p><p>Discuss in the conversation below.</p></article>
<div class="bubble">Keep the rollout reversible.</div><div class="reply"><p>I’ll keep the existing index available until the new results are verified. The final switch stays held for your review.</p></div>
<div class="active" hidden>● L3 is answering</div><div class="queued" hidden><small>Queued · runs next</small><button>Remove</button><div>Also check pagination.</div></div>
</div></div><div class="dock"><div class="task-jump" hidden><button class="jump-question">View question</button><button class="jump-latest" hidden>Latest messages</button></div><div class="entry"><textarea rows="1" aria-label="Message L3" placeholder="Message L3"></textarea><button class="mic" aria-label="Start voice input">'''+mic+'''</button><div class="voice-controls" style="display:flex;align-items:center;flex:1" hidden><button class="word" aria-label="Cancel voice input">Cancel</button><div class="wave" aria-hidden="true">'''+''.join(f'<i style="height:{v}px"></i>' for v in [8,16,23,11,28,17,25,12])+'''</div><span class="timer">0:12</span><button class="word stop" aria-label="Stop voice input">Stop</button></div><button class="send" aria-label="Send" disabled>'''+arrow+'''</button></div><div class="hint" role="status" hidden></div><div class="desktop-hint">Message the L2 · Shift + Enter for a new line.</div></div>
<nav class="nav" aria-label="Main navigation"><button class="selected">'''+icon('chat')+'''Chat</button><button>'''+icon('work')+'''Work</button><button>'''+icon('tray')+'''Needs you <b>2</b></button><button>'''+icon('pulse')+'''Monitor</button></nav>
</div><div class="keyboard"><p>Illustrative keyboard · 334px</p><div class="keys">'''+''.join('<span>'+c+'</span>' for c in 'qwertyuiop')+'''</div><div class="keys mid">'''+''.join('<span>'+c+'</span>' for c in 'asdfghjkl')+'''</div><div class="keys bottom">'''+''.join('<span>'+c+'</span>' for c in 'zxcvbnm')+'''</div><div class="keys"><span>123</span><span class="space">space</span><span>return</span></div><button class="dismiss">Dismiss keyboard</button></div><div class="scrim" hidden><section class="sheet" role="dialog" aria-modal="true" aria-label="Chat details"></section></div></div>'''
    for name,w,h,body in [('MobileChatProposal',390,844,content),('TaskStatusProposal',1440,900,content.replace('class="phone"','class="phone" data-desktop',1))]:
        board(name,w,h,body)
        path=out / (name+'.html')
        html=path.read_text().replace('</head>','<meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="mobile-chat.css"></head>').replace('</body>','<script src="mobile-chat.js"></script></body>')
        path.write_text(html)
    return [('Compact mobile chat · approved 9 September','TaskStatusProposal','MobileChatProposal')]
