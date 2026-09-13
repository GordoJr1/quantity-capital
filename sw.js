const CACHE = "qc-shell-v142";
const SHELL = [
  "./",
  "./index.html",
  "./beta.html",
  "./shell.css",
  "./shell.css?v=105",
  "./shell.css?v=106",
  "./shell.css?v=107",
  "./shell.css?v=108",
  "./shell.css?v=109",
  "./shell.css?v=110",
  "./shell.css?v=111",
  "./shell.css?v=112",
  "./shell.css?v=113",
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
  "./insider-checks.html",
  "./chart-revamp-mockups.html",
  "./qc.js",
  "./qc.js?v=105",
  "./qc.js?v=106",
  "./qc.js?v=107",
  "./qc.js?v=108",
  "./qc.js?v=109",
  "./qc.js?v=110",
  "./qc.js?v=111",
  "./qc.js?v=112",
  "./qc.js?v=113",
  "./qc.js?v=114",
  "./qc.js?v=115",
  "./qc.js?v=116",
  "./qc.js?v=117",
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

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const dest = (event.notification.data && event.notification.data.url) || "./insiders.html";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (let i = 0; i < list.length; i++) {
        const client = list[i];
        if (client.url && client.url.indexOf(dest) >= 0 && "focus" in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(dest);
    })
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

  const isData = /(?:^|\/)(trades|trades-lite|bios|tickers|traders|analysis|tells|backtest|insider-trades|insider-trades-lite|insider-analysis|insider-companies|insider-repeatable|insider-follow|price-checks)\.json$/.test(url.pathname)
    || /(?:^|\/)prices\/[^/]+\.json$/.test(url.pathname)
    || /(?:^|\/)beta\/[^/]+\.json$/.test(url.pathname);
  const isDoc = event.request.mode === "navigate"
    || url.pathname.endsWith(".html")
    || url.pathname.endsWith("/")
    || url.pathname.endsWith("/refresh.js")
    || url.pathname.endsWith("/sw.js")
    || url.pathname.endsWith("/shell.css")
    || url.pathname.endsWith("/qc.js")
    || url.pathname.endsWith("/chart.js");
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
