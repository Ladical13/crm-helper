// Execute the shipped functions with isolated storage/network doubles.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const path = require('path');
const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'canvasser/static/app.js'), 'utf8');
function fn(name) {
  const start = source.search(new RegExp('(?:async )?function ' + name + '\\('));
  assert(start >= 0, name);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}
async function run() {
  const els = {}, queue = new Map();
  const ctx = {
    Date, Math, String, Number, isNaN, console,
    window:{}, navigator:{onLine:true}, currentUser:{username:'alice'},
    outboxFlushing:false, allPins:[],
    PIN_STAGE:{appointment:'appt_set',interested:'contacted'},
    PIN_TYPES:{interested:{label:'Interested',color:'#00f'}},
    $:id=>els[id] ||= {innerHTML:'',textContent:''},
    show:()=>{}, displayName:s=>s,timeAgo:()=>'', prettyAppt:s=>s,
    newClientId:()=> 'unique-door', updateOutboxBadge:async()=>{},
    outboxAll:async()=>[...queue.values()], outboxDelete:async id=>queue.delete(id),
    outboxPut:async()=>{throw new Error('storage unavailable');},
    postPinRaw:async()=>{throw new Error('no network');},
    showMapNotice:()=>{}, replacePendingPin:()=>{},renderPins:()=>{},autoHandoff:async()=>{},
  };
  vm.createContext(ctx);
  vm.runInContext(['savePinThroughOutbox','pendingPinFrom','flushOutbox','escHtml',
                   'showPinDetail','handoffToPipeline','canHandoff','renderMeshHistory',
                   'hailCoverageText','prettyDate'].map(fn).join('\n'),ctx);
  await assert.rejects(ctx.savePinThroughOutbox({}), /Not saved/);
  ctx.outboxPut = async e => queue.set(e.client_id, e);
  const saved = await ctx.savePinThroughOutbox({lat:40.5,lng:-105});
  assert(saved.queued && queue.get('unique-door').owner === 'alice');
  let posted = [];
  ctx.postPinRaw = async payload => {posted.push(payload);return {ok:true,status:200,json:{id:'server-pin'}};};
  ctx.currentUser = {username:'bob'};
  await ctx.flushOutbox();
  assert.equal(posted.length, 0); assert.equal(queue.size, 1);
  ctx.currentUser = {username:'alice'};
  await ctx.flushOutbox();
  assert.equal(posted.length, 1); assert.equal(queue.size, 0);
  queue.set('legacy', {client_id:'legacy'});
  await ctx.flushOutbox();
  assert.equal(posted.length, 1, 'ownerless legacy entries require explicit recovery');
  ctx.showPinDetail({id:'p',rep:'alice',pin_type:'interested',notes:'<b>untrusted</b>',
                    contact_phone:'\" onmouseover=\"bad',created_at:''});
  assert(!els['pin-detail-body'].innerHTML.includes('<b>untrusted</b>'));
  assert(!els['pin-detail-body'].innerHTML.includes('href="tel:" onmouseover='));
  assert(els['pin-detail-body'].innerHTML.includes('&lt;b&gt;untrusted'));
  const appt={id:'p',rep:'alice',pin_type:'appointment',contact_name:'Ada',crm_lead_id:'lead',pipeline_pending:true};
  assert(ctx.canHandoff(appt), 'linked reschedules must retry');
  ctx.api=async()=>{throw new Error('backend transaction failed');};
  await assert.rejects(ctx.handoffToPipeline(appt));
  assert(appt.pipeline_pending, 'failed handoff must remain retryable');
  ctx.api=async()=>({id:'lead'});
  await ctx.handoffToPipeline(appt); assert(!appt.pipeline_pending);
  ctx.renderMeshHistory({storm_count:0,coverage:{status:'partial',days_held:200,days_verified:0,days_requested:365}});
  assert(els['hail-address-results'].innerHTML.includes('Not enough radar history'));
  ctx.renderMeshHistory({storm_count:0,coverage:{status:'complete',threshold_in:1,days_verified:365,days_requested:365}});
  assert(els['hail-address-results'].innerHTML.includes('No archived hail ≥1 inches'));
  ctx.renderMeshHistory({storm_count:0,coverage:{status:'outside_area',outside_area:true}});
  assert(els['hail-address-results'].innerHTML.includes('Outside radar archive'));

  for (const app of ['canvasser','portal','salescrm','estimator']) {
    let activate, waited; const removed=[];
    const src=fs.readFileSync(path.join(root,app,'static/sw.js'),'utf8');
    const current=/const CACHE = '([^']+)'/.exec(src)[1];
    const old=current.replace(/\d+$/, '0');
    const sw={URL,self:{registration:{scope:'https://test.invalid/'},
      addEventListener:(event,f)=>{if(event==='activate')activate=f;},clients:{claim:async()=>{}}},
      caches:{keys:async()=>[current,old,'another-app-cache'],delete:async k=>removed.push(k)}};
    vm.runInNewContext(src,sw); activate({waitUntil:p=>waited=p});await waited;
    assert.deepEqual(removed,[old], app + ' must retain other apps caches');
  }
  console.log('Offline durability, account boundaries, handoff retry, escaping, hail coverage and four cache namespaces passed.');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
