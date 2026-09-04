/**
 * Evaluates the REAL report-only helpers lifted out of static/app.js, so
 * test_report_only.py can hold them to the same numbers app.py produces.
 *
 * These are a hand-mirrored pair with no parity harness behind them, and they
 * decide money: when no trade carries scope, the condition report's recommended
 * repairs ARE the price of the estimate. The browser prints that number on the
 * PDF and the server puts it on the customer's sign page, the estimate list,
 * the funnel and the Den push — so the two disagreeing is a bid that quotes
 * itself twice.
 *
 * Usage: node report_only_runner.js <fixtures.json> <out.json>
 * Fixture: {cases: [estimate doc, ...]}   (the doc IS `S` in the browser)
 * Out:     [{immediate, soon, monitor, total, anyRange, hasScope, reportOnly}]
 */
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

/** Lift `function <name>(` through its closing brace at column 0. */
function grabFn(name) {
  const header = 'function ' + name + '(';
  const i = src.indexOf(header);
  if (i < 0) throw new Error('report_only_runner: not found in app.js: ' + name);
  const j = src.indexOf('\n}', i);
  if (j < 0) throw new Error('report_only_runner: unterminated: ' + name);
  return src.slice(i, j + 2);
}

/** Lift `const <name> = ...;` through the first line that is just `];`. */
function grabConst(name) {
  const header = 'const ' + name + ' = ';
  const i = src.indexOf(header);
  if (i < 0) throw new Error('report_only_runner: not found in app.js: ' + name);
  const eol = src.indexOf('\n', i);
  if (src.slice(i, eol).trimEnd().endsWith(';')) return src.slice(i, eol);
  const j = src.indexOf('\n];', i);
  if (j < 0) throw new Error('report_only_runner: unterminated: ' + name);
  return src.slice(i, j + 3);
}

const parts = [
  grabConst('RETAIL_TRADE_KEYS'), grabConst('RH_CONDITIONS'), grabConst('PC_SECTIONS'),
  // pcGet() is reached only by a legacy roof_health estimate, and that is
  // exactly the path worth exercising — so it is lifted too, with the two
  // globals it touches stubbed rather than the function reimplemented.
  'function fmtDate(d){ return "2026-09-01"; }',
  'function setDirty(){}',
  grabFn('pcBlankSection'), grabFn('pcGet'), grabFn('pcIsRange'),
  grabFn('pcRepairLines'), grabFn('pcRepairTotals'),
  grabFn('hasPricedScope'), grabFn('isReportOnly'),
  'exports.run = function(doc){ S = doc;' +
  '  const t = pcRepairTotals();' +
  '  return {immediate:t.immediate, soon:t.soon, monitor:t.monitor, total:t.total,' +
  '          anyRange:t.anyRange, hasScope:hasPricedScope(), reportOnly:isReportOnly()}; };',
].join('\n');

const mod = {};
new Function('exports', 'var S;\n' + parts)(mod);

const fx = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
fs.writeFileSync(process.argv[3],
  JSON.stringify((fx.cases || []).map(mod.run), null, 2), 'utf8');
