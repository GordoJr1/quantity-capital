const CACHE = "qc-shell-v52";
const SHELL = [
  "./",
  "./index.html",
  "./politician.html",
  "./ticker.html",
  "./signals.html",
  "./landed.html",
  "./tells.html",
  "./paper.html",
  "./insiders.html",
  "./insider-landed.html",
  "./insider-signals.html",
  "./insider-ticker.html",
  "./insider.html",
  "./insider-board.html",
  "./qc.js",
  "./manifest.webmanifest",
  "./refresh.js",
  "./chart.js",
  "./city.jpg?v=15",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/icon-512-maskable.png",
  "./icons/apple-touch-icon.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  const isData = /(?:^|\/)(trades|trades-lite|bios|tickers|traders|analysis|tells|backtest|insider-trades|insider-trades-lite|insider-analysis|insider-companies)\.json$/.test(url.pathname)
    || /(?:^|\/)prices\/[^/]+\.json$/.test(url.pathname);
  const isDoc = event.request.mode === "navigate"
    || url.pathname.endsWith(".html")
    || url.pathname.endsWith("/")
    || url.pathname.endsWith("/refresh.js")
    || url.pathname.endsWith("/sw.js");
  if (isData) {
    event.respondWith((async () => {
      const cached = await caches.match(event.request);
      const network = fetch(event.request).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        return res;
      }).catch(() => cached);
      return cached || network;
    })());
    return;
  }
  if (isDoc) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          return res;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return res;
      });
    })
  );
});
