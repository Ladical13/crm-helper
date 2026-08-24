// P1 Lift — service worker.
//
// Gyms are basements. The whole shell is precached so a session can be logged
// with no signal at all: the page, the CSS and the JS come from cache, and the
// API falls back to cache when the network is gone.
//
// Bump CACHE and every ?v= below together with the two in static/index.html.
// There is no bump_version.py here (same as the canvasser); tests/test_assets.py
// fails if the numbers disagree.
const BASE = new URL(self.registration.scope).pathname.replace(/\/$/, '');
const CACHE = 'p1lift-v2';
const SHELL = [
  BASE + '/',
  BASE + '/static/style.css?v=2',
  BASE + '/static/app.js?v=2',
  // Precached so an installed app that is opened offline still has its icon
  // and its manifest.
  BASE + '/static/icon-192.png?v=2',
  BASE + '/manifest.json',
];

self.addEventListener('install', (e) => {
  // addAll is all-or-nothing: one 404 would leave the app with no offline shell
  // at all, so a failure falls back to the network rather than breaking install.
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.origin !== self.location.origin) return;

  const path = BASE && url.pathname.startsWith(BASE)
    ? url.pathname.slice(BASE.length) : url.pathname;

  // Network-first for the API, unlike the shell. A cached workout would show
  // sets that are not really stored, and the one thing a training log must
  // never do is claim work that did not get written. The cache is a fallback
  // for a genuinely dead network, so history stays readable mid-session.
  if (path.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request).then((res) => {
        if (res && res.ok && e.request.method === 'GET') {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        }
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  // Cache-first with background revalidation, so an asset whose ?v= was not
  // bumped is one load stale rather than stale forever.
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
