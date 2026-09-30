const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[2], 'utf8');
const context = vm.createContext({URLSearchParams, location:{search:'',href:''}, S:{estimate_id:'old'},
  document:{getElementById:()=>null}, selected:'', saved:0,
  showClientTab: tab => context.selected=tab,
  doLoadEstimate: async id => {context.S={estimate_id:id};},
  saveCurrentWork:async()=>{context.saved++;return true;},
  saveEstimate:async()=>true});
vm.runInContext(source, context);
(async()=>{
  vm.runInContext("hailReturnEstimateId='existing-estimate'",context);
  await vm.runInContext('reviewHailHandoff()',context);
  assert.equal(context.S.estimate_id,'existing-estimate');
  assert.equal(context.selected,'documents');
  context.S={estimate_id:'existing-estimate',customer:{address:{street:'123 Main',city:'Fort Collins',state:'CO',zip:'80521'}}};
  await vm.runInContext('findEstimateHailReport()',context);
  assert.equal(context.saved,1);
  assert(context.location.href.includes('estimate_id=existing-estimate'));
  assert(context.location.href.includes('123%20Main'));
  const prior=context.location.href;
  context.saveCurrentWork=async()=>false;
  await vm.runInContext('findEstimateHailReport()',context);
  assert.equal(context.location.href,prior);
  context.saveCurrentWork=async()=>{context.S={estimate_id:'different'};return true;};
  await vm.runInContext('findEstimateHailReport()',context);
  assert.equal(context.location.href,prior);
  console.log('Hail handoff preserves estimate identity and opens Documents');
})().catch(e=>{console.error(e);process.exitCode=1;});
