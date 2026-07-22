const CACHE = 'po-v111';
const SHELL = ['/', '/static/style.css?v=111', '/static/app.js?v=111', '/static/logo.png', '/static/icon-192.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL).catch(() => {})));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API calls, signing links, uploads: always hit the network — never serve stale
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/sign/') ||
      url.pathname.startsWith('/sign-co/') ||
      url.pathname.startsWith('/uploads/')) {
    e.respondWith(fetch(e.request));
    return;
  }

  // Static assets (CSS, JS, images): cache-first, update in background
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request).then(hit => {
        const net = fetch(e.request).then(r => {
          const clone = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return r;
        }).catch(() => hit);  // offline: fall back to cache if we had one
        return hit || net;
      })
    );
    return;
  }

  // Navigation (HTML pages): network-first, app-shell fallback when offline.
  // NOTE: caches.match() returns a Promise (always truthy), so the fallback
  // must live inside .then() — `caches.match('/') || response` never falls back.
  e.respondWith(
    fetch(e.request).catch(() =>
      caches.match('/').then(hit =>
        hit ||
        new Response(
          '<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>' +
          '<body style="font-family:system-ui,-apple-system,sans-serif;padding:48px 24px;text-align:center;background:#f1f4f8">' +
          '<img src="/static/logo.png" style="height:64px;margin-bottom:24px"><br>' +
          '<h2 style="color:#1a3a5c;margin-bottom:8px">You\'re offline</h2>' +
          '<p style="color:#6b7280">Open the app when you have a connection.</p>' +
          '</body></html>',
          { headers: { 'Content-Type': 'text/html' } }
        )
      )
    )
  );
});
