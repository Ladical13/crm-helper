// Exercise the real polling code without a provider call or a browser dependency.
const fs = require('fs');
const vm = require('vm');
const assert = require('node:assert/strict');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '../static/nimbus/nimbus.html'), 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1].split('/* ── boot')[0];

function harness() {
  const nodes = Object.fromEntries(['seo-live', 'social-live', 'seo-run', 'flash'].map(
    id => [id, {innerHTML: '', textContent: '', disabled: true}]));
  const intervals = new Map();
  let next = 0;
  const ctx = vm.createContext({
    document: {body: {addEventListener() {}}, getElementById: id => nodes[id],
      querySelectorAll: () => [nodes['seo-run']]},
    window: {addEventListener() {}},
    setInterval: cb => {intervals.set(++next, cb); return next;},
    clearInterval: id => intervals.delete(id), setTimeout: () => 1, clearTimeout() {},
    URL, console,
  });
  vm.runInContext(source, ctx);
  return {ctx, nodes, intervals, last: () => intervals.get(next)};
}

async function main() {
  const {ctx, nodes, last} = harness();
  let requested;
  ctx.fetch = async url => {
    requested = url;
    return {ok: true, json: async () => ({kind: 'seo', running: false,
      manifest: {ok: true, recommendations: [], pages_crawled: 2, cost_usd: 0}})};
  };
  ctx.pollSeo(true, 41);
  await last()();
  assert.equal(requested, '/nimbus/api/seo/result?job_id=41');
  assert.match(nodes['seo-live'].innerHTML, /no recommendations were saved/);
  assert.equal(nodes['seo-run'].disabled, false);

  // An earlier in-flight response cannot overwrite a newly selected job.
  let resolve;
  ctx.fetch = () => new Promise(r => {resolve = r;});
  ctx.pollSeo(true, 42);
  const pending = last()();
  ctx.pollSeo(true, 43);
  nodes['seo-live'].innerHTML = 'new run';
  resolve({ok: true, json: async () => ({kind: 'seo', running: false,
    manifest: {ok: false, error: 'old error'}})});
  await pending;
  assert.equal(nodes['seo-live'].innerHTML, 'new run');

  ctx.fetch = async url => {requested = url; return {ok: true, json: async () =>
    ({running: false, manifest: {ok: false, error: 'provider unavailable'}})};};
  ctx.pollSocial(44, false);
  await last()();
  assert.equal(requested, '/nimbus/api/seo/result?job_id=44');
  assert.match(nodes['social-live'].innerHTML, /provider unavailable/);
  console.log('Nimbus polling regression checks passed');
}
main().catch(err => {console.error(err); process.exit(1);});
