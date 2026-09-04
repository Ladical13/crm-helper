// P1 Canvasser — service worker.
//
// Mount prefix, derived from the worker's own scope: '/canvass' inside the
// portal, '' when served standalone. A worker can never claim a scope broader
// than the path it is served from, which is why app.py serves this file from
// the app root rather than /static/ — and is what keeps this worker, the
// estimator's and the CRM's from fighting now that they share an origin.
const BASE = new URL(self.registration.scope).pathname.replace(/\/$/, '');

// Bump on any change to a SHELL asset. The ?v= numbers must match
// static/index.html — tests/test_assets.py in salescrm has the equivalent
// guard; canvasser/tests/test_assets.py holds this one.
const CACHE = 'p1canvasser-v3';
const SHELL = [
  BASE + '/',
  BASE + '/static/style.css?v=3',
  BASE + '/static/app.js?v=3',
  // Vendored Leaflet. The whole point of pulling these off unpkg was that a
  // rep on one bar in a driveway could not be left staring at a blank screen
  // waiting for a CDN — precaching them is the other half of that fix.
  BASE + '/static/vendor/leaflet.css?v=3',
  BASE + '/static/vendor/leaflet.js?v=3',
  BASE + '/static/vendor/MarkerCluster.css?v=3',
  BASE + '/static/vendor/leaflet.markercluster.js?v=3',
  BASE + '/static/vendor/images/marker-icon.png',
  BASE + '/static/vendor/images/marker-icon-2x.png',
  BASE + '/static/vendor/images/marker-shadow.png',
  BASE + '/static/vendor/images/layers.png',
  BASE + '/static/vendor/images/layers-2x.png',
];

self.addEventListener('install', (e) => {
  // addAll is all-or-nothing; one 404 would leave the app with no offline
  // shell at all, so failures are swallowed and the fetch handler falls back
  // to the network as it would have anyway.
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

  // Map tiles (arcgisonline, cartocdn) and anything else cross-origin: straight
  // through, never cached. Leaflet requests them without CORS, so the responses
  // come back opaque, and browsers charge opaque entries against the storage
  // quota at a heavily padded size — a few hundred cached tiles can evict the
  // entire app shell, which would trade the thing this worker exists to
  // guarantee for a partial map. Caching tiles properly needs crossOrigin on
  // the tile layer plus a bounded cache; deliberately not done here.
  if (url.origin !== self.location.origin) return;

  const path = BASE && url.pathname.startsWith(BASE)
    ? url.pathname.slice(BASE.length) : url.pathname;

  // Pins, GPS, config: always the network. A canvasser acting on a cached pin
  // list would knock doors a teammate already worked, so stale data here is
  // worse than an honest failure. Falls back to cache only when truly offline.
  if (path.startsWith('/api/')) {
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
    return;
  }

  // Everything else: cache-first, but ALWAYS revalidate in the background, so
  // an unversioned asset (the portal's /shell.js and /shell.css) is one load
  // stale rather than stale forever.
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
