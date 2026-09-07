// Project One Pipeline — service worker. Bump CACHE on any shell asset change.
//
// Mount prefix, derived from the worker's own scope: '/crm' inside the portal,
// '' when served standalone. A worker can never claim a scope broader than the
// path it is served from, which is what keeps this one and the estimator's
// from fighting now that they share an origin.
const BASE = new URL(self.registration.scope).pathname.replace(/\/$/, '');

const CACHE = 'p1pipeline-v21';
const SHELL = [
  BASE + '/',
  BASE + '/static/style.css?v=21',
  BASE + '/static/app.js?v=21',
  BASE + '/static/icon-192.png',
  BASE + '/static/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL))
    .catch(() => {})   // one missing asset must not abort the whole install
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE && k !== API_CACHE)
                      .map((k) => caches.delete(k)))
    ).then(() => self.clients.claim()).then(() => drain())
  );
});

// Network-first for API (always fresh data); cache-first for the static shell.
// ── The outbox ───────────────────────────────────────────────────────────────
//
// A rep works this app in a driveway, in a basement stairwell, in the foothills.
// Every write used to be dropped on the floor the moment the signal went: the
// door knock they just logged simply did not happen, with a red toast as the
// only trace. The canvasser has had an offline shell for a year; the CRM, which
// is where the knock is actually RECORDED, had none.
//
// So mutations that fail to reach the network are stored and replayed. Two
// things make that safe rather than reckless:
//
//   * every queued write carries an Idempotency-Key minted by the page. The
//     retry is indistinguishable from the original, so a request whose RESPONSE
//     was lost -- the row already written -- does not log the call twice.
//   * only /api/ writes are queued. Anything else fails as it always did.
const OUTBOX_DB = 'p1pipeline-outbox';
const OUTBOX_STORE = 'writes';
const API_CACHE = 'p1pipeline-api';
const API_CACHE_MAX = 60;

function outbox() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(OUTBOX_DB, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(OUTBOX_STORE)) {
        req.result.createObjectStore(OUTBOX_STORE, { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx(db, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = db.transaction(OUTBOX_STORE, mode);
    const out = fn(t.objectStore(OUTBOX_STORE));
    t.oncomplete = () => resolve(out && out.result !== undefined ? out.result : out);
    t.onerror = () => reject(t.error);
  });
}

async function enqueue(request) {
  const body = await request.clone().text();
  const headers = {};
  request.headers.forEach((v, k) => { headers[k] = v; });
  const db = await outbox();
  await tx(db, 'readwrite', (store) => store.add({
    url: request.url, method: request.method, headers, body, queued_at: Date.now(),
  }));
}

async function queued() {
  const db = await outbox();
  return tx(db, 'readonly', (store) => store.getAll());
}

// Replay in the order they were made. A stage move followed by a note is not
// the same story as a note followed by a stage move, and the timeline is read
// by a human. One failure stops the drain: the next attempt starts from the
// same place rather than reordering around a stuck write.
async function drain() {
  let rows;
  try { rows = await queued(); } catch (err) { return 0; }
  rows.sort((a, b) => a.id - b.id);
  let sent = 0;
  for (const row of rows) {
    let res;
    try {
      res = await fetch(row.url, { method: row.method, headers: row.headers,
                                   body: row.body, credentials: 'same-origin' });
    } catch (err) {
      break;                        // still offline; keep the rest for later
    }
    // 5xx is the server having a bad moment — worth retrying. Anything else,
    // including a 4xx rejection, is an answer: keeping it would retry a write
    // the server will never accept, forever.
    if (res.status >= 500) break;
    const db = await outbox();
    await tx(db, 'readwrite', (store) => store.delete(row.id));
    sent += 1;
  }
  if (sent) {
    const clients = await self.clients.matchAll();
    clients.forEach((c) => c.postMessage({ type: 'outbox-drained', count: sent }));
  }
  return sent;
}

self.addEventListener('message', (e) => {
  if (e.data && e.data.type === 'drain-outbox') e.waitUntil(drain());
});

async function trimApiCache() {
  const cache = await caches.open(API_CACHE);
  const keys = await cache.keys();
  for (const k of keys.slice(0, Math.max(0, keys.length - API_CACHE_MAX))) {
    await cache.delete(k);
  }
}

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') {
    // Only our own API, and only writes. A cross-origin POST is none of our
    // business and a queued one would be replayed at someone else's server.
    if (url.origin === self.location.origin && url.pathname.includes('/api/')) {
      e.respondWith((async () => {
        try {
          const res = await fetch(e.request.clone());
          if (res.status < 500) { e.waitUntil(drain()); return res; }
          throw new Error('server error');
        } catch (err) {
          await enqueue(e.request);
          // 202 Accepted, not a fake 200: the page must be able to tell "saved
          // on this phone" from "saved". Pretending it landed is how a rep
          // finds out on Monday that Thursday never happened.
          return new Response(JSON.stringify({ queued: true }), {
            status: 202, headers: { 'Content-Type': 'application/json' },
          });
        }
      })());
    }
    return;
  }
  // Path relative to the mount, so the rules read the same either way.
  const path = BASE && url.pathname.startsWith(BASE)
    ? url.pathname.slice(BASE.length) : url.pathname;
  if (path.startsWith('/api/') || path === '/health') {
    // Network-FIRST, always: a rep acting on a stale lead list calls someone a
    // teammate already closed. The cache is strictly a last resort for when
    // there is no network at all, and the page shows an offline banner when it
    // serves one, so nobody mistakes yesterday's pipeline for today's.
    e.respondWith((async () => {
      try {
        const res = await fetch(e.request);
        if (res && res.ok) {
          const copy = res.clone();
          e.waitUntil(caches.open(API_CACHE)
            .then((c) => c.put(e.request, copy)).then(trimApiCache).catch(() => {}));
        }
        return res;
      } catch (err) {
        const hit = await caches.match(e.request, { cacheName: API_CACHE });
        if (hit) {
          const stale = hit.clone();
          const headers = new Headers(stale.headers);
          headers.set('X-P1-Stale', '1');
          return new Response(await stale.blob(), { status: 200, headers });
        }
        throw err;
      }
    })());
    return;
  }
  // Everything else: cache-first, but ALWAYS revalidate in the background.
  //
  // This used to be `hit || fetch(...)`, which never refetched once something
  // was cached. Versioned bundles survived that (a new ?v= is a new URL), but
  // the portal's /shell.js and /shell.css carry no version, so the app-switcher
  // bar froze at whatever was first cached and only a CACHE bump could shift
  // it. Kicking off `live` unconditionally is what makes an unversioned asset
  // one load stale instead of stale forever.
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const live = fetch(e.request).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        }
        return res;
      }).catch(() => hit || caches.match(BASE + '/'));
      return hit || live;
    })
  );
});
