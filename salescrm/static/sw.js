// Project One Pipeline — service worker. Bump CACHE on any shell asset change.
//
// Mount prefix, derived from the worker's own scope: '/crm' inside the portal,
// '' when served standalone. A worker can never claim a scope broader than the
// path it is served from, which is what keeps this one and the estimator's
// from fighting now that they share an origin.
const BASE = new URL(self.registration.scope).pathname.replace(/\/$/, '');

const CACHE = 'p1pipeline-v9';
const SHELL = [
  BASE + '/',
  BASE + '/static/style.css?v=9',
  BASE + '/static/app.js?v=9',
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
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Network-first for API (always fresh data); cache-first for the static shell.
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  // Path relative to the mount, so the rules read the same either way.
  const path = BASE && url.pathname.startsWith(BASE)
    ? url.pathname.slice(BASE.length) : url.pathname;
  if (path.startsWith('/api/') || path === '/health') {
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
    return;
  }
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match(BASE + '/')))
  );
});
