/**
 * Renders the carrier-import review modal using the REAL openXactModal lifted
 * out of static/app.js — never a reimplementation, or the test would agree
 * with itself while the shipped bundle drifted.
 *
 * Usage: node xact_modal_runner.js <payload.json> <out.html>
 * See test_symbility_import.py.
 */
const fs = require('fs');
const path = require('path');

const APP_JS = path.join(__dirname, '..', 'static', 'app.js');
const src = fs.readFileSync(APP_JS, 'utf8');

// A '/' opens a regex literal (rather than division) only where a value cannot
// already have ended — after an operator, an opening bracket or a separator.
const REGEX_CAN_START = /[(,=:[!&|?{};+\-*%<>~^]/;

/** Extract a top-level `function name(...) {...}` by brace matching. Skips
 *  strings, comments AND regex literals. All three matter here: an apostrophe
 *  in a comment ("app.py's") would open a phantom string, and esc()/fmtCur()
 *  hold regexes carrying a bare double quote (/"/g) and unbalanced braces
 *  (/\B(?=(\d{3})+(?!\d))/g) — either would run the match off the end of the
 *  file. This is parity_runner.js's lifter with that third case added. */
function grab(name) {
  const re = new RegExp('^function ' + name + '\\s*\\([^)]*\\)\\s*\\{', 'm');
  const m = re.exec(src);
  if (!m) throw new Error('xact_modal_runner: function not found in app.js: ' + name);
  let i = m.index + m[0].length;
  let depth = 1;
  let prev = '{';                       // last significant char seen
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
      prev = 'x';                       // a value just ended
    } else if (ch === '/' && REGEX_CAN_START.test(prev)) {
      i++;
      let inClass = false;
      while (i < src.length) {
        const c = src[i];
        if (c === '\\') { i += 2; continue; }
        if (c === '[') inClass = true;
        else if (c === ']') inClass = false;
        else if (c === '/' && !inClass) break;
        i++;
      }
      i++;
      while (i < src.length && /[a-z]/.test(src[i])) i++;   // flags
      prev = 'x';
    } else {
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
      if (!/\s/.test(ch)) prev = ch;
      i++;
    }
  }
  return src.slice(m.index, i);
}

// Minimal DOM: the modal only ever sets innerHTML and toggles a class.
const els = {};
const el = id => (els[id] = els[id] || {
  id, innerHTML: '', classList: { remove() {}, add() {}, toggle() {} },
});
const document = {
  getElementById: el,
  querySelector: () => null,
  querySelectorAll: () => [],
};

let _xactData = null;
let _xactExcluded = new Set();

const body = [
  grab('esc'), grab('fmtCur'), grab('xactUpdateTotals'), grab('openXactModal'),
  'openXactModal(PAYLOAD);',
  'return { modal: els["xact-modal-body"].innerHTML,'
  + ' totals: els["xact-footer-totals"].innerHTML };',
].join('\n\n');

const payload = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const run = new Function('PAYLOAD', 'document', 'els', '_xactData', '_xactExcluded', body);
const out = run(payload, document, els, _xactData, _xactExcluded);
fs.writeFileSync(process.argv[3], JSON.stringify(out), 'utf8');
