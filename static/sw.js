// Bump cache version to force refresh after deployments
const CACHE_NAME = 'tawtheeq-v4';

// Avoid pre-caching '/' because it can be a redirect (login/dashboard) and can
// cause stale HTML that references removed hashed assets after deployments.
const CORE = ['/static/manifest.json'];

// Install
self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    try {
      await cache.addAll(CORE);
    } catch (e) {
      // If any core asset isn't available (e.g., hashed filenames in prod),
      // don't fail the service worker install.
    }
    self.skipWaiting();
  })());
});

// Activate
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

// Helpers
function isSameOrigin(url) {
  try { return new URL(url).origin === self.location.origin; }
  catch { return false; }
}
function isStaticRequest(req) {
  const u = new URL(req.url);
  return isSameOrigin(req.url) && u.pathname.startsWith('/static/');
}

// Fetch
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  // HTML navigations: network-first, fallback to cached '/'
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          // Cache successful navigations to allow offline fallback
          try {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((cache) => {
              if (copy && copy.ok) cache.put(event.request, copy);
            });
          } catch (e) {}
          return res;
        })
        .catch(async () => {
          // Prefer cached version of the same page; last resort: cached root
          const cached = await caches.match(event.request);
          if (cached) return cached;
          const root = await caches.match('/');
          if (root) return root;
          return new Response('Offline', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
        })
    );
    return;
  }

  // Static: stale-while-revalidate
  if (isStaticRequest(event.request)) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      const cached = await cache.match(event.request);
      const fetchPromise = fetch(event.request).then((res) => {
        if (res && res.ok) cache.put(event.request, res.clone());
        return res;
      }).catch(() => cached);
      return cached || fetchPromise;
    })());
    return;
  }

  // Default: cache-first
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  );
});
