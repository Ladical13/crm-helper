/**
 * Evaluates the REAL customer-grouping helpers lifted out of static/app.js, so
 * test_customer_file.py can check them instead of restating the rules.
 *
 * Two functions, both of which failed silently when they were wrong:
 *   custKey() decides which estimates share a customer file. It used to be a
 *   substring `.includes()` on one side and `===` on the other, so "Jon Smith"
 *   and "Jon Smithson" were the same customer to the file and different
 *   customers to the button that creates the next estimate.
 *   jsq() quotes a customer name for the inline onclick handlers the file is
 *   built from. esc() escapes for HTML but not for the JS string literal, so a
 *   customer named O'Brien produced a handler that would not parse — the
 *   button was simply dead, with nothing logged.
 *
 * Usage: node customer_key_runner.js <fixtures.json> <out.json>
 * Fixture: {keys: [string], quotes: [string]}
 * Out:     {keys: [string], quotes: [string]}
 */
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

/** Lift `function <name>(` through its closing brace at column 0. */
function grabFn(name) {
  const header = 'function ' + name + '(';
  const i = src.indexOf(header);
  if (i < 0) throw new Error('customer_key_runner: not found in app.js: ' + name);
  const j = src.indexOf('\n}', i);
  if (j < 0) throw new Error('customer_key_runner: unterminated: ' + name);
  return src.slice(i, j + 2);
}

const mod = {};
new Function('exports', [grabFn('esc'), grabFn('custKey'), grabFn('jsq'),
  'exports.custKey = custKey; exports.jsq = jsq;'].join('\n'))(mod);

const fx = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
fs.writeFileSync(process.argv[3], JSON.stringify({
  keys:   (fx.keys   || []).map(mod.custKey),
  quotes: (fx.quotes || []).map(mod.jsq),
}, null, 2), 'utf8');
