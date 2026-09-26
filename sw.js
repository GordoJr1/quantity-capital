const CACHE = "qc-shell-v224";
const SHELL = [
  "./",
  "./index.html",
  "./beta.html",
  "./canada.html",
  "./claims.html",
  "./claims-db.html",
  "./contracts.html",
  "./claims-db.js?v=5",
  "./claims-neighbors.js?v=1",
  "./claims-map.js?v=22",
  "./shell.css",
  "./shell.css?v=113",
  "./shell.css?v=164",
  "./shell.css?v=165",
  "./shell.css?v=168",
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
  "./qc.js?v=107",
  "./qc.js?v=113",
  "./qc.js?v=124",
  "./qc.js?v=126",
  "./qc.js?v=128",
  "./qc.js?v=129",
  "./manifest.webmanifest",
  "./refresh.js",
  "./chart.js",
  "./chart.js?v=122",
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
  // PMTiles needs Range requests. Do not put the gitignored archive in the shell cache.
  if (url.pathname.includes("/claims/tiles/") || url.pathname.endsWith(".pmtiles") || url.pathname.endsWith(".pmtiles.png") || url.pathname.includes("/.build/") || url.pathname.includes("/.db/")) {
    return;
  }

  const isTrade = /(?:^|\/)(trades|trades-lite|landed|heat)\.json$/.test(url.pathname)
    || /\/tape\/(?:page|all|filers)\.json$/.test(url.pathname)
    || /\/tape\/kind\/[^/]+\.json$/.test(url.pathname)
    || /\/tape\/politicians\/[^/]+\.json$/.test(url.pathname)
    || /\/tape\/tickers\/[^/]+\.json$/.test(url.pathname);
  const isData = isTrade
    || /(?:^|\/)(trades|trades-lite|bios|tickers|traders|analysis|tells|backtest|contracts|insider-trades|insider-trades-lite|insider-analysis|insider-companies|insider-repeatable|insider-follow|price-checks)\.json$/.test(url.pathname)
    || /(?:^|\/)prices\/[^/]+\.json$/.test(url.pathname)
    || /(?:^|\/)beta\/[^/]+\.json$/.test(url.pathname)
    || /(?:^|\/)canada\/[^/]+\.json$/.test(url.pathname);
  const isDoc = event.request.mode === "navigate"
    || url.pathname.endsWith(".html")
    || url.pathname.endsWith("/")
    || url.pathname.endsWith("/refresh.js")
    || url.pathname.endsWith("/sw.js")
    || url.pathname.endsWith("/shell.css")
    || url.pathname.endsWith("/qc.js")
    || url.pathname.endsWith("/chart.js");
  function storeIfOk(res) {
    if (res && res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then((cache) => cache.put(event.request, copy));
    }
    return res;
  }
  // Trade files are network-first. A cached body must not win, and a failed
  // response must not be stored.
  if (isTrade) {
    event.respondWith((async () => {
      try {
        const res = await fetch(event.request);
        storeIfOk(res);
        return res;
      } catch (err) {
        const cached = await caches.match(event.request);
        if (cached) return cached;
        throw err;
      }
    })());
    return;
  }
  if (isData) {
    event.respondWith((async () => {
      const cached = await caches.match(event.request);
      const network = fetch(event.request).then((res) => {
        storeIfOk(res);
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
          storeIfOk(res);
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
