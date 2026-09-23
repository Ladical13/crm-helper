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

// Mount prefix: '/crm' inside the portal (portal/mounts.py), '' when this app
// is served standalone. Derived from the URL so one bundle works both ways.
const BASE = location.pathname.startsWith('/crm') ? '/crm' : '';

async function api(path, opts={}) {
  const r = await fetch(BASE+'/api'+path, {
    method: opts.method||'GET',
    headers: opts.body ? {'Content-Type':'application/json'} : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  // Session expired or never signed in: the portal owns login, so hand off
  // rather than rendering an empty pipeline.
  if (r.status === 401) { window.location = '/login'; throw new Error('Unauthorized'); }
  let data = null;
  try { data = await r.json(); } catch(e) {}
  if (!r.ok) throw new Error((data && data.error) || ('HTTP '+r.status));
  if(opts.method&&opts.method!=='GET'&&S.openLeadId) S.detailDirty=true;
  return data;
}

// Calls the PORTAL's API rather than this app's — note the missing BASE. Team
// administration (invites, roles) moved there when the three tools merged onto
// one user store, but the panel that drives it still lives here.
async function portalApi(path, opts={}) {
  const r = await fetch(path, {
    method: opts.method||'GET',
    headers: opts.body ? {'Content-Type':'application/json'} : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401) { window.location = '/login'; throw new Error('Unauthorized'); }
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
  stage_change:'↔️',system:'⚙️',research:'🔎'};

// ── Research: who was found, from where, and how much to trust it ─────────
// research_notes is the JSON the research run stored. Old rows (the import-
// time enrichment) have a slightly different shape; both are read here.
function researchOf(l){
  let d={}; try{ d=JSON.parse(l.research_notes||'{}')||{}; }catch(e){ d={}; }
  const known=v=>{ v=(typeof v==='string'?v:'').trim(); return /^(unknown|n\/a|none|null)$/i.test(v)?'':v; };
  const dm=(d.decision_maker&&typeof d.decision_maker==='object')?d.decision_maker:{};
  let cites=d.citations||[]; if(typeof cites==='string'){ try{cites=JSON.parse(cites);}catch(e){cites=[];} }
  return {name:known(dm.name), title:known(dm.title), email:known(dm.email), phone:known(dm.phone),
    org_email:known(d.org_email), news:known(typeof d.news==='string'?d.news:''),
    summary:known(d.summary), cites:(cites||[]).filter(c=>typeof c==='string'&&/^https?:/.test(c))};
}
const CQ_CLASS={3:'cq-3',2:'cq-2',1:'cq-1',0:'cq-0'};
function cqChip(l){
  const q=l.contact_quality||0, lab=l.contact_quality_label||(S.cfg.contact_quality.find(x=>x.key===q)||{}).label||'';
  return `<span class="chip cq ${CQ_CLASS[q]}" title="${esc((S.cfg.contact_quality.find(x=>x.key===q)||{}).hint||'')}">${q===3?'✓ ':''}${esc(lab)}</span>`;
}
// "Ask for: Pastor John Smith (Senior Pastor)" - the line a rep needs before
// the phone is answered.
function askFor(l){
  const r=researchOf(l);
  const name=(`${l.first_name||''} ${l.last_name||''}`).trim()||r.name;
  if(!name) return '';
  return `<div class="ask-for">Ask for <b>${esc(name)}</b>${r.title?` · ${esc(r.title)}`:''}</div>`;
}
function researchPanelHtml(l){
  const r=researchOf(l);
  const host=u=>{ try{ return new URL(u).hostname.replace(/^www\./,''); }catch(e){ return u; } };
  const researched=!!l.enriched_at;
  return `<div class="dsec dsec-wide"><h5>Research ${cqChip(l)}</h5>
    ${researched?`
      ${r.name?`<div class="rs-row"><b>${esc(r.name)}</b>${r.title?` · ${esc(r.title)}`:''}</div>`:'<div class="rs-row lead-context">No named decision-maker published.</div>'}
      ${r.email||r.phone?`<div class="rs-row">${r.email?`✉️ ${esc(r.email)} `:''}${r.phone?` 📞 ${esc(r.phone)}`:''}</div>`:''}
      ${r.org_email?`<div class="rs-row lead-context">General inbox: ${esc(r.org_email)}</div>`:''}
      ${r.summary?`<div class="rs-row">${esc(r.summary)}</div>`:''}
      ${r.news?`<div class="rs-row"><span class="lead-context">News:</span> ${esc(r.news)}</div>`:''}
      ${r.cites.length?`<div class="rs-row rs-cites">Sources: ${r.cites.slice(0,5).map(u=>`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(host(u))}</a>`).join(' · ')}</div>`:'<div class="rs-row lead-context">No sources were cited, so nothing was filled in from this.</div>'}
      <div class="rs-row lead-context">Researched ${esc(timeAgo(l.enriched_at))}${l.contact_source==='research'?' · the name/email above were filled in from this':''}</div>`
    :'<div class="rs-row lead-context">Not researched yet.</div>'}
    ${l.contact_verified_at?`<div class="rs-row rs-ok">✓ Confirmed by ${esc(repName(l.contact_verified_by))} ${esc(timeAgo(l.contact_verified_at))}</div>`:''}
    <div class="drawer-btns rs-btns">
      ${(l.phone||l.email)&&!l.contact_verified_at?'<button class="btn-ghost small" id="rs-ok">✓ Contact is right</button>':''}
      ${(l.first_name||l.email)?'<button class="btn-ghost small" id="rs-wrong">✗ Wrong contact</button>':''}
      <button class="btn-ghost small" id="rs-again">↻ ${researched?'Research again':'Research now'}</button>
    </div>
    <div id="rs-form"></div></div>`;
}
function wireResearch(p,l){
  const reload=async()=>{ const fresh=await api('/leads/'+l.id); renderDrawer(fresh); };
  const ok=p.querySelector('#rs-ok');
  if(ok) ok.onclick=async()=>{ try{ await api('/leads/'+l.id+'/contact/verify',{method:'POST'}); toast('Contact confirmed ✓'); reload(); }catch(e){ toast(e.message,true); } };
  const form=(label,btn,go)=>{
    p.querySelector('#rs-form').innerHTML=`<div class="field"><input id="rs-note" placeholder="${esc(label)}"></div><button class="btn-brand small" id="rs-go">${esc(btn)}</button>`;
    p.querySelector('#rs-note').focus();
    p.querySelector('#rs-go').onclick=()=>go(p.querySelector('#rs-note').value.trim());
  };
  const research=async hint=>{
    const b=p.querySelector('#rs-go')||p.querySelector('#rs-again'); if(b){ b.disabled=true; b.textContent='Researching… (~15s)'; }
    try{ const r=await api('/leads/'+l.id+'/research',{method:'POST',body:{hint}});
      const f=r.filled||{}; toast(Object.keys(f).length?'Found: '+Object.values(f).join(' '):'No new contact found - notes updated');
      reload();
    }catch(e){ toast(e.message,true); if(b) b.disabled=false; }
  };
  const wrong=p.querySelector('#rs-wrong');
  if(wrong) wrong.onclick=()=>form('What did they say? e.g. "Mike Ross runs facilities"','Clear it and research again',async note=>{
    try{ await api('/leads/'+l.id+'/contact/wrong',{method:'POST',body:{note}}); }catch(e){ toast(e.message,true); return; }
    research(note);
  });
  p.querySelector('#rs-again').onclick=()=>form('Optional hint for the search, e.g. "ask for facilities"','Research',research);
}

// ── Outreach composer: templates → a draft in the rep's own phone or Gmail ──
// Shared by the queue card and the lead drawer. The templates come rendered
// from the server (/leads/<id>/messages); the rep may pick another one and edit
// the words before opening it. Nothing is ever sent from here: a text opens
// the phone's Messages app pre-filled, an email opens the rep's own Gmail.
const CH_LABEL={call:'Call script',voicemail:'Voicemail',text:'Text',email:'Email',offer:'🎁 Offer'};

// iOS wants `sms:NUMBER&body=`, everything else `sms:NUMBER?body=`. An iPad
// reports itself as a Mac, hence the touch check.
function smsUrl(phone, body){
  const ios=/iP(hone|ad|od)/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
  return 'sms:'+phone+(ios?'&':'?')+'body='+encodeURIComponent(body);
}

// `script` is a playbook fallback, used only when the library has no call
// script for this lead — every audience has one, so in practice it never is.
function composerTabs(msgs, script, st){
  const tabs=[];
  if(script&&!(msgs&&msgs.call&&msgs.call.templates.length)) tabs.push('call');
  for(const ch of ['call','voicemail','text','email'])
    if(msgs&&msgs[ch]&&msgs[ch].templates.length&&!tabs.includes(ch)) tabs.push(ch);
  if(msgs&&msgs.offers&&msgs.offers.length) tabs.push('offer');
  if(!tabs.includes(st.tab)) st.tab=tabs[0]||'';
  return tabs;
}

// The Offer tab reuses the email/text editing below: each live offer that fits
// this lead becomes a "template" for whichever way it is being sent.
function offerBox(msgs, via){
  const t=(msgs.offers||[]).map(o=>({id:o.key, name:o.name, link:o.link,
    subject:via==='email'?o.email.subject:'', body:via==='email'?o.email.body:o.text}));
  return {recommended:t.length?t[0].id:'', templates:t};
}

function composerHtml(lead, msgs, script, st){
  const tabs=composerTabs(msgs, script, st);
  if(!tabs.length) return '';
  const head=`<div class="oq-tabs">${tabs.map(t=>`<button data-ctab="${t}" class="${st.tab===t?'on':''}">${CH_LABEL[t]}</button>`).join('')}</div>`;
  if(st.tab==='call'&&!(msgs&&msgs.call&&msgs.call.templates.length))
    return head+`<div class="oq-script">${esc(script.body)}</div>`;
  const isOffer=st.tab==='offer';
  st.via=st.via||(lead.email?'email':'text');
  const ch=isOffer?st.via:st.tab;                 // how it is actually being sent
  const box=isOffer?offerBox(msgs,st.via):msgs[st.tab];
  st.sel=st.sel||{};
  if(!box.templates.find(t=>t.id===st.sel[st.tab])) st.sel[st.tab]=box.recommended||box.templates[0].id;
  const tpl=box.templates.find(t=>t.id===st.sel[st.tab]);
  st.edit=st.edit||{};
  const ekey=(isOffer?'offer:'+st.via:st.tab)+':'+tpl.id;
  const ed=st.edit[ekey]||{subject:tpl.subject,body:tpl.body};
  st.edit[ekey]=ed;
  const opts=box.templates.map(t=>`<option value="${esc(t.id)}" ${t.id===tpl.id?'selected':''}>${t.id===box.recommended&&!isOffer?'★ ':''}${esc(t.name)}</option>`).join('');
  const phone=(lead.phone||'').replace(/[^0-9+]/g,'');
  let action='';
  if(ch==='text') action=phone
    ? `<a class="text" href="${esc(smsUrl(phone,ed.body))}" data-touch="text">💬 Open in Messages</a>`
    : '<button disabled>Phone needed</button>';
  if(ch==='email') action=lead.email
    ? `<a class="email" target="_blank" rel="noopener" href="${esc(gmailUrl(lead.email,ed))}" data-touch="email">✉️ Open in Gmail</a>`
    : '<button disabled>Email needed</button>';
  const chars=ch==='text'?`<span class="cmp-count ${ed.body.length>(S.cfg.text_max_chars||320)?'over':''}">${ed.body.length} chars</span>`:'';
  const offerBar=isOffer?`<div class="cmp-via">
      <button data-via="email" class="${st.via==='email'?'on':''}">By email</button>
      <button data-via="text" class="${st.via==='text'?'on':''}">By text</button>
      <a class="linkish" href="${esc(tpl.link)}" target="_blank" rel="noopener">👁 See the offer page</a></div>`:'';
  return head+`<div class="cmp">
    <select class="mini-select cmp-pick" data-cpick aria-label="${isOffer?'Offer':'Template'}">${opts}</select>
    ${offerBar}
    ${ch==='email'?`<input class="cmp-subj" data-csubj value="${esc(ed.subject)}" aria-label="Subject">`:''}
    ${ch==='call'
      ? `<div class="oq-script">${esc(ed.body)}</div><p class="cmp-hint">The beats, not a script to read word for word. Tap how it went below.</p>`
      : ch==='voicemail'
      ? `<div class="oq-script">${esc(ed.body)}</div><p class="cmp-hint">Read this if it goes to voicemail, then tap <b>Left voicemail</b>.</p>`
      : `<textarea class="cmp-body" data-cbody rows="${ch==='text'?4:8}" aria-label="Message">${esc(ed.body)}</textarea>
         <div class="cmp-foot">${chars}<span class="cmp-hint">${isOffer?'The link opens the offer page with your name on it. ':''}Edit freely - it opens as a draft, you press send.</span></div>
         <div class="oq-actions">${action}</div>`}
  </div>`;
}

// Re-render just the composer when the rep switches tab/template, and keep
// their edits; typing updates the link in place so focus is never lost.
function wireComposer(root, lead, msgs, script, st, onTouch){
  const redraw=()=>{ root.querySelector('[data-composer]').innerHTML=composerHtml(lead,msgs,script,st); wire(); };
  const wire=()=>{
    root.querySelectorAll('[data-ctab]').forEach(b=>b.onclick=()=>{st.tab=b.dataset.ctab;redraw();});
    root.querySelectorAll('[data-via]').forEach(b=>b.onclick=()=>{st.via=b.dataset.via;redraw();});
    const pick=root.querySelector('[data-cpick]'); if(pick) pick.onchange=()=>{st.sel[st.tab]=pick.value;redraw();};
    const key=()=>(st.tab==='offer'?'offer:'+st.via:st.tab)+':'+st.sel[st.tab];
    const body=root.querySelector('[data-cbody]'), subj=root.querySelector('[data-csubj]');
    const refresh=()=>{
      const ed=st.edit[key()];
      const a=root.querySelector('[data-composer] [data-touch]');
      if(a&&a.dataset.touch==='text') a.href=smsUrl((lead.phone||'').replace(/[^0-9+]/g,''),ed.body);
      if(a&&a.dataset.touch==='email') a.href=gmailUrl(lead.email,ed);
      const c=root.querySelector('.cmp-count');
      if(c){ c.textContent=ed.body.length+' chars'; c.classList.toggle('over',ed.body.length>(S.cfg.text_max_chars||320)); }
    };
    if(body) body.oninput=()=>{ st.edit[key()].body=body.value; refresh(); };
    if(subj) subj.oninput=()=>{ st.edit[key()].subject=subj.value; refresh(); };
    root.querySelectorAll('[data-composer] [data-touch]').forEach(a=>a.addEventListener('click',()=>onTouch&&onTouch(a.dataset.touch)));
  };
  wire();
}

// The outcome buttons. `touched` pre-highlights the one that matches what the
// rep just did (opened Messages → Texted).
function outcomeHtml(touched){
  const hint={text:'texted',email:'emailed'}[touched]||'';
  return `<div class="oc-grid">${S.cfg.outcomes.map(o=>
    `<button class="oc-btn ${o.key===hint?'hint':''} ${['not_interested','wrong_number'].includes(o.key)?'neg':''}" data-outcome="${o.key}">${o.icon} ${esc(o.label)}</button>`).join('')}</div>
    <div class="oc-date hidden" data-ocdate>
      <label>Call back on <input type="datetime-local" data-ocwhen></label>
      <button class="btn-brand small" data-ocok>Book it</button></div>`;
}

// The template behind an outcome: the one open on the channel the outcome is
// about (texted -> the text picked, left voicemail -> the voicemail read, a
// call outcome -> the call script on screen). '' when the rep never opened one.
function templateFor(st, outcome){
  if(!st||!st.sel) return '';
  const ch={texted:'text',emailed:'email',left_vm:'voicemail'}[outcome]
    ||(['dropped_by'].includes(outcome)?'':'call');
  if(ch==='call'&&st.tab!=='call') return '';
  return (ch&&st.sel[ch])||'';
}

// POST the outcome; resolves to the server's reply or null. Asks for the day
// when the outcome needs one (a callback the person agreed to).
function wireOutcomes(root, leadId, getCtx, done){
  root.querySelectorAll('[data-outcome]').forEach(b=>b.onclick=async()=>{
    const key=b.dataset.outcome;
    const o=S.cfg.outcomes.find(x=>x.key===key);
    if(o.ask_date){
      const box=root.querySelector('[data-ocdate]'); box.classList.remove('hidden');
      const when=box.querySelector('[data-ocwhen]');
      if(!when.value){ const d=new Date(Date.now()+86400000); d.setHours(10,0,0,0);
        when.value=new Date(d-d.getTimezoneOffset()*60000).toISOString().slice(0,16); }
      box.querySelector('[data-ocok]').onclick=()=>send(key, when.value);
      when.focus(); return;
    }
    send(key);
  });
  async function send(key, when){
    const ctx=getCtx()||{};
    const body={outcome:key};
    if(ctx.kind) body.kind=ctx.kind;
    if(ctx.task_id) body.task_id=ctx.task_id;
    // Sent from the Offer tab: record which offer, so the library can count it.
    if(ctx.cmp&&ctx.cmp.tab==='offer'&&['texted','emailed'].includes(key)) body.offer=(ctx.cmp.sel||{}).offer;
    else { const tid=templateFor(ctx.cmp,key); if(tid) body.template_id=tid; }
    if(when) body.follow_up_at=new Date(when).toISOString().slice(0,16);
    try{
      const r=await api('/leads/'+leadId+'/outcome',{method:'POST',body});
      const o=S.cfg.outcomes.find(x=>x.key===key);
      toast(`${o.icon} ${o.label}`+(r.follow_up?` · next: ${dueLabel(r.follow_up.due_at)}`:''));
      done&&done(r);
    }catch(e){ toast(e.message,true); }
  }
}

// ── Auth ─────────────────────────────────────────────────────────────────────
// There is no sign-in screen here any more: the portal owns login, and api()
// bounces to /login on a 401. Reaching afterLogin() means the session is good.
function showApp(){ $('#app-screen').classList.add('active'); }

async function boot() {
  const me = await api('/me');
  if (!me.authenticated) { window.location = '/login'; return; }
  S.me = me;
  await afterLogin();
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
  routeFromUrl();
  // Scoped to the mount prefix. Before the merge this worker and the
  // estimator's both claimed root scope with different cache names, so on one
  // origin whichever registered last would win and serve the other app's shell.
  if('serviceWorker' in navigator) navigator.serviceWorker.register(BASE+'/sw.js',{scope:BASE+'/'}).catch(()=>{});
}

// ── Router ───────────────────────────────────────────────────────────────────
const TITLES={myday:'My Day',outreach:'Outreach',pipeline:'Pipeline',partners:'Partners',
  dashboard:'Numbers',coaching:'Coaching',playbook:'Playbook'};
function go(view, push=true){
  const returning=!!S.openLeadId&&S.view===view;
  const refresh=!returning||S.detailDirty||S.detailNeedsRefresh;
  detailReq++;
  S.openLeadId=null;
  if(push) history.pushState({},'', '#'+view);
  S.view=view;
  $$('.view').forEach(v=>v.classList.remove('active'));
  $('#view-'+view).classList.add('active');
  $$('.tab').forEach(t=>t.classList.toggle('active',t.dataset.view===view));
  $$('.side-item').forEach(t=>t.classList.toggle('active',t.dataset.view===view));
  $('#view-title').textContent=TITLES[view];
  document.title='Project One — Pipeline';
  if(refresh) ({myday:renderMyDay,outreach:renderOutreach,pipeline:renderPipeline,partners:renderPartners,
    dashboard:renderDashboard,coaching:renderCoaching,playbook:renderPlaybook}[view])();
  if(returning){
    window.scrollTo(0,S.detailScroll||0);
    S.detailTrigger?.focus({preventScroll:true});
  }else window.scrollTo(0,0);
}
function routeFromUrl(){
  if(!S.cfg) return;
  const route=location.hash.slice(1);
  if(route.startsWith('lead/')){
    S.view=history.state?.returnView||'pipeline';
    let id=route.slice(5);
    try{ id=decodeURIComponent(id); }catch(e){}
    openLead(id,false);
  }else go(Object.hasOwn(TITLES,route)?route:'myday',false);
}
window.addEventListener('popstate',routeFromUrl);
$$('.tab').forEach(t=>t.onclick=()=>go(t.dataset.view));
$$('.side-item').forEach(t=>t.onclick=()=>go(t.dataset.view));

// ── Sidebar: live stage counts + quick-jump ──────────────────────────────────
function updateSidebar(){
  const box=$('#side-stages');
  if(!box||!S.cfg) return;
  const counts=S.summary?.stage_counts||{};
  box.innerHTML=S.cfg.stages.map(s=>`
    <div class="side-stage" data-stage="${s.key}">
      <span class="kcol-dot" style="background:${s.color}"></span>${esc(s.label)}
      <span class="n">${counts[s.key]||0}</span>
    </div>`).join('');
  box.querySelectorAll('.side-stage').forEach(r=>r.onclick=()=>{
    pipelineFocus({stage:r.dataset.stage});
  });
}

function buildRepSelects(){
  const opts='<option value="">All reps</option>'+
    S.users.filter(u=>u.username!=='apibot').map(u=>`<option value="${esc(u.username)}">${esc(u.full_name||u.username)}</option>`).join('');
  ['#pipeline-rep','#dash-rep'].forEach(sel=>{ const e=$(sel); if(e){e.innerHTML=opts;
    e.classList.toggle('hidden', !S.me.is_manager);} });
  const coach=$('#coach-rep');
  if(coach){
    coach.innerHTML=S.users.filter(u=>u.username!=='apibot').map(u=>`<option value="${esc(u.username)}">${esc(u.full_name||u.username)}</option>`).join('');
    if([...coach.options].some(o=>o.value===S.me.username)) coach.value=S.me.username;
  }
}
function repName(u){ const x=S.users.find(z=>z.username===u); return x&&x.full_name?x.full_name:u; }

// ── Outreach queue ───────────────────────────────────────────────────────────
// One partner at a time until the day's number is done. Every action logs an
// activity through the normal endpoint, which is what makes the leaderboard
// count the day without any new reporting code.
const Q={items:[],idx:0,target:0,done:0,mode:'ready'};
let queueReq=0;
$('#queue-ready').onclick=()=>{Q.mode='ready';renderOutreach();};
$('#queue-research').onclick=()=>{Q.mode='research';renderOutreach();};
$('#queue-refresh').onclick=()=>renderOutreach();

// Call scripts come from the template library, one per lead type. The old
// SCRIPT_FOR map sent every partner type the playbook's referral ask - a
// script for a HAPPY PAST CUSTOMER ("Glad you're happy with how it turned
// out") - which is the wrong opener to read to a cold HOA board.

async function renderOutreach(){
  const token=++queueReq;
  const q=await api('/queue/today?contact='+Q.mode);
  if(token!==queueReq) return;
  $('#queue-ready').setAttribute('aria-pressed',Q.mode==='ready');
  $('#queue-research').setAttribute('aria-pressed',Q.mode==='research');
  $('#queue-intro').textContent=Q.mode==='research'
    ? 'Find and save a phone or email, or plan an in-person visit. Research does not count as a sales touch.'
    : 'Scheduled follow-ups first, then new prospects with a phone or email. Open a lead to log a visit or schedule the next step.';
  Q.target=q.target; Q.done=q.done_today; Q.idx=0;
  // Re-touches lead. A partner who already knows you converts better than a
  // cold name, so they must never sit behind thirty fresh cards.
  Q.items=[
    ...q.due.map(d=>({lead_id:d.lead_id,task_id:d.id,kind:d.kind||'call',retouch:true,
      why:d.title||'Follow-up due',name:d.name,company:d.company,phone:d.phone,
      email:d.email,city:d.city,lead_type:d.lead_type,overdue:d.overdue,
      draft:d.draft,hook:d.hook,address:d.address,website:d.website,
      touches:d.touches,outreach_label:d.outreach_label,outreach_color:d.outreach_color,
      first_name:d.first_name,last_name:d.last_name,research_notes:d.research_notes,
      contact_quality:d.contact_quality})),
    ...q.new.map(l=>({lead_id:l.id,kind:'call',retouch:false,
      why:'New — first touch',name:l.name,company:l.company,phone:l.phone,
      email:l.email,city:l.city,lead_type:l.lead_type,score:l.icp_score,
      draft:l.draft,hook:l.hook,address:l.address,website:l.website,
      touches:l.touches,outreach_label:l.outreach_label,outreach_color:l.outreach_color,
      first_name:l.first_name,last_name:l.last_name,research_notes:l.research_notes,
      contact_quality:l.contact_quality,contact_quality_label:l.contact_quality_label})),
  ];
  if(!PB){ try{ PB=await api('/playbook'); }catch(e){} }
  drawQueue();
  renderStatusBoard();
}

// Managers: recent storms from the radar hail archive, and the follow-ups
// each one booked. The nightly job queues them on its own; the button is for
// a storm that landed before a lead was imported or geocoded.
async function renderStorms(){
  const box=$('#storm-panel'); if(!box) return;
  let st; try{ st=await api('/storms?days=60'); }catch(e){ return; }
  if(!st.events.length){ box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  box.innerHTML=`<div class="storm-h">⛈ Storms over your leads, last 60 days (${st.min_size_in}"+ hail) · ${st.leads_tagged} leads carry a hail line${st.leads_unplaced?` · <button class="linkish" id="fix-addr">${st.leads_unplaced} leads can't be located - fix addresses</button>`:''}</div>`+
    st.events.slice(0,6).map(e=>`<div class="storm-row"><span><b>${esc(e.event_date)}</b> · ${e.affected} lead${e.affected===1?'':'s'} under it</span>
      <span class="lead-context">${e.queued} queued</span>
      <button class="btn-ghost small" data-storm="${esc(e.event_id)}">Queue follow-ups</button></div>`).join('');
  const fx=box.querySelector('#fix-addr'); if(fx) fx.onclick=fixAddressesModal;
  box.querySelectorAll('[data-storm]').forEach(b=>b.onclick=async()=>{
    b.disabled=true;
    try{ const r=await api('/storms/'+encodeURIComponent(b.dataset.storm)+'/queue',{method:'POST'});
      toast(`${r.queued} follow-ups queued`+(r.already_queued?`, ${r.already_queued} already were`:'')+(r.no_coords?` · ${r.no_coords} leads had no location to check`:''));
      renderStorms(); renderOutreach();
    }catch(e){ toast(e.message,true); b.disabled=false; }
  });
}

// Managers: how often reps confirm what research found, per lead type. Hidden
// until reps have judged at least one contact.
async function renderAccuracy(){
  const box=$('#research-accuracy'); if(!box) return;
  let rows=[]; try{ rows=await api('/research/accuracy'); }catch(e){ return; }
  const judged=rows.filter(r=>r.confirmed+r.wrong>0);
  box.classList.toggle('hidden',!judged.length);
  box.innerHTML=`<div class="storm-h">🔎 Research accuracy (reps' ✓ / ✗)</div>`+judged.map(r=>
    `<div class="storm-row"><span><b>${esc(r.label)}</b> · ${r.rate}% right</span>
      <span class="lead-context">${r.confirmed} confirmed · ${r.wrong} wrong · ${r.found} found</span></div>`).join('');
}

// Leads whose address the geocoder could not place: no storm can be checked
// against them. Open one, correct the address, and saving re-locates it.
async function fixAddressesModal(){
  let rows=[]; try{ rows=await api('/leads/unplaced'); }catch(e){ toast(e.message,true); return; }
  openModal(`Addresses to fix (${rows.length})`,`
    <p class="lead-context">These couldn't be placed on a map - usually a PO box, a typo or a new street.
    Open one and correct the street address; saving locates it again.</p>
    <div class="mini-lead-list">${rows.map(l=>`<div class="mini-lead" data-fix="${esc(l.id)}">
      <div class="nm">${esc(l.name)}</div><div class="sub">${esc([l.address,l.city,l.zip].filter(Boolean).join(', '))}</div></div>`).join('')
      ||'<div class="empty">Every address is located.</div>'}</div>`,null,{hideOk:true});
  $$('#modal-box [data-fix]').forEach(r=>r.onclick=()=>{ closeModal(); openLead(r.dataset.fix); });
}

// Managers: the Do Not Call registry has to be re-loaded every 31 days. The
// notice appears only when it matters - homeowner leads exist and the
// registry is missing or stale.
async function renderDncNotice(){
  const n=$('#dnc-notice'); if(!n) return;
  let st; try{ st=await api('/dnc-registry'); }catch(e){ return; }
  const need=st.open_homeowner_leads>0&&(!st.areas.length||st.stale);
  n.classList.toggle('hidden',!need&&!st.areas.length);
  n.innerHTML=need
    ? `📵 ${st.areas.length?'The Do Not Call registry is over '+st.refresh_days+' days old':'No Do Not Call registry loaded'} - homeowner numbers can't be checked. <button class="btn-ghost small" id="dnc-load">Load registry file</button>`
    : `📵 Do Not Call registry: ${st.areas.map(a=>a.area).join(', ')} loaded. <button class="btn-ghost small" id="dnc-load">Refresh</button>`;
  n.classList.toggle('warn',need);
  const b=$('#dnc-load'); if(b) b.onclick=dncModal;
}
function dncModal(){
  openModal('Do Not Call registry',`
    <p class="lead-context">Download your area codes from telemarketing.donotcall.gov, then pick the file here.
    Each area code in the file replaces what was loaded before. Homeowners on it drop out of the call queue
    and show a warning; business numbers are not affected.</p>
    <div class="field"><input type="file" id="dnc-file" accept=".txt,.csv,text/plain,text/csv"></div>
    <div id="dnc-result" class="lead-context"></div>`,
  async()=>{
    const f=$('#dnc-file').files[0]; if(!f){ toast('Pick the registry file first',true); throw new Error('name'); }
    const text=await f.text();
    try{ const r=await api('/dnc-registry',{method:'POST',body:{text}});
      toast(`Loaded ${r.loaded.toLocaleString()} numbers (${r.areas.join(', ')})`); renderDncNotice();
    }catch(e){ toast(e.message,true); throw e; }
  },{okText:'Load'});
}

// Where every contact stands, one tap from the list of them. Closed statuses
// are left off the strip — they are answers, not work.
async function renderStatusBoard(){
  const box=$('#oq-status'); if(!box) return;
  if(S.me.is_manager){ renderDncNotice(); renderStorms(); renderAccuracy(); }
  let rows=[]; try{ rows=await api('/outreach/summary'); }catch(e){ return; }
  box.innerHTML=rows.filter(r=>r.open&&r.count).map(r=>
    `<button class="os-chip" data-os="${r.key}" style="--c:${r.color}"><span class="n">${r.count}</span>${esc(r.label)}${r.due?`<span class="due">${r.due} due</span>`:''}</button>`).join('')
    ||'<span class="lead-context">No contacts yet.</span>';
  box.querySelectorAll('[data-os]').forEach(b=>b.onclick=()=>pipelineFocus({outreach:b.dataset.os,rep:S.me.username}));
}

function drawQueue(){
  const pct=Math.min(100,Math.round(Q.done/Math.max(1,Q.target)*100));
  const fill=$('#oq-fill');
  fill.style.width=pct+'%';
  fill.classList.toggle('done',Q.done>=Q.target);
  $('#oq-label').textContent=Q.mode==='research' ? `${Q.items.length} prospects to research` : `${Q.done} of ${Q.target} touches today`+
    (Q.done>=Q.target?' — target hit 🎉':'');
  const badge=$('#side-queue-badge');
  if(badge){ const left=Q.items.length-Q.idx;
    badge.textContent=left; badge.classList.toggle('hidden',!left); }

  const rest=Q.items.slice(Q.idx+1);
  $('#oq-left').textContent=Math.max(0,rest.length);
  $('#oq-upnext').innerHTML=rest.slice(0,12).map(i=>`
    <div class="mini-lead"><span class="nm">${esc(i.name||i.company)}</span>
      <span class="sub">${esc(i.city||'')}</span></div>`).join('')||
    '<div class="empty">Nothing else queued.</div>';

  $('#view-outreach .oq-bar').classList.toggle('hidden',Q.mode==='research');
  const it=Q.items[Q.idx];
  if(!it){
    $('#oq-card').innerHTML=Q.mode==='research' ? '<div class="empty">No untouched prospects need contact research right now.</div>' : Q.done>=Q.target
      ? '<div class="empty">Day\'s number is done. 🎯</div>'
      : '<div class="empty">No outreach is ready right now. Open Needs research to complete contact details, or ask your manager for more prospects.</div>';
    return;
  }

  const tel=(it.phone||'').replace(/[^0-9+]/g,'');
  const script=null;
  const type=(S.cfg.lead_types.find(t=>t.key===it.lead_type)||{}).label||it.lead_type;
  const draft=it.draft;
  // Default to whichever channel this partner can actually be reached on.
  if(it.tab===undefined) it.tab=(tel||!draft)?'call':'email';
  const card=el('div','oq-card'+(it.retouch?' retouch':''));
  card.innerHTML=`
    <div class="oq-why">${it.retouch?'↻ ':''}${esc(it.why)}${it.overdue?' · overdue':''}</div>
    <h3 class="oq-name">${esc(it.name||it.company||'(no name)')}</h3>
    <div class="oq-sub">${esc(it.company&&it.company!==it.name?it.company+' · ':'')}${esc(it.city||'')}</div>
    ${askFor(it)}
    <div class="oq-meta">${cqChip(it)}<span class="chip os" style="--c:${it.outreach_color||'#6B7280'}">${esc(it.outreach_label||'Not contacted')}</span>
      ${it.touches?`<span class="chip">Touch ${it.touches+1}</span>`:''}
      <span class="chip">${esc(type)}</span>
      ${it.score?`<span class="chip" title="Higher scores are prioritized within this queue">Priority score ${it.score}</span>`:''}
      ${tel?'':'<span class="chip">no phone</span>'}
      ${it.email?'':'<span class="chip">no email</span>'}
      ${it.hook?'':'<span class="chip">not researched</span>'}</div>
    ${!tel&&!it.email?'<p class="research-notice">Add contact details before calling or emailing. You can also schedule a visit from the lead.</p>':''}
    <div class="view-actions"><button class="btn-brand" data-open-lead>${!tel&&!it.email?'Research / edit contact':'Open lead / next step'}</button></div>
    ${Q.mode==='research'?'':`
    <div class="oq-actions">
      ${tel?`<a class="call" href="tel:${tel}" data-touch="call">📞 Call</a>`:'<button disabled>Phone needed</button>'}
    </div>
    <div data-composer>${it.msgs?composerHtml(it,it.msgs,script,it.cmp):'<div class="lead-context">Loading templates…</div>'}</div>
    <h4 class="oc-h">How did it go?</h4>
    ${outcomeHtml(it.touched)}`}
    <div class="oq-skips">
      <button data-act="skip">Skip</button>
      <button data-act="lost">Not a fit</button>
      <button class="dnc" data-act="dnc">Do not contact</button>
    </div>`;
  // The href does the dialling / opens the compose window; we only record that
  // it happened. Nothing is ever sent from here.
  card.querySelector('[data-open-lead]').onclick=()=>openLead(it.lead_id);
  card.querySelectorAll('[data-act]').forEach(b=>b.onclick=()=>qSkip(b.dataset.act));
  $('#oq-card').innerHTML=''; $('#oq-card').appendChild(card);
  if(Q.mode==='research') return;
  // Opening the dialler / Messages / Gmail records nothing by itself — the
  // outcome does, because "I tapped Call" says nothing about who to call back.
  const touched=kind=>{
    it.touched=kind;
    card.querySelectorAll('[data-outcome]').forEach(b=>b.classList.toggle('hint',
      b.dataset.outcome==={text:'texted',email:'emailed'}[kind]));
  };
  card.querySelectorAll('.oq-actions [data-touch="call"]').forEach(a=>a.addEventListener('click',()=>touched('call')));
  wireOutcomes(card, it.lead_id, ()=>({kind:it.touched, task_id:it.task_id, cmp:it.cmp}), ()=>{ Q.done++; qNext(); renderStatusBoard(); });
  if(it.msgs){ wireComposer(card, it, it.msgs, script, it.cmp, touched); return; }
  const idx=Q.idx;
  api('/leads/'+it.lead_id+'/messages').then(m=>{
    it.msgs=m; it.cmp=it.cmp||{tab:tel?(m.call.templates.length?'call':'voicemail'):(m.email.templates.length?'email':'text')};
    if(Q.idx===idx) drawQueue();
  }).catch(()=>{ it.msgs={voicemail:{templates:[]},text:{templates:[]},email:{templates:[]}}; it.cmp={}; if(Q.idx===idx) drawQueue(); });
}

// Prefills a compose window in the rep's OWN Google account. Draft-only by
// construction — Gmail opens it, the rep reads it, the rep decides to send.
function gmailUrl(to, draft){
  const q=encodeURIComponent;
  return 'https://mail.google.com/mail/?view=cm&fs=1'+
    '&to='+q(to)+'&su='+q(draft.subject)+'&body='+q(draft.body);
}

async function qLog(kind){
  const it=Q.items[Q.idx];
  try{
    await api('/leads/'+it.lead_id+'/activities',{method:'POST',body:{kind}});
    if(it.task_id) await api('/tasks/'+it.task_id,{method:'PATCH',body:{done:true}});
    Q.done++; toast('Logged ✓');
  }catch(e){ toast(e.message,true); return; }
  qNext();
}

async function qSkip(act){
  const it=Q.items[Q.idx];
  try{
    if(act==='lost'){
      await api('/leads/'+it.lead_id+'/stage',{method:'PATCH',
        body:{stage:'lost',lost_reason:'Not a fit'}});
      toast('Marked not a fit');
    } else if(act==='dnc'){
      // Suppress whichever handle we have; both when we have both, since the
      // point is that nobody here contacts them again by any route.
      if(it.email) await api('/suppressions',{method:'POST',
        body:{kind:'email',value:it.email,reason:'Rep marked do-not-contact'}});
      if(it.phone) await api('/suppressions',{method:'POST',
        body:{kind:'phone',value:it.phone,reason:'Rep marked do-not-contact'}});
      if(!it.email&&!it.phone) await api('/leads/'+it.lead_id,{method:'PUT',body:{dnc:1}});
      toast('Added to do-not-contact');
    }
  }catch(e){ toast(e.message,true); return; }
  qNext();
}

function qNext(){ Q.idx++; drawQueue(); }

// ── My Day ───────────────────────────────────────────────────────────────────
$('#start-outreach').onclick=()=>{Q.mode='ready';go('outreach');};
$('#all-hot').onclick=()=>pipelineFocus({attention:'hot',rep:S.me.username});
$('#all-needs-step').onclick=()=>pipelineFocus({attention:'needs_step',rep:S.me.username});
async function renderMyDay(){
  const hour=new Date().getHours();
  const greet=hour<12?'Good morning':hour<17?'Good afternoon':'Good evening';
  $('#myday-greeting').textContent=`${greet}, ${S.me.full_name||S.me.username}`;
  const rep=encodeURIComponent(S.me.username);
  const [tasks, summary, hot, needsStep, globalSummary] = await Promise.all([
    api('/tasks?scope=today&rep='+rep), api('/pipeline/summary?rep='+rep),
    api('/leads?attention=hot&limit=6&rep='+rep),
    api('/leads?attention=needs_step&limit=6&rep='+rep), api('/pipeline/summary'),
  ]);
  S.summary=globalSummary;
  const overdue=tasks.filter(t=>t.overdue).length;
  $('#myday-direction').textContent=tasks.length
    ? `${tasks.length} scheduled follow-ups need attention. Work these first, then start outreach.`
    : 'No follow-ups scheduled for today. Start outreach or set the next step on an active conversation.';
  $('#myday-stats').innerHTML=[
    ['Tasks due', tasks.length, 'tasks'], ['Overdue', overdue, 'tasks'],
    ['My open leads', summary.open_leads, 'pipeline'],
    ['My pipeline', money(summary.open_value), 'pipeline'],
  ].map(([l,n,nav])=>`<button class="stat-chip" data-nav="${nav}"><span class="n">${n}</span><span class="l">${l}</span></button>`).join('');
  $$('#myday-stats .stat-chip').forEach(c=>c.onclick=()=>{
    if(c.dataset.nav==='pipeline') pipelineFocus({rep:S.me.username});
    else $('#myday-tasks').scrollIntoView({behavior:'smooth',block:'start'});
  });
  $('#tasks-count').textContent=tasks.length;
  const badge=$('#side-task-badge');
  if(badge){ badge.textContent=overdue; badge.classList.toggle('hidden',!overdue); }
  updateSidebar();
  $('#myday-tasks').innerHTML=tasks.length?'':'<div class="empty">No scheduled follow-ups today. Your outreach queue is ready to review.</div>';
  tasks.forEach(t=>$('#myday-tasks').appendChild(taskRow(t)));
  renderMini($('#myday-hot'),hot,'No hot leads right now.');
  renderMini($('#myday-stalled'),needsStep,'Every active conversation has a next step, or you have not started one yet.');
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
//
// Search runs on the SERVER (?q=). It used to filter the fetched page in the
// browser, which meant it only ever searched the most recently updated 1000
// leads — fine for a few hundred homeowners, but it hid most of the table once
// prospecting started importing partners in the tens of thousands.
let pipeSearch='';
let searchTimer=null;
// Monotonic token: keystrokes fire overlapping requests and they can come back
// out of order, so a stale response must not overwrite a newer board.
let pipeReq=0;

$('#pipeline-search').oninput=e=>{
  pipeSearch=e.target.value.trim();
  // Debounced, because each render is now a query rather than an array filter.
  clearTimeout(searchTimer);
  searchTimer=setTimeout(renderPipeline, 200);
};
$('#pipeline-rep').onchange=()=>renderPipeline();
$('#pipeline-service').onchange=()=>renderPipeline();
function buildServiceSelect(){
  const sel=$('#pipeline-service');
  if(sel&&!sel.options.length)
    sel.innerHTML='<option value="">All services</option>'+
      S.cfg.services.map(s=>`<option value="${s.key}">${s.icon} ${esc(s.label)}</option>`).join('');
}
let pipeMode='list', pipeRows=[];
function pipelineFocus(filters={}){
  buildPipelineFilters();
  pipeSearch=''; $('#pipeline-search').value='';
  for(const key of ['rep','service','type','contact','attention','stage','outreach','contact_quality']) $('#pipeline-'+key).value=filters[key]||'';
  go('pipeline');
}
function buildPipelineFilters(){
  buildServiceSelect();
  const type=$('#pipeline-type'), stage=$('#pipeline-stage');
  if(!type.options.length) type.innerHTML='<option value="">All lead types</option>'+S.cfg.lead_types.map(t=>`<option value="${esc(t.key)}">${esc(t.label)}</option>`).join('');
  if(!stage.options.length) stage.innerHTML='<option value="">All stages</option>'+S.cfg.stages.map(t=>`<option value="${esc(t.key)}">${esc(t.label)}</option>`).join('');
  const os=$('#pipeline-outreach');
  const cq=$('#pipeline-contact_quality');
  if(!cq.options.length) cq.innerHTML='<option value="">Any contact quality</option>'+S.cfg.contact_quality.map(q=>`<option value="${q.key}">${esc(q.label)}</option>`).join('');
  if(!os.options.length) os.innerHTML='<option value="">Any outreach status</option>'+S.cfg.outreach_statuses.map(t=>`<option value="${esc(t.key)}">${esc(t.label)}</option>`).join('');
}
for(const key of ['type','contact','attention','stage','outreach','contact_quality']) $('#pipeline-'+key).onchange=()=>renderPipeline();
$('#pipeline-reset').onclick=()=>pipelineFocus();
$('#pipe-list').onclick=()=>{pipeMode='list';renderPipeline();};
$('#pipe-grouped').onclick=()=>{pipeMode='grouped';renderPipeline();};
$('#pipe-board').onclick=()=>{pipeMode='board';renderPipeline();};
// Category tabs drive the same #pipeline-type select the filters read, so
// pipelineFocus() and "Clear filters" keep working through one value.
$('#pipeline-type-tabs').onclick=e=>{
  const b=e.target.closest('.type-tab'); if(!b) return;
  $('#pipeline-type').value=b.dataset.type; renderPipeline();
};
function renderTypeTabs(counts){
  const cur=$('#pipeline-type').value;
  const total=Object.values(counts).reduce((a,n)=>a+n,0);
  // Empty categories are left off so the row stays short on a phone, except
  // the selected one: a tab that vanished while picked could not be seen.
  const tabs=S.cfg.lead_types.filter(t=>counts[t.key]||t.key===cur)
    .map(t=>[t.key,t.label,counts[t.key]||0]);
  $('#pipeline-type-tabs').innerHTML=[['','All',total],...tabs].map(([key,label,n])=>
    `<button class="type-tab" data-type="${esc(key)}" aria-pressed="${key===cur}">${esc(label)}<span class="n">${n.toLocaleString()}</span></button>`).join('');
}
$('#pipeline-more').onclick=()=>renderPipeline(true);
async function renderPipeline(more=false){
  buildPipelineFilters();
  const offset=more===true?pipeRows.length:0;
  const filters=[];
  for(const key of ['rep','service','type','contact','attention','stage','outreach','contact_quality']){
    const value=$('#pipeline-'+key).value;
    if(value&&(key!=='rep'||S.me.is_manager)) filters.push(key+'='+encodeURIComponent(value));
  }
  if(pipeSearch) filters.push('q='+encodeURIComponent(pipeSearch));
  const qs=[...filters,'limit=101&offset='+offset];
  // Grouped, the server orders by category so paging continues within a group.
  if(pipeMode==='grouped') qs.push('sort=type');
  const token=++pipeReq;
  const [page,summary,typeCounts]=await Promise.all([api('/leads?'+qs.join('&')),api('/pipeline/summary'),
    api('/leads/type-counts?'+filters.join('&'))]);
  if(token!==pipeReq) return;
  const leads=offset?pipeRows.concat(page.slice(0,100)):page.slice(0,100);
  pipeRows=leads;
  S.summary=summary;
  renderTypeTabs(typeCounts);
  // A filtered page must never replace the unfiltered cache used by referrals.
  if(!filters.length) S.leadCache=leads;
  const list=$('#pipeline-list'), board=$('#kanban');
  list.innerHTML=''; board.innerHTML='';
  list.classList.toggle('hidden',pipeMode==='board');
  board.classList.toggle('hidden',pipeMode!=='board');
  for(const m of ['list','grouped','board']) $('#pipe-'+m).setAttribute('aria-pressed',pipeMode===m);
  $('#pipeline-count').textContent=leads.length?`Showing ${leads.length} leads${page.length>100?' · show more to continue':''}. Sidebar totals include the whole pipeline.`:'No leads match these filters.';
  $('#pipeline-more').classList.toggle('hidden',page.length<=100);
  if(pipeMode!=='board'){
    let group=null;
    leads.forEach(l=>{
      if(pipeMode==='grouped'&&l.lead_type!==group){
        group=l.lead_type;
        // The count is the category's real total; the list holds one page of it.
        const h=el('h3','type-group-h');
        h.innerHTML=`${esc((S.cfg.lead_types.find(t=>t.key===group)||{}).label||group)}<span class="n">${(typeCounts[group]||0).toLocaleString()}</span>`;
        list.appendChild(h);
      }
      const row=el('button','lead-list-row'); row.dataset.id=l.id;
      row.innerHTML=`<span><b>${esc(l.name)}</b><span class="lead-context">${esc([l.company!==l.name?l.company:'',l.city].filter(Boolean).join(' · '))}</span></span>
        <span>${esc(l.stage_label)}<span class="lead-context"><span class="os-dot" style="background:${l.outreach_color}"></span>${esc(l.outreach_label)} · ${esc((S.cfg.lead_types.find(t=>t.key===l.lead_type)||{}).label||l.lead_type)}</span></span>
        <span>${l.phone||l.email?'Contact details available':'Needs contact research'}<span class="lead-context">${esc(l.phone||l.email||'Open to add phone or email')}</span></span>
        <span>${l.next_action_at?esc(dueLabel(l.next_action_at)):['won','lost'].includes(l.stage)?'Closed':'No next step'}<span class="lead-context">${esc(repName(l.rep))}${l.est_value?' · '+money(l.est_value):''}</span></span>`;
      row.onclick=()=>openLead(l.id); list.appendChild(row);
    });
  }else{
    S.cfg.stages.forEach(st=>{
      const col=el('div','kcol'); col.dataset.stage=st.key;
      const items=leads.filter(l=>l.stage===st.key);
      col.innerHTML=`<div class="kcol-head"><span class="kcol-dot" style="background:${st.color}"></span>${esc(st.label)}<span class="kcol-count">${items.length}</span></div><div class="kcol-body"></div>`;
      items.forEach(l=>col.querySelector('.kcol-body').appendChild(kcard(l)));
      board.appendChild(col);
    });
  }
  updateSidebar();
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

// ── Lead detail: a full page with its own URL from every screen ────────────
let detailReq=0;
async function openLead(id, push=true){
  const token=++detailReq;
  if(!S.openLeadId){
    S.detailTrigger=document.activeElement; S.detailDirty=false;
    S.detailScroll=window.scrollY;
    S.detailNeedsRefresh=!$('#view-'+S.view).classList.contains('active');
    // Give the page underneath an explicit route for browser Back.
    if(push) history.replaceState(history.state,'','#'+S.view);
  }
  if(push) history.pushState({returnView:S.view},'','#lead/'+encodeURIComponent(id));
  S.openLeadId=id;
  $$('.view').forEach(v=>v.classList.remove('active'));
  $('#view-lead').classList.add('active');
  $('#view-title').textContent='Contact details';
  $('#lead-back').textContent='← Back to '+TITLES[S.view];
  $('#lead-panel').innerHTML='<div class="dsec" role="status">Loading…</div>';
  window.scrollTo(0,0);
  $('#lead-back').focus({preventScroll:true});
  let l;
  try{ l=await api('/leads/'+encodeURIComponent(id)); }catch(e){
    if(token===detailReq) $('#lead-panel').innerHTML=`<div class="dsec" role="alert">Unable to load this contact: ${esc(e.message)}. Use Back to return to your list.</div>`;
    return;
  }
  if(token!==detailReq) return;
  renderDrawer(l);
}
function closeDetail(){
  history.replaceState({},'','#'+S.view);
  go(S.view,false);
}
$('#lead-back').onclick=closeDetail;
document.addEventListener('keydown',e=>{
  const panel=$('#modal').classList.contains('open')?$('#modal-box'):null;
  if(!panel&&e.key==='Escape'&&S.openLeadId){e.preventDefault();closeDetail();}
  if(!panel) return;
  if(e.key==='Escape'){
    e.preventDefault();
    if(panel.id==='modal-box') closeModal(); else closeDetail();
  }
  if(e.key==='Tab'){
    const focusable=$$('button:not(:disabled),a[href],input,select,textarea,summary',panel).filter(x=>x.getClientRects().length);
    const first=focusable[0], last=focusable[focusable.length-1];
    if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
    else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
  }
});

function renderDrawer(l){
  if(S.openLeadId!==l.id) return;
  document.title=l.name+' — Pipeline';
  const typeMeta=S.cfg.lead_types.find(t=>t.key===l.lead_type);
  const phone=(l.phone||'').replace(/[^0-9+]/g,'');
  const fullAddress=[l.address,l.city,[l.state,l.zip].filter(Boolean).join(' ')].filter(Boolean).join(', ');
  const stageOpts=S.cfg.stages.map(s=>`<option value="${s.key}" ${s.key===l.stage?'selected':''}>${esc(s.label)}</option>`).join('');
  const p=$('#lead-panel');
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
    <div class="dh">
      <h1 class="dh-name">${esc(l.name)}</h1>
      ${l.company&&l.company!==l.name?`<p class="lead-context">${esc(l.company)}</p>`:''}
      <div class="lead-address"><span class="lead-address-label">Address</span>
        <div class="lead-address-text">${esc(fullAddress||'No address added')}</div>
        <div class="lead-address-actions">
          ${fullAddress?`<a class="btn-ghost small" href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(fullAddress)}" target="_blank" rel="noopener noreferrer">Open in Maps ↗</a>`:''}
          <button class="btn-ghost small" id="edit-address">${fullAddress?'Edit address':'Add address'}</button>
        </div>
      </div>
      <div class="task-meta"><span class="type-badge">${l.service_icon} ${esc(l.service_label)}</span>
      <span class="type-badge">${esc(typeMeta?typeMeta.label:l.lead_type)}</span>
      ${l.plan_name?`<span class="type-badge plan">♻ ${esc(l.plan_name)}</span>`:''}
      <span>${esc(repName(l.rep))}</span>${l.est_value?'<span>'+money(l.est_value)+esc(l.value_suffix)+'</span>':''}
      ${l.referred_by_name?'<span>via '+esc(l.referred_by_name)+'</span>':''}</div>
    </div>
    ${l.stalled?'<div class="stalled-banner">⚠ No activity in a while. Reach out or schedule a next step.</div>':''}
    ${l.dnc_registry?'<div class="stalled-banner dnc-banner">📵 On the National Do Not Call Registry. Don\'t cold call or text this number. Exempt only if they bought from us in the last 18 months or contacted us in the last 3.</div>':''}
    <div class="dgrid">
    ${referralsHtml}
    <div class="dsec"><h5>Stage</h5>
      <select class="stage-select" id="d-stage">${stageOpts}</select></div>
    <div class="dsec"><h5>Reach out</h5>
      <p class="lead-context">${esc(l.phone||'No phone')} · ${esc(l.email||'No email')}</p>
      <button class="btn-ghost small" id="edit-contact">Edit contact details</button>
      ${l.company?`<a class="research-link" target="_blank" rel="noopener noreferrer" href="https://www.google.com/search?q=${encodeURIComponent(l.company+' '+(l.city||'')+' contact')}">Search business contact ↗</a>`:''}
      ${l.hook?`<details class="research-notes"><summary>Research notes</summary><p>${esc(l.hook)}</p></details>`:''}
      <div class="contact-actions">
        <a class="call" href="${phone?'tel:'+phone:'#'}" data-log="call">📞 Call</a>
        <a class="text" href="${phone?'sms:'+phone:'#'}" data-log="text">💬 Text</a>
        <a class="email" href="${l.email?'mailto:'+esc(l.email):'#'}" data-log="email">✉️ Email</a>
      </div></div>
    <div class="dsec dsec-wide"><h5>Outreach <span class="chip os" style="--c:${l.outreach_color}">${esc(l.outreach_label)}</span></h5>
      <div data-composer><div class="lead-context">Loading templates…</div></div>
      <h5 class="oc-h">Log what happened</h5>
      <div data-outcomes>${outcomeHtml('')}</div>
      <label class="os-set">Set status manually
        <select class="mini-select" id="d-os">${S.cfg.outreach_statuses.map(s=>`<option value="${s.key}" ${s.key===l.outreach_status?'selected':''}>${esc(s.label)}</option>`).join('')}</select></label>
    </div>
    ${researchPanelHtml(l)}
    <div class="dsec"><h5>Log activity</h5>
      <div class="log-row">
        ${['call','text','email','door','meeting','note'].map(k=>`<button class="log-btn" data-logkind="${k}">${KIND_ICO[k]} ${k}</button>`).join('')}
      </div>
      <div id="d-log-form"></div>
    </div>
    <div class="dsec"><h5>Tasks</h5><div id="d-tasks"></div>
      <button class="btn-ghost small" id="d-add-task">+ Add task</button></div>
    <div class="dsec"><h5>Handoff &amp; cross-sell</h5>
      <div class="drawer-btns">
        <button class="btn-brand" id="d-estimate">📄 Start estimate</button>
        <button class="btn-ghost" id="d-den">${l.crm_contact_id?'✓ In The Den — view job status':'⬆ Push to The Den'}</button>
        <div id="d-pitch-row"></div>
        <div class="est-status" id="d-est-status"></div>
      </div></div>
    <details class="dsec"><summary>Maintenance plan</summary><div id="d-plan"></div></details>
    <div class="dsec"><h5>Follow-up cadence</h5>
      <div id="d-cadences"></div></div>
    <div class="dsec dsec-wide"><h5>Documents</h5><div id="d-documents"></div>
      <label class="btn-ghost small doc-upload">＋ Upload document
        <input type="file" id="d-doc-file" hidden></label></div>
    <div class="dsec"><h5>Timeline</h5><div class="timeline" id="d-timeline"></div></div>
    <details class="dsec" id="lead-details"><summary>Edit contact &amp; lead details</summary><div id="d-fields"></div></details>
    <div class="dsec"><button class="btn-danger" id="d-delete">Delete lead</button></div>
    </div><!-- /dgrid -->
  `;
  $('#edit-address').onclick=()=>{
    $('#lead-details').open=true;
    $('#lead-details').scrollIntoView({behavior:'smooth',block:'start'});
    $('#f-address')?.focus({preventScroll:true});
  };
  $('#edit-contact').onclick=()=>{
    $('#lead-details').open=true;
    $('#lead-details').scrollIntoView({behavior:'smooth',block:'start'});
    $('#f-phone')?.focus({preventScroll:true});
  };
  p.querySelectorAll('.contact-actions a[href="#"]').forEach(a=>{
    a.removeAttribute('href');a.setAttribute('aria-disabled','true');a.classList.add('disabled');
  });
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
  $('#d-stage').onchange=async e=>{
    await moveStage(l, e.target.value);
    const fresh=await api('/leads/'+l.id); renderDrawer(fresh);
  };
  // contact action logging
  p.querySelectorAll('[data-log]').forEach(a=>a.addEventListener('click',()=>{
    if(!a.getAttribute('href')) return;
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
  wireResearch(p,l);
  // Outreach: templates, outcomes, and a manual status fix.
  const dSt={};
  const dScript=null;
  api('/leads/'+l.id+'/messages').then(m=>{
    if(S.openLeadId&&S.openLeadId!==l.id) return;
    const box=p.querySelector('[data-composer]'); if(!box) return;
    box.innerHTML=composerHtml(l,m,dScript,dSt);
    wireComposer(p, l, m, dScript, dSt, kind=>p.querySelectorAll('[data-outcome]').forEach(b=>
      b.classList.toggle('hint', b.dataset.outcome==={text:'texted',email:'emailed'}[kind])));
  }).catch(()=>{ const b=p.querySelector('[data-composer]'); if(b) b.innerHTML=''; });
  wireOutcomes(p, l.id, ()=>({cmp:dSt}), async()=>{ const fresh=await api('/leads/'+l.id); renderDrawer(fresh);
    if(S.view==='pipeline') renderPipeline(); });
  $('#d-os').onchange=async e=>{
    try{ await api('/leads/'+l.id+'/outreach-status',{method:'PATCH',body:{status:e.target.value}});
      toast('Status updated'); const fresh=await api('/leads/'+l.id); renderDrawer(fresh);
    }catch(err){ toast(err.message,true); }
  };
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
  // Cross-sell: one button per OTHER service line — spins off a new deal for the
  // same customer, letting a roof lead become a window-cleaning or plan deal.
  const pitchRow=p.querySelector('#d-pitch-row');
  S.cfg.services.filter(s=>s.key!==l.service).forEach(s=>{
    const b=el('button','btn-ghost'); b.innerHTML=`${s.icon} Pitch ${esc(s.label)}`;
    b.onclick=async()=>{
      try{
        const body={first_name:l.first_name,last_name:l.last_name,company:l.company,
          phone:l.phone,email:l.email,address:l.address,city:l.city,state:l.state,zip:l.zip,
          lead_type:l.lead_type,service:s.key,source:'existing_customer',
          temperature:'warm',referred_by:l.referred_by};
        if(S.me.is_manager) body.rep=l.rep;
        const nw=await api('/leads',{method:'POST',body});
        await api('/leads/'+nw.id+'/activities',{method:'POST',
          body:{kind:'system',body:`${s.label} pitch — spun off from the ${l.service_label} deal`}});
        toast(`${s.icon} ${s.label} deal created`);
        if(S.view==='pipeline')renderPipeline();
        openLead(nw.id);
      }catch(e){ toast(e.message,true); }
    };
    pitchRow.appendChild(b);
  });
  renderPlanSection(l);
  renderDocuments(l);
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
// Maintenance plan: assign a catalog plan + billing to this deal. Selecting a
// plan prefills the billing cadence, suggested price, and est_value.
function renderPlanSection(l){
  const box=$('#d-plan');
  const plans=S.cfg.plans||[];
  const billOpts=S.cfg.billing_options.map(b=>`<option value="${b.key}" ${b.key===(l.billing||'')?'selected':''}>${esc(b.label)}</option>`).join('');
  const planOpts='<option value="">— No plan (one-time / custom) —</option>'+
    plans.map(p=>`<option value="${p.id}" ${p.id===l.plan?'selected':''}>${esc(p.name)} · ${p.custom_pricing?'custom':'$'+p.suggested_price+'/mo'}</option>`).join('');
  const cur=plans.find(p=>p.id===l.plan);
  box.innerHTML=`
    <div class="field"><label>Plan</label><select id="d-plan-sel">${planOpts}</select></div>
    <div class="field-row">
      <div class="field"><label>Billing</label><select id="d-bill-sel">${billOpts}</select></div>
      <div class="field"><label>Price ${l.billing?money(0).slice(0,1):''}</label><input id="d-plan-price" type="number" value="${l.est_value||''}"></div>
    </div>
    ${cur?`<div class="plan-includes"><b>${esc(cur.name)}</b> includes:<ul>${cur.includes.map(i=>`<li>${esc(i)}</li>`).join('')}</ul>
      <div class="plan-pitch">💬 ${esc(cur.pitch)}</div></div>`:''}
    <button class="btn-brand small" id="d-plan-save">Save plan</button>`;
  const sel=box.querySelector('#d-plan-sel');
  sel.onchange=()=>{
    const p=plans.find(x=>x.id===sel.value);
    if(p){
      box.querySelector('#d-bill-sel').value=p.billing||'monthly';
      if(!p.custom_pricing) box.querySelector('#d-plan-price').value=p.suggested_price;
    }
  };
  box.querySelector('#d-plan-save').onclick=async()=>{
    try{
      await api('/leads/'+l.id,{method:'PUT',body:{plan:sel.value,
        billing:box.querySelector('#d-bill-sel').value,
        est_value:box.querySelector('#d-plan-price').value}});
      toast('Plan saved'); const fresh=await api('/leads/'+l.id); renderDrawer(fresh);
    }catch(e){ toast(e.message,true); }
  };
}

// Documents: contracts, photos, proposals — stored on the persistent volume.
async function renderDocuments(l){
  const box=$('#d-documents');
  box.innerHTML='<div class="empty">Loading…</div>';
  let docs=[];
  try{ docs=await api('/leads/'+l.id+'/documents'); }catch(e){}
  box.innerHTML = docs.length?'' : '<div class="empty">No documents yet.</div>';
  docs.forEach(d=>{
    const row=el('div','doc-row');
    row.innerHTML=`<span class="doc-ico">${docIcon(d.orig_name)}</span>
      <a class="doc-name" href="${d.url}" target="_blank" rel="noopener">${esc(d.orig_name)}</a>
      <span class="doc-meta">${fmtBytes(d.size)} · ${timeAgo(d.created_at)}</span>
      <button class="doc-del" title="Delete">🗑</button>`;
    row.querySelector('.doc-del').onclick=async(ev)=>{
      ev.preventDefault();
      if(!confirm('Delete '+d.orig_name+'?')) return;
      await api('/documents/'+d.id,{method:'DELETE'}); toast('Deleted'); renderDocuments(l);
    };
    box.appendChild(row);
  });
  const input=$('#d-doc-file');
  if(input) input.onchange=async()=>{
    const f=input.files[0]; if(!f) return;
    const fd=new FormData(); fd.append('file', f);
    try{
      const r=await fetch(BASE+'/api/leads/'+l.id+'/documents',{method:'POST',body:fd});
      const j=await r.json();
      if(!r.ok) throw new Error(j.error||'Upload failed');
      toast('Uploaded ✓'); renderDocuments(l);
    }catch(e){ toast(e.message,true); }
    input.value='';
  };
}
function docIcon(name){
  const e=(name.split('.').pop()||'').toLowerCase();
  if(['pdf'].includes(e)) return '📄';
  if(['png','jpg','jpeg','gif','heic','webp'].includes(e)) return '🖼';
  if(['doc','docx'].includes(e)) return '📝';
  if(['xls','xlsx','csv'].includes(e)) return '📊';
  return '📎';
}
function fmtBytes(n){ if(!n) return '0 B'; const u=['B','KB','MB','GB']; let i=0; while(n>=1024&&i<3){n/=1024;i++;} return n.toFixed(i?1:0)+' '+u[i]; }

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
async function renderFields(l){
  const cfg=S.cfg;
  const typeSel=cfg.lead_types.map(t=>`<option value="${t.key}" ${t.key===l.lead_type?'selected':''}>${esc(t.label)}</option>`).join('');
  const sources=[...new Set([...cfg.sources,l.source].filter(Boolean))];
  const srcSel='<option value="">—</option>'+sources.map(s=>`<option value="${esc(s)}" ${s===l.source?'selected':''}>${esc(s)}</option>`).join('');
  const tempSel=cfg.temperature.map(t=>`<option value="${t}" ${t===l.temperature?'selected':''}>${esc(t)}</option>`).join('');
  let partners=[];
  try{ partners=await api('/partners'); }catch(e){ toast('Could not load referral partners',true); }
  if(S.openLeadId!==l.id) return;
  if(l.referred_by&&!partners.some(x=>x.id===l.referred_by)) partners.push({id:l.referred_by,name:l.referred_by_name||'Current partner'});
  const partnerOpts='<option value="">—</option>'+partners.filter(x=>x.id!==l.id)
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
  const date=new Date(Date.now()+86400000);
  const tomorrow=new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
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
    ['Active plans', d.active_plans, 'on maintenance'],
    ['Monthly recurring revenue', money(d.mrr), money(d.arr)+'/yr recurring'],
  ].map(([l,n,s])=>`<div class="kpi"><div class="n">${n}</div><div class="l">${l}</div>${s?`<div class="sub">${s}</div>`:''}</div>`).join('');
  // service-line split
  $('#dash-services').innerHTML=Object.values(d.by_service||{}).map(s=>
    `<div class="kpi"><div class="n">${s.icon} ${money(s.open_value)}</div>
      <div class="l">${esc(s.label)} pipeline</div>
      <div class="sub">${s.open} open · ${s.won} won (${money(s.won_value)}) in ${d.days}d</div></div>`).join('');
  // plan mix (recurring book of business), rebuilt idempotently
  document.getElementById('dash-planmix')?.remove();
  const mix=Object.entries(d.plan_mix||{});
  if(mix.length){
    const wrap=el('div','bar-list'); wrap.id='dash-planmix'; wrap.style.marginTop='10px';
    $('#dash-services').after(wrap);
    barList(wrap, Object.fromEntries(mix));
  }
  // funnel
  const maxC=Math.max(1,...d.stages.map(s=>d.stage_counts[s.key]||0));
  $('#dash-funnel').innerHTML=d.stages.map(s=>{
    const c=d.stage_counts[s.key]||0;
    return `<div class="funnel-row" data-stage="${s.key}" title="Open in pipeline"><div class="funnel-label">${esc(s.label)}</div>
      <div class="funnel-bar" style="width:${Math.max(8,100*c/maxC)}%;background:${s.color}">${c}</div></div>`;
  }).join('');
  $$('#dash-funnel .funnel-row').forEach(r=>r.onclick=()=>{
    pipelineFocus({stage:r.dataset.stage,rep});
  });
  barList($('#dash-activity'), Object.fromEntries(Object.entries(d.activity||{}).filter(([kind])=>kind!=='system')));
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
// ── Template library (Playbook) ─────────────────────────────────────────────
// Everyone reads it and can copy from it; managers add, edit and archive.
// The server enforces the rules (banned openers, known fill-ins, length) and
// the editor previews against a sample contact as the manager types.
let TPL=null;
const TF={channel:'',audience:''};
async function renderTemplates(){
  if(!TPL) TPL=await api('/templates');
  const box=$('#playbook-templates'); if(!box) return;
  const q=($('#playbook-search').value||'').toLowerCase();
  const aud=S.cfg.audiences, audLabel=k=>(aud.find(a=>a.key===k)||{}).label||k;
  const rows=TPL.filter(t=>(!TF.channel||t.channel===TF.channel)&&(!TF.audience||t.audience===TF.audience)
    &&(!q||(t.name+' '+t.subject+' '+t.body).toLowerCase().includes(q)));
  $('#tpl-filters').innerHTML=`
    <select class="mini-select" id="tf-channel"><option value="">All channels</option>${S.cfg.template_channels.map(c=>`<option value="${c}" ${TF.channel===c?'selected':''}>${esc(CH_LABEL[c])}</option>`).join('')}</select>
    <select class="mini-select" id="tf-audience"><option value="">Everyone</option>${aud.map(a=>`<option value="${a.key}" ${TF.audience===a.key?'selected':''}>${esc(a.label)}</option>`).join('')}</select>
    ${S.me.is_manager?'<button class="btn-brand small" id="tpl-new">＋ New template</button>':''}`;
  $('#tf-channel').onchange=e=>{TF.channel=e.target.value;renderTemplates();};
  $('#tf-audience').onchange=e=>{TF.audience=e.target.value;renderTemplates();};
  if($('#tpl-new')) $('#tpl-new').onclick=()=>templateModal({channel:TF.channel||'text',audience:TF.audience||'homeowner',step:'first'});
  box.innerHTML=rows.map(t=>`<div class="card tpl-card">
      <h4>${{email:'✉️',text:'💬',voicemail:'📼',call:'📞'}[t.channel]} ${esc(t.name)}
        <span class="type-badge">${esc(audLabel(t.audience))}</span>
        <span class="type-badge">${esc(t.step==='any'?'any touch':t.step)}</span>
        ${t.used?`<span class="type-badge tpl-stat" title="Touches that used it, and how many became a conversation">used ${t.used} · ${t.good} engaged (${Math.round(100*t.good/t.used)}%)</span>`:''}</h4>
      ${t.subject?`<div class="tpl-subj">${esc(t.subject)}</div>`:''}
      <div class="a tpl-body">${esc(t.body)}</div>
      <div class="drawer-btns"><button class="btn-ghost small" data-copy="${t.id}">Copy</button>
      ${S.me.is_manager?`<button class="btn-ghost small" data-edit="${t.id}">Edit</button>`:''}</div></div>`).join('')
    ||'<div class="empty">No templates match.</div>';
  box.querySelectorAll('[data-copy]').forEach(b=>b.onclick=async()=>{
    const t=TPL.find(x=>x.id===b.dataset.copy);
    try{ await navigator.clipboard.writeText((t.subject?t.subject+'\n\n':'')+t.body); toast('Copied'); }
    catch(e){ toast('Copy not allowed here',true); }
  });
  box.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>templateModal(TPL.find(x=>x.id===b.dataset.edit)));
}

function templateModal(t){
  const isNew=!t.id;
  const opt=(list,val,lab)=>list.map(k=>`<option value="${k}" ${k===val?'selected':''}>${esc(lab(k))}</option>`).join('');
  const steps={first:'First touch',followup:'Follow-up',breakup:'Last touch',any:'Any touch'};
  openModal(isNew?'New template':'Edit template', `
    <div class="field"><label>Name</label><input id="tm-name" value="${esc(t.name||'')}"></div>
    <div class="tm-row">
      <div class="field"><label>Channel</label><select id="tm-channel">${opt(S.cfg.template_channels,t.channel,c=>CH_LABEL[c])}</select></div>
      <div class="field"><label>For</label><select id="tm-audience">${opt(S.cfg.audiences.map(a=>a.key),t.audience,k=>S.cfg.audiences.find(a=>a.key===k).label)}</select></div>
      <div class="field"><label>Touch</label><select id="tm-step">${opt(S.cfg.template_steps,t.step,s=>steps[s])}</select></div>
    </div>
    <div class="field" id="tm-subj-wrap"><label>Subject</label><input id="tm-subject" value="${esc(t.subject||'')}"></div>
    <div class="field"><label>Message</label><textarea id="tm-body" rows="7">${esc(t.body||'')}</textarea>
      <div class="cmp-hint">Fill-ins: ${S.cfg.template_slots.map(s=>`<code>{${s}}</code>`).join(' ')}</div></div>
    <div class="tm-preview"><b>Preview</b> <span class="cmp-hint" id="tm-stats"></span><div id="tm-out" class="oq-script"></div>
      <div id="tm-problems" class="tm-problems"></div></div>
    ${isNew?'':'<button class="btn-danger small" id="tm-archive">Archive this template</button>'}`,
  async()=>{
    const body=tmRead();
    try{
      if(isNew) await api('/templates',{method:'POST',body});
      else await api('/templates/'+t.id,{method:'PUT',body});
      TPL=null; toast('Template saved'); renderTemplates();
    }catch(e){ toast(e.message,true); throw e; }
  });
  const tmRead=()=>({name:$('#tm-name').value,channel:$('#tm-channel').value,audience:$('#tm-audience').value,
    step:$('#tm-step').value,subject:$('#tm-subject').value,body:$('#tm-body').value});
  let pt;
  const preview=()=>{ clearTimeout(pt); pt=setTimeout(async()=>{
    const b=tmRead();
    $('#tm-subj-wrap').classList.toggle('hidden',b.channel!=='email');
    try{
      const r=await api('/templates/preview',{method:'POST',body:b});
      $('#tm-out').textContent=(r.rendered.subject?r.rendered.subject+'\n\n':'')+r.rendered.body;
      $('#tm-stats').textContent=b.channel==='text'?`${r.chars} characters`:`${r.words} words`;
      $('#tm-problems').innerHTML=r.problems.map(p=>`<div>⚠ ${esc(p)}</div>`).join('');
    }catch(e){}
  },250); };
  ['tm-name','tm-channel','tm-audience','tm-step','tm-subject','tm-body'].forEach(id=>$('#'+id).addEventListener('input',preview));
  preview();
  if(!isNew) $('#tm-archive').onclick=async()=>{
    if(!confirm('Archive this template? Reps will stop seeing it.')) return;
    await api('/templates/'+t.id,{method:'DELETE'}); TPL=null; closeModal(); toast('Archived'); renderTemplates();
  };
}

// ── Offers (Playbook) ───────────────────────────────────────────────────────
// Each offer is a public page (/crm/offer/<key>?r=<rep>) plus an email and a
// text that link to it; reps send them from the 🎁 Offer tab on a lead. Going
// live is gated on the server: no [AMOUNT]-style blanks left, no claim we
// cannot back up, and the link in both messages.
let OFFERS=null;
async function renderOffers(){
  const box=$('#playbook-offers'); if(!box) return;
  if(!OFFERS) OFFERS=await api('/offers');
  const forLabel=f=>f==='past_customer'?'Past customers':((S.cfg.lead_types.find(t=>t.key===f)||{}).label||f);
  box.innerHTML=OFFERS.filter(o=>o.status!=='archived'||S.me.is_manager).map(o=>`<div class="card tpl-card">
    <h4>🎁 ${esc(o.name)} <span class="chip ${o.status==='live'?'cq-3':'cq-1'}">${esc(o.status)}</span>
      ${o.sent?`<span class="type-badge">sent ${o.sent}</span>`:''}</h4>
    <div class="lead-context">For: ${o.for.map(forLabel).map(esc).join(', ')}</div>
    <div class="tpl-subj">${esc(o.headline)}</div>
    <ul class="plan-ul">${o.bullets.map(b=>`<li>${esc(b)}</li>`).join('')}</ul>
    ${o.placeholders.length?`<div class="cr-warn">Needs: ${o.placeholders.map(esc).join(', ')}</div>`:''}
    <div class="drawer-btns">
      <a class="btn-ghost small" href="${BASE}/offer/${esc(o.key)}?r=${encodeURIComponent(S.me.username)}" target="_blank" rel="noopener">👁 Page</a>
      ${S.me.is_manager?`<button class="btn-ghost small" data-oedit="${esc(o.key)}">Edit</button>`:''}</div></div>`).join('')
    ||'<div class="empty">No offers yet.</div>';
  box.querySelectorAll('[data-oedit]').forEach(b=>b.onclick=()=>offerModal(OFFERS.find(o=>o.key===b.dataset.oedit)));
}

function offerModal(o){
  const types=[...S.cfg.lead_types.map(t=>[t.key,t.label]),['past_customer','Past customers']];
  openModal('Edit offer: '+o.name,`
    <div class="tm-row">
      <div class="field"><label>Name</label><input id="om-name" value="${esc(o.name)}"></div>
      <div class="field"><label>Status</label><select id="om-status">${['draft','live','archived'].map(s=>`<option ${s===o.status?'selected':''}>${s}</option>`).join('')}</select></div>
    </div>
    <div class="field"><label>For</label><div class="om-for">${types.map(([k,l])=>`<label><input type="checkbox" value="${k}" ${o.for.includes(k)?'checked':''}> ${esc(l)}</label>`).join('')}</div></div>
    <div class="field"><label>Headline</label><input id="om-headline" value="${esc(o.headline)}"></div>
    <div class="field"><label>Intro</label><textarea id="om-intro" rows="3">${esc(o.intro)}</textarea></div>
    <div class="field"><label>What they get (one per line)</label><textarea id="om-bullets" rows="6">${esc(o.bullets.join('\n'))}</textarea></div>
    <div class="field"><label>Call to action</label><input id="om-cta" value="${esc(o.cta)}"></div>
    <div class="field"><label>Fine print</label><textarea id="om-fine" rows="3">${esc(o.fine_print)}</textarea></div>
    <div class="field"><label>Email subject</label><input id="om-subj" value="${esc(o.email_subject)}"></div>
    <div class="field"><label>Email</label><textarea id="om-email" rows="7">${esc(o.email_body)}</textarea></div>
    <div class="field"><label>Text</label><textarea id="om-text" rows="3">${esc(o.text_body)}</textarea>
      <div class="cmp-hint">Fill-ins: ${[...S.cfg.template_slots,'offer_link'].map(s=>`<code>{${s}}</code>`).join(' ')}. Put amounts in brackets, e.g. [AMOUNT], until they're decided - it can't go live until they're filled.</div></div>
    <div id="om-problems" class="tm-problems"></div>
    <button class="btn-ghost small" id="om-check">Check it's ready to go live</button>`,
  async()=>{
    try{ await api('/offers/'+o.key,{method:'PUT',body:omRead()}); OFFERS=null; toast('Offer saved'); renderOffers(); }
    catch(e){ $('#om-problems').innerHTML=esc(e.message); throw e; }
  },{noAutoClose:false});
  const omRead=()=>({name:$('#om-name').value,status:$('#om-status').value,headline:$('#om-headline').value,
    intro:$('#om-intro').value,bullets:$('#om-bullets').value.split('\n'),cta:$('#om-cta').value,
    fine_print:$('#om-fine').value,email_subject:$('#om-subj').value,email_body:$('#om-email').value,
    text_body:$('#om-text').value,for:$$('.om-for input:checked').map(i=>i.value)});
  $('#om-check').onclick=async()=>{
    const r=await api('/offers/'+o.key+'/check',{method:'POST',body:omRead()});
    $('#om-problems').innerHTML=r.problems.length?r.problems.map(p=>`<div>⚠ ${esc(p)}</div>`).join(''):'<div class="rs-ok">✓ Ready to go live.</div>';
  };
}

function renderPlaybookLists(){
  renderOffers();
  renderTemplates();
  const q=($('#playbook-search').value||'').toLowerCase();
  const match=s=>!q||s.toLowerCase().includes(q);
  const plans=S.cfg.plans||[];
  $('#playbook-plans').innerHTML=plans.filter(p=>match(p.name+p.pitch+p.includes.join(' '))).map(p=>{
    const price=p.custom_pricing?'Custom pricing':money(p.suggested_price)+'/mo';
    const aud=(S.cfg.lead_types.find(t=>t.key===p.audience)||{}).label||p.audience;
    return `<div class="card"><h4>🏡 ${esc(p.name)} <span class="type-badge">${esc(aud)}</span> <span class="kcard-val">${price}</span></h4>
      <div class="plan-pitch">💬 ${esc(p.pitch)}</div>
      <ul class="plan-ul">${p.includes.map(i=>`<li>${esc(i)}</li>`).join('')}</ul></div>`;
  }).join('')||'<div class="empty">No matching plans.</div>';
  $('#playbook-objections').innerHTML=(PB.objections||[]).filter(o=>match(o.objection+o.rebuttal+o.category)).map(o=>
    `<div class="card"><div class="cat">${esc(o.category)}</div><h4 class="q">“${esc(o.objection)}”</h4>
      <div class="a">${esc(o.rebuttal)}</div><div class="coach">🎯 ${esc(o.coach_note)}</div></div>`).join('')
    ||'<div class="empty">No matches.</div>';
  $('#playbook-scripts').innerHTML=(PB.scripts||[]).filter(s=>match(s.name+s.body)).map(s=>
    `<div class="card"><h4>${esc(s.name)}</h4><div class="a">${esc(s.body.replaceAll('[name]',S.me.full_name||S.me.username))}</div></div>`).join('')
    ||'<div class="empty">No matches.</div>';
}

// ── Add lead ─────────────────────────────────────────────────────────────────
// preset: {referred_by, source, returnTo} — used by a partner's "add referred project".
function newLeadModal(preset={}){
  const cfg=S.cfg;
  const typeSel=cfg.lead_types.map(t=>`<option value="${t.key}" ${t.key===preset.lead_type?'selected':''}>${esc(t.label)}</option>`).join('');
  const srcSel='<option value="">Source…</option>'+cfg.sources.map(s=>
    `<option ${s===preset.source?'selected':''}>${esc(s)}</option>`).join('');
  const repSel=S.me.is_manager?`<div class="field"><label>Assign to</label><select id="nl-rep">
    ${S.users.filter(u=>u.username!=='apibot').map(u=>`<option value="${esc(u.username)}" ${u.username===S.me.username?'selected':''}>${esc(u.full_name||u.username)}</option>`).join('')}</select></div>`:'';
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
    <div class="field-row"><div class="field"><label>Plan (optional)</label><select id="nl-plan">
        <option value="">— None —</option>${(cfg.plans||[]).map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></div>
      <div class="field"><label>Billing</label><select id="nl-billing">
        ${cfg.billing_options.map(b=>`<option value="${b.key}">${esc(b.label)}</option>`).join('')}</select></div></div>
    <div class="field-row"><div class="field"><label>Est. value</label><input id="nl-value" type="number"></div>
      <div class="field"><label>Temp</label><select id="nl-temp">${cfg.temperature.map(t=>`<option>${t}</option>`).join('')}</select></div></div>
    ${repSel}`,
    async()=>{
      const first=$('#nl-first').value.trim(), last=$('#nl-last').value.trim(), company=$('#nl-company').value.trim();
      if(!first&&!last&&!company){ toast('Enter a name or company',true); throw new Error('name'); }
      const body={first_name:first,last_name:last,company,phone:$('#nl-phone').value,email:$('#nl-email').value,
        address:$('#nl-address').value,city:$('#nl-city').value,state:$('#nl-state').value.toUpperCase(),zip:$('#nl-zip').value,
        lead_type:$('#nl-type').value,service:$('#nl-service').value,source:$('#nl-source').value,
        plan:$('#nl-plan').value,billing:$('#nl-billing').value,
        est_value:$('#nl-value').value,temperature:$('#nl-temp').value};
      if(preset.referred_by) body.referred_by=preset.referred_by;
      if(S.me.is_manager&&$('#nl-rep')) body.rep=$('#nl-rep').value;
      const lead=await api('/leads',{method:'POST',body});
      toast(preset.referred_by?'Referred project added':'Lead added'); closeModal();
      if(S.view==='pipeline')renderPipeline(); else if(S.view==='myday')renderMyDay(); else if(S.view==='partners')renderPartners();
      // From a partner: land back on the partner so the new project shows underneath.
      openLead(preset.returnTo||lead.id);
    }, {noAutoClose:true});
  // Selecting a plan prefills service→exterior, billing, and suggested price.
  const planSel=$('#nl-plan');
  if(planSel) planSel.onchange=()=>{
    const p=(cfg.plans||[]).find(x=>x.id===planSel.value);
    if(!p) return;
    $('#nl-service').value='exterior_maintenance';
    $('#nl-billing').value=p.billing||'monthly';
    if(!p.custom_pricing) $('#nl-value').value=p.suggested_price;
  };
}
$('#add-lead-btn').onclick=()=>newLeadModal();
$('#add-partner').onclick=()=>newLeadModal({lead_type:'referral_partner'});

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
  // Both belong to the portal now. Changing a password there asks for the
  // current one first, which this modal never did, so send them to the real
  // form rather than reimplementing it.
  $('#mn-logout').onclick=()=>{location='/logout';};
  $('#mn-pw').onclick=()=>{location='/account/password?next='+encodeURIComponent(location.pathname);};
  if($('#mn-invite')) $('#mn-invite').onclick=inviteModal;
  if($('#mn-team')) $('#mn-team').onclick=teamModal;
};
async function inviteModal(){
  openModal('Invite a rep',
    `<div class="field"><label>Rep username (blank = open invite)</label><input id="iv-user" autocapitalize="none"></div>
     <div id="iv-result"></div>`,
    async()=>{
      const r=await portalApi('/api/invites',{method:'POST',body:{username:$('#iv-user').value.trim()}});
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
    try{ await portalApi('/api/users/'+sel.dataset.role+'/role',{method:'POST',body:{role:sel.value}}); toast('Role updated');
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
