/**
 * Runs the REAL bundle→tier loader lifted out of static/app.js against a
 * scenario, so the test exercises the shipped code rather than a copy of it.
 *
 * Usage: node bundle_runner.js <scenario.json> <out.json>
 * Scenario: { priceBook, estimate, ops: [{op:'applyBundle',trade,tier,id}
 *                                       |{op:'buildDefaults',trade}] }
 * Output:   the resulting estimate.
 * See test_bundles.py.
 */
const fs = require('fs');
const path = require('path');

const APP_JS = path.join(__dirname, '..', 'static', 'app.js');
const src = fs.readFileSync(APP_JS, 'utf8');

/** Extract a top-level `function name(...) {...}` by brace matching, skipping
 *  strings and comments (shared approach with parity_runner.js). */
function grab(name) {
  const re = new RegExp('^function ' + name + '\\s*\\([^)]*\\)\\s*\\{', 'm');
  const m = re.exec(src);
  if (!m) throw new Error('bundle_runner: function not found in app.js: ' + name);
  let i = m.index + m[0].length;
  let depth = 1;
  while (i < src.length && depth > 0) {
    const ch = src[i], next = src[i + 1];
    if (ch === '/' && next === '/') {
      while (i < src.length && src[i] !== '\n') i++;
    } else if (ch === '/' && next === '*') {
      i += 2;
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) i++;
      i += 2;
    } else if (ch === '"' || ch === "'" || ch === '`') {
      const quote = ch;
      i++;
      while (i < src.length && src[i] !== quote) {
        if (src[i] === '\\') i++;
        i++;
      }
      i++;
    } else {
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
      i++;
    }
  }
  if (depth !== 0) throw new Error('bundle_runner: unbalanced braces reading ' + name);
  return src.slice(m.index, i);
}

function grabConst(name) {
  // Single-line const first — the fast, common case.
  const re = new RegExp('^const ' + name + '\\s*=\\s*[^;\\n]+;', 'm');
  const m = re.exec(src);
  if (m) return m[0];
  // Multi-line object literal (SIDING_PROFILE_FACTORS et al) — brace-match
  // through strings/comments the same way grab() does for function bodies.
  const openRe = new RegExp('^const ' + name + '\\s*=\\s*\\{', 'm');
  const om = openRe.exec(src);
  if (!om) throw new Error('bundle_runner: const not found in app.js: ' + name);
  let i = om.index + om[0].length;
  let depth = 1;
  while (i < src.length && depth > 0) {
    const ch = src[i], next = src[i + 1];
    if (ch === '/' && next === '/') {
      while (i < src.length && src[i] !== '\n') i++;
    } else if (ch === '/' && next === '*') {
      i += 2;
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) i++;
      i += 2;
    } else if (ch === '"' || ch === "'" || ch === '`') {
      const quote = ch;
      i++;
      while (i < src.length && src[i] !== quote) {
        if (src[i] === '\\') i++;
        i++;
      }
      i++;
    } else {
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
      i++;
    }
  }
  if (depth !== 0) throw new Error('bundle_runner: unbalanced braces reading ' + name);
  // Include the trailing semicolon if present so the const statement ends cleanly.
  if (src[i] === ';') i++;
  return src.slice(om.index, i);
}

const CONSTS = ['TIERS', 'BUNDLE_TRADES', 'SIMPLE_MODE_TRADES', 'DEFAULT_RATE',
                'SIDING_PROFILE_FACTORS', 'SIDING_BUNDLE_PROFILES', 'SIDING_PROFILE_LABELS'];
const NAMES = ['isBundleTrade', 'effectiveTradeMode', '_tradeCatalog', '_tradeBundles',
               '_tradeBundle', 'bundleFeatures', 'bundleDescription', '_rateValue', '_resolveRate', 'tradeRate', 'tierRate',
               'tradeTier', 'simpleApplyMargin', 'applyBundleToTier', 'seedTradeBundles',
               'defaultSimpleBundle', 'buildSimpleItemsFromBundle', 'applyBundleToSimple',
               'buildBundleDefaults', '_carryItemIdentity', 'setTradeMode',
               '_commAttachProfile', '_syncCommAttachment'];

const scenario = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

let _uidN = 0;
const harness = `
  // ── stubs for the UI/persistence side of the app the loader touches ──
  let activePage = 'scope';
  const _tierDetailsOpen = {};
  function uid() { return 'id' + (++__uid); }
  function setDirty() {}
  function renderTradeContent() {}
  function renderTotals() {}
  function applyMeasurements() {}
  // The fastening table is fetched at boot in the real app; these scenarios
  // exercise bundle loading, not fastener math, so an empty table is enough
  // for _commAttachProfile to fall through to the product attach tags.
  // (No backticks in this harness string - it is itself a template literal.)
  let _fastenTable = null;
  function renderTabBar() {}
  function seedTradeFromDefaults() {}
  function _syncLegacyTier() {}
  function tradeTierContent(trade) {
    const td = S.trades[trade];
    td.tier_features = td.tier_features || {good:[],better:[],best:[]};
    td.tier_descriptions = td.tier_descriptions || {good:'',better:'',best:''};
    return { features: td.tier_features, descriptions: td.tier_descriptions };
  }
`;

const body = `
  ${CONSTS.map(grabConst).join('\n')}
  ${harness}
  ${NAMES.map(grab).join('\n')}
  for (const o of ops) {
    if (o.op === 'applyBundle') applyBundleToTier(o.trade, o.tier, o.id, false);
    else if (o.op === 'buildDefaults') buildBundleDefaults(o.trade);
    else if (o.op === 'applySimpleBundle') applyBundleToSimple(o.trade, o.id);
    else if (o.op === 'setMode') setTradeMode(o.trade, o.mode);
    else throw new Error('unknown op: ' + o.op);
  }
  return S;
`;

const run = new Function('S', 'priceBook', 'ops', '__uid', body);
const out = run(scenario.estimate, scenario.priceBook, scenario.ops || [], _uidN);
fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 2));
