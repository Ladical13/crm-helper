/**
 * Drives the REAL building (structure) functions lifted out of static/app.js —
 * never a reimplementation, or the test would agree with itself while the
 * shipped bundle drifted. Same extraction the bundle/parity runners use.
 *
 * Usage: node structures_runner.js <scenario.json> <out.json>
 * See test_structures.py.
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
  if (!m) throw new Error('structures_runner: function not found in app.js: ' + name);
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
  if (depth !== 0) throw new Error('structures_runner: unbalanced braces reading ' + name);
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
  if (!om) throw new Error('structures_runner: const not found in app.js: ' + name);
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
  if (depth !== 0) throw new Error('structures_runner: unbalanced braces reading ' + name);
  // Include the trailing semicolon if present so the const statement ends cleanly.
  if (src[i] === ';') i++;
  return src.slice(om.index, i);
}


const CONSTS = ['TIERS', 'SIMPLE_MODE_TRADES', 'FASTEN_ZONES', 'MEASURE_DEFS'];
const NAMES = ['mnum', 'itemSection', 'tradeSections', 'effectiveTradeMode', 'tradeTier',
               '_rateValue', '_resolveRate', 'tradeRate', 'tierRate', 'lineTotal',
               'lineTotalEffective',
               'estStructures', 'findStructure', 'structureNamed', 'tradeStructures',
               'itemMeasurements', 'structureMeasurements', 'syncStructureSections',
               'structureTotal', '_nextStructureName', '_promoteTradeToStructures',
               'addStructure', 'duplicateStructure', 'renameStructure', 'removeStructure',
               '_asceZoneWidth', 'commercialFastening', 'atticVentilation',
               'measuredQty', 'displayUnit', 'applyMeasurements'];

const NL = String.fromCharCode(10);   // keeps this builder free of escapes
const scenario = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const harness = `
  // ── stubs for the UI side these functions call on the way past ──
  let activePage = 'pricing';
  let _structureOpen = '';
  let _fastenTable = ${JSON.stringify(scenario.fastenTable || null)};
  let __confirm = true;
  function uid() { return 'id' + (++__uid.n); }
  function setDirty() {}
  function rerender() {}
  function renderTotals() {}
  function renderScopePage() {}
  function alert() {}
  function prompt(_q, d) { return __rename; }
  function confirm() { return __confirm; }
  function evalFormula() { return 0; }
`;

const code = `
  ${CONSTS.map(grabConst).join(NL)}
  ${harness}
  ${NAMES.map(grab).join(NL)}
  for (const o of ops) {
    if (o.op === 'applyMeasurements')      applyMeasurements();
    else if (o.op === 'addStructure')      addStructure(o.trade);
    else if (o.op === 'duplicate')         duplicateStructure(o.id);
    else if (o.op === 'rename')            renameStructure(o.id, o.name);
    else if (o.op === 'remove')            removeStructure(o.id);
    else throw new Error('unknown op: ' + o.op);
  }
  return {
    estimate: S,
    // What each item priced off, so a test can say WHICH building's numbers a
    // row used rather than inferring it from the quantity.
    items: (((S.trades || {}).commercial || {}).line_items || []).map(i => ({
      id: i.id, name: i.name, section: i.section || '', quantity: i.quantity,
      measured_from: (itemMeasurements(i) || {}).comm_squares,
    })),
    totals: estStructures().map(st => ({ id: st.id, name: st.name, total: structureTotal(st) })),
    fastening: estStructures().map(st => {
      const f = commercialFastening(st.measurements || {}, _fastenTable);
      return { name: st.name, ok: f.ok, insul: f.insul.total, seam: f.seam.total };
    }),
  };
`;

const run = new Function('S', 'priceBook', 'ops', '__uid', '__rename', code);
const out = run(scenario.estimate, scenario.priceBook || {}, scenario.ops || [],
                { n: 0 }, scenario.renameTo || '');
fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 2));
