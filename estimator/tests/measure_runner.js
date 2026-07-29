/**
 * Evaluates the REAL MEASURE_DEFS auto-quantity formulas lifted out of
 * static/app.js, so test_measures.py can check them against fixtures instead of
 * restating the arithmetic in Python.
 *
 * MEASURE_DEFS has no Python twin — auto-quantities are computed in the browser
 * and the resulting number is stored on the line item. That makes these
 * formulas untested by the pricing parity suite, which only extracts the
 * rate/total functions. Soffit width in particular is a `|| 0` trap: a missing
 * width must fall back to the identity multiplier, never zero the line.
 *
 * Only defs whose calc is a self-contained arithmetic expression can run here.
 * comm_fast_insul / comm_fast_seam call commercialFastening and are skipped —
 * they have their own runner (fastening_runner.js).
 *
 * Usage: node measure_runner.js <fixtures.json> <out.json>
 * Fixture: [{name, key, m}]  →  Out: [{name, value}]
 */
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

/** Lift `const MEASURE_DEFS = { ... };` and the mnum() it depends on. */
function grabBlock(header, endMarker) {
  const i = src.indexOf(header);
  if (i < 0) throw new Error('measure_runner: not found in app.js: ' + header);
  const j = src.indexOf(endMarker, i);
  if (j < 0) throw new Error('measure_runner: unterminated block: ' + header);
  return src.slice(i, j + endMarker.length);
}

const mnumSrc = grabBlock('function mnum(v, dflt) {', '\n}');
const defsSrc = grabBlock('const MEASURE_DEFS = {', '\n};');

// commercialFastening is out of scope here; stub it so the object literal
// evaluates. Any fixture that reaches those two keys fails loudly instead.
const stub = 'function commercialFastening() {' +
  ' throw new Error("measure_runner: use fastening_runner.js for fastener counts"); }\n' +
  'const _fastenTable = null;\n';

const MEASURE_DEFS = new Function(
  mnumSrc + '\n' + stub + defsSrc + '\nreturn MEASURE_DEFS;')();

const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = fixtures.map(f => {
  const def = MEASURE_DEFS[f.key];
  if (!def) throw new Error('measure_runner: unknown measure key: ' + f.key);
  return { name: f.name, value: def.calc(f.m) };
});
fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 2));
