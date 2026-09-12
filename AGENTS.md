# AGENTS.md

Operating notes for Quantity Capital. This is a **static, dependency-free PWA**: plain HTML/CSS/JS pages plus committed JSON data. There is no bundler, package manager, or backend server. See `README.md` for the product description.

## Run it locally

The pages `fetch` their JSON data over HTTP, so the site **must be served** — opening a file from disk (`file://`) breaks data loading and the service worker. Serve the repo root:

```
python3 -m http.server 8000   # then open http://localhost:8000/index.html
```

In Cloud Agents this server is started automatically (see `.cursor/environment.json`), and port 8000 is exposed.

## Rebuild derived data

`backtest.json` (the `paper.html` filed-date copy backtest) is generated from `trades-lite.json` + `prices/`. `insider-repeatable.json` (the Leaders → Repeatable filing-date hit-rate board) is generated from `insider-trades-lite.json` + `prices/`. `insider-follow.json` (Follow / Best officers + new-print alerts) is derived from that ranking plus the tape. All three are committed, so regenerate them after changing tape or price data:

```
python3 fetch-prices.py                 # stdlib only; appends new Yahoo daily closes into prices/
python3 build-backtest.py               # stdlib only, ~2.4s, idempotent apart from a timestamp
python3 build-insider-repeatable.py     # stdlib only; 30/90/180-day open-market copy ranks
python3 build-insider-follow.py         # stdlib only; follow list + alerts since last tape
```

`.cursor/environment.json` runs those builders in `install`, so a fresh Cloud Agent always has up-to-date derived JSON.

## Daily site refresh

`.github/workflows/daily-update.yml` runs weekdays at 23:30 UTC: fetch Yahoo closes, rebuild `backtest.json` / `insider-repeatable.json` if prices moved, always rebuild `insider-follow.json`, commit to `main`, then request a GitHub Pages rebuild. `follow-alerts.yml` rebuilds the follow list when `insider-trades-lite.json` is pushed. Tape JSON (`trades*.json`, `insider-*.json`, `analysis.json`, `tells.json`) is still produced by an off-repo collector that is not in this repository.

## Service worker cache — read before editing shell assets

`sw.js` precaches the app shell under a versioned cache name (`CACHE = "qc-shell-v..."`), and pages load `shell.css`/`qc.js` with `?v=NNN` cache-busting query strings. When you change a cached shell asset (CSS/JS/HTML listed in `sw.js`), your change may not appear until you:

- bump the `CACHE` version string in `sw.js`, and
- bump the matching `?v=` query on that asset's `<link>`/`<script>` reference.

When testing changes, do a hard reload or clear the site's caches; a stale service worker is the usual reason an edit "doesn't show up".

## Layout

- Pages: `index.html`, `politician.html`, `ticker.html`, `signals.html`, `landed.html`, `tells.html`, `paper.html`, and the `insider*.html` set.
- Scripts: `qc.js` (shared app logic), `chart.js`, `refresh.js`, `sw.js`.
- Data: `trades*.json`, `bios.json`, `tickers.json`, `traders.json`, `analysis.json`, `tells.json`, `backtest.json`, `insider-*.json`, and per-ticker `prices/<TICKER>.json`.

## Data collection (not runnable in this repo yet)

`README.md` references a collection pipeline (`collect/insider_collect.py`, `collect/fetch_insider_prices.py`, `collect/build_insider_analysis.py`) that refreshes the insider datasets. Those scripts are **not present** in this repository. Daily Yahoo price refresh and `backtest.json` rebuild *are* in-repo (`fetch-prices.py` + `daily-update.yml`). If the collect scripts are added, they will likely need outbound network access (SEC/SEDI/CEO.CA) and possibly credentials; wire them into `.cursor/environment.json`, the egress allowlist, and `daily-update.yml` at that point.
