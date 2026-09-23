/* Marketing Studio: drafts and human posting records, never a publish client. */
window.MarketingStudio = (() => {
  'use strict';
  const API = '/nimbus/api/studio';
  const metrics = ['reach','impressions','saves','shares','comments','clicks','inquiries'];
  const edits = new Map(); // Keep edits to other cards when a save or job refresh redraws the page.
  let generation = 0, timer = null, current = null, catalog = null, selected = null, tab = 'ideas', filter = 'all', busy = false;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeLink = value => {try {const u=new URL(value);return u.protocol==='https:' ? esc(u.href) : '';}catch{return '';}};
  const link = (url,title) => safeLink(url) ? `<a href="${safeLink(url)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>` : esc(title);
  async function api(path, data) {
    const response = await fetch(API + path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    let result; try {result=await response.json();} catch {throw Error('Your session may have expired. Reload Nimbus and sign in.');}
    if (!response.ok) throw Error(result.error || `Request failed (${response.status})`);
    return result;
  }
  function message(text, error=false) {
    const box=document.getElementById('studio-status');
    if (box) {box.textContent=text;box.classList.toggle('studio-error',error);}
  }
  function leave() {generation++;clearTimeout(timer);timer=null;current=null;busy=false;}
  function choices(name, values, chosen, labels={}) {
    return values.map(v=>`<label><input type="checkbox" name="${name}" value="${esc(v)}" ${chosen.includes(v)?'checked':''}>${esc(labels[v]?.label || v.replaceAll('_',' '))}</label>`).join('');
  }
  async function open() {
    const epoch=++generation;
    document.getElementById('main').innerHTML='<section class="studio"><div id="studio-status" class="studio-status" role="status" aria-live="polite">Loading Marketing Studio…</div><div id="studio-body"></div></section>';
    try {
      const data=await api('/campaigns');
      if(epoch!==generation)return;
      catalog=data;
      if(selected && !catalog.campaigns.some(c=>c.id===selected))selected=null;
      selected=selected || data.campaigns[0]?.id || null;
      if(selected)await load(epoch);else draw();
      if(epoch!==generation)return;
      message('');
      const response=await fetch('/nimbus/api/seo/result');
      const job=await response.json();
      if(epoch===generation && job.running) watch(job.job_id, epoch);
    }catch(e){if(epoch===generation)message(e.message,true);}
  }
  async function load(epoch=generation) {
    const id=selected;
    const data=await api(`/campaigns/${id}`);
    if(epoch!==generation || selected!==id)return;
    current=data;draw();
  }
  function draw() {
    const root=document.getElementById('studio-body');if(!root)return;
    const title=current?.campaign.brief.name || 'Useful answers. Local awareness.';
    root.innerHTML=`<div class="studio-hero"><span class="studio-tag">PROJECT ONE ROOFING · ORGANIC GROWTH</span>
      <h1>${esc(title)}</h1><p>Turn real customer concerns into useful posts, practical visuals and a consistent weekly plan.</p>
      <div class="studio-row"><select id="studio-campaign" aria-label="Campaign" style="max-width:350px"><option value="">Create a campaign</option>${catalog.campaigns.map(c=>`<option value="${c.id}" ${c.id===selected?'selected':''}>${esc(c.brief.name)}</option>`).join('')}</select><button data-action="new">New weekly plan</button></div></div>
      ${current ? workspace() : newForm()}
      <dialog id="studio-dialog"><button data-action="close-dialog">Close</button><div id="studio-dialog-body"></div></dialog>`;
    root.querySelector('#studio-campaign').addEventListener('change', async e=>{
      if(!e.target.value){current=null;selected=null;draw();return;}
      selected=Number(e.target.value);try{await load();}catch(err){message(err.message,true);}
    });
    root.onclick=onClick;
    root.onsubmit=onSubmit;
    root.oninput=event=>{
      const form=event.target.closest('form.studio-edit');if(!form)return;
      const p=current.posts.find(p=>p.id===Number(form.dataset.id));if(!p)return;
      const revision=edits.get(p.id)?.revision ?? p.revision;
      edits.set(p.id,{...formData(form,p),revision});
    };
    root.querySelector('#studio-filter')?.addEventListener('change',e=>{filter=e.target.value;draw();});
    root.querySelectorAll('[data-idea-format]').forEach(input=>input.addEventListener('change',async e=>{
      try{await api(`/campaigns/${selected}/ideas/${input.dataset.ideaFormat}`,{format:e.target.value});await load();}catch(err){message(err.message,true);}
    }));
  }
  function newForm() {
    return `<form id="studio-new" class="studio-card"><h2>Build a weekly plan</h2><div class="studio-grid">
      <label>Plan name<input name="name" required maxlength="120" placeholder="September · Helpful homeowner answers"></label>
      <label>First planned date<input type="date" name="start_date" required value="${new Date().toLocaleDateString('en-CA')}"></label>
      <label>Local area<input name="market" value="Northern Colorado" required maxlength="100"></label>
      <label>Awareness objective<input name="goal" value="Help local homeowners make confident exterior decisions" required maxlength="500"></label></div>
      <fieldset><legend>Who should this help?</legend>${choices('audience',catalog.audiences,['homeowner'])}</fieldset>
      <fieldset><legend>Services</legend>${choices('services',catalog.services,catalog.services)}</fieldset>
      <fieldset><legend>Channels</legend>${choices('platforms',Object.keys(catalog.platforms),['facebook','instagram','linkedin','google_business'],catalog.platforms)}</fieldset>
      <button class="primary" type="submit">Create plan</button><p class="muted">Drafting and current research use the existing Perplexity connection and spending cap. The curated research library and manual editor work without an API key.</p></form>`;
  }
  function workspace() {
    const b=current.campaign.brief;
    return `<div class="studio-row muted">${esc(b.market)} · ${esc(b.audience.join(', '))} · Week of ${esc(b.start_date)}<span>${esc(b.goal)}</span></div>
      <div class="studio-tabs">${[['ideas','1 · Research & ideas'],['posts','2 · Calendar & posts'],['results','3 · Learn & improve']].map(([id,label])=>`<button data-action="tab" data-tab="${id}" class="${tab===id?'active':''}">${label}</button>`).join('')}</div>
      ${tab==='ideas'?ideaView():tab==='posts'?postView():resultsView()}`;
  }
  function ideaView() {
    const count=current.ideas.filter(i=>i.status==='selected').length;
    return `<div class="studio-card"><h2>Start with what people need to know</h2><p>Research concerns, then choose up to seven ideas. Mix practical decisions, comfort, trust and property planning. Source evidence supports the topic; it does not prove social popularity.</p>
      <div class="studio-row"><button data-action="research" data-live="false">Load source library & team questions</button><button data-action="research" data-live="true">Research current public sources</button></div>
      <p class="muted">${esc(current.campaign.research_note || 'Research has not run for this plan yet.')}</p>
      <details><summary>Add a question your team actually heard</summary><form id="studio-question"><label>Paraphrase the question; omit names and personal details<input name="question" minlength="8" maxlength="300" required></label><button type="submit">Save team question</button></form></details>
      <div class="studio-row"><b>${count} ideas selected</b><button data-action="suggest" ${!current.ideas.length?'disabled':''}>Suggest a balanced week</button><button class="primary" data-action="generate" ${!count || busy?'disabled':''}>Generate channel packages</button><button data-action="manual" ${!count || busy?'disabled':''}>Create drafts to write myself</button></div></div>
      <div class="studio-grid">${current.ideas.map(i=>`<article class="studio-card"><span class="studio-tag">${esc(i.pillar)}</span><span class="studio-tag">${esc(i.format)}</span>${i.recently_used?'<span class="studio-tag">Similar topic used in last 45 days</span>':''}
      <h3>${esc(i.question)}</h3><p>${esc(i.why_care)}</p><p><b>Approach:</b> ${esc(i.angle)}</p><div class="studio-evidence">${i.evidence.map(e=>`<p>${link(e.url,e.title)}<br>${esc(e.finding)}</p>`).join('')}<div class="muted">${esc(i.basis)} · ${esc(i.researched_at?.slice(0,10))}</div></div>
      <label>Preferred visual format<select data-idea-format="${i.id}">${['photo','carousel','reel'].map(f=>`<option ${f===i.format?'selected':''}>${f}</option>`).join('')}</select></label>
      <div class="studio-row"><button data-action="select" data-id="${i.id}" data-status="${i.status==='selected'?'idea':'selected'}" class="${i.status==='selected'?'active':''}">${i.status==='selected'?'✓ Selected':'Select idea'}</button><button data-action="select" data-id="${i.id}" data-status="${i.status==='dismissed'?'idea':'dismissed'}">${i.status==='dismissed'?'Restore idea':'Dismiss'}</button><span class="muted">${esc(i.status)}</span></div></article>`).join('') || '<p class="muted">Load the source library to see your first ideas.</p>'}</div>`;
  }
  function postView() {
    const visible=current.posts.filter(p=>filter==='all'||p.status===filter);
    return `<div class="studio-card"><h2>Your publishing plan</h2><p class="muted">Dates organise the work. After review, copy the caption and use the finished visual to post on the platform. Record its link here.</p>
      <div class="studio-row"><select id="studio-filter" aria-label="Filter posts" style="max-width:200px">${['all','draft','approved','ready','posted','rejected'].map(s=>`<option ${filter===s?'selected':''}>${s}</option>`).join('')}</select><a class="studio-link" href="${API}/campaigns/${selected}/export">Export CSV</a><a class="studio-link" target="_blank" rel="noopener" href="${API}/campaigns/${selected}/export?format=json">Export complete plan</a></div></div>
      ${visible.map(p=>postCard(p)).join('') || '<div class="studio-card">No posts here yet. Select ideas and create channel packages.</div>'}`;
  }
  function postCard(p) {
    const unsaved=edits.has(p.id);
    p={...p,...edits.get(p.id)};
    const creative=p.creative;
    return `<article class="studio-card" data-post="${p.id}"><span class="studio-tag">${esc(p.platform_label)}</span><span class="studio-tag">${esc(p.status)}</span><span class="studio-tag">${esc(p.planned_date || 'Unplanned')}</span>${unsaved?'<span class="studio-tag">Unsaved edits</span>':''}<h3>${esc(p.topic)}</h3>
      <form class="studio-edit" data-id="${p.id}"><div class="studio-grid"><div><label>Public caption · ${p.draft_text.length} characters<textarea name="draft_text" ${p.status==='posted'?'readonly':''}>${esc(p.draft_text)}</textarea></label><div class="studio-row"><button type="button" data-action="copy" data-id="${p.id}">Copy saved caption</button>${p.platform_link?link(p.platform_link,'Open '+p.platform_label):''}<button type="button" data-action="history" data-id="${p.id}">Version history</button></div></div>
      <div><label>Planned date<input type="date" name="planned_date" value="${esc(p.planned_date)}"></label><label>Owner<input name="owner" value="${esc(p.owner)}" maxlength="100"></label><label>Finished asset link (Canva, Drive or other https link)<input name="asset_url" type="url" value="${esc(p.asset_url)}"></label><label><input name="asset_ready" type="checkbox" ${p.asset_ready?'checked':''}>Visual finished, checked and cleared for use</label></div></div>
      <details><summary>Creative brief · ${esc(creative.format || 'photo')} · separate from the caption</summary><p class="muted">Production instructions, slide text and scripts stay here. Use real, permissioned work photos; check the final asset and its accessibility text.</p>
      ${['image_prompt','alt_text','script','subject'].map(k=>`<label>${esc(k.replaceAll('_',' '))}<textarea name="creative_${k}" ${p.status==='posted'?'readonly':''}>${esc(typeof creative[k]==='string'?creative[k]:'')}</textarea></label>`).join('')}
      ${['slides','shot_list'].map(k=>`<label>${esc(k.replaceAll('_',' '))} · one per line<textarea name="creative_${k}" ${p.status==='posted'?'readonly':''}>${esc(Array.isArray(creative[k])?creative[k].map(v=>typeof v==='string'?v:JSON.stringify(v)).join('\n'):'')}</textarea></label>`).join('')}</details>
      <details><summary>Evidence & review notes</summary><p>${esc(p.review_notes)}</p>${p.citations.map(u=>`<p>${link(u,u)}</p>`).join('')}</details>
      <label>Published post link<input name="posted_url" type="url" value="${esc(p.posted_url)}" placeholder="https://…"></label>
      <details><summary>Record organic results</summary><p class="muted">Leave unavailable numbers blank. Enter results at a consistent age, for example seven days after posting.</p><div class="studio-metrics">${metrics.map(k=>`<label>${k}<input type="number" min="0" step="1" name="metric_${k}" value="${p.metrics[k]??''}"></label>`).join('')}</div></details>
      <div class="studio-row"><button type="submit" class="primary">Save edits</button>${p.status!=='posted'?`<button type="button" data-action="status" data-status="approved" data-id="${p.id}">Approve saved copy</button><button type="button" data-action="status" data-status="ready" data-id="${p.id}">Mark ready</button><button type="button" data-action="status" data-status="posted" data-id="${p.id}">Record posted</button><button type="button" data-action="status" data-status="rejected" data-id="${p.id}">Reject</button>`:''}</div><p class="muted">Save before changing status. Copy or creative edits return the post to draft for a fresh review.</p></form></article>`;
  }
  function resultsView() {
    const r=current.results;
    return `<div class="studio-card"><h2>Learn from useful engagement</h2><p>${r.posted_count} posts recorded as published. ${esc(r.note)}</p><div class="studio-metrics">${metrics.map(k=>`<div><div class="studio-count">${r.coverage[k]?r.totals[k].toLocaleString():'—'}</div>${k}<p class="muted">${r.coverage[k]} posts with data</p></div>`).join('')}</div></div>
      <div class="studio-card"><h2>Topics to investigate next</h2><p class="muted">Ordered by recorded saves + shares + inquiries. These are learning candidates, not proven winners.</p>${r.learning_candidates.map(p=>`<p><b>${esc(p.topic)}</b><br>${esc(p.platform)} · ${esc(Object.entries(p.metrics).map(([k,v])=>`${v} ${k}`).join(' · '))}</p>`).join('') || '<p>Record results after posting to identify promising follow-up topics.</p>'}
      <p>Look for recurring questions in comments. Add their paraphrased questions to the next plan, answer them directly, and compare results at the same post age.</p></div>`;
  }
  async function watch(jobId, epoch=generation) {
    busy=true;message('Nimbus is working. Your drafts and research are saved as each package finishes.');
    async function tick(){
      try{
        const response=await fetch(`/nimbus/api/seo/result?job_id=${jobId}`);if(!response.ok)throw Error('Unable to check job progress');
        const job=await response.json();if(epoch!==generation)return;
        if(job.running){timer=setTimeout(tick,2500);return;}
        busy=false;if(selected)await load(epoch);if(epoch!==generation)return;
        const result=job.manifest || {};
        const rejected=(result.packages||[]).flatMap(p=>p.rejected||[]);
        message([result.error || result.note || 'Finished.',...rejected.map(p=>`${p.platform}: ${p.reason}`)].join('\n'),job.status==='error');
      }catch(e){if(epoch===generation){busy=false;message(e.message+' Reload to reconnect to the saved job.',true);}}
    }
    timer=setTimeout(tick,1200);
  }
  function formData(form,p) {
    const f=new FormData(form), creative={...p.creative}, values={};
    for(const k of ['image_prompt','alt_text','script','subject'])creative[k]=f.get('creative_'+k)||'';
    for(const k of ['slides','shot_list'])creative[k]=String(f.get('creative_'+k)||'').split('\n').filter(v=>v.trim());
    for(const k of metrics)if(f.get('metric_'+k)!=='')values[k]=Number(f.get('metric_'+k));
    return {revision:p.revision,draft_text:f.get('draft_text'),planned_date:f.get('planned_date'),owner:f.get('owner'),asset_url:f.get('asset_url'),asset_ready:f.has('asset_ready'),posted_url:f.get('posted_url'),creative,metrics:values};
  }
  async function onSubmit(event) {
    event.preventDefault();const form=event.target,button=form.querySelector('[type=submit]');if(button)button.disabled=true;
    try{
      if(form.id==='studio-new'){
        const f=new FormData(form),data=Object.fromEntries(f);
        for(const k of ['audience','services','platforms'])data[k]=f.getAll(k);
        const result=await api('/campaigns',data);selected=result.id;tab='ideas';await open();message('Plan created. Load the library or research current sources to find relevant questions.');
      }else if(form.id==='studio-question'){
        const response=await fetch('/nimbus/api/seo/field-notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:new FormData(form).get('question'),city:current.campaign.brief.market,heard_where:'Marketing Studio team question'})});
        const data=await response.json();if(!response.ok)throw Error(data.error||'Unable to save question');
        const job=await api(`/campaigns/${selected}/research`,{live:false});watch(job.job_id);
      }else if(form.classList.contains('studio-edit')){
        const p=current.posts.find(p=>p.id===Number(form.dataset.id));const payload=formData(form,p);payload.revision=edits.get(p.id)?.revision ?? p.revision;const result=await api(`/posts/${p.id}`,payload);edits.delete(p.id);await load();message(`Saved. Status: ${result.status}.`);
      }
    }catch(e){message(e.message,true);}finally{if(button)button.disabled=false;}
  }
  async function onClick(event){
    const button=event.target.closest('button[data-action]');if(!button)return;const action=button.dataset.action,id=Number(button.dataset.id);
    button.disabled=true;
    try{
      if(action==='new'){current=null;selected=null;draw();}
      if(action==='tab'){tab=button.dataset.tab;draw();}
      if(action==='close-dialog')document.getElementById('studio-dialog').close();
      if(action==='select'){await api(`/campaigns/${selected}/ideas/${id}`,{status:button.dataset.status});await load();}
      if(action==='suggest'){await api(`/campaigns/${selected}/suggest`,{});await load();message('Selected a mix of useful themes. Review the choices and adjust formats before drafting.');}
      if(action==='research'){const job=await api(`/campaigns/${selected}/research`,{live:button.dataset.live==='true'});watch(job.job_id);}
      if(action==='generate'||action==='manual'){const job=await api(`/campaigns/${selected}/generate`,{manual:action==='manual'});tab='posts';draw();watch(job.job_id);}
      if(action==='copy'){await navigator.clipboard.writeText(current.posts.find(p=>p.id===id).draft_text);message('Saved caption copied. Creative instructions were kept separate.');}
      if(action==='status'){
        const p=current.posts.find(p=>p.id===id),form=button.closest('form');
        // Never approve hidden unsaved changes: status actions operate on the saved revision.
        const edited=formData(form,p);
        const baseline={...p,creative:{...p.creative}};
        for(const k of ['image_prompt','alt_text','script','subject'])baseline.creative[k] ||= '';
        for(const k of ['slides','shot_list'])baseline.creative[k] ||= [];
        if(edited.draft_text!==p.draft_text || JSON.stringify(edited.creative)!==JSON.stringify(baseline.creative) || edited.posted_url!==p.posted_url || edited.asset_url!==p.asset_url || edited.asset_ready!==Boolean(p.asset_ready))throw Error('Save your edits first, then change status.');
        const result=await api(`/posts/${id}`,{revision:p.revision,status:button.dataset.status});await load();message(`Status: ${result.status}.`);
      }
      if(action==='history'){
        const result=await api(`/posts/${id}/history`);const box=document.getElementById('studio-dialog-body');
        if(!box)return;box.innerHTML='<h2>Saved versions</h2>'+result.history.map(h=>`<article><h3>${esc(h.saved_at)} · ${esc(h.username)} · ${esc(h.snapshot.status)}</h3><p class="studio-creative">${esc(h.snapshot.draft_text)}</p></article>`).join('') || 'No earlier versions yet.';document.getElementById('studio-dialog').showModal();
      }
    }catch(e){message(e.message,true);}finally{button.disabled=false;}
  }
  return {open,leave};
})();
