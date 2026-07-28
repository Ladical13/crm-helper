/**
 * Runs the REAL commercial fastener calculator lifted out of static/app.js
 * against fixtures, so test_fastening.py can hold the JS and Python
 * implementations to the same numbers.
 *
 * The fastening TABLE is not extracted — it ships in the fixture JSON. That is
 * the whole point of commercialFastening(m, table) taking the table as an
 * argument: grabConst() below only matches single-line `const NAME = ...;`, so
 * a multi-line table constant would silently fail to extract.
 *
 * Also runs atticVentilation on the same fixtures — its JS and Python copies
 * have never had a parity test, and it costs five lines to close that gap.
 *
 * Usage: node fastening_runner.js <fixtures.json> <out.json>
 * Fixture: [{name, m, table}]  →  Out: [{name, fasten:{...}, vent:{...}}]
 * See test_fastening.py.
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
  if (!m) throw new Error('fastening_runner: function not found in app.js: ' + name);
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
  if (depth !== 0) throw new Error('fastening_runner: unbalanced braces reading ' + name);
  return src.slice(m.index, i);
}

function grabConst(name) {
  const re = new RegExp('^const ' + name + '\\s*=\\s*[^;\\n]+;', 'm');
  const m = re.exec(src);
  if (!m) throw new Error('fastening_runner: const not found in app.js: ' + name);
  return m[0];
}

const CONSTS = ['FASTEN_ZONES', 'NFA_TURTLE_SQIN', 'NFA_RIDGE_SQIN_LF',
                'NFA_INTAKE_SQIN_LF', 'VENT_RULE_DIVISOR'];
const NAMES = ['mnum', '_asceZoneWidth', 'commercialFastening', 'atticVentilation'];

const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const body = `
  ${CONSTS.map(grabConst).join('\n')}
  ${NAMES.map(grab).join('\n')}
  return fixtures.map(f => ({
    name: f.name,
    fasten: commercialFastening(f.m || {}, f.table || null),
    vent: atticVentilation(f.m || {}),
  }));
`;

const out = new Function('fixtures', body)(fixtures);
fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 2));
