# Insiders tape

The insiders tape lists officer and director prints. A user searches by officer, ticker, or company and can see a window with no matches.

## Sub-features

- `insiders-ready` paints rows from `insider-trades-lite.json` and an as-of line.
- `insiders-search` keeps only rows whose ticker text contains the query and writes `?q=`.
- `insiders-empty` shows the no-prints line for a query that matches nothing.

## How to get to it (user POV)

- Choose **Insiders** in the header (the link `insiders.html`).
- On an insider page, choose **Tape** in the navigation named `Insider tape`, or the same label on the phone tab bar.
- Type in the search box whose placeholder is `Search officer, ticker, or company`.

## Driving it with Playwright

Preconditions:

- Doctor passed for `http://127.0.0.1:<port>`.
- `insider-trades-lite.json` is the committed file. Do not rebuild it.
- Evidence directory is outside the repo.

- **Both viewports.** Windows: `python .cursor/skills/verify-quantity-capital/qc_verify.py drive insiders-tape --base http://127.0.0.1:<port> --evidence <evidence-dir>`. Linux and the QC box: the same command with `python3` after `source <venv>/bin/activate` when a venv is required. Exit code 0. `insiders-tape/report.json` records the ticker taken from the first visible `.qc-txn-tk`. Stop the server with `Stop-Process -Id <pid> -Force` on Windows or `kill <pid>` on Linux. Never kill by process name.
- **Ready list.** Wait until `#asof` is non-empty and `#rows li.qc-txn-tape` exists. The list does not say `Could not load insider tape`.
- **Search that ticker.** Fill `#q` with the first `.qc-txn-tk` token. The URL `q` equals that ticker. Every `#rows .qc-txn-tk` contains it. `local-after.png` shows the filtered list.
- **Empty query.** Fill `#q` with `zzzz-no-such-ticker`. `#rows` contains `No insider prints in this window.` `local-empty.png` shows that line.

## Gotchas

- The default window is 90 days (`?w=` is omitted until the user changes it). A search only sees prints inside that window. The harness uses a ticker already on screen so the query cannot fall outside the window.
- URL updates are debounced (`syncUrlSoon`, about 200 ms). Wait for `q=` rather than the input event.
- Below 900px the same rows use the phone trade-card layout. The `.qc-txn-tk` handle is still there. Do not require the desktop column header to be visible.
- Issuers, Basket, Buys, and Sells are separate controls (`#windows`). This recipe does not verify them.
- An issuer with no prints can still draw an issuer row under the muted line. `zzzz-no-such-ticker` must not do that.
