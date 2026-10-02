const assert=require('assert'),fs=require('fs'),vm=require('vm');
const source=fs.readFileSync(process.argv[2],'utf8');
const start=source.indexOf('async function loadConditionReport() {');
const end=source.indexOf('function renderConditionReportForm()',start);
assert(start>=0&&end>start);
const pending=[];
const c=vm.createContext({S:{estimate_id:'first'},_crFor:null,_crData:null,_crLoadGeneration:0,
  activePage:'client',tab:'roofhealth',renders:0,
  clientTabNow:()=>c.tab,renderConditionReportForm:()=>c.renders++,
  fetch:()=>new Promise(resolve=>pending.push(resolve))});
vm.runInContext(source.slice(start,end),c);
const respond=(resolve,body,ok=true)=>resolve({ok,json:async()=>body,statusText:'Unavailable'});
(async()=>{
  const first=c.loadConditionReport();respond(pending.shift(),{id:'first'});await first;
  assert.equal(c.renders,1,'Roof Health tab must render after its data arrives');
  const old=c.loadConditionReport(),oldResponse=pending.shift();
  c.S={estimate_id:'second'};
  const current=c.loadConditionReport();respond(pending.shift(),{id:'second'});await current;
  respond(oldResponse,{id:'first'});await old;
  assert.equal(c._crData.id,'second');assert.equal(c.renders,2);
  const stale=c.loadConditionReport(),staleResponse=pending.shift();
  const newest=c.loadConditionReport();respond(pending.shift(),{id:'latest'});await newest;
  respond(staleResponse,{id:'older'});await stale;
  assert.equal(c._crData.id,'latest');
  c.tab='documents';const away=c.loadConditionReport();respond(pending.shift(),{id:'cached'});await away;
  assert.equal(c.renders,3,'Background response must not switch customer tabs');
  c.tab='roofhealth';const failed=c.loadConditionReport();respond(pending.shift(),{error:'Try again'},false);await failed;
  assert.equal(c.renders,4);assert.equal(c._crData.error,'Try again');
  console.log('Roof Health tab loads, reports failures, and rejects stale responses');
})().catch(e=>{console.error(e);process.exitCode=1;});
