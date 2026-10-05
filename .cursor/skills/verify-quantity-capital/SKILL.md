---
name: verify-quantity-capital
description: "Drive the Quantity Capital static PWA in Chromium — politician tape, insiders, leaders, ticker charts, and filed drops — with sync Playwright at 390x844 and 1280x800, and capture local-versus-live screenshot evidence. Use when proving a UI change against the committed JSON, checking console errors, or when asked to verify quantity-capital."
---

# Verify Quantity Capital

Quantity Capital is a static PWA (HTML, CSS, JS, committed JSON). The user-facing surface is the browser. There is no app server, bundler, or login. Other surfaces (Python builders, GitHub Actions, the QC box Jev gate) are not this harness. Do not rebuild data, run `publish.py`, run the collector pipeline, or open `qc.sqlite` / `scripts/qc_sqlite/` as part of a verification run.

Read `features/README.md` before driving. A proof of one feature does not cover the others.

## Launch

From the repo root, bind a free loopback port. Do not assume port 8765 is free or that it is the instance under test. The string `127.0.0.1:8765` inside `index.html` is only the copy shown when `file://` fails to load JSON.

```
python -m http.server <port> --bind 127.0.0.1
```

Ready means `http://127.0.0.1:<port>/index.html` returns HTTP 200 and `tape/page.json` parses. Opening the HTML files from disk does not load the tape.

Record the PID of that process. Two runs use two ports. Do not drive a server you did not start. The site is read-only during a drive: filters call `history.replaceState` and do not write files.

Playwright and Pillow must import. Chromium must be installed for Playwright (`python -m playwright install chromium`). If either import fails, create a venv outside the repo and install there. Do not commit the venv.

```
python -m venv %TEMP%\qc-verify-venv
%TEMP%\qc-verify-venv\Scripts\python.exe -m pip install playwright pillow
%TEMP%\qc-verify-venv\Scripts\python.exe -m playwright install chromium
```

Then invoke the helpers with that interpreter.

### Shell cache

`sw.js` precaches the shell under `const CACHE = "qc-shell-v…"` (the constant on line 1). Pages load `shell.css`, `qc.js`, and `chart.js` with `?v=` query strings. Any edit to a file listed in the `SHELL` array of `sw.js` stays invisible to installed clients until you bump `CACHE` and the matching `?v=` on the tag you changed. This harness blocks service workers so a proof sees the files the server just sent. That does not replace the bump.

## Doctor

Run this before every drive. It is read-only. Exit 0 means the instance is worth driving: loopback base, the PID you started owns `127.0.0.1:<port>`, `index.html` contains the wire, `tape/page.json` has a non-empty `trades` list and a `collected` stamp, and `sw.js` still uses a `qc-shell-v` cache name.

```
python .cursor/skills/verify-quantity-capital/qc_verify.py doctor --base http://127.0.0.1:<port> --pid <pid> --evidence <evidence-dir>
```

`<evidence-dir>` must be outside the repo. Default is `%TEMP%\qc-verify-evidence` (or `QC_VERIFY_EVIDENCE` when set). The doctor writes `<evidence-dir>\doctor.json`.

If Playwright or Pillow is missing, doctor fails and the Launch venv commands apply. Do not point doctor at the live site. Live is a screenshot comparison target only.

## Drive

Harness: Python Playwright sync API, headless Chromium, one fresh context per viewport, service workers blocked. Viewports are `390x844` and `1280x800`. Both are required for a full proof of a feature.

```
python .cursor/skills/verify-quantity-capital/qc_verify.py drive <feature> --base http://127.0.0.1:<port> --evidence <evidence-dir>
```

`<feature>` is one of `politician-tape`, `insiders-tape`, `leaders`, `ticker-chart`, `filed`. Recipes, selectors, and observable results are in `features/`. Prefer the ARIA names and ids in those files over coordinates.

`politician-tape` also screenshots the same filtered wire on the live site:

```
python .cursor/skills/verify-quantity-capital/qc_verify.py drive politician-tape --base http://127.0.0.1:<port> --live https://gordojr1.github.io/quantity-capital --evidence <evidence-dir>
```

Drive against the JSON already in the clone (`tape/page.json`, `tape/all.json`, `tape/politicians/<id>.json`, `insider-trades-lite.json`, `insider-analysis.json`, `insider-repeatable.json`, `landed.json`, `prices/<TICKER>.json`). Live Pages is whatever the desktop publish job and GitHub Actions (`daily-update`, `follow-alerts`) last pushed. Do not rebuild to make the two match. A pixel difference is not a failure. A local failure to filter, navigate, or paint the committed JSON is.

On each viewport the script records console warnings and errors, uncaught page errors, and failed requests in `console.json`. The feature report lists them. A proof is the user action plus the resulting DOM (URL query, row text, heading), not only the final PNG. `politician-tape` also checks that a company filter fetches `tape/all.json` (the first page slice is not the whole book) and that choosing a politician opens `politician.html?id=` with that person's heading.

### Not this harness

GitHub Actions check `pr-check` (`.github/workflows/pr-check.yml`, job `check`) is the CI syntax gate: key JSON parses, `node --check` on the shell scripts, `py_compile` and unit tests for the builders. It runs the Jev merge gates `--packet-only`. It does not launch the PWA.

The Jev merge gate is `scripts/run_pr_merge_gate.py` (Work A, claims to Insiders), `scripts/run_claims_beta_gate.py` (Work B), and `scripts/run_canada_commodities_gate.py`, sharing `scripts/qc_gate.py`. `pr-check` calls them with `--packet-only` because CI has no `TYPESAFE_API_KEY`. The QC box runs `--require-jev` (key from `/home/box/shared/typesafe/env` or `%USERPROFILE%\.grok\typesafe.env`). The operator calls that box runner the Model Router bot. The in-repo scripts call the TypeSafe Jev API (Choice / Noul / Score), not Jev Bot. Do not run `--require-jev` from a UI verification. It is not a browser proof and it spends TypeSafe calls.

## Evidence

Proof files live in the evidence directory, never in the repo (a repo path would be eligible to publish).

For `politician-tape`, each viewport folder (`390x844`, `1280x800`) contains:

- `local-before.png` — wire before the filter
- `local-action.png` — company field set to `NVDA`
- `local-after.png` — wire showing only NVDA rows
- `local-empty.png` — empty-filter copy
- `local-politician.png` — member page reached from the picker
- `live-after.png` — same NVDA filter on the live site
- `local-vs-live.png` — local after and live after, side by side, labeled
- `wire-after.txt`, `wire-live.txt` — URL plus wire text
- `console.json` — console errors, page errors, failed requests

`politician-tape/report.json` is the pass/fail summary. Other features write the same shape under their own folder, without the live stitch (they do not change the comparison target).

Capture both viewports. Do not treat a skipped viewport as verified.

Stitch by hand:

```
python .cursor/skills/verify-quantity-capital/qc_verify.py stitch --left <local-after.png> --right <live-after.png> --out <local-vs-live.png> --label-left local --label-right live
```

## Cleanup

Stop only the PID you started. Do not kill by process name (`python`, `chrome`, `http.server`).

```
Stop-Process -Id <pid> -Force
```

Confirm with doctor or `netstat` that `127.0.0.1:<port>` is no longer LISTENING. Cleanup does not delete the evidence directory. After a failed drive, stop that PID before starting another server on the same port.

## Helpers

| Command | What it does |
| --- | --- |
| `qc_verify.py doctor --base http://127.0.0.1:<port> --pid <pid> --evidence <dir>` | Read-only readiness check. Prints JSON. Writes `doctor.json`. |
| `qc_verify.py drive politician-tape --base http://127.0.0.1:<port> --live https://gordojr1.github.io/quantity-capital --evidence <dir>` | Both viewports. Local filter, empty state, member page, live stitch. |
| `qc_verify.py drive insiders-tape --base http://127.0.0.1:<port> --evidence <dir>` | Officer tape search and empty state. |
| `qc_verify.py drive leaders --base http://127.0.0.1:<port> --evidence <dir>` | Performers board, then Repeatable at 30 days. |
| `qc_verify.py drive ticker-chart --base http://127.0.0.1:<port> --evidence <dir>` | `ticker.html?t=NVDA`, then the 1Y range. |
| `qc_verify.py drive filed --base http://127.0.0.1:<port> --evidence <dir>` | Politician Filed page, then Last 7 days. |
| `qc_verify.py stitch --left A --right B --out C` | Side-by-side PNG with Pillow. |

Invoke from the repo root with `python .cursor/skills/verify-quantity-capital/qc_verify.py …`.

## Maintenance

When routes, labels, or breakpoints change, update this skill and `features/` with `/maintain-verification-skill`. Re-run doctor and one mapped feature after the edit. Do not treat an unexecuted skill edit as verified.
