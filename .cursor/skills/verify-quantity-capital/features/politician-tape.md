# Politician tape

The politician tape is the homepage wire of STOCK Act filings. A user filters it by company or ticker, sees an empty state when nothing matches, and opens a member page from the name lookup.

## Sub-features

- `tape-ready` paints the wire from `tape/page.json` and shows the as-of line.
- `tape-nav` shows the header nav at 1280px and the bottom tab bar at 390px.
- `tape-filter` narrows the wire to one ticker and writes `?q=` on the URL.
- `tape-empty` shows the no-match line for a ticker that is not on the tape.
- `tape-person` opens `politician.html?id=` from the politician lookup.

## How to get to it (user POV)

- Open the site root, which serves `index.html`.
- On a wide window, choose **Tape** in the navigation named `Politician tape`.
- On a phone, choose **Tape** in the bottom tab bar named `Politician tape`.
- Type a company or ticker in **Company** under **Search & filters**.
- Type a name in **Look up a politician** and choose a match.

## Driving it with Playwright

Preconditions:

- Doctor passed for `http://127.0.0.1:<port>`.
- Evidence directory is outside the repo.
- The committed `tape/page.json` filer index includes `abigail-spanberger` (Abigail Davis Spanberger). If a later tape drops that id, stop and do not report this person as verified.

- **Both viewports, full proof.** Windows: `python .cursor/skills/verify-quantity-capital/qc_verify.py drive politician-tape --base http://127.0.0.1:<port> --live https://gordojr1.github.io/quantity-capital --evidence <evidence-dir>`. Linux and the QC box: the same command with `python3` after `source <venv>/bin/activate` when a venv is required. Exit code 0. `politician-tape/report.json` has `"ok": true` and both `390x844` and `1280x800`. Stop the server with `Stop-Process -Id <pid> -Force` on Windows or `kill <pid>` on Linux. Never kill by process name.
- **Ready wire.** The page waits until `body` has `is-ready`, `#asof` is non-empty, and `#wire` has `li.w-row` or `li.w1`. The wire text does not contain `Could not load trades.json`. `a.mark` reads Quantity Capital. Compare case-insensitively: the header CSS uppercases it, so the rendered text is `QUANTITY` / `CAPITAL`. At 1280, `nav.qc-nav[aria-label="Politician tape"]` is visible and `nav.tabbar` is hidden. At 390 the opposite is true.
- **Filter NVDA.** If `details.filter-fold` is closed, choose the **Search & filters** summary. Fill `#filter-company` with `NVDA`. The URL contains `q=NVDA`. `#wire` text includes `NVDA` and does not include `No trades match these filters.` At least one `#wire .w-tk` or `#wire .w1-tk b` is exactly `NVDA`. The browser requested `tape/all.json` with HTTP 200. `local-action.png` shows the field. `local-after.png` shows the wire.
- **Empty filter.** Fill `#filter-company` with `zzzz-no-such-ticker`. `#wire` reads `No trades match these filters.` `local-empty.png` shows that line.
- **Open the member.** Fill `#who-search` with `Spanberger` and choose `#who-menu button[data-id="abigail-spanberger"]`. The URL is `politician.html?id=abigail-spanberger`. `#who` contains `Spanberger`. `#rows` does not say `Could not load`. `local-politician.png` shows the heading.
- **Live comparison.** The same NVDA filter on `https://gordojr1.github.io/quantity-capital/index.html` produces `live-after.png`. `local-vs-live.png` places local after on the left and live after on the right.

## Gotchas

- `file://` never loads the tape. The error copy mentions port 8765; that is not the port you bound.
- Search & filters starts closed below 820px. Filling `#filter-company` while the details element is closed times out.
- Desktop rows are `li.w-row` at `min-width: 900px`. Phone rows are `li.w1`. Do not require `li.w-row` at 390.
- The default **Stocks / bonds** value `stock` keeps stocks and options. It is not stocks-only.
- A company query always fetches `tape/all.json` (about 10 MB). Wait for `q=NVDA` and a visible `NVDA` symbol, not a fixed sleep.
- The filter matches ticker, company, and asset text. A filing whose asset paste mentions NVIDIA can show another symbol (National Storage Affiliates is `NSA` and its asset text contains an NVDA lot). Option rows stored as `NVDA PUT` with no code render as `No ticker`. Do not require every visible symbol to be `NVDA`.
- The member page reads `tape/politicians/<id>.json` plus `bios.json`. A heading of `Not found` is a failed proof.
- Live data is published separately. Do not rebuild the clone to match pixels.
- Service workers are blocked in this harness. Shipping a shell edit still requires a `sw.js` `CACHE` bump. See `../SKILL.md`.
