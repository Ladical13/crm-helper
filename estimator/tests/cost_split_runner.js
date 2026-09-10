/**
 * Splits a set of fixtures into material and labor using the REAL functions
 * lifted out of static/app.js — never a reimplementation, or the test would
 * happily agree with itself while the shipped bundle drifted.
 *
 * Usage: node cost_split_runner.js <fixtures.json> <out.json>
 * See test_cost_split.py.
 */
const fs = require('fs');
const path = require('path');

const APP_JS = path.join(__dirname, '..', 'static', 'app.js');
const src = fs.readFileSync(APP_JS, 'utf8');

/** Extract a top-level `function name(...) {...}` by brace matching.
 *  Skips strings AND comments — an apostrophe in a comment ("app.py's") would
 *  otherwise open a phantom string and swallow the rest of the file. */
function grab(name) {
  const re = new RegExp('^function ' + name + '\\s*\\([^)]*\\)\\s*\\{', 'm');
  const m = re.exec(src);
  if (!m) throw new Error('cost_split_runner: function not found in app.js: ' + name);
  let i = m.index + m[0].length;
  let depth = 1;
  while (i < src.length && depth > 0) {
    const ch = src[i], next = src[i + 1];
    if (ch === '/' && next === '/') {                 // line comment
      while (i < src.length && src[i] !== '\n') i++;
    } else if (ch === '/' && next === '*') {          // block comment
      i += 2;
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) i++;
      i += 2;
    } else if (ch === '"' || ch === "'" || ch === '`') {  // string literal
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
  if (depth !== 0) throw new Error('cost_split_runner: unbalanced braces reading ' + name);
  return src.slice(m.index, i);
}

/** Extract a top-level `const NAME = ...;` — lifted from source, never
 *  redefined here, or the constant could drift from the shipped bundle. */
function grabConst(name) {
  const re = new RegExp('^const ' + name + '\\s*=\\s*[^;]+?;', 'm');
  const m = re.exec(src);
  if (!m) throw new Error('cost_split_runner: const not found in app.js: ' + name);
  return m[0];
}

// Lifted, never restated. COST_CLASS_* especially: the whole point of this
// runner is that a keyword added on one side and not the other fails loudly.
const CONSTS = ['DEFAULT_RATE', 'RETAIL_TRADE_KEYS', 'SIMPLE_MODE_TRADES',
                'MODE_DEFAULT_FLIPPED', 'FRANCHISE_RATE',
                'COST_CLASS_NEVER_LABOR', 'COST_CLASS_LABOR_WORDS',
                'COST_CLASS_LABOR_PREFIXES', 'COST_CLASS_EXTRA_PREFIXES'];
const NAMES = ['_rateValue', '_resolveRate', 'tierRate', 'tradeRate', 'lineTotal',
               'lineTotalEffective', 'effectiveTradeMode', 'tradeTotal', 'tradeTier',
               'guessCostClass', 'normCostClass', '_tradeCatalog',
               '_catalogClassByName', 'costClassOf', 'lineCostSplit',
               'simpleCostSplit', 'tierProfit'];

// Globals the extracted functions close over in the real bundle. `priceBook`
// matters as much as `S` here — _tradeCatalog reads it, and without it every
// line would fall through to the name index and report material.
const TIERS = ['good', 'better', 'best'];
let S, priceBook;

eval(CONSTS.map(grabConst).join('\n') + '\n' + NAMES.map(grab).join('\n'));

const r2 = (n) => Math.round(n * 100) / 100;

const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = fixtures.map((f) => {
  S = f.state;
  priceBook = f.price_book || {};
  const row = { name: f.name, tiers: {}, lines: {} };

  for (const t of TIERS) {
    const p = tierProfit(t);
    row.tiers[t] = { material: r2(p.material), labor: r2(p.labor),
                     cost: r2(p.cost), sell: r2(p.sell) };
  }
  row.all_tier_blind = tierProfit('better').allTierBlind;

  // Per-line, so the Python side can be held to the same DECISION on the same
  // row rather than to a total that could agree for the wrong reasons.
  //
  // Iterates the fixture's own trades rather than RETAIL_TRADE_KEYS: `const`
  // declarations inside the eval above stay in the eval's scope (only function
  // declarations hoist out), so the lifted constant is reachable from tierProfit
  // but not from here. The Python side walks est['trades'] the same way.
  for (const trade of Object.keys(S.trades || {})) {
    const td = S.trades && S.trades[trade];
    if (!td || !td.enabled) continue;
    const simple = effectiveTradeMode(trade, td) === 'simple';
    const byName = _catalogClassByName(trade);
    row.lines[trade] = (td.line_items || []).map((item) => {
      const qty = parseFloat(item.quantity) || 0;
      const sp = simple ? simpleCostSplit(trade, item, qty, byName)
                        : lineCostSplit(trade, item,
                                        (item.tiers || {})['better'] || {}, qty, byName);
      return { name: item.name || '', cls: costClassOf(trade, item, byName),
               material: r2(sp.material), labor: r2(sp.labor) };
    });
  }
  return row;
});

fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 2));
