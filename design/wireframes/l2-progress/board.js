// Fictional interaction study only. No application requests, provider calls or microphone access.
const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
let state = params.get('state') || 'running';
let listening = false;
let stopped = state === 'stopped';
if (params.has('dark')) document.body.dataset.theme = 'dark';
if (params.has('compact')) document.body.classList.add('compact');
const draft = $('draft');
const defaultNote = $('note').textContent;
function live(open) {
  $('live').hidden = !open;
  document.body.classList.toggle('live-open', open);
  $('live-tab').setAttribute('aria-selected', String(open));
  $('conversation-tab').setAttribute('aria-selected', String(!open));
}
function sendEnabled() { $('send').disabled = !draft.value.trim() || ['stopping', 'stop-error', 'denied', 'sending'].includes(state); }
function setState(next) {
  const history = $('history');
  const following = history.scrollHeight - history.scrollTop - history.clientHeight < 32;
  state = next;
  stopped = state === 'stopped';
  document.body.classList.toggle('quiet', ['quiet', 'unavailable', 'stopped', 'loading', 'no-commentary'].includes(state));
  $('note').textContent = defaultNote;
  $('activity-title').textContent = 'Latest from L2';
  $('note-age').textContent = '2 min ago';
  $('activity-detail').textContent = 'Tool output observed · 8 sec ago';
  $('hint').textContent = 'Messages wait for a checkpoint. Stop to redirect now.';
  $('worker-stop').hidden = ['stopped', 'resuming', 'blocked', 'finished'].includes(state);
  $('worker-stop').disabled = ['stopping', 'denied'].includes(state);
  $('live-stop').hidden = $('worker-stop').hidden;
  $('live-stop').disabled = $('worker-stop').disabled;
  $('continue').hidden = !stopped;
  $('retry').hidden = !['stop-error', 'unavailable'].includes(state);
  $('retry').textContent = state === 'stop-error' ? 'Check status' : 'Retry';
  $('question').hidden = state !== 'blocked';
  $('result').hidden = state !== 'finished';
  $('activity').hidden = ['blocked', 'finished'].includes(state);
  $('composer').hidden = state === 'finished';
  $('hint').hidden = state === 'finished';
  $('task-state').textContent = ({ stopped: 'Stopped', stopping: 'Stopping…', resuming: 'Waiting to resume', blocked: 'Needs your answer', finished: 'Finished' })[state] || 'Running';
  $('compact-state').textContent = $('task-state').textContent;
  if (['queued', 'received', 'sending', 'unconfirmed'].includes(state)) $('steering-row').hidden = false;
  const receipt = ({ queued: 'Queued · waiting for a checkpoint', received: 'Delivered to session · 10:42', sending: 'Sending…', unconfirmed: 'Delivery unconfirmed' })[state];
  if (receipt) $('receipt').textContent = receipt;
  if (state === 'quiet') { $('note-age').textContent = '7 min ago'; $('activity-detail').textContent = 'No new activity for 4 min'; $('activity-title').textContent = 'Last update'; }
  if (state === 'no-commentary') { $('note').textContent = 'No public update yet.'; $('note-age').textContent = ''; }
  if (state === 'unavailable') { $('activity-title').textContent = 'Activity unavailable'; $('note').textContent = 'Last known update: ' + defaultNote; $('activity-detail').textContent = 'Connection lost · last checked 2 min ago'; }
  if (state === 'loading') { $('activity-title').textContent = 'Reading activity…'; $('note').textContent = 'Waiting for the session records.'; $('note-age').textContent = ''; $('activity-detail').textContent = 'No observation yet'; }
  if (state === 'stopping') { $('activity-title').textContent = 'Stopping…'; $('activity-detail').textContent = 'Waiting for the worker to end'; $('hint').textContent = 'Keep editing your draft. Send after the worker stops.'; }
  if (stopped) { $('activity-title').textContent = 'Stopped'; $('note').textContent = 'Your edits and this session are kept.'; $('note-age').textContent = ''; $('activity-detail').textContent = 'Queued messages wait until you continue'; $('hint').textContent = 'Send a correction to continue this session.'; }
  if (state === 'resuming') { $('activity-title').textContent = 'Waiting to resume'; $('note').textContent = 'Waiting to continue this saved session.'; $('note-age').textContent = ''; $('activity-detail').textContent = 'Waiting for a worker slot'; $('hint').textContent = 'You can add another message while it waits.'; }
  if (state === 'resumed') { $('note').textContent = 'I’ll check the old cursor format first, keeping the existing edits.'; $('note-age').textContent = 'Just now'; $('activity-detail').textContent = 'New session output observed'; }
  if (state === 'stop-error') { $('activity-title').textContent = 'Stop unconfirmed'; $('activity-detail').textContent = 'The worker may still be running'; $('hint').textContent = 'Check task status before continuing. Your draft is kept.'; }
  if (state === 'denied') { $('hint').textContent = 'Action denied. Your draft is kept.'; }
  if (state === 'blocked') $('hint').textContent = 'Reply or ask a question. Discussion keeps the decision open.';
  if (state === 'refused') $('hint').textContent = 'Not sent. Retry.';
  if (state === 'mic-denied') { $('mic').disabled = true; $('hint').textContent = 'Microphone blocked in the browser. Typing works.'; }
  if (state === 'voice-unavailable') $('mic').hidden = true;
  sendEnabled();
  if (following) history.scrollTop = history.scrollHeight;
}
function stop() { if (!['stopping', 'stopped', 'resuming', 'blocked', 'finished', 'denied'].includes(state)) setState('stopping'); }
function voice(on) { listening = on; $('voice-row').hidden = !on; draft.hidden = on; $('mic').hidden = on; }
$('worker-stop').onclick = stop;
$('live-stop').onclick = stop;
$('continue').onclick = () => setState('resuming');
$('retry').onclick = () => setState(state === 'stop-error' ? 'stopping' : 'running');
$('expand').onclick = () => { const expanded = $('activity').classList.toggle('expanded'); $('expand').setAttribute('aria-expanded', String(expanded)); $('expand').setAttribute('aria-label', expanded ? 'Collapse latest update' : 'Expand latest update'); };
$('details').onclick = () => { $('task-details').hidden = !$('task-details').hidden; $('details').setAttribute('aria-expanded', String(!$('task-details').hidden)); };
$('reject').onclick = () => $('reject-dialog').showModal();
$('cancel-reject').onclick = () => $('reject-dialog').close();
$('mic').onclick = () => voice(true);
$('cancel-voice').onclick = () => voice(false);
$('stop-voice').onclick = () => { voice(false); draft.value += (draft.value ? ' ' : '') + 'Check the old cursor format first.'; sendEnabled(); };
draft.oninput = sendEnabled;
$('composer').onsubmit = event => {
  event.preventDefault();
  if ($('send').disabled) return;
  const waitingToResume = stopped || state === 'resuming';
  if (!$('steering-row').hidden) {
    const earlier = $('steering-row').cloneNode(true);
    earlier.removeAttribute('id');
    earlier.querySelectorAll('[id]').forEach(element => element.removeAttribute('id'));
    $('steering-row').before(earlier);
  }
  $('steering-text').textContent = draft.value;
  draft.value = '';
  setState(waitingToResume ? 'resuming' : 'queued');
  $('steering-row').hidden = false;
  $('receipt').textContent = 'Queued · ' + (waitingToResume ? 'waiting to resume' : 'waiting for a checkpoint');
  $('history').scrollTop = $('history').scrollHeight;
};
$('open-live').onclick = () => live(true);
$('live-tab').onclick = () => live(true);
$('conversation-tab').onclick = () => live(false);
$('close-live').onclick = () => live(false);
$('back-chat').onclick = () => live(false);
document.querySelectorAll('.inert').forEach(a => a.onclick = event => event.preventDefault());
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing) return;
  if ($('reject-dialog').open) return; // Native dialog owns Escape.
  if (listening) { event.preventDefault(); voice(false); return; }
  if (!$('task-details').hidden) { $('details').click(); return; }
  if (!$('live').hidden && innerWidth < 1280) { live(false); return; }
  if (innerWidth < 1024 || event.target.closest('input,textarea,select,[contenteditable="true"]')) return;
  event.preventDefault(); stop();
});
setState(state);
$('history').scrollTop = $('history').scrollHeight;
if (['stopped', 'stopping', 'stop-error', 'refused', 'denied'].includes(state)) { draft.value = 'Check the old cursor format first.'; sendEnabled(); }
if (state === 'listening') voice(true);
// The review harness advances observed server events explicitly; buttons never call a real API.
window.observe = state => {
  // A confirmed message handoff is separate from worker activity or task state.
  if (state === 'received') {
    $('receipt').textContent = 'Delivered to session · 10:42';
    $('steering-row').hidden = false;
  } else setState(state);
};
