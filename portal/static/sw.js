// Portal service worker — root scope, deliberately near-inert.
//
// The estimator (/estimate/sw.js) and the CRM (/crm/sw.js) register their own
// workers at narrower scopes, and a narrower scope wins for pages inside it.
// This one therefore controls only the launcher and the login page. It must
// NOT try to be clever about the sub-apps: before the merge, the estimator and
// CRM workers both claimed scope '/' with different cache names and whichever
// registered last won. Passing everything else straight through is what keeps
// them from fighting again.
const CACHE = 'p1portal-v2';
const SHELL = [
  '/',
  '/shell.css',
  '/shell.js',
  '/static/icon-192.png',
];

// Prefixes owned by another service worker or by the server. Never intercept.
//
// `/nimbus` is here because its API lives at `/nimbus/api/...`, which does NOT
// start with `/api/` — so without this entry the cache-first handler below
// served every Nimbus API response from cache and revalidated in the
// background. On a live-status dashboard that is worse than useless: "Re-check
// all" on the Connections page returned a cached answer, and the SEO page
// showed "no runs yet" while a completed run sat in the database. Nimbus is an
// admin dashboard, it has no offline story, and stale status beats no status
// only in situations that do not apply here.
const NOT_OURS = ['/canvass', '/crm', '/estimate', '/nimbus', '/api/',
                  '/login', '/logout', '/sign/', '/sign-co/', '/uploads/',
                  '/account/'];

self.addEventListener('install', (e) => {
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
  if (url.origin !== self.location.origin) return;
  if (NOT_OURS.some((p) => url.pathname.startsWith(p))) return;
  if (e.request.method !== 'GET') return;

  // Launcher shell: cache-first, refresh in the background.
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const live = fetch(e.request).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => hit);
      return hit || live;
    })
  );
});
