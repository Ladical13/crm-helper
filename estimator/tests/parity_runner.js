/**
 * Prices a set of fixtures using the REAL pricing functions lifted out of
 * static/app.js — never a reimplementation, or the test would happily agree
 * with itself while the shipped bundle drifted.
 *
 * Usage: node parity_runner.js <fixtures.json> <out.json>
 * See test_parity.py.
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
  if (!m) throw new Error('parity_runner: function not found in app.js: ' + name);
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
  if (depth !== 0) throw new Error('parity_runner: unbalanced braces reading ' + name);
  return src.slice(m.index, i);
}

/** Extract a top-level `const NAME = ...;` — lifted from source, never
 *  redefined here, or the constant could drift from the shipped bundle. */
function grabConst(name) {
  const re = new RegExp('^const ' + name + '\\s*=\\s*[^;\\n]+;', 'm');
  const m = re.exec(src);
  if (!m) throw new Error('parity_runner: const not found in app.js: ' + name);
  return m[0];
}

const CONSTS = ['DEFAULT_RATE', 'RETAIL_TRADE_KEYS', 'SIMPLE_MODE_TRADES', 'MODE_DEFAULT_FLIPPED'];
const NAMES = ['_rateValue', '_resolveRate', 'tierRate', 'tradeRate', 'lineTotal',
               'lineTotalEffective', 'effectiveTradeMode', 'tradeTotal', 'grandTotal',
               'selectedTotal', 'tradeTier'];

// Globals the extracted functions close over in the real bundle.
// RETAIL_TRADE_KEYS is lifted from app.js (see CONSTS) rather than redefined,
// so a trade added there can never silently go unpriced in this runner.
const TIERS = ['good', 'better', 'best'];
let S;

eval(CONSTS.map(grabConst).join('\n') + '\n' + NAMES.map(grab).join('\n'));

const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = fixtures.map((f) => {
  S = f.state;
  const row = { name: f.name };
  for (const t of TIERS) row[t] = grandTotal(t);
  row.selected = selectedTotal();
  if (f.rate_probe) {
    row.rate = tierRate(f.rate_probe.trade, f.rate_probe.tier);
  }
  return row;
});

fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 2));
