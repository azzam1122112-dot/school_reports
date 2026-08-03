const CACHE_NAME = "tawtheeq-v8";
const OFFLINE_URL = "/static/offline.html";
const CORE_ASSETS = [
  OFFLINE_URL,
  "/static/manifest.json?v=20260803.1",
  "/static/img/pwa/icon-192.png",
  "/static/img/pwa/icon-512.png",
  "/static/img/pwa/icon-maskable-192.png",
  "/static/img/pwa/icon-maskable-512.png",
  "/static/img/pwa/apple-touch-icon-180.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    await Promise.allSettled(CORE_ASSETS.map(async (url) => {
      const response = await fetch(new Request(url, { cache: "reload" }));
      if (response.ok) await cache.put(url, response);
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)));
    if (self.registration.navigationPreload) {
      await self.registration.navigationPreload.enable();
    }
    await self.clients.claim();
  })());
});

function isSameOrigin(request) {
  return new URL(request.url).origin === self.location.origin;
}

function isStaticRequest(request) {
  return new URL(request.url).pathname.startsWith("/static/");
}

function isManifestRequest(request) {
  return new URL(request.url).pathname === "/static/manifest.json";
}

function isApiRequest(request) {
  return new URL(request.url).pathname.startsWith("/api/");
}

async function networkFirstManifest(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(request, response.clone());
    return response;
  } catch (error) {
    return (await cache.match(request)) || Response.error();
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  const network = fetch(request).then(async (response) => {
    if (response.ok) await cache.put(request, response.clone());
    return response;
  });

  if (cached) {
    network.catch(() => {});
    return cached;
  }

  return network;
}

async function handleNavigation(event) {
  try {
    const preload = await event.preloadResponse;
    if (preload) return preload;
    return await fetch(event.request);
  } catch (error) {
    const cached = await caches.match(OFFLINE_URL);
    return cached || new Response("لا يوجد اتصال بالإنترنت.", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8" }
    });
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || !isSameOrigin(request)) return;

  // صفحات الحساب والتقارير شخصية؛ لا نخزّن HTML أو ردود API في الجهاز.
  if (request.mode === "navigate") {
    event.respondWith(handleNavigation(event));
    return;
  }

  if (isApiRequest(request)) {
    event.respondWith(fetch(request).catch(() => new Response(
      JSON.stringify({ detail: "لا يوجد اتصال بالإنترنت." }),
      { status: 503, headers: { "Content-Type": "application/json; charset=utf-8" } }
    )));
    return;
  }

  if (isManifestRequest(request)) {
    event.respondWith(networkFirstManifest(request));
    return;
  }

  if (isStaticRequest(request)) {
    event.respondWith(staleWhileRevalidate(request));
  }
});

self.addEventListener("message", (event) => {
  if (event.data === "SKIP_WAITING") self.skipWaiting();
});
