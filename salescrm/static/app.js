/* Project One — Pipeline. Vanilla JS PWA. */
(() => {
'use strict';

// ── State & helpers ──────────────────────────────────────────────────────────
const S = { me:null, cfg:null, view:'myday', users:[], leadCache:[] };
const $  = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];
const el = (t, c, h) => { const e=document.createElement(t); if(c)e.className=c; if(h!=null)e.innerHTML=h; return e; };
const esc = s => (s==null?'':String(s)).replace(/[&<>"']/g, m => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const money = n => '$' + Math.round(n||0).toLocaleString();

async function api(path, opts={}) {
  const r = await fetch('/api'+path, {
    method: opts.method||'GET',
    headers: opts.body ? {'Content-Type':'application/json'} : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await r.json(); } catch(e) {}
  if (!r.ok) throw new Error((data && data.error) || ('HTTP '+r.status));
  return data;
}

let toastT;
function toast(msg, err=false) {
  const t=$('#toast'); t.textContent=msg; t.classList.toggle('err',err); t.classList.add('show');
  clearTimeout(toastT); toastT=setTimeout(()=>t.classList.remove('show'), 2400);
}

function timeAgo(iso) {
  if(!iso) return '';
  const d=(Date.now()-new Date(iso).getTime())/1000;
  if(d<60) return 'just now';
  if(d<3600) return Math.floor(d/60)+'m ago';
  if(d<86400) return Math.floor(d/3600)+'h ago';
  const days=Math.floor(d/86400);
  return days<30 ? days+'d ago' : Math.floor(days/30)+'mo ago';
}
function dueLabel(iso) {
  if(!iso) return '';
  const diff=(new Date(iso).getTime()-Date.now())/86400000;
  if(diff< -1) return Math.abs(Math.round(diff))+'d overdue';
  if(diff< 0) return 'overdue';
  if(diff< 1) return 'today';
  if(diff< 2) return 'tomorrow';
  return 'in '+Math.round(diff)+'d';
}
const KIND_ICO={call:'📞',text:'💬',email:'✉️',door:'🚪',meeting:'🤝',note:'📝',
  stage_change:'↔️',system:'⚙️'};

// ── Auth ─────────────────────────────────────────────────────────────────────
function showLogin(){ $('#login-screen').classList.add('active'); $('#app-screen').classList.remove('active'); }
function showApp(){ $('#login-screen').classList.remove('active'); $('#app-screen').classList.add('active'); }

async function boot() {
  try {
    const me = await api('/me');
    if (me.authenticated) { S.me=me; await afterLogin(); }
    else showLogin();
  } catch(e){ showLogin(); }
  // prefill invite code from URL
  const p=new URLSearchParams(location.search);
  if(p.get('invite')){ $('#signup-code').value=p.get('invite');
    if(p.get('u')) $('#signup-username').value=p.get('u');
    $('#signup-toggle').click(); }
}

async function afterLogin() {
  showApp();
  S.cfg = await api('/config');
  try { S.users = await api('/users'); } catch(e){ S.users=[]; }
  document.body.classList.toggle('is-mgr', !!S.me.is_manager);
  $$('.mgr-only').forEach(t=>t.classList.toggle('hidden', !S.me.is_manager));
  buildRepSelects();
  const foot=$('#side-user');
  if(foot){
    foot.innerHTML=`<div class="side-title">${esc(S.me.full_name||S.me.username)}</div>
      <div class="side-sub">${esc(S.me.role)} · tap for menu</div>`;
    foot.onclick=()=>$('#menu-btn').click();
  }
  go('myday');
  if('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
}

$('#login-btn').onclick = async () => {
  try {
    S.me = await api('/login', {method:'POST', body:{
      username:$('#login-username').value.trim(), password:$('#login-password').value}});
    await afterLogin();
  } catch(e){ $('#login-error').textContent=e.message; }
};
$('#login-password').addEventListener('keydown',e=>{if(e.key==='Enter')$('#login-btn').click();});
$('#signup-btn').onclick = async () => {
  try {
    S.me = await api('/signup', {method:'POST', body:{
      signup_code:$('#signup-code').value.trim(), username:$('#signup-username').value.trim(),
      full_name:$('#signup-fullname').value.trim(), password:$('#signup-password').value}});
    await afterLogin();
  } catch(e){ $('#signup-error').textContent=e.message; }
};
$('#signup-toggle').onclick=()=>{$('#login-form').classList.add('hidden');$('#signup-form').classList.remove('hidden');};
$('#login-toggle').onclick=()=>{$('#signup-form').classList.add('hidden');$('#login-form').classList.remove('hidden');};

// ── Router ───────────────────────────────────────────────────────────────────
const TITLES={myday:'My Day',pipeline:'Pipeline',partners:'Partners',
  dashboard:'Numbers',coaching:'Coaching',playbook:'Playbook'};
function go(view){
  S.view=view;
  $$('.view').forEach(v=>v.classList.remove('active'));
  $('#view-'+view).classList.add('active');
  $$('.tab').forEach(t=>t.classList.toggle('active',t.dataset.view===view));
  $$('.side-item').forEach(t=>t.classList.toggle('active',t.dataset.view===view));
  $('#view-title').textContent=TITLES[view];
  ({myday:renderMyDay,pipeline:renderPipeline,partners:renderPartners,
    dashboard:renderDashboard,coaching:renderCoaching,playbook:renderPlaybook}[view])();
}
$$('.tab').forEach(t=>t.onclick=()=>go(t.dataset.view));
$$('.side-item').forEach(t=>t.onclick=()=>go(t.dataset.view));

// ── Sidebar: live stage counts + quick-jump ──────────────────────────────────
function updateSidebar(){
  const box=$('#side-stages');
  if(!box||!S.cfg) return;
  const counts={};
  S.leadCache.forEach(l=>counts[l.stage]=(counts[l.stage]||0)+1);
  box.innerHTML=S.cfg.stages.map(s=>`
    <div class="side-stage" data-stage="${s.key}">
      <span class="kcol-dot" style="background:${s.color}"></span>${esc(s.label)}
      <span class="n">${counts[s.key]||0}</span>
    </div>`).join('');
  box.querySelectorAll('.side-stage').forEach(r=>r.onclick=()=>{
    S.stageFocus=r.dataset.stage;
    go('pipeline');
  });
}

function buildRepSelects(){
  const opts='<option value="">All reps</option>'+
    S.users.map(u=>`<option value="${esc(u.username)}">${esc(u.full_name||u.username)}</option>`).join('');
  ['#pipeline-rep','#dash-rep'].forEach(sel=>{ const e=$(sel); if(e){e.innerHTML=opts;
    e.classList.toggle('hidden', !S.me.is_manager);} });
  const coach=$('#coach-rep');
  if(coach) coach.innerHTML=S.users.map(u=>`<option value="${esc(u.username)}">${esc(u.full_name||u.username)}</option>`).join('');
}
function repName(u){ const x=S.users.find(z=>z.username===u); return x&&x.full_name?x.full_name:u; }

// ── My Day ───────────────────────────────────────────────────────────────────
async function renderMyDay(){
  const hour=new Date().getHours();
  const greet=hour<12?'Good morning':hour<17?'Good afternoon':'Good evening';
  $('#myday-greeting').textContent=`${greet}, ${esc(S.me.full_name||S.me.username)} 👋`;
  const [tasks, leads] = await Promise.all([
    api('/tasks?scope=today'),
    api('/leads?limit=1000'),
  ]);
  S.leadCache=leads;
  const open=leads.filter(l=>['won','lost'].indexOf(l.stage)<0);
  const hot=open.filter(l=>l.temperature==='hot');
  const stalled=open.filter(l=>l.stalled);
  const overdue=tasks.filter(t=>t.overdue).length;
  // Each chip is a shortcut: tasks scroll to the list, the rest jump to the board.
  $('#myday-stats').innerHTML=[
    ['Tasks today', tasks.length, 'tasks'],
    ['Overdue', overdue, 'tasks'],
    ['Open leads', open.length, 'pipeline'],
    ['Hot', hot.length, 'pipeline'],
    ['Pipeline', money(open.reduce((s,l)=>s+(l.est_value||0),0)), 'pipeline'],
  ].map(([l,n,nav])=>`<div class="stat-chip" data-nav="${nav}"><div class="n">${n}</div><div class="l">${l}</div></div>`).join('');
  $$('#myday-stats .stat-chip').forEach(c=>c.onclick=()=>{
    if(c.dataset.nav==='pipeline') go('pipeline');
    else $('#myday-tasks').scrollIntoView({behavior:'smooth',block:'start'});
  });
  $('#tasks-count').textContent=tasks.length;
  const badge=$('#side-task-badge');
  if(badge){ badge.textContent=overdue; badge.classList.toggle('hidden', !overdue); }
  updateSidebar();

  $('#myday-tasks').innerHTML = tasks.length ? '' :
    '<div class="empty">All caught up. Add a follow-up so nothing goes cold. 🎯</div>';
  tasks.forEach(t=>$('#myday-tasks').appendChild(taskRow(t)));

  renderMini($('#myday-hot'), hot, 'No hot leads right now.');
  renderMini($('#myday-stalled'), stalled, 'Nothing stalled — nice.');
}
function taskRow(t){
  const row=el('div','task'+(t.overdue?' overdue':''));
  row.innerHTML=`<button class="task-check" title="Complete"></button>
    <div class="task-body"><div class="task-title">${esc(t.title||t.kind)}</div>
    <div class="task-meta"><span class="task-kind">${KIND_ICO[t.kind]||'📌'}</span>
    <span>${esc(t.lead_name)}</span>
    <span class="task-due ${t.overdue?'overdue':''}">${dueLabel(t.due_at)}</span></div></div>`;
  row.querySelector('.task-check').onclick=async(ev)=>{
    ev.stopPropagation();
    await api('/tasks/'+t.id,{method:'PATCH',body:{done:true}});
    toast('Done ✓'); renderMyDay();
  };
  row.querySelector('.task-body').onclick=()=>gotoLead(t.lead_id, t.stage);
  return row;
}
// Jump straight to a lead: pipeline view, its column pulsed, detail open inline.
function gotoLead(id, stage){
  S.stageFocus=stage||null;
  go('pipeline');
  openLead(id);
}
function renderMini(container, leads, emptyMsg){
  container.innerHTML = leads.length ? '' : `<div class="empty">${emptyMsg}</div>`;
  leads.forEach(l=>{
    const row=el('div','mini-lead');
    row.innerHTML=`<span class="temp-dot temp-${esc(l.temperature||'warm')}"></span>
      <div class="nm">${l.service!=='roofing'?l.service_icon+' ':''}${esc(l.name)}</div>
      <div class="sub">${esc(l.stage_label)}${l.est_value?' · '+money(l.est_value):''}</div>`;
    row.onclick=()=>gotoLead(l.id, l.stage);
    container.appendChild(row);
  });
}

// ── Pipeline (kanban) ────────────────────────────────────────────────────────
let pipeSearch='';
$('#pipeline-search').oninput=e=>{pipeSearch=e.target.value.trim().toLowerCase();renderPipeline();};
$('#pipeline-rep').onchange=()=>renderPipeline();
$('#pipeline-service').onchange=()=>renderPipeline();
function buildServiceSelect(){
  const sel=$('#pipeline-service');
  if(sel&&!sel.options.length)
    sel.innerHTML='<option value="">All services</option>'+
      S.cfg.services.map(s=>`<option value="${s.key}">${s.icon} ${esc(s.label)}</option>`).join('');
}
async function renderPipeline(){
  buildServiceSelect();
  const rep=S.me.is_manager ? $('#pipeline-rep').value : '';
  const svc=$('#pipeline-service').value;
  let leads=await api('/leads'+(rep?'?rep='+encodeURIComponent(rep):''));
  S.leadCache=leads;
  if(svc) leads=leads.filter(l=>l.service===svc);
  if(pipeSearch) leads=leads.filter(l=>(l.name+l.phone+l.address+l.company).toLowerCase().includes(pipeSearch));
  const board=$('#kanban'); board.innerHTML='';
  S.cfg.stages.forEach(st=>{
    const col=el('div','kcol'); col.dataset.stage=st.key;
    const items=leads.filter(l=>l.stage===st.key);
    const val=items.reduce((s,l)=>s+(l.est_value||0),0);
    col.innerHTML=`<div class="kcol-head"><span class="kcol-dot" style="background:${st.color}"></span>
      ${esc(st.label)}<span class="kcol-count">${items.length}${val?' · '+money(val):''}</span></div>
      <div class="kcol-body"></div>`;
    const body=col.querySelector('.kcol-body');
    items.forEach(l=>body.appendChild(kcard(l)));
    board.appendChild(col);
  });
  updateSidebar();
  // Keep the open lead's card highlighted across board re-renders.
  if(S.openLeadId){
    const sel=board.querySelector(`.kcard[data-id="${S.openLeadId}"]`);
    if(sel) sel.classList.add('selected');
  }
  // Sidebar/funnel quick-jump: scroll the requested column into view and pulse it.
  if(S.stageFocus){
    const col=board.querySelector(`.kcol[data-stage="${S.stageFocus}"]`);
    S.stageFocus=null;
    if(col){
      col.scrollIntoView({behavior:'smooth',inline:'center',block:'nearest'});
      col.classList.add('pulse');
      setTimeout(()=>col.classList.remove('pulse'),1500);
    }
  }
}
function kcard(l){
  const c=el('div','kcard'); c.dataset.id=l.id;
  const typeMeta=S.cfg.lead_types.find(t=>t.key===l.lead_type);
  let nextChip='';
  if(l.overdue) nextChip=`<div class="chip next overdue">⏰ ${esc(dueLabel(l.next_action_at))}</div>`;
  else if(l.next_action_at) nextChip=`<div class="chip next">Next: ${esc(dueLabel(l.next_action_at))}</div>`;
  else if(l.stalled) nextChip=`<div class="chip stall">⚠ no next step</div>`;
  const svcBadge=l.service!=='roofing'?`<span class="svc-badge" title="${esc(l.service_label)}">${l.service_icon}</span>`:'';
  c.innerHTML=`<div class="kcard-top"><span class="temp-dot temp-${esc(l.temperature||'warm')}"></span>
    <span class="kcard-name">${esc(l.name)}</span>${svcBadge}</div>
    <div class="kcard-sub"><span class="type-badge">${esc(typeMeta?typeMeta.label:l.lead_type)}</span>
    ${l.est_value?`<span class="kcard-val">${money(l.est_value)}</span>`:''}</div>${nextChip}`;
  attachDrag(c, l);
  return c;
}
// Pointer-based drag/drop (works on touch + mouse). Tap (no drag) opens the lead.
// Listeners live on window so the drag keeps tracking once the pointer leaves the
// card; pointer capture is a best-effort enhancement (throws for synthetic events).
function attachDrag(card, lead){
  let sx,sy,dragging=false,clone=null,curCol=null;
  card.addEventListener('pointerdown', down);
  function down(e){
    if(e.button&&e.button!==0) return;
    sx=e.clientX; sy=e.clientY; dragging=false;
    try{ card.setPointerCapture(e.pointerId); }catch(_){}
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  }
  function move(e){
    if(!dragging){
      if(Math.hypot(e.clientX-sx,e.clientY-sy)<8) return;
      dragging=true; card.classList.add('dragging');
      clone=card.cloneNode(true); clone.style.cssText=
        'position:fixed;z-index:100;width:'+card.offsetWidth+'px;pointer-events:none;opacity:.9;box-shadow:0 8px 30px rgba(0,0,0,.5)';
      document.body.appendChild(clone);
    }
    clone.style.left=(e.clientX-clone.offsetWidth/2)+'px';
    clone.style.top=(e.clientY-24)+'px';
    if(clone) clone.style.display='none';
    const under=document.elementFromPoint(e.clientX,e.clientY);
    if(clone) clone.style.display='';
    const col=under&&under.closest('.kcol');
    if(col!==curCol){ if(curCol)curCol.classList.remove('drop'); curCol=col; if(curCol)curCol.classList.add('drop'); }
  }
  async function up(e){
    window.removeEventListener('pointermove',move);
    window.removeEventListener('pointerup',up);
    window.removeEventListener('pointercancel',up);
    if(!dragging){ openLead(lead.id); return; }
    card.classList.remove('dragging');
    if(clone){clone.remove();clone=null;}
    if(curCol) curCol.classList.remove('drop');
    const target=curCol&&curCol.dataset.stage;
    curCol=null;
    if(target && target!==lead.stage) await moveStage(lead, target);
  }
}
async function moveStage(lead, stage){
  try{
    let body={stage};
    if(stage==='lost'){ const reason=prompt('Lost reason (optional):','')||''; body.lost_reason=reason; }
    const res=await api('/leads/'+lead.id+'/stage',{method:'PATCH',body});
    if(stage==='won'){
      if(res.den&&res.den.ok) toast('🎉 Won! Customer + job created in The Den');
      else if(res.den&&!res.den.ok) toast('Won ✓ (Den sync: '+res.den.error+')', true);
      else toast('🎉 Marked Won');
    } else toast('Moved to '+ (S.cfg.stages.find(s=>s.key===stage)||{}).label);
    renderPipeline();
  }catch(e){ toast(e.message,true); renderPipeline(); }
}

// ── Lead detail (inline under Pipeline/Partners; drawer elsewhere) ───────────
// On the Pipeline and Partners views the detail renders in a full-width panel
// UNDER the content (better snapshot of board + lead together); other views
// (My Day mini-lists, task rows) keep the slide-over drawer.
function detailTargetFor(view){
  if(view==='pipeline') return $('#pipeline-detail');
  if(view==='partners') return $('#partners-detail');
  return null; // drawer
}
async function openLead(id){
  S.openLeadId=id;
  S.detailEl=detailTargetFor(S.view);
  if(S.detailEl){
    closeDrawer();
    $('#lead-panel').innerHTML='';           // avoid duplicate #d-* ids lingering in the drawer
    S.detailEl.classList.remove('hidden');
    S.detailEl.innerHTML='<div class="dsec">Loading…</div>';
  }else{
    $$('.inline-detail').forEach(d=>{d.classList.add('hidden');d.innerHTML='';});  // ...or in inline panels
    $('#lead-drawer').classList.add('open');
    $('#lead-panel').innerHTML='<div class="dsec">Loading…</div>';
  }
  let l;
  try{ l=await api('/leads/'+id); }catch(e){ toast(e.message,true); return; }
  renderDrawer(l);
}
function closeDrawer(){ $('#lead-drawer').classList.remove('open'); }
function closeDetail(){
  if(S.detailEl){ S.detailEl.classList.add('hidden'); S.detailEl.innerHTML=''; S.detailEl=null; }
  else closeDrawer();
  S.openLeadId=null;
  $$('.kcard.selected').forEach(c=>c.classList.remove('selected'));
}
$$('[data-close-drawer]').forEach(x=>x.onclick=closeDetail);

function renderDrawer(l){
  const typeMeta=S.cfg.lead_types.find(t=>t.key===l.lead_type);
  const phone=(l.phone||'').replace(/[^0-9+]/g,'');
  const stageOpts=S.cfg.stages.map(s=>`<option value="${s.key}" ${s.key===l.stage?'selected':''}>${esc(s.label)}</option>`).join('');
  const p=S.detailEl||$('#lead-panel');
  // Partners get a "Referred projects" block: their referral book + one-tap add.
  let referralsHtml='';
  if(S.cfg.partner_types.includes(l.lead_type)){
    const refs=l.referrals||[];
    const wonRefs=refs.filter(r=>r.stage==='won');
    const totalVal=refs.reduce((s,r)=>s+(r.est_value||0),0);
    referralsHtml=`<div class="dsec dsec-wide"><h5>Referred projects (${refs.length})</h5>
      <div class="partner-stat">
        <div><b>${refs.length}</b><span class="l">Referrals</span></div>
        <div><b>${wonRefs.length}</b><span class="l">Won</span></div>
        <div><b>${refs.length?Math.round(100*wonRefs.length/refs.length):0}%</b><span class="l">Close rate</span></div>
        <div><b>${money(totalVal)}</b><span class="l">Total value</span></div>
      </div>
      <div class="mini-lead-list" id="d-referrals" style="margin-top:10px"></div>
      <button class="btn-brand" id="d-add-referral" style="margin-top:10px">＋ Add referred project</button></div>`;
  }
  p.innerHTML=`
    <div class="dh"><button class="dh-close" data-x>✕</button>
      <div class="dh-name">${esc(l.name)}</div>
      <div class="task-meta"><span class="type-badge">${l.service_icon} ${esc(l.service_label)}</span>
      <span class="type-badge">${esc(typeMeta?typeMeta.label:l.lead_type)}</span>
      <span>${esc(repName(l.rep))}</span>${l.est_value?'<span>'+money(l.est_value)+'</span>':''}
      ${l.referred_by_name?'<span>via '+esc(l.referred_by_name)+'</span>':''}</div>
    </div>
    ${l.stalled?'<div class="stalled-banner">⚠ No activity in a while. Reach out or schedule a next step.</div>':''}
    <div class="dgrid">
    ${referralsHtml}
    <div class="dsec"><h5>Stage</h5>
      <select class="stage-select" id="d-stage">${stageOpts}</select></div>
    <div class="dsec"><h5>Reach out</h5>
      <div class="contact-actions">
        <a class="call" href="${phone?'tel:'+phone:'#'}" data-log="call">📞 Call</a>
        <a class="text" href="${phone?'sms:'+phone:'#'}" data-log="text">💬 Text</a>
        <a class="email" href="${l.email?'mailto:'+esc(l.email):'#'}" data-log="email">✉️ Email</a>
      </div></div>
    <div class="dsec"><h5>Log activity</h5>
      <div class="log-row">
        ${['call','text','email','door','meeting','note'].map(k=>`<button class="log-btn" data-logkind="${k}">${KIND_ICO[k]} ${k}</button>`).join('')}
      </div>
      <div id="d-log-form"></div>
    </div>
    <div class="dsec"><h5>Follow-up cadence</h5>
      <div id="d-cadences"></div></div>
    <div class="dsec"><h5>Tasks</h5><div id="d-tasks"></div>
      <button class="btn-ghost small" id="d-add-task">+ Add task</button></div>
    <div class="dsec"><h5>Timeline</h5><div class="timeline" id="d-timeline"></div></div>
    <div class="dsec"><h5>Details</h5><div id="d-fields"></div></div>
    <div class="dsec"><h5>Handoff</h5>
      <div class="drawer-btns">
        <button class="btn-brand" id="d-estimate">📄 Start estimate</button>
        <button class="btn-ghost" id="d-den">${l.crm_contact_id?'✓ In The Den — view job status':'⬆ Push to The Den'}</button>
        ${l.service==='roofing'?'<button class="btn-ghost" id="d-pitch-wc">🪟 Pitch window cleaning</button>':''}
        <div class="est-status" id="d-est-status"></div>
      </div></div>
    <div class="dsec"><button class="btn-danger" id="d-delete">Delete lead</button></div>
    </div><!-- /dgrid -->
  `;
  p.querySelector('[data-x]').onclick=closeDetail;
  // Referred projects list (partners only)
  if(referralsHtml){
    const box=p.querySelector('#d-referrals');
    const refs=l.referrals||[];
    if(!refs.length) box.innerHTML='<div class="empty">No referred projects yet — add their first one.</div>';
    refs.forEach(r=>{
      const row=el('div','mini-lead');
      row.innerHTML=`<span class="kcol-dot" style="background:${r.stage_color}"></span>
        <div class="nm">${r.service!=='roofing'?r.service_icon+' ':''}${esc(r.name)}</div>
        <div class="sub">${esc(r.stage_label)}${r.est_value?' · '+money(r.est_value):''}</div>`;
      row.onclick=()=>openLead(r.id);
      box.appendChild(row);
    });
    p.querySelector('#d-add-referral').onclick=()=>newLeadModal({referred_by:l.id, source:'referral', returnTo:l.id});
  }
  // Inline mode: highlight the open card and bring the panel into view.
  if(S.detailEl){
    $$('.kcard.selected').forEach(c=>c.classList.remove('selected'));
    const card=document.querySelector(`.kcard[data-id="${l.id}"]`);
    if(card) card.classList.add('selected');
    S.detailEl.scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  $('#d-stage').onchange=async e=>{
    await moveStage(l, e.target.value);
    const fresh=await api('/leads/'+l.id); renderDrawer(fresh);
  };
  // contact action logging
  p.querySelectorAll('[data-log]').forEach(a=>a.addEventListener('click',()=>{
    if(a.getAttribute('href')==='#') return;
    api('/leads/'+l.id+'/activities',{method:'POST',body:{kind:a.dataset.log}}).then(()=>toast('Logged'));
  }));
  // log kind buttons -> inline note form
  p.querySelectorAll('[data-logkind]').forEach(b=>b.onclick=()=>{
    const k=b.dataset.logkind;
    $('#d-log-form').innerHTML=`<div class="field"><textarea id="d-log-body" placeholder="${k} notes (optional)…"></textarea></div>
      <button class="btn-brand" id="d-log-save">Save ${k}</button>`;
    $('#d-log-save').onclick=async()=>{
      await api('/leads/'+l.id+'/activities',{method:'POST',body:{kind:k,body:$('#d-log-body').value}});
      toast('Logged'); const fresh=await api('/leads/'+l.id); renderDrawer(fresh);
    };
  });
  renderCadences(l);
  renderTasks(l);
  renderTimeline(l);
  renderFields(l);
  $('#d-add-task').onclick=()=>addTaskModal(l);
  $('#d-estimate').onclick=async()=>{
    try{ const r=await api('/leads/'+l.id+'/start-estimate',{method:'POST'});
      toast('Opening estimator…'); window.open(r.estimator_url,'_blank');
    }catch(e){ toast(e.message,true); }
  };
  $('#d-den').onclick=async()=>{
    if(l.crm_contact_id){ // show estimate/job status
      const st=await api('/leads/'+l.id+'/estimate');
      $('#d-est-status').innerHTML = st.linked ?
        `The Den: ${st.projects.length} job(s), ${st.documents.length} document(s) linked.` :
        'Not linked yet.';
      return;
    }
    try{ const r=await api('/leads/'+l.id+'/convert',{method:'POST'});
      if(r.ok){ toast('Pushed to The Den ✓'); const fresh=await api('/leads/'+l.id); renderDrawer(fresh); }
      else toast(r.error,true);
    }catch(e){ toast(e.message,true); }
  };
  const pitchBtn=p.querySelector('#d-pitch-wc');
  if(pitchBtn) pitchBtn.onclick=async()=>{
    // Same customer, new deal on the window-cleaning line.
    try{
      const nw=await api('/leads',{method:'POST',body:{
        first_name:l.first_name,last_name:l.last_name,company:l.company,
        phone:l.phone,email:l.email,address:l.address,city:l.city,state:l.state,zip:l.zip,
        lead_type:l.lead_type,service:'window_cleaning',source:'existing_customer',
        temperature:'warm',referred_by:l.referred_by,rep:l.rep}});
      await api('/leads/'+nw.id+'/activities',{method:'POST',
        body:{kind:'system',body:`Window cleaning pitch — spun off from the ${l.service_label} deal`}});
      toast('🪟 Window cleaning deal created');
      if(S.view==='pipeline')renderPipeline();
      openLead(nw.id);
    }catch(e){ toast(e.message,true); }
  };
  $('#d-delete').onclick=async()=>{
    if(!confirm('Delete this lead and its history?')) return;
    await api('/leads/'+l.id,{method:'DELETE'}); closeDetail(); toast('Deleted');
    if(S.view==='pipeline')renderPipeline(); else if(S.view==='myday')renderMyDay();
  };
}
async function renderCadences(l){
  const cads=await api('/cadences');
  const active=(l.enrollments||[]).map(e=>e.cadence_id);
  const box=$('#d-cadences');
  box.innerHTML=cads.map(c=>{
    const on=active.includes(c.id);
    return `<div class="goal"><div class="prog"><b>${esc(c.name)}</b>
      <div style="font-size:12px;color:var(--txt3)">${esc(c.description)}</div></div>
      <button class="btn-ghost small" data-cad="${c.id}" ${on?'disabled':''}>${on?'✓ Active':'Enroll'}</button></div>`;
  }).join('');
  box.querySelectorAll('[data-cad]').forEach(b=>b.onclick=async()=>{
    try{ await api('/leads/'+l.id+'/enroll',{method:'POST',body:{cadence_id:b.dataset.cad}});
      toast('Enrolled — first task scheduled'); const fresh=await api('/leads/'+l.id); renderDrawer(fresh);
    }catch(e){ toast(e.message,true); }
  });
}
function renderTasks(l){
  const box=$('#d-tasks');
  const tasks=(l.tasks||[]).filter(t=>!t.done);
  if(!tasks.length){ box.innerHTML='<div class="empty">No open tasks. Schedule a next step.</div>'; return; }
  box.innerHTML='';
  tasks.forEach(t=>{
    const row=el('div','task'+((!t.done&&t.due_at<=new Date().toISOString())?' overdue':''));
    row.innerHTML=`<button class="task-check"></button><div class="task-body">
      <div class="task-title">${esc(t.title||t.kind)}</div>
      <div class="task-meta"><span>${KIND_ICO[t.kind]||'📌'}</span><span>${dueLabel(t.due_at)}</span></div></div>`;
    row.querySelector('.task-check').onclick=async()=>{
      await api('/tasks/'+t.id,{method:'PATCH',body:{done:true}});
      toast('Done ✓'); const fresh=await api('/leads/'+l.id); renderDrawer(fresh);
    };
    box.appendChild(row);
  });
}
function renderTimeline(l){
  const box=$('#d-timeline');
  if(!l.activities||!l.activities.length){ box.innerHTML='<div class="empty">No activity yet.</div>'; return; }
  box.innerHTML='';
  l.activities.forEach(a=>{
    const row=el('div','tl');
    row.innerHTML=`<div class="tl-ico">${KIND_ICO[a.kind]||'•'}</div>
      <div class="tl-body"><div class="tl-txt">${esc(a.body||a.kind)}</div>
      <div class="tl-time">${esc(a.kind)} · ${timeAgo(a.created_at)}</div></div>`;
    box.appendChild(row);
  });
}
function renderFields(l){
  const cfg=S.cfg;
  const typeSel=cfg.lead_types.map(t=>`<option value="${t.key}" ${t.key===l.lead_type?'selected':''}>${esc(t.label)}</option>`).join('');
  const srcSel='<option value="">—</option>'+cfg.sources.map(s=>`<option ${s===l.source?'selected':''}>${esc(s)}</option>`).join('');
  const tempSel=cfg.temperature.map(t=>`<option value="${t}" ${t===l.temperature?'selected':''}>${esc(t)}</option>`).join('');
  const partnerOpts='<option value="">—</option>'+S.leadCache.filter(x=>cfg.partner_types.includes(x.lead_type)&&x.id!==l.id)
    .map(x=>`<option value="${x.id}" ${x.id===l.referred_by?'selected':''}>${esc(x.name)}</option>`).join('');
  $('#d-fields').innerHTML=`
    <div class="field-row"><div class="field"><label>First</label><input id="f-first" value="${esc(l.first_name)}"></div>
      <div class="field"><label>Last</label><input id="f-last" value="${esc(l.last_name)}"></div></div>
    <div class="field"><label>Company</label><input id="f-company" value="${esc(l.company)}"></div>
    <div class="field-row"><div class="field"><label>Phone</label><input id="f-phone" value="${esc(l.phone)}"></div>
      <div class="field"><label>Email</label><input id="f-email" value="${esc(l.email)}"></div></div>
    <div class="field"><label>Address</label><input id="f-address" value="${esc(l.address)}"></div>
    <div class="field-row"><div class="field"><label>City</label><input id="f-city" value="${esc(l.city)}"></div>
      <div class="field"><label>State</label><input id="f-state" value="${esc(l.state)}"></div>
      <div class="field"><label>Zip</label><input id="f-zip" value="${esc(l.zip)}"></div></div>
    <div class="field-row"><div class="field"><label>Service</label><select id="f-service">
        ${cfg.services.map(s=>`<option value="${s.key}" ${s.key===l.service?'selected':''}>${s.icon} ${esc(s.label)}</option>`).join('')}</select></div>
      <div class="field"><label>Type</label><select id="f-type">${typeSel}</select></div>
      <div class="field"><label>Temp</label><select id="f-temp">${tempSel}</select></div></div>
    <div class="field-row"><div class="field"><label>Source</label><select id="f-source">${srcSel}</select></div>
      <div class="field"><label>Est. value</label><input id="f-value" type="number" value="${l.est_value||''}"></div></div>
    <div class="field"><label>Referred by (partner)</label><select id="f-ref">${partnerOpts}</select></div>
    <button class="btn-brand" id="f-save">Save details</button>`;
  $('#f-save').onclick=async()=>{
    const body={first_name:$('#f-first').value,last_name:$('#f-last').value,company:$('#f-company').value,
      phone:$('#f-phone').value,email:$('#f-email').value,address:$('#f-address').value,
      city:$('#f-city').value,state:$('#f-state').value,zip:$('#f-zip').value,
      lead_type:$('#f-type').value,service:$('#f-service').value,temperature:$('#f-temp').value,
      source:$('#f-source').value,est_value:$('#f-value').value,referred_by:$('#f-ref').value};
    try{ await api('/leads/'+l.id,{method:'PUT',body}); toast('Saved');
      const fresh=await api('/leads/'+l.id); renderDrawer(fresh);
    }catch(e){ toast(e.message,true); }
  };
}
function addTaskModal(l){
  const tomorrow=new Date(Date.now()+86400000).toISOString().slice(0,16);
  openModal('Add task',`
    <div class="field"><label>What</label><input id="m-title" placeholder="e.g. Call to confirm appointment"></div>
    <div class="field"><label>Type</label><select id="m-kind">
      ${['call','text','email','meeting','door','note'].map(k=>`<option value="${k}">${k}</option>`).join('')}</select></div>
    <div class="field"><label>Due</label><input id="m-due" type="datetime-local" value="${tomorrow}"></div>`,
    async()=>{
      await api('/leads/'+l.id+'/tasks',{method:'POST',body:{
        title:$('#m-title').value,kind:$('#m-kind').value,
        due_at:new Date($('#m-due').value).toISOString()}});
      toast('Task added'); const fresh=await api('/leads/'+l.id); renderDrawer(fresh);
    });
}

// ── Partners ─────────────────────────────────────────────────────────────────
async function renderPartners(){
  const list=await api('/partners');
  const box=$('#partners-list');
  if(!list.length){ box.innerHTML='<div class="empty">No partners yet. Add a realtor, HOA, or insurance agent as a lead type to track referrals.</div>'; return; }
  box.innerHTML='';
  list.forEach(p=>{
    const typeMeta=S.cfg.lead_types.find(t=>t.key===p.lead_type);
    const card=el('div','card');
    card.innerHTML=`<h4>${esc(p.name)} <span class="type-badge">${esc(typeMeta?typeMeta.label:p.lead_type)}</span></h4>
      <div style="font-size:13px;color:var(--txt3)">${esc(p.phone||'')} ${p.email?'· '+esc(p.email):''}</div>
      <div class="partner-stat">
        <div><b>${p.referrals_total}</b><span class="l">Referrals</span></div>
        <div><b>${p.referrals_won}</b><span class="l">Won</span></div>
        <div><b>${p.referrals_total?Math.round(100*p.referrals_won/p.referrals_total):0}%</b><span class="l">Close rate</span></div>
      </div>`;
    card.onclick=()=>openLead(p.id);
    box.appendChild(card);
  });
}

// ── Dashboard ────────────────────────────────────────────────────────────────
$('#dash-days').onchange=renderDashboard;
$('#dash-rep').onchange=renderDashboard;
async function renderDashboard(){
  const days=$('#dash-days').value;
  const rep=S.me.is_manager?$('#dash-rep').value:'';
  const qs=`?days=${days}`+(rep?`&rep=${encodeURIComponent(rep)}`:'');
  const [d,lb]=await Promise.all([api('/dashboard'+qs), api('/leaderboard?days='+days)]);
  $('#dash-kpis').innerHTML=[
    ['Won', d.won_count, money(d.won_value)],
    ['Win rate', d.win_rate+'%', d.lost_count+' lost'],
    ['Pipeline', money(d.pipeline_value), d.pipeline_count+' open'],
    ['Avg deal', money(d.avg_deal), ''],
    ['New leads', d.new_leads, 'in '+d.days+'d'],
    ['Outreach', d.outreach_total, 'calls/texts/etc'],
  ].map(([l,n,s])=>`<div class="kpi"><div class="n">${n}</div><div class="l">${l}</div>${s?`<div class="sub">${s}</div>`:''}</div>`).join('');
  // service-line split
  $('#dash-services').innerHTML=Object.values(d.by_service||{}).map(s=>
    `<div class="kpi"><div class="n">${s.icon} ${money(s.open_value)}</div>
      <div class="l">${esc(s.label)} pipeline</div>
      <div class="sub">${s.open} open · ${s.won} won (${money(s.won_value)}) in ${d.days}d</div></div>`).join('');
  // funnel
  const maxC=Math.max(1,...d.stages.map(s=>d.stage_counts[s.key]||0));
  $('#dash-funnel').innerHTML=d.stages.map(s=>{
    const c=d.stage_counts[s.key]||0;
    return `<div class="funnel-row" data-stage="${s.key}" title="Open in pipeline"><div class="funnel-label">${esc(s.label)}</div>
      <div class="funnel-bar" style="width:${Math.max(8,100*c/maxC)}%;background:${s.color}">${c}</div></div>`;
  }).join('');
  $$('#dash-funnel .funnel-row').forEach(r=>r.onclick=()=>{
    S.stageFocus=r.dataset.stage; go('pipeline');
  });
  barList($('#dash-activity'), d.activity);
  barList($('#dash-source'), d.by_source);
  // leaderboard
  $('#dash-leaderboard').innerHTML=lb.length?'':'<div class="empty">No data yet.</div>';
  lb.forEach((r,i)=>{
    const row=el('div','lb-row');
    row.innerHTML=`<div class="lb-rank ${i<3?'g'+(i+1):''}">${i+1}</div>
      <div class="lb-name">${esc(repName(r.rep))}</div>
      <div class="lb-stats"><b>${r.won}</b> won · ${money(r.won_value)}<br>${r.outreach} touches · ${r.appts_set} appts</div>`;
    // Managers: click a rep to open their coaching scorecard.
    if(S.me.is_manager) row.onclick=()=>{ $('#coach-rep').value=r.rep; go('coaching'); };
    $('#dash-leaderboard').appendChild(row);
  });
}
function barList(box, obj){
  const entries=Object.entries(obj||{}).sort((a,b)=>b[1]-a[1]);
  const max=Math.max(1,...entries.map(e=>e[1]));
  box.innerHTML=entries.length?'':'<div class="empty">No data.</div>';
  entries.forEach(([k,v])=>{
    box.appendChild(el('div','bar-item',
      `<span class="bl">${esc(k)}</span><span class="bar-track"><span class="bar-fill" style="width:${100*v/max}%"></span></span><span class="bar-n">${v}</span>`));
  });
}

// ── Coaching ─────────────────────────────────────────────────────────────────
$('#coach-rep').onchange=renderCoaching;
$('#coach-days').onchange=renderCoaching;
async function renderCoaching(){
  const rep=$('#coach-rep').value||S.me.username;
  const days=$('#coach-days').value;
  const [sc, notes, stalled, goals]=await Promise.all([
    api(`/scorecard/${rep}?days=${days}`), api('/coaching/'+rep),
    api('/stalled?rep='+encodeURIComponent(rep)), api('/goals?rep='+encodeURIComponent(rep)),
  ]);
  $('#coach-scorecard').innerHTML=[
    ['Outreach', sc.outreach_total, ''],
    ['New leads', sc.new_leads, ''],
    ['Estimates', sc.estimates_presented, 'presented'],
    ['Won', sc.won, money(sc.won_value)],
    ['Win rate', sc.win_rate+'%', sc.lost+' lost'],
    ['Avg cycle', sc.avg_cycle_days!=null?sc.avg_cycle_days+'d':'—', 'to close'],
    ['Open pipe', money(sc.open_pipeline_value), sc.open_pipeline_count+' deals'],
    ['Avg deal', money(sc.avg_deal), ''],
  ].map(([l,n,s])=>`<div class="kpi"><div class="n">${n}</div><div class="l">${l}</div>${s?`<div class="sub">${s}</div>`:''}</div>`).join('');
  // goals
  $('#coach-goals').innerHTML = goals.length?'':'<div class="empty">No goals set.</div>';
  goals.forEach(g=>{
    const actual=goalActual(g, sc);
    const pct=g.target?Math.min(100,Math.round(100*actual/g.target)):0;
    const row=el('div','goal');
    row.innerHTML=`<div class="prog"><b>${esc(g.metric)}</b> — ${actual} / ${g.target} <span style="color:var(--txt3)">(${esc(g.period)})</span>
      <span class="bar-track"><span class="bar-fill" style="width:${pct}%"></span></span></div>
      ${S.me.is_admin?`<button class="btn-ghost small" data-goal="${g.id}">✕</button>`:''}`;
    $('#coach-goals').appendChild(row);
  });
  $$('#coach-goals [data-goal]').forEach(b=>b.onclick=async()=>{await api('/goals/'+b.dataset.goal,{method:'DELETE'});renderCoaching();});
  renderMini($('#coach-stalled'), stalled, 'No stalled deals for this rep. 👏');
  $('#coach-notes').innerHTML='';
  notes.forEach(n=>{
    $('#coach-notes').appendChild(el('div','note',
      `${esc(n.body)}<div class="meta">${esc(repName(n.author))} · ${timeAgo(n.created_at)}</div>`));
  });
  $('#add-note-btn').onclick=async()=>{
    const body=$('#coach-note-input').value.trim(); if(!body) return;
    await api('/coaching/'+rep,{method:'POST',body:{body}});
    $('#coach-note-input').value=''; toast('Note saved'); renderCoaching();
  };
  $('#add-goal-btn').onclick=()=>addGoalModal(rep);
}
function goalActual(g, sc){
  const m=(g.metric||'').toLowerCase();
  if(m.includes('outreach')||m.includes('call')||m.includes('touch')) return sc.outreach_total;
  if(m.includes('estimate')) return sc.estimates_presented;
  if(m.includes('lead')) return sc.new_leads;
  if(m.includes('won')||m.includes('sale')||m.includes('close')) return sc.won;
  if(m.includes('revenue')||m.includes('value')) return sc.won_value;
  return 0;
}
function addGoalModal(rep){
  const period=new Date().toISOString().slice(0,7);
  openModal('Add goal for '+repName(rep),`
    <div class="field"><label>Metric</label><select id="g-metric">
      <option>outreach</option><option>new leads</option><option>estimates presented</option>
      <option>won deals</option><option>revenue</option></select></div>
    <div class="field"><label>Target</label><input id="g-target" type="number" placeholder="e.g. 100"></div>
    <div class="field"><label>Period</label><input id="g-period" value="${period}" placeholder="YYYY-MM"></div>`,
    async()=>{
      await api('/goals',{method:'POST',body:{rep,metric:$('#g-metric').value,
        target:$('#g-target').value,period:$('#g-period').value}});
      toast('Goal added'); renderCoaching();
    });
}

// ── Playbook ─────────────────────────────────────────────────────────────────
let PB=null;
$('#playbook-search').oninput=renderPlaybookLists;
async function renderPlaybook(){
  if(!PB) PB=await api('/playbook');
  $('#playbook-principles').innerHTML=(PB.principles||[]).map(p=>`<div class="principle">💡 ${esc(p)}</div>`).join('');
  renderPlaybookLists();
}
function renderPlaybookLists(){
  const q=($('#playbook-search').value||'').toLowerCase();
  const match=s=>!q||s.toLowerCase().includes(q);
  $('#playbook-objections').innerHTML=(PB.objections||[]).filter(o=>match(o.objection+o.rebuttal+o.category)).map(o=>
    `<div class="card"><div class="cat">${esc(o.category)}</div><h4 class="q">“${esc(o.objection)}”</h4>
      <div class="a">${esc(o.rebuttal)}</div><div class="coach">🎯 ${esc(o.coach_note)}</div></div>`).join('')
    ||'<div class="empty">No matches.</div>';
  $('#playbook-scripts').innerHTML=(PB.scripts||[]).filter(s=>match(s.name+s.body)).map(s=>
    `<div class="card"><h4>${esc(s.name)}</h4><div class="a">${esc(s.body)}</div></div>`).join('')
    ||'<div class="empty">No matches.</div>';
}

// ── Add lead ─────────────────────────────────────────────────────────────────
// preset: {referred_by, source, returnTo} — used by a partner's "add referred project".
function newLeadModal(preset={}){
  const cfg=S.cfg;
  const typeSel=cfg.lead_types.map(t=>`<option value="${t.key}">${esc(t.label)}</option>`).join('');
  const srcSel='<option value="">Source…</option>'+cfg.sources.map(s=>
    `<option ${s===preset.source?'selected':''}>${esc(s)}</option>`).join('');
  const repSel=S.me.is_manager?`<div class="field"><label>Assign to</label><select id="nl-rep">
    ${S.users.map(u=>`<option value="${esc(u.username)}" ${u.username===S.me.username?'selected':''}>${esc(u.full_name||u.username)}</option>`).join('')}</select></div>`:'';
  openModal(preset.referred_by?'New referred project':'New lead',`
    <div class="field-row"><div class="field"><label>First</label><input id="nl-first"></div>
      <div class="field"><label>Last</label><input id="nl-last"></div></div>
    <div class="field"><label>Company (optional)</label><input id="nl-company"></div>
    <div class="field-row"><div class="field"><label>Phone</label><input id="nl-phone" inputmode="tel"></div>
      <div class="field"><label>Email</label><input id="nl-email" inputmode="email"></div></div>
    <div class="field"><label>Address</label><input id="nl-address"></div>
    <div class="field-row"><div class="field"><label>City</label><input id="nl-city"></div>
      <div class="field"><label>State</label><input id="nl-state" placeholder="CO/TX"></div>
      <div class="field"><label>Zip</label><input id="nl-zip"></div></div>
    <div class="field-row"><div class="field"><label>Service</label><select id="nl-service">
        ${cfg.services.map(s=>`<option value="${s.key}" ${s.key===(preset.service||'roofing')?'selected':''}>${s.icon} ${esc(s.label)}</option>`).join('')}</select></div>
      <div class="field"><label>Type</label><select id="nl-type">${typeSel}</select></div>
      <div class="field"><label>Source</label><select id="nl-source">${srcSel}</select></div></div>
    <div class="field-row"><div class="field"><label>Est. value</label><input id="nl-value" type="number"></div>
      <div class="field"><label>Temp</label><select id="nl-temp">${cfg.temperature.map(t=>`<option>${t}</option>`).join('')}</select></div></div>
    ${repSel}`,
    async()=>{
      const first=$('#nl-first').value.trim(), last=$('#nl-last').value.trim(), company=$('#nl-company').value.trim();
      if(!first&&!last&&!company){ toast('Enter a name or company',true); throw new Error('name'); }
      const body={first_name:first,last_name:last,company,phone:$('#nl-phone').value,email:$('#nl-email').value,
        address:$('#nl-address').value,city:$('#nl-city').value,state:$('#nl-state').value.toUpperCase(),zip:$('#nl-zip').value,
        lead_type:$('#nl-type').value,service:$('#nl-service').value,source:$('#nl-source').value,
        est_value:$('#nl-value').value,temperature:$('#nl-temp').value};
      if(preset.referred_by) body.referred_by=preset.referred_by;
      if(S.me.is_manager&&$('#nl-rep')) body.rep=$('#nl-rep').value;
      const lead=await api('/leads',{method:'POST',body});
      toast(preset.referred_by?'Referred project added':'Lead added'); closeModal();
      if(S.view==='pipeline')renderPipeline(); else if(S.view==='myday')renderMyDay();
      // From a partner: land back on the partner so the new project shows underneath.
      openLead(preset.returnTo||lead.id);
    }, {noAutoClose:true});
}
$('#add-lead-btn').onclick=()=>newLeadModal();

// ── Menu (admin/account) ─────────────────────────────────────────────────────
$('#menu-btn').onclick=async()=>{
  let adminHtml='';
  if(S.me.is_admin){
    adminHtml=`<button class="btn-ghost" id="mn-invite">👥 Invite a rep</button>
      <button class="btn-ghost" id="mn-team">🔑 Manage team</button>`;
  }
  openModal('Menu',`<div class="drawer-btns">
    <div style="color:var(--txt3);font-size:13px">Signed in as <b>${esc(S.me.username)}</b> (${esc(S.me.role)})</div>
    ${adminHtml}
    <button class="btn-ghost" id="mn-pw">Change my password</button>
    <button class="btn-danger" id="mn-logout">Log out</button></div>`, null, {hideOk:true});
  $('#mn-logout').onclick=async()=>{await api('/logout',{method:'POST'});location.reload();};
  $('#mn-pw').onclick=()=>openModal('Change password',
    `<div class="field"><label>New password (6+)</label><input id="pw-new" type="password"></div>`,
    async()=>{await api('/account/password',{method:'POST',body:{password:$('#pw-new').value}});toast('Password changed');});
  if($('#mn-invite')) $('#mn-invite').onclick=inviteModal;
  if($('#mn-team')) $('#mn-team').onclick=teamModal;
};
async function inviteModal(){
  openModal('Invite a rep',
    `<div class="field"><label>Rep username (blank = open invite)</label><input id="iv-user" autocapitalize="none"></div>
     <div id="iv-result"></div>`,
    async()=>{
      const r=await api('/invites',{method:'POST',body:{username:$('#iv-user').value.trim()}});
      $('#iv-result').innerHTML=`<div class="field"><label>Share this link</label>
        <input value="${esc(r.link)}" readonly onclick="this.select()"></div>`;
    }, {okText:'Create link', noAutoClose:true});
}
async function teamModal(){
  const users=await api('/users');
  openModal('Team', users.map(u=>`<div class="goal"><div class="prog"><b>${esc(u.full_name||u.username)}</b>
    <div style="font-size:12px;color:var(--txt3)">${esc(u.username)} · ${esc(u.role)}</div></div>
    <select class="mini-select" data-role="${esc(u.username)}">
      ${['rep','manager','admin'].map(r=>`<option ${r===u.role?'selected':''}>${r}</option>`).join('')}
    </select></div>`).join(''), null, {hideOk:true});
  $$('#modal-box [data-role]').forEach(sel=>sel.onchange=async()=>{
    try{ await api('/users/'+sel.dataset.role+'/role',{method:'POST',body:{role:sel.value}}); toast('Role updated');
      S.users=await api('/users'); buildRepSelects();
    }catch(e){ toast(e.message,true); }
  });
}

// ── Modal ────────────────────────────────────────────────────────────────────
function openModal(title, bodyHtml, onOk, opts={}){
  const box=$('#modal-box');
  box.innerHTML=`<h3>${esc(title)}</h3>${bodyHtml}
    <div class="modal-actions">
      <button class="btn-ghost" data-cancel>${opts.hideOk?'Close':'Cancel'}</button>
      ${opts.hideOk?'':`<button class="btn-brand" data-ok>${esc(opts.okText||'Save')}</button>`}
    </div>`;
  $('#modal').classList.add('open');
  box.querySelector('[data-cancel]').onclick=closeModal;
  const ok=box.querySelector('[data-ok]');
  if(ok) ok.onclick=async()=>{ try{ if(onOk) await onOk(); if(!opts.noAutoClose) closeModal(); }catch(e){ if(e.message!=='name')console.error(e); } };
}
function closeModal(){ $('#modal').classList.remove('open'); }
$$('[data-close-modal]').forEach(x=>x.onclick=closeModal);

boot();
})();
