/**
 * Runs the REAL insuranceCostReport() lifted out of static/app.js so
 * test_insurance_margin.py can hold it to the same numbers as
 * insurance_cost_report() in app.py.
 *
 * Insurance margin is money math implemented twice for the same reason every
 * other price in this app is: the rep's browser needs the figure to move as
 * they type, and the server cannot trust the client for anything that reaches
 * analytics. That duplication is deliberate and staying — this runner is what
 * stops the two halves drifting.
 *
 * Usage: node insurance_cost_runner.js <fixtures.json> <out.json>
 * Fixture: [{name, estimate}]  →  Out: [{name, report}]
 */
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

/** Lift one `function name(...) { ... }` by brace matching. */
function grabFn(name) {
  const i = src.indexOf('function ' + name + '(');
  if (i < 0) throw new Error('insurance_cost_runner: not found in app.js: ' + name);
  let depth = 0;
  for (let k = src.indexOf('{', i); k < src.length; k++) {
    if (src[k] === '{') depth++;
    else if (src[k] === '}' && --depth === 0) return src.slice(i, k + 1);
  }
  throw new Error('insurance_cost_runner: unterminated ' + name);
}

function grabConst(decl, endMarker) {
  const i = src.indexOf(decl);
  if (i < 0) throw new Error('insurance_cost_runner: not found: ' + decl);
  const j = src.indexOf(endMarker, i);
  return src.slice(i, j + endMarker.length);
}

// insuranceTotal is the revenue basis, so it is lifted too rather than
// restated — a change to how a claim totals must move both sides at once.
const bundle = [
  grabConst('const INSURANCE_ADDERS =', '];'),
  // Revenue is roof-only now, so the scope split is part of the money math and
  // has to be lifted with it.
  grabConst('const CARRIER_SCOPE_RULES =', '];'),
  grabFn('classifyCarrierItem'),
  grabFn('carrierScopeReport'),
  grabFn('insCost'),
  grabFn('unpricedInsuranceCostLines'),
  grabFn('insuranceCostReport'),
  grabFn('insuranceTotal'),
].join('\n');

const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = fixtures.map(({ name, estimate }) => {
  // eslint-disable-next-line no-new-func
  const run = new Function('S', `${bundle}; return insuranceCostReport();`);
  return { name, report: run(estimate) };
});
fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 2));
