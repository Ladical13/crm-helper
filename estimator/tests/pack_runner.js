/**
 * Runs the REAL pack-pricing code lifted out of static/app.js: the quantity
 * resolver (measuredQty), the bundle loaders that build and re-price lines, and
 * the Price Book's Bought-as editor. A quantity is computed in the browser and
 * stored on the line - the server never recomputes one - so nothing here has a
 * Python twin and the only honest test is to run the shipped code.
 *
 * Unlike bundle_runner.js this keeps the real applyMeasurements, because the
 * whole point is that a pack, its cost and its quantity land together.
 *
 * Usage: node pack_runner.js <scenario.json> <out.json>
 * Scenario: { priceBook, estimate, catalog, suggestions, ops: [...] }
 * Output:   { S, probes, catalog, alerts }
 * See test_pack_pricing.py.
 */
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

/** Index just past the brace block opening at `i` (which is past its `{`),
 *  skipping strings and comments - the approach bundle_runner.js uses. */
function matchBraces(i, what) {
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
  if (depth !== 0) throw new Error('pack_runner: unbalanced braces reading ' + what);
  return i;
}

function grab(name) {
  const re = new RegExp('^(?:async )?function ' + name + '\\s*\\([^)]*\\)\\s*\\{', 'm');
  const m = re.exec(src);
  if (!m) throw new Error('pack_runner: function not found in app.js: ' + name);
  return src.slice(m.index, matchBraces(m.index + m[0].length, name));
}

function grabConst(name) {
  const one = new RegExp('^const ' + name + '\\s*=\\s*[^;\\n{]+;', 'm').exec(src);
  if (one) return one[0];
  const open = new RegExp('^const ' + name + '\\s*=\\s*\\{', 'm').exec(src);
  if (!open) throw new Error('pack_runner: const not found in app.js: ' + name);
  let i = matchBraces(open.index + open[0].length, name);
  if (src[i] === ';') i++;
  return src.slice(open.index, i);
}

const CONSTS = ['TIERS', 'BUNDLE_TRADES', 'SIMPLE_MODE_TRADES', 'MODE_DEFAULT_FLIPPED',
                'DEFAULT_RATE', 'SIDING_PROFILE_FACTORS', 'SIDING_BUNDLE_PROFILES',
                'SIDING_PROFILE_LABELS', 'CATALOG_RANK_LAST', 'PRODUCT_VARIANTS',
                'MEASURE_DEFS'];
const NAMES = ['mnum', 'evalFormula',
               'isBundleTrade', 'effectiveTradeMode', '_tradeCatalog', '_tradeBundles',
               '_tradeBundle', 'featuresFromCatalog', 'bundleFeatures', 'bundleDescription',
               '_rateValue', '_resolveRate', 'tradeRate', 'tierRate', 'tradeTier',
               'simpleApplyMargin', 'applyBundleToTier', 'seedTradeBundles',
               'defaultSimpleBundle', 'buildSimpleItemsFromBundle', 'applyBundleToSimple',
               'buildBundleDefaults', '_commBundleId', '_commAttachProfileForBundle',
               '_commAttachProfile', '_syncCommAttachment',
               'estStructures', 'tradeStructures', 'itemSection', 'structureNamed',
               'tradeSections', 'catalogRank', '_itemGroupName', 'insertByCatalogOrder',
               'variantSlot', 'variantRowFor', 'liSwapVariant',
               // The subject of the test.
               'itemMeasurements', 'measuredQty', 'displayUnit', 'applyMeasurements',
               'packCover', 'packWaste', 'packCount', 'packOf', 'packRebaseQty',
               'packRebaseCost', 'syncLinePack', '_lineHasCost', '_packCostsReadPerUnit',
               // The Price Book's Bought-as editor.
               'pbCat', 'pbRoofCatSetOrder', 'pbRoofCatSetPackPriced', 'pbParsePackCover',
               'pbPackCoverText', 'pbPackOne', 'pbPackMany', 'pbRoundCost',
               'pbPackSuggestion', 'pbPackPriceHint'];

const scenario = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const harness = `
  // ── stubs for the UI/persistence side the loaders touch on the way past ──
  let activePage = 'scope';
  const _tierDetailsOpen = {};
  let __n = 0;
  function uid() { return 'id' + (++__n); }
  function setDirty() {}
  function renderTradeContent() {}
  function renderTotals() {}
  function renderPBModal() {}
  function rerender() {}
  function alert(msg) { __alerts.push(String(msg)); }
  function confirm() { return true; }
  function esc(s) { return String(s == null ? '' : s); }
  function fmtCur(n) { return '$' + (Math.round(n * 100) / 100).toFixed(2); }
  let _fastenTable = null;
  // Not under test here; a scenario that reaches either fails loudly.
  function atticVentilation() { throw new Error('pack_runner: no ventilation measures'); }
  function commercialFastening() { throw new Error('pack_runner: no fastener measures'); }
  function tradeTierContent(trade) {
    const td = S.trades[trade];
    td.tier_features = td.tier_features || {good:[],better:[],best:[]};
    td.tier_descriptions = td.tier_descriptions || {good:'',better:'',best:''};
    return { features: td.tier_features, descriptions: td.tier_descriptions };
  }
  // The Price Book editor's working copy.
  let pbActiveTrade = 'roofing';
  const pbCatalogs = { roofing: __catalog };
  let _pbPackSug = __suggestions;
`;

const body = `
  ${CONSTS.map(grabConst).join('\n')}
  ${harness}
  ${NAMES.map(grab).join('\n')}
  const probes = [];
  for (const o of ops) {
    if (o.op === 'setPriceBook') priceBook = o.priceBook;
    else if (o.op === 'applyBundle') applyBundleToTier(o.trade, o.tier, o.id, false);
    else if (o.op === 'applySimpleBundle') applyBundleToSimple(o.trade, o.id);
    else if (o.op === 'buildDefaults') buildBundleDefaults(o.trade);
    else if (o.op === 'applyMeasurements') applyMeasurements();
    else if (o.op === 'setMeasurements') S.measurements = o.m;
    else if (o.op === 'measure') probes.push(measuredQty(o.item));
    else if (o.op === 'displayUnit') probes.push(displayUnit(o.item));
    else if (o.op === 'packOf') probes.push(packOf(o.p));
    else if (o.op === 'rebaseQty') probes.push(packRebaseQty(o.qty, o.from, o.to));
    else if (o.op === 'sync') syncLinePack(S.trades[o.trade].line_items[o.index], o.product);
    else if (o.op === 'pbSetOrder') pbRoofCatSetOrder(o.i, o.field, o.val);
    else if (o.op === 'pbSetPackPriced') pbRoofCatSetPackPriced(o.i, o.on);
    else if (o.op === 'parseCover') probes.push(pbParsePackCover(o.s));
    else if (o.op === 'coverText') probes.push(pbPackCoverText(o.v));
    else if (o.op === 'priceHint') probes.push(pbPackPriceHint(__catalog[o.i]));
    else throw new Error('unknown op: ' + o.op);
  }
  return { S, probes, catalog: __catalog, alerts: __alerts };
`;

const run = new Function('S', 'priceBook', 'ops', '__catalog', '__suggestions', '__alerts', body);
const out = run(scenario.estimate || { trades: {}, measurements: {} },
                scenario.priceBook || {}, scenario.ops || [],
                scenario.catalog || [], scenario.suggestions || {}, []);
fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 2));
