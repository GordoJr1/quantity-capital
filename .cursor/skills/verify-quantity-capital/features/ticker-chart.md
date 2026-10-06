# Ticker chart

The politician ticker page charts one symbol from `prices/<TICKER>.json` and lists the politician prints for that symbol.

## Sub-features

- `ticker-open` loads `ticker.html?t=NVDA` with a company heading, a last price, and a drawn chart.
- `ticker-range` selects the 1Y range tab.
- `ticker-marks` lists the company tape under the chart.

## How to get to it (user POV)

- From a politician wire row, choose the ticker link (`ticker.html?t=<symbol>`).
- Open `ticker.html?t=NVDA` directly.
- Choose a range tab under the chart: `1M`, `3M`, `6M`, `1Y`, `3Y`.

## Driving it with Playwright

Preconditions:

- Doctor passed for `http://127.0.0.1:<port>`.
- `prices/NVDA.json` is already in the clone. Do not run `fetch-prices.py`.
- Evidence directory is outside the repo.

- **Both viewports.** Windows: `python .cursor/skills/verify-quantity-capital/qc_verify.py drive ticker-chart --base http://127.0.0.1:<port> --evidence <evidence-dir>`. Linux and the QC box: the same command with `python3` after `source <venv>/bin/activate` when a venv is required. Exit code 0. The report heading is the company name, not an em dash. Stop the server with `Stop-Process -Id <pid> -Force` on Windows or `kill <pid>` on Linux. Never kill by process name.
- **Chart ready.** Wait until `#who` is not empty and not an em dash, `#last-px` is a price, and `#chart-svg` has at least one child.
- **1Y range.** Choose `#range button[data-r="1y"]` (accessible name `1Y`). That button's `aria-selected` is `true`.
- **Trade list.** `#marks li.qc-txn-tape` has at least one row. `local-after.png` shows the chart.

## Gotchas

- This is the politician chart (`ticker.html`), not `insider-ticker.html`. Insider charts are unmapped.
- The page fetches `prices/NVDA.json`. A note of `No daily prices on file` means the proof failed; do not fetch prices to fix it.
- Range buttons are painted by script. Wait for `#range button[data-r="1y"]` instead of clicking a coordinate.
- Phone layout shortens the chart. Still require an SVG child, not a particular pixel height.
- Option purchase dates can be marked. The legend says option P&L is not calculated. Do not assert an option profit figure.
