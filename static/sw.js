const CACHE_NAME = 'tuuinmemo-shell-v8';

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network first: always show the latest deployed version, and fall back to
// the last copy seen only when offline.
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  if (event.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) {
    return; // let API calls and other origins go straight to the network
  }

  const isPage = event.request.mode === 'navigate';
  event.respondWith(
    fetch(event.request, { cache: 'no-store' })
      .then(response => {
        if (response.ok) {
          const clone = response.clone();
          // Store the page under "/" so ?guest=... visits share one offline copy.
          caches.open(CACHE_NAME).then(cache => cache.put(isPage ? '/' : event.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(isPage ? '/' : event.request))
  );
});
