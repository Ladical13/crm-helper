// Exercise the real isolated UI functions without network or a paid provider.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(require('path').join(__dirname,'../static/app.js'),'utf8');
const start=source.indexOf('const _vzRealisticRequests =');
const end=source.indexOf('async function renderVisualizerPage()',start);
assert(start>0 && end>start);
const elevation={id:'front',base_image:'test/original.png',tier_renders:{}};
const panel={innerHTML:''},owner={estimate_id:'test',visualizer:{}};
let posts=0, checked=false, enabled=true, stale=false;
const ctx={console,Map,Promise,encodeURIComponent,JSON,crypto:require('crypto').webcrypto,
  S:owner,vzState:{owner,activeTier:'good',saving:false},BASE:'/estimate',
  clearTimeout(){},setTimeout(){},esc:v=>String(v),_vzConceptName:t=>t,
  _vzElevation:()=>elevation,_vzGet:()=>owner.visualizer,setDirty(){},
  _vzScopeRoles:()=>[],_VZ_ROLE_META:{},
  dirty:false,_vzHasUnsavedCanvasWork:()=>false,_vzMetaPending:()=>false,
  confirm:()=>true,alert(){},saveCurrentWork:async()=>true,
  document:{getElementById:id=>id==='vz-realistic'?panel:id==='vz-realistic-reviewed'?{checked}:null},
  fetch:async(url,options={})=>{
    if(options.method==='POST')posts++;
    let data;
    if(url.endsWith('realistic-capabilities')) data={enabled,user_daily_limit:10};
    else if(url.endsWith('/accept')) data={visualizer:{elevations:{front:{tier_renders:{good:'test/vr_ai_abc.png'},realistic_previews:{good:{job_id:'abc'}}}}}};
    else data={jobs:[{id:'abc',elevation:'front',tier:'good',status:'ready',stale}]};
    return {ok:true,json:async()=>data};
  }
};
vm.createContext(ctx);vm.runInContext(source.slice(start,end),ctx);
(async()=>{
  await ctx._vzRealisticRefresh();
  assert(panel.innerHTML.includes('/estimate/api/estimates/test/realistic-previews/abc/image'));
  assert(panel.innerHTML.includes('Use reviewed preview'));
  await ctx._vzRealisticAccept('abc');assert.equal(posts,0,'Unchecked result must not be accepted');
  checked=true;await ctx._vzRealisticAccept('abc');assert.equal(posts,1);
  assert.equal(elevation.base_image,'test/original.png');
  assert.equal(elevation.tier_renders.good,'test/vr_ai_abc.png');
  assert.equal(ctx.vzState.saving,false);
  enabled=false;await ctx._vzRealisticRefresh();assert(panel.innerHTML.includes('Setup needed'));
  stale=true;await ctx._vzRealisticRefresh();assert(panel.innerHTML.includes('cannot be applied'));
  console.log('Realistic preview UI contract checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
