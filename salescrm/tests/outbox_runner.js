// Exercise the service worker's outbox against a fake IndexedDB, fake caches
// and a scriptable fetch. Same approach as the estimator's parity_runner: the
// real source is loaded rather than restated, so a rename fails loudly here
// instead of passing while the shipped worker does something else.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SW = fs.readFileSync(path.join(__dirname, '..', 'static', 'sw.js'), 'utf8');

function fakeIndexedDB() {
  const rows = [];
  let nextId = 1;
  return {
    rows,
    open() {
      const req = {};
      setTimeout(() => {
        req.result = {
          objectStoreNames: { contains: () => true },
          createObjectStore: () => {},
          transaction() {
            const t = {};
            setTimeout(() => t.oncomplete && t.oncomplete(), 0);
            return {
              objectStore: () => ({
                add: (v) => { rows.push({ ...v, id: nextId++ }); return {}; },
                getAll: () => ({ get result() { return rows.slice(); } }),
                delete: (id) => {
                  const i = rows.findIndex((r) => r.id === id);
                  if (i >= 0) rows.splice(i, 1);
                  return {};
                },
              }),
              set oncomplete(fn) { t.oncomplete = fn; },
              set onerror(fn) { t.onerror = fn; },
              get oncomplete() { return t.oncomplete; },
            };
          },
        };
        req.onsuccess && req.onsuccess();
      }, 0);
      return req;
    },
  };
}

function makeSandbox({ fetchImpl }) {
  const idb = fakeIndexedDB();
  const posted = [];
  const listeners = {};
  const sandbox = {
    indexedDB: idb,
    console,
    setTimeout,
    Headers: global.Headers,
    Response: global.Response,
    Request: global.Request,
    URL,
    fetch: fetchImpl,
    caches: {
      open: async () => ({ keys: async () => [], put: async () => {}, delete: async () => {} }),
      match: async () => undefined,
      keys: async () => [],
    },
    self: {
      location: { origin: 'https://p1.example' },
      registration: { scope: 'https://p1.example/crm/' },
      clients: { matchAll: async () => [{ postMessage: (m) => posted.push(m) }], claim: async () => {} },
      addEventListener: (name, fn) => { listeners[name] = fn; },
      skipWaiting: () => {},
    },
  };
  sandbox.self.indexedDB = idb;
  vm.createContext(sandbox);
  vm.runInContext(SW, sandbox);
  return { sandbox, idb, posted, listeners };
}

// A fetch event whose respondWith we can await.
function fetchEvent(request) {
  let settle;
  const done = new Promise((r) => { settle = r; });
  return {
    request,
    respondWith: (p) => settle(p),
    waitUntil: () => {},
    response: done,
  };
}

const results = {};

(async () => {
  // ── A write with no network is queued, and answered 202 ──────────────────
  {
    const { idb, listeners } = makeSandbox({ fetchImpl: async () => { throw new Error('offline'); } });
    const req = new Request('https://p1.example/crm/api/leads/1/activities',
      { method: 'POST', body: '{"kind":"door"}', headers: { 'Idempotency-Key': 'k1' } });
    const e = fetchEvent(req);
    listeners.fetch(e);
    const res = await e.response;
    results.queued_status = res.status;
    results.queued_body = await res.json();
    results.queued_rows = idb.rows.length;
    results.queued_keeps_key = idb.rows[0].headers['idempotency-key'] === 'k1'
                            || idb.rows[0].headers['Idempotency-Key'] === 'k1';
  }

  // ── A cross-origin write is NOT queued ───────────────────────────────────
  {
    const { idb, listeners } = makeSandbox({ fetchImpl: async () => { throw new Error('offline'); } });
    const req = new Request('https://base44.app/api/entities/Contact',
      { method: 'POST', body: '{}' });
    const e = fetchEvent(req);
    const handled = listeners.fetch(e);
    results.cross_origin_rows = idb.rows.length;
    results.cross_origin_untouched = handled === undefined;
  }

  // ── Draining replays in order and clears the queue ───────────────────────
  {
    const seen = [];
    let online = false;
    const { idb, listeners, posted } = makeSandbox({
      fetchImpl: async (url, init) => {
        if (!online) throw new Error('offline');
        seen.push(JSON.parse(init.body).n);
        return new Response('{}', { status: 200 });
      },
    });
    for (const n of [1, 2, 3]) {
      const e = fetchEvent(new Request('https://p1.example/crm/api/leads',
        { method: 'POST', body: JSON.stringify({ n }) }));
      listeners.fetch(e);
      await e.response;
    }
    results.before_drain = idb.rows.length;
    online = true;
    listeners.message({ data: { type: 'drain-outbox' }, waitUntil: (p) => p });
    await new Promise((r) => setTimeout(r, 50));
    results.drain_order = seen;
    results.after_drain = idb.rows.length;
    results.notified = posted;
  }

  // ── A 4xx is an answer: dropped, not retried forever ─────────────────────
  {
    let online = false;
    const { idb, listeners } = makeSandbox({
      fetchImpl: async () => {
        if (!online) throw new Error('offline');
        return new Response('{"error":"nope"}', { status: 400 });
      },
    });
    const e = fetchEvent(new Request('https://p1.example/crm/api/leads',
      { method: 'POST', body: '{}' }));
    listeners.fetch(e);
    await e.response;
    online = true;
    listeners.message({ data: { type: 'drain-outbox' }, waitUntil: (p) => p });
    await new Promise((r) => setTimeout(r, 50));
    results.rejected_dropped = idb.rows.length;
  }

  // ── A 5xx is the server stumbling: kept for the next attempt ─────────────
  {
    let online = false;
    const { idb, listeners } = makeSandbox({
      fetchImpl: async () => {
        if (!online) throw new Error('offline');
        return new Response('boom', { status: 503 });
      },
    });
    const e = fetchEvent(new Request('https://p1.example/crm/api/leads',
      { method: 'POST', body: '{}' }));
    listeners.fetch(e);
    await e.response;
    online = true;
    listeners.message({ data: { type: 'drain-outbox' }, waitUntil: (p) => p });
    await new Promise((r) => setTimeout(r, 50));
    results.server_error_kept = idb.rows.length;
  }

  process.stdout.write(JSON.stringify(results));
})().catch((err) => { console.error(err); process.exit(1); });
