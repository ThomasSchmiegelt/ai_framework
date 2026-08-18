// sw.js — Service Worker für die installierbare PWA.
// Strategie: /api/* immer live (nie cachen), Navigationen network-first mit
// Cache-Fallback (App läuft auch offline-Shell), statische Assets
// stale-while-revalidate. Nur GET, nur same-origin.
// WICHTIG: Cache-Version bei jedem Release anheben — der Browser erkennt die
// geänderte sw.js, aktiviert sie sofort (skipWaiting) und löscht alte Caches,
// damit Updates statischer Dateien ohne Hard-Reload durchschlagen.
const CACHE = 'localai-v31';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return; // Backend immer live (Streaming/SSE/POST ohnehin)

  if (req.mode === 'navigate') {
    e.respondWith((async () => {
      try {
        const net = await fetch(req);
        const cache = await caches.open(CACHE);
        cache.put('/', net.clone());
        return net;
      } catch (_) {
        return (await caches.match('/')) || Response.error();
      }
    })());
    return;
  }

  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(req);
    const fetchP = fetch(req)
      .then((res) => { if (res && res.ok) cache.put(req, res.clone()); return res; })
      .catch(() => cached);
    return cached || fetchP;
  })());
});
