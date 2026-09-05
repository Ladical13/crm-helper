/**
 * Runs the REAL labor classifier and cost split out of static/app.js — never a
 * reimplementation, or the test would agree with itself while the shipped
 * bundle drifted. Same extraction technique as parity_runner.js.
 *
 * Usage: node labor_split_runner.js <fixtures.json> <out.json>
 * See test_labor_split.py.
 */
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

/** Extract a top-level `function name(...) {...}` by brace matching, skipping
 *  strings and comments. Lifted from parity_runner.js. */
function grab(name) {
  const re = new RegExp('^function ' + name + '\\s*\\([^)]*\\)\\s*\\{', 'm');
  const m = re.exec(src);
  if (!m) throw new Error('labor_split_runner: function not found in app.js: ' + name);
  let i = m.index + m[0].length, depth = 1;
  while (i < src.length && depth > 0) {
    const ch = src[i], next = src[i + 1];
    if (ch === '/' && next === '/') { while (i < src.length && src[i] !== '\n') i++; }
    else if (ch === '/' && next === '*') {
      i += 2;
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) i++;
      i += 2;
    } else if (ch === '"' || ch === "'" || ch === '`') {
      const q = ch; i++;
      while (i < src.length && src[i] !== q) { if (src[i] === '\\') i++; i++; }
      i++;
    } else { if (ch === '{') depth++; else if (ch === '}') depth--; i++; }
  }
  return src.slice(m.index, i);
}

/** Lift a top-level `const NAME = <value>;` so the test uses app.js's own
 *  regex rather than restating it. */
function grabConst(name) {
  const m = new RegExp('^const ' + name + '\\s*=\\s*(.+?);\\s*$', 'm').exec(src);
  if (!m) throw new Error('labor_split_runner: const not found in app.js: ' + name);
  return eval('(' + m[1] + ')');
}

/** Lift one MEASURES entry's calc by key — the derived-quantity math. */
function grabMeasure(key) {
  const m = new RegExp(key + ':\\s*\\{[\\s\\S]*?calc:\\s*(m =>[\\s\\S]*?)\\s*\\},\\n', 'm').exec(src);
  if (!m) throw new Error('labor_split_runner: measure not found in app.js: ' + key);
  return eval('(' + m[1] + ')');
}

// Scaffolding tierProfit closes over. Deliberately minimal: everything that
// decides material-vs-labor is the real code below.
const TIERS = ['good', 'better', 'best'];
const RETAIL_TRADE_KEYS = ['roofing', 'siding', 'windows', 'gutters', 'commercial', 'other'];
const FRANCHISE_RATE = 0.08;
let S = null, priceBook = null;
const effectiveTradeMode = (trade, td) => td.mode || 'gbb';
// Sell is not what this runner tests; a fixed markup keeps it deterministic.
const tradeTotal = (trade, tier) => {
  let c = 0;
  (S.trades[trade].line_items || []).forEach(i => {
    const q = parseFloat(i.quantity) || 0; if (q <= 0) return;
    const t = (i.tiers || {})[tier] || {};
    if (t.included === false) return;
    c += ((parseFloat(t.material_unit_cost) || 0) + (parseFloat(t.labor_unit_cost) || 0)) * q;
  });
  return c / (1 - 0.35);
};

eval(grab('mnum'));
globalThis._LABOR_ID_RE = grabConst('_LABOR_ID_RE');
eval(grab('isLaborLine'));
eval(grab('tierProfit'));
const extraLayerSquares = grabMeasure('extra_layer_squares');

const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = fixtures.map(fx => {
  if (fx.measure_only) return { extra_layer_squares: extraLayerSquares(fx.measurements) };
  priceBook = fx.price_book;
  S = { selected_tier: 'better', measurements: fx.measurements || {}, trades: fx.trades };
  const p = tierProfit(fx.tier || 'better');
  return { material: p.material, labor: p.labor, cost: p.cost,
           per_trade: p.perTrade };
});
fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 2));
