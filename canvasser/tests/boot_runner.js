/* Loads static/app.js the way a browser does and reports what happened.
 *
 * `node --check` only parses. It cannot see a reference to a name that was
 * deleted, which is exactly how `window.syncToCRM = syncToCRM` (a leftover of
 * the old Den sync) sat at the bottom of the bundle throwing a ReferenceError
 * before the `boot()` call on the next line — every rep got a blank screen and
 * the suite stayed green.
 *
 * runInThisContext, not require(): in a CommonJS module a top-level
 * `function foo(){}` is module-scoped, but in a browser classic script it is a
 * global — which is what the inline onclick= handlers in this app resolve
 * against. Only runInThisContext reproduces that.
 *
 * Usage: node boot_runner.js <name> [<name> ...]
 *   where each <name> is a function an inline handler expects to be global.
 * Prints one JSON object on stdout.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_JS = path.join(__dirname, '..', 'static', 'app.js');

// ── Browser stubs ──────────────────────────────────────────────────────────
// Deliberately permissive: this runner is asking "does the bundle survive being
// loaded", not "does the map render". Anything the module touches on the way
// through answers to something rather than throwing.
const stubEl = new Proxy({}, {
  get(_t, prop) {
    switch (prop) {
      case 'addEventListener':
      case 'appendChild':
      case 'removeChild':
      case 'setAttribute':
      case 'focus':
      case 'blur':          return () => {};
      case 'classList':     return { add() {}, remove() {}, toggle() {}, contains: () => false };
      case 'querySelectorAll': return () => [];
      case 'querySelector': return () => null;
      case 'style':         return {};
      case 'dataset':       return {};
      case 'value':
      case 'textContent':
      case 'innerHTML':     return '';
      default:              return undefined;
    }
  },
  set: () => true,
});

const responses = {
  '/api/config': { pin_types: {}, pin_stage: {} },
  '/api/me': { authenticated: true, username: 'tester', full_name: 'Tester', is_admin: false },
  '/api/pins': [],
  '/api/team-locations': [],
};

const sandbox = {
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  Promise, JSON, Date, Math, Object, Array, String, Number, Set, Map, Error,
  parseInt, parseFloat, encodeURIComponent, decodeURIComponent, isNaN,
  document: {
    getElementById: () => stubEl,
    querySelectorAll: () => [],
    createElement: () => stubEl,
    addEventListener: () => {},
  },
  location: { pathname: '/canvass/', href: '' },
  navigator: { geolocation: null, permissions: null, clipboard: null },
  devicePixelRatio: 1,
  alert: () => {}, confirm: () => true, prompt: () => null,
  fetch: (url) => {
    const hit = Object.keys(responses).find((k) => String(url).includes(k));
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve(hit ? responses[hit] : {}),
    });
  },
  // Leaflet: every access returns a callable that returns the same proxy, so
  // chained builder calls (L.map(...).setView(...).addTo(...)) all resolve.
  L: null,
};
const leaflet = new Proxy(function () {}, {
  get: () => leaflet,
  apply: () => leaflet,
  construct: () => leaflet,
});
sandbox.L = leaflet;
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

// Async failures past boot() are not what this runner judges — faithfully
// stubbing Leaflet is out of scope and would make the test brittle. Record
// them so a human can see them; the assertion is about the load itself.
const rejections = [];
process.on('unhandledRejection', (e) => rejections.push(String(e && e.message || e)));

const context = vm.createContext(sandbox);
const source = fs.readFileSync(APP_JS, 'utf8');

let result;
try {
  vm.runInContext(source, context, { filename: 'app.js' });
  const wanted = process.argv.slice(2);
  const missing = wanted.filter((n) => typeof sandbox[n] !== 'function');
  result = { ok: missing.length === 0, missing, rejections };
} catch (e) {
  result = {
    ok: false,
    threw: `${e.constructor.name}: ${e.message}`,
    missing: process.argv.slice(2),
    rejections,
  };
}
console.log(JSON.stringify(result));
// app.js starts 30s GPS timers on boot, which would otherwise keep node alive
// forever. Give the async boot path a tick to surface anything, then leave.
setTimeout(() => process.exit(0), 50);
