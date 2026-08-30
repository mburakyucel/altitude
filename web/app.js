/* Altitude web app — vanilla JS, polls the JSON API. */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const age = (iso) => { if(!iso) return ""; const s=(Date.now()-new Date(iso).getTime())/1000; return s<3600?`${Math.floor(s/60)}m`:s<86400?`${Math.floor(s/3600)}h`:`${Math.floor(s/86400)}d`; };
const state = { tab: "inbox", project: null, overview: null, timer: null, cards: {} };
try { state.tab = localStorage.getItem("alt.tab") || "inbox"; state.project = localStorage.getItem("alt.project") || null; } catch (e) {}
const api = async (path, body) => { const r = await fetch(path, body ? {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)} : {}); const j = await r.json(); if (j.error) throw new Error(j.error); return j; };

function go(tab, project) { state.tab = tab; if (project !== undefined) state.project = project; try { localStorage.setItem("alt.tab", tab); if (state.project) localStorage.setItem("alt.project", state.project); } catch (e) {} render(); }
function closeModal() { $("#modal").classList.add("hidden"); }
function openModal(html) { $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); }

async function render() {
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === state.tab));
  const v = $("#view");
  try {
    if (state.tab === "inbox") await renderInbox(v);
    else if (state.tab === "projects") await renderProjects(v);
    else if (state.tab === "project") await renderProject(v);
    else if (state.tab === "chat") await renderChat(v);
    else if (state.tab === "monitor") await renderMonitor(v);
    else if (state.tab === "listen") await renderListen(v);
  } catch (e) { v.innerHTML = `<div class="card">${esc(e.message)}</div>`; }
}

async function loadOverview() {
  const o = await api("/api/overview"); state.overview = o;
  const q = o.quota || {}; $("#quota").textContent = q.known ? `5h ${q.five_hour}% · 7d ${q.seven_day}%` : "quota unknown";
  const b = $("#badge-inbox"); b.textContent = o.queue.length; b.classList.toggle("on", o.queue.length > 0);
  return o;
}

/* References a card or a file mentions (I-007, R-003, decision 31) open the ledger entry on tap — the card itself stays plain (decision 46). */
const REF_RE = /\b(I-\d{3}|R-\d{3})\b|\b[Dd]ecisions?\s*#?\s*(\d+)\b|DECISIONS\.md\s*#(\d+)/g;
function linkify(html, project) {
  return html.replace(REF_RE, (m, id, d1, d2) => { const ref = id || ("decision " + (d1 || d2)); return `<a href="#" class="ref" onclick="openRef('${project}','${ref}');return false">${m}</a>`; });
}
async function openRef(project, ref) {
  try { const r = await api(`/api/ref/${encodeURIComponent(project)}/${encodeURIComponent(ref)}`);
    openModal(`<div class="row"><span class="pill">${esc(r.kind)}</span><b class="grow">${esc(r.title)}</b><button class="btn small" onclick="closeModal()">close</button></div><div class="detail">${linkify(esc(r.text), project)}</div>`);
  } catch (e) { alert(e.message); }
}
function decisionCard(i) {
  state.cards[i.project + "/" + i.slug] = i;
  const opts = (i.options || []).map((o, n) => `<button class="btn ${n===0?'primary':''}" onclick="decide('${i.project}','${i.slug}',${n})">${esc(o)}</button>`).join("");
  return `<div class="card ${i.kind}"><div class="row"><span class="pill ${i.class}">${i.class}</span><b class="grow">${esc(i.title)}</b><span class="muted small">${esc(i.project)} · ${age(i.asked)}</span></div>
    <div style="margin:6px 0">${linkify(esc(i.question), i.project)}</div><div class="options">${opts}</div>
    <div class="row" style="margin-top:6px"><input id="note-${i.slug}" placeholder="note (optional, goes with your choice)">${i.detail?`<button class="btn small" onclick="openDetail('${i.project}','${i.slug}')">why</button>`:""}<button class="btn small" onclick="openTask('${i.project}','${i.slug}')">files</button></div></div>`;
}
function openDetail(project, slug) {
  const i = state.cards[project + "/" + slug]; if (!i) return;
  openModal(`<div class="row"><span class="pill ${i.class}">${i.class}</span><b class="grow">${esc(i.title)}</b><button class="btn small" onclick="closeModal()">close</button></div>
    <div style="margin:6px 0">${linkify(esc(i.question), project)}</div><ol>${(i.options||[]).map(o=>`<li>${esc(o)}</li>`).join("")}</ol>
    <h3>Reasoning</h3><div class="detail">${linkify(esc(i.detail), project)}</div>
    <div class="row" style="margin-top:8px"><button class="btn small" onclick="openTask('${project}','${slug}')">task files</button></div>`);
}

async function renderInbox(v) {
  const o = await loadOverview();
  const w = o.wip;
  v.innerHTML = `<h1>Decisions <span class="muted">(${o.queue.length})</span></h1>` +
    (o.queue.length ? o.queue.map(decisionCard).join("") : `<div class="card muted">Nothing needs you.</div>`) +
    `<h2>Running</h2><div class="card small">${w.machine} orchestrator(s) — ${Object.entries(w.per_project).filter(([p,n])=>n).map(([p,n])=>`${esc(p)} ${n}`).join(", ") || "none"}${w.waiting.length?`<br>waiting for a slot: ${w.waiting.map(x=>esc(x.project+"/"+x.slug+(x.why==="resume"?" (resume)":""))).join(", ")}`:""}</div>` +
    `<h2>FYI</h2>` + (o.fyis.length ? o.fyis.map(f => `<div class="card small"><span class="muted">${esc(f.project)}${f.slug?" · "+esc(f.slug):""} · ${age(f.at)}</span><br>${esc(f.text)}</div>`).join("") : `<div class="card muted">No FYIs yet.</div>`);
}

async function decide(project, slug, option) {
  const note = ($(`#note-${slug}`) || {}).value || "";
  try { await api("/api/decide", {project, slug, option, note}); } catch (e) { alert(e.message); }
  render();
}

async function renderProjects(v) {
  const o = await loadOverview();
  v.innerHTML = `<h1>Projects</h1>` + o.projects.map(p => {
    if (!p.managed) return `<div class="card"><div class="row"><b class="grow">${esc(p.name)}</b><span class="muted small">${p.git?"git":"no git"}</span><button class="btn small" onclick="startL3('${esc(p.name)}')">Start L3</button></div></div>`;
    const c = p.counts || {}; const l3 = p.l3 || {};
    return `<div class="card" onclick="go('project','${esc(p.name)}')" style="cursor:pointer"><div class="row"><b class="grow">${esc(p.name)}</b>${p.hold?`<span class="pill blocked">hold</span>`:""}<span class="muted small">L3 ${l3.session_id?`${l3.context_percent??0}% · ${age(l3.last_turn)}`:"not started"}</span></div>
      <div class="row small muted" style="margin-top:4px">${["blocked","running","reported","proposed","approved","requested","parked"].filter(s=>c[s]).map(s=>`<span class="pill ${s}">${s} ${c[s]}</span>`).join(" ") || "no tasks"}</div></div>`;
  }).join("");
}

async function startL3(name) {
  const stacks = prompt(`Stacks for ${name} (comma-separated, e.g. python,cdk) — optional:`, "") ;
  if (stacks === null) return;
  try { await api("/api/project/add", {name, stacks}); go("project", name); } catch (e) { alert(e.message); }
}

function taskCard(p, t) {
  const live = t.live || {}; const a = live.agent || {};
  const env = t.envelope || {}; const sp = t.spend || {};
  const extra = [];
  if (t.state === "running") extra.push(`L2 ${a.status || "?"}${a.state?"/"+a.state:""}`, `ctx ${live.context_percent ?? "?"}%`, `agents ${live.subagent_launches ?? 0}/${env.subagent_launches ?? "?"}`, `edits ${live.edits ?? 0}`);
  if (t.prs && t.prs.length) extra.push("PRs " + t.prs.map(n => `#${n}`).join(" "));
  if (t.state === "blocked" && t.resume_after) extra.push("queued: Altitude resumes this L2 itself when the WIP / one-rule-task-at-a-time hold clears (" + (t.blocked_reason || "") + ")");
  else if (t.blocked_reason) extra.push("blocked: " + t.blocked_reason);
  const btns = [];
  if (t.state === "requested") btns.push(`<button class="btn small" onclick="act('${p}','${t.slug}','propose')">propose</button>`);
  if (["requested","parked","proposed"].includes(t.state)) btns.push(`<button class="btn small primary" title="Executive override: approve as requested and dispatch now, skipping the proposal/critic loop" onclick="act('${p}','${t.slug}','build')">build now</button>`);
  if (t.state === "approved") btns.push(`<button class="btn small" onclick="act('${p}','${t.slug}','dispatch')">dispatch</button>`);
  if (t.state === "parked") btns.push(`<button class="btn small" onclick="act('${p}','${t.slug}','unpark')">unpark</button>`);
  if (["running","blocked"].includes(t.state)) btns.push(`<button class="btn small" onclick="messageL2('${p}','${t.slug}')">message L2</button>`);
  if (["requested","proposed","approved","blocked","reported"].includes(t.state)) btns.push(`<button class="btn small" onclick="act('${p}','${t.slug}','park')">park</button>`);
  return `<div class="card ${t.state==='blocked'&&!t.resume_after?'blocked':''}"><div class="row"><span class="pill ${t.class}">${t.class}</span><span class="pill ${t.state}">${t.state}</span><b class="grow" style="cursor:pointer" onclick="openTask('${p}','${t.slug}')">${esc(t.title)}</b><span class="muted small">${age(t.updated)}</span></div>
    ${extra.length?`<div class="muted small" style="margin-top:4px">${esc(extra.join(" · "))}</div>`:""}
    ${t.progress_tail?`<details><summary class="small">progress</summary><pre>${esc(t.progress_tail)}</pre></details>`:""}
    <div class="row" style="margin-top:6px">${btns.join("")}<button class="btn small" onclick="openTask('${p}','${t.slug}')">open</button></div></div>`;
}

async function renderProject(v) {
  const name = state.project; if (!name) return go("projects");
  const d = await api(`/api/project/${encodeURIComponent(name)}`); await loadOverview();
  const l3 = d.l3 || {};
  v.innerHTML = `<div class="row"><button class="btn small" onclick="go('projects')">‹ projects</button><h1 class="grow" style="margin:0">${esc(name)}</h1></div>
    <div class="card"><div class="row"><b>L3</b><span class="grow muted small">${l3.session_id?`session ${l3.session_id.slice(0,8)} · ${l3.turns||0} turns · context ${l3.context_percent??0}% · last ${age(l3.last_turn)}${l3.rotate_next?" · rotates next turn":""}`:"not started yet — send a chat message"}${d.busy?" · <b>thinking…</b>":""}</span>
      <button class="btn small" onclick="go('chat','${esc(name)}')">chat</button><button class="btn small" onclick="resetL3('${esc(name)}')">rotate</button></div>
      <div class="muted small" style="margin-top:4px">stacks: ${esc((d.config.stacks||[]).join(", ")||"—")} · approval: ${esc(d.config.approval)} · WIP ${d.config.wip}${d.hold?` · <span class="pill blocked">${esc(d.hold.reason)}</span>`:""}</div></div>
    <div class="row" style="margin-bottom:10px"><input id="newtask" placeholder="New request for L3 (creates a task)…" class="grow"><select id="newclass" style="width:auto"><option>M</option><option>S</option><option>L</option></select><button class="btn primary small" onclick="newTask('${esc(name)}')">add</button></div>
    ${d.decisions.length?`<h2>Needs you</h2>${d.decisions.map(decisionCard).join("")}`:""}
    <h2>Tasks (${d.tasks.length})</h2>${d.tasks.map(t=>taskCard(name,t)).join("")||`<div class="card muted">No open tasks.</div>`}
    <h2>Recent FYIs</h2>${d.inbox.slice().reverse().slice(0,10).map(f=>`<div class="card small"><span class="muted">${age(f.at)}${f.slug?" · "+esc(f.slug):""}</span><br>${esc(f.text)}</div>`).join("")||`<div class="card muted">none</div>`}
    ${d.incidents.length?`<h2>Incidents</h2>${d.incidents.slice().reverse().map(i=>`<div class="card small"><b>${esc(i.id)}</b> ${esc(i.title)} <span class="muted">${esc((i.tags||[]).join(", "))} → ${esc(i.rule||"incident-only")}</span></div>`).join("")}`:""}
    <details><summary>done / rejected (${d.archive.length})</summary>${d.archive.slice().reverse().map(t=>`<div class="small muted">${esc(t.slug)} [${t.class}] ${t.state} — ${esc(t.title)}</div>`).join("")}</details>
    <details><summary>STATE.md</summary><pre>${esc(d.state_md)}</pre></details>`;
}

async function newTask(project) {
  const title = $("#newtask").value.trim(); if (!title) return;
  try { await api("/api/task/action", {project, slug: "", action: "new", title, class: $("#newclass").value, request: title}); $("#newtask").value = ""; } catch (e) { alert(e.message); }
  render();
}
async function act(project, slug, action) { const reason = ["park","reject"].includes(action) ? (prompt("Reason?") || "") : ""; if (["park","reject"].includes(action) && reason === "") return; try { await api("/api/task/action", {project, slug, action, reason}); } catch (e) { alert(e.message); } render(); }
async function resetL3(project) { await api("/api/l3/reset", {project}); render(); }
async function messageL2(project, slug) { const text = prompt("Message to the L2 (it resumes the session with this):"); if (!text) return; try { const r = await api("/api/l2/message", {project, slug, text}); alert(r.stderr || r.stdout || "sent"); } catch (e) { alert(e.message); } }

async function openTask(project, slug) {
  const t = await api(`/api/task/${encodeURIComponent(project)}/${encodeURIComponent(slug)}`);
  const files = Object.entries(t.files || {}).map(([k, v]) => `<details ${k==='report'||k==='proposal'?'open':''}><summary>${k}.md</summary><pre>${linkify(esc(v), project)}</pre></details>`).join("");
  openModal(`<div class="row"><span class="pill ${t.class}">${t.class}</span><span class="pill ${t.state}">${t.state}</span><b class="grow">${esc(t.title)}</b><button class="btn small" onclick="closeModal()">close</button></div>
    <div class="muted small">${esc(slug)} · dispatch ${esc(t.dispatch_id||"—")} · session ${esc((t.session_id||"—").slice(0,8))} · worktree ${esc(t.worktree||"—")}${t.dispatch_id?`<br>attach: <code>claude attach ${esc(t.agent_id||"")}</code>`:""}</div>
    ${t.critique?`<details><summary>critique (${esc(t.critique.verdict)})</summary><pre>${esc(JSON.stringify(t.critique,null,1))}</pre></details>`:""}
    ${files}<details><summary>events (${t.events.length})</summary><pre>${esc(t.events.map(e=>`${e.at} ${e.kind} ${JSON.stringify(Object.fromEntries(Object.entries(e).filter(([k])=>!["at","kind"].includes(k))))}`).join("\n"))}</pre></details>`);
}

async function renderChat(v) {
  const o = await loadOverview();
  const managed = o.projects.filter(p => p.managed);
  if (!state.project || !managed.find(p => p.name === state.project)) state.project = managed[0]?.name || null;
  if (!state.project) { v.innerHTML = `<div class="card">Start an L3 on a project first (Projects tab).</div>`; return; }
  const c = await api(`/api/chat/${encodeURIComponent(state.project)}`);
  v.innerHTML = `<div class="tabs">${managed.map(p=>`<button class="btn small ${p.name===state.project?'on':''}" onclick="go('chat','${esc(p.name)}')">${esc(p.name)}</button>`).join("")}</div>
    <div class="chat" id="chatlog">${c.history.map(m=>`<div class="msg ${m.role}"><div class="meta">${m.role==='user'?'you':'L3'} · ${esc(m.trigger||'')} · ${age(m.at)}${m.context_percent!=null?` · ctx ${m.context_percent}%`:''}</div>${esc(m.text)}</div>`).join("")}</div>
    <div class="chatbox"><textarea id="chatin" placeholder="Talk to L3 — or /idea … , /backlog"></textarea><button class="btn primary" id="sendbtn" onclick="sendChat()" ${c.busy?"disabled":""}>${c.busy?"busy":"send"}</button></div>`;
  window.scrollTo(0, document.body.scrollHeight);
}

async function sendChat() {
  const ta = $("#chatin"); const text = ta.value.trim(); if (!text) return;
  ta.value = ""; $("#sendbtn").disabled = true;
  const log = $("#chatlog");
  log.insertAdjacentHTML("beforeend", `<div class="msg user"><div class="meta">you</div>${esc(text)}</div><div class="msg assistant" id="streaming"><div class="meta">L3</div><span id="stream-text"></span></div>`);
  window.scrollTo(0, document.body.scrollHeight);
  try {
    const r = await fetch("/api/chat", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({project: state.project, text})});
    if (!r.ok) { const j = await r.json(); throw new Error(j.error || r.statusText); }
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    while (true) { const {value, done} = await reader.read(); if (done) break; buf += dec.decode(value, {stream: true});
      let i; while ((i = buf.indexOf("\n")) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 1); if (!line.trim()) continue;
        const o = JSON.parse(line); if (o.t) { $("#stream-text").textContent += o.t; window.scrollTo(0, document.body.scrollHeight); }
        if (o.done && o.done.error) $("#stream-text").textContent += `\n[error] ${o.done.error}`; } }
  } catch (e) { $("#stream-text").textContent += `\n[error] ${e.message}`; }
  setTimeout(render, 400);
}

async function renderMonitor(v) {
  const m = await api("/api/monitor"); await loadOverview();
  const bar = (p) => `<div class="bar"><i class="${p>=70?'bad':p>=55?'warn':''}" style="width:${Math.min(100,p||0)}%"></i></div>`;
  const q = m.quota;
  v.innerHTML = `<h1>Monitor</h1><div class="card"><b>Seat quota</b> ${q.known?`<div class="small muted">5-hour window ${q.five_hour}%</div>${bar(q.five_hour)}<div class="small muted">7-day ${q.seven_day}%</div>${bar(q.seven_day)}`:`<div class="muted small">unknown — needs the statusline wrapper (<code>alt install-statusline</code>) and one interactive session</div>`}</div>
    <h2>Sessions</h2>${m.sessions.map(s=>`<div class="card small"><div class="row"><span class="pill">${esc(s.kind)}</span><b class="grow">${esc(s.project||"")}${s.slug?" / "+esc(s.slug):""}${s.cwd?" "+esc(s.cwd):""}</b><span class="muted">${age(s.at)}</span></div>
      <div class="muted">context ${s.context_percent??"?"}%${s.subagent_launches!=null?` · agents ${s.subagent_launches}/${s.cap??"?"} · edits ${s.edits}`:""}${s.agent?` · ${esc(s.agent.status||"")} ${esc(s.agent.state||"")}`:""}${s.rotate_next?" · rotating":""}</div>${bar(s.context_percent||0)}</div>`).join("")||`<div class="card muted">no sessions</div>`}
    <h2>claude agents</h2><pre>${esc(m.agents.map(a=>`${(a.id||"").slice(0,8)} ${a.name||""} ${a.status||""} ${a.state||""} ${a.cwd||""}`).join("\n")||"none")}</pre>`;
}

async function renderListen(v) {
  const d = await api("/api/digest");
  v.innerHTML = `<h1>Listen</h1><div class="card">${d.audio?`<audio controls src="/digest.wav?${Date.now()}" style="width:100%"></audio>`:`<div class="muted small">no rendered digest yet</div>`}<div class="row" style="margin-top:8px"><button class="btn" onclick="api('/api/digest/speak',{}).then(()=>setTimeout(render,4000))">render with Kokoro</button></div></div><pre>${esc(d.text)}</pre>`;
}

render();
setInterval(() => { if (!$("#modal").classList.contains("hidden")) return; if (state.tab === "chat" && $("#sendbtn") && $("#sendbtn").disabled) return; render(); }, 20000);
