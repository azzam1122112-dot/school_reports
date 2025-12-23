const CACHE_NAME = 'tawtheeq-v2';
// IMPORTANT: keep install caching same-origin only.
// Caching cross-origin (Google Fonts / CDN) can fail the install and prevent PWA eligibility.
const ASSETS_TO_CACHE = [
  '/',
  '/static/css/app.css',
  '/static/img/logo1.png',
  '/static/img/pattern.svg',
  '/static/manifest.json'
];

// Install Event
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    const results = await Promise.allSettled(
      ASSETS_TO_CACHE.map((url) => cache.add(url))
    );
    // Do not fail install if some assets can't be cached.
    // This keeps the SW installable even if a single request fails.
    const hasSomeSuccess = results.some(r => r.status === 'fulfilled');
    if (!hasSomeSuccess) {
      throw new Error('Service Worker install failed: could not cache any core assets');
    }
    self.skipWaiting();
  })());
});

// Activate Event
self.addEventListener('activate', event => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)));
      await self.clients.claim();
    })()
  );
});

// Fetch Event
self.addEventListener('fetch', event => {
  // Skip non-GET requests
  if (event.request.method !== 'GET') return;

  // Navigation requests: prefer network, fallback to cached shell.
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .then((res) => res)
        .catch(() => caches.match('/'))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then(cachedResponse => {
      if (cachedResponse) {
        return cachedResponse;
      }
      return fetch(event.request);
    })
  );
});
