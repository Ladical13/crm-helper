/**
 * Exercises the REAL Other-tab quantity guards lifted out of static/app.js —
 * never a reimplementation, or the test would agree with itself while the
 * shipped bundle drifted. Same extraction trick as parity_runner.js.
 *
 * Usage: node other_qty_runner.js <fixtures.json> <out.json>
 * See test_other_qty.py.
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
  if (!m) throw new Error('other_qty_runner: function not found in app.js: ' + name);
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
  if (depth !== 0) throw new Error('other_qty_runner: unbalanced braces reading ' + name);
  return src.slice(m.index, i);
}

/** Extract a top-level `const NAME = ...;` — lifted from source, never
 *  redefined here, or the constant could drift from the shipped bundle. */
function grabConst(name) {
  const re = new RegExp('^const ' + name + '\\s*=\\s*[^;\\n]+;', 'm');
  const m = re.exec(src);
  if (!m) throw new Error('other_qty_runner: const not found in app.js: ' + name);
  return m[0];
}


// TIERS is lifted from app.js (healOtherZeroQty closes over it) rather than
// redefined here, so a fourth package could never quietly go unhealed.
// One eval, not three: a `const` declared inside its own eval would be scoped
// to that eval and never reach this file. TIERS is lifted from app.js rather
// than redefined, so a fourth package could not quietly go unhealed.
eval([grabConst('TIERS'), grab('healOtherZeroQty'), grab('otherEnsureQty')].join(`
`));

const fx = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const out = {
  // Opening an estimate: how many rows were healed, what the Other rows look
  // like afterwards, and the healed doc itself so app.py can price it.
  heal: fx.heal.map((f) => {
    const healed = healOtherZeroQty(f.est);
    return {
      name: f.name,
      healed,
      quantities: (((f.est.trades || {}).other || {}).line_items || [])
        .map((i) => parseFloat(i.quantity) || 0),
      est: f.est,
    };
  }),
  // Typing into the Unit Cost / Sell Price box: does the row get a quantity?
  ensure: fx.ensure.map((f) => {
    const item = { quantity: f.quantity };
    otherEnsureQty(item, f.entered);
    return { name: f.name, quantity: parseFloat(item.quantity) || 0 };
  }),
};

fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 2));
