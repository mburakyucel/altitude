
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
