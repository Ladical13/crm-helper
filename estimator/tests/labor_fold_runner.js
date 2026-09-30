/**
 * Folds fixtures' hidden lines with the REAL functions lifted out of
 * static/app.js — foldHiddenLines, foldHostSku, linePriceView — so
 * test_labor_fold.py can hold them to _fold_hidden_lines / _line_price_view.
 *
 * Usage: node labor_fold_runner.js <fixtures.json> <out.json>
 */
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

/** Extract a top-level `function name(...) {...}` by brace matching, skipping
 *  strings, comments and regex-free code (none of these three use a regex
 *  literal containing a brace). */
function grab(name) {
  const re = new RegExp('^function ' + name + '\\s*\\([^)]*\\)\\s*\\{', 'm');
  const m = re.exec(src);
  if (!m) throw new Error('labor_fold_runner: function not found in app.js: ' + name);
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
  if (depth !== 0) throw new Error('labor_fold_runner: unbalanced braces reading ' + name);
  return src.slice(m.index, i);
}

const lib = new Function(
  [grab('foldHostSku'), grab('foldHiddenLines'), grab('linePriceView'),
   'return { foldHiddenLines, linePriceView };'].join('\n'))();

const [fxPath, outPath] = process.argv.slice(2);
const fx = JSON.parse(fs.readFileSync(fxPath, 'utf8'));
const out = {
  folds: fx.folds.map(f => {
    const r = lib.foldHiddenLines(f.trade, f.entries.map(e => ({ item: e.item, line: e.line })));
    return { rows: r.rows.map(e => [e.item.name, e.line]), unfolded: r.unfolded };
  }),
  views: fx.views.map(v => lib.linePriceView(v.pv, v.signed)),
};
fs.writeFileSync(outPath, JSON.stringify(out));
