# Quantity Capital SQLite brain (pilot)

SQLite sits between the collectors’ JSON and the static PWA. This pilot wires **claims ↔ companies ↔ tickers**, then one politician trade calc on top. Quebec GESTIM, Ontario MLAS, and BC MTA **per-title** rows are ingested from local GeoJSON (properties only). Polygons stay in those files. Collectors are not rewritten.

Last local rebuild (2026-09-19): **61,797** title rows — Quebec 35,077 (17,451 focus / 17,626 neighbor), Ontario 26,567, British Columbia 153 — plus 68,561 title parties.

## Rebuild

From `C:\Users\gordo\Desktop\quantity-capital`:

```
python scripts/qc_sqlite/rebuild.py
python scripts/qc_sqlite/rebuild.py --boards-only
python scripts/qc_sqlite/rebuild.py --skip-jev
python scripts/qc_sqlite/paper.py
python scripts/qc_sqlite/tells.py
python scripts/qc_sqlite/analysis.py
python scripts/qc_sqlite/insider_boards.py
python scripts/qc_sqlite/export_excel.py
python scripts/qc_sqlite/fetch_province_claims.py --ontario --bc --resume
```

Needs `pip install typesafe-sdk` for Jev calls. `TYPESAFE_API_KEY` is read from the process env, or from `%USERPROFILE%\.grok\typesafe.env`. The key is never written to the repo.

Outputs:

- `qc.sqlite` (repo root) — primary DB
- `C:\Users\gordo\Desktop\Groks folder\qc.sqlite` — optional collect-side mirror
- `scripts/qc_sqlite/export/*.json` — PWA-sized stubs (attributes/links + top calc rows)
- `scripts/qc_sqlite/.cache/jev.json` — local Jev answer cache (not the API key)

Excel: `python scripts/qc_sqlite/export_excel.py` writes `quantity-capital-brain.xlsx` and `quantity-capital-claims.xlsx` (no polygons) when `openpyxl` is already installed. Without it, it writes `quantity-capital-brain-csv/` and `quantity-capital-claims-csv/` (one CSV per sheet). It never installs packages.

A full `rebuild.py` starts from `schema.sql` in `qc.sqlite.tmp`, then copies the `province:*` packs, titles, parties, links, and `province_*` resume offsets from the previous `qc.sqlite` so `fetch_province_claims.py` work survives.

Daily bats (`Groks folder/collect/update-politicians.bat`, `update-insiders.bat`) still run the scrapers, then `rebuild.py --boards-only`. `publish.py` also runs `--boards-only` on the publish worktree **when** `scripts/qc_sqlite` exists on that tree (after this PR lands). Until merge, live Pages keeps collector JSON.

## Schema (v5)

| Table | What |
| --- | --- |
| `companies` | Union of mines registry / explorers / mcap / claims-map / insider-companies, keyed by slug |
| `tickers` | `tickers.json` + `market-caps.json` |
| `company_tickers` | company ↔ ticker (first ticker per source is `is_primary`) |
| `claim_packs` | Focus/neighbor packs from the claims-map catalog, plus `{id}:quebec` / `{id}:ontario` / `{id}:bc` packs when extracts exist |
| `claim_company_links` | pack ↔ company with `source` (`catalog`, `name_match`, `extract_alias`, `jev`) and Jev score/outcome |
| `claim_titles` | One row per Quebec GESTIM / Ontario MLAS / BC MTA title (properties only — no polygons). Quebec extracts include `role=neighbor` titles. PK is `{pack}:{role}:{claim_id}` so extra polygons for the same title collapse. |
| `claim_title_parties` | Parsed `(pct, holder)` parties on a title |
| `mines` | Pin lat/lon from the claims-map catalog |
| `people` | `bios.json` |
| `politician_trades` | PTR rows with parsed amount band low/high/mid |
| `insider_trades` | Form 4 / SEDI rows |
| `trade_size_vs_cap` | **Pilot calc** (see below) |
| `tell_events` | Raw timed-spike hits (official stock buy near 20-session low, +20% within 15 sessions) |
| `tell_hands` / `tell_hand_tells` | Ranked filers (top 16) and their best tells per ticker |
| `tell_now` / `tell_now_hands` | Recent buys from those hands (top 12) |
| `jev_decisions` | Raw Choice/Noul/Score answers |
| `v_trade_size_vs_cap` | The calc as a view; the table is the materialized copy plus Jev flags |
| `v_tell_hands` / `v_tell_now` | Ranked Tells export views |
| `canada_mines` | Map 900A mine → company_id / asset_id (no FK; standalone ingest ok) |
| `canada_mine_sources` | Filing URL / title / blocker per mine-year |
| `canada_mine_production` | Cited 2025 actuals (gold in troy oz). Never invent. |

## Pilot calc: `size_vs_cap`

```
size_bps = 10000 * amount_mid / market_cap
```

`amount_mid` is the midpoint of the STOCK Act band (e.g. `$1,001–$15,000` → `8000.5`). Join is `politician_trades.ticker → tickers → company_tickers (primary) → companies`. Jev reviews the 20 largest `size_bps` prints and stores `jev_flag` / `jev_anomaly_noul`.

This is an estimate: PTR amounts are bands, not fills.

## Tells board (`tells.json`)

Same rules as `collect/build_tells.py` (deterministic — no Jev on this path):

- Official **stock purchases** only (`chart()` ticker, skip options/bonds/ETFs in the SKIP set)
- SPIKE=20%, FWD=21 sessions, NEAR=1.08 (within 8% of prior 20-session low), spike within 15 sessions
- Hand bar: MIN_SCORED=4, MIN_HITS=2 (or a whale ≥ $500k), rate ≥ 8% unless whale
- `now`: last 90 days of buys from the hot hands; OPEN_CAP=15%

Rebuild writes `tells.json` at the site root (and `scripts/qc_sqlite/export/tells.json`) when `prices/` exists. `python scripts/qc_sqlite/tells.py` refreshes Tells without a full claims rebuild. Do not change `tells.html`; it still fetches `tells.json`.

## Analysis / signals book (`analysis.json`)

Same scaffolding as `collect/build_analysis.py` (90-day window, heat, weekly/monthly TA, conflict regex). **Jev gates the export:**

| Question | Primitive | Effect |
| --- | --- | --- |
| `ship` | Choice ship/drop | Row is omitted if drop |
| `action` | Choice buy-dip / watch / avoid | Replaces rule action when confidence ≥ 0.55 |
| `borderline` | Score | Weak buy-dip without a washout flag becomes watch |
| `conflict_real` | Noul | Committee overlap stripped if noul < 0.45 |

Default `python scripts/qc_sqlite/analysis.py` calls Jev (needs `TYPESAFE_API_KEY`). `--skip-jev` is rules-only. `signals.html` still fetches `analysis.json`. Do not rewrite `build_analysis.py`.

## What Jev decides

| Call | Primitive | Used for |
| --- | --- | --- |
| Schema once per rebuild | Choice ×3 | claims grain, company key, which calc |
| Each catalog producer + fuzzy neighbor holders | Score (same / related / different) + Noul `same_name`, `holder_is_vehicle` | claim ↔ company link confidence; round Score to `leave_unlinked` / `curator` / `same_entity` |
| Unique title holders that fuzzy-match one catalog issuer or share distinctive tokens with the extract issuer | same Score + Nouls | JV co-holders, Quebec neighbor holders, and title vehicles. Unmatched names (SMM Gold Côté, numbered Quebec inc., persons) stay unlinked. Extract-alias is **not** applied to `role=neighbor` titles. |
| Top 20 `size_bps` trades | Choice flag + Noul `is_anomaly` | calc flags |
| Analysis book/avoid candidates | Choice `ship`/`action`, Score `borderline`, Noul `conflict_real` | Ship vs drop, action label, conflict framing |

Code still owns exact slug matches, amount-band parsing, and the bps formula. `--skip-jev` ingests everything and leaves Jev columns null.

If Jev prefers a different calc than `size_vs_cap`, the rebuild still ships `size_vs_cap` this session and records the Choice in `meta.jev_schema`.

## Pinned inputs (read-only)

- Groks `collect/_jev_review/claims-map/companies.json` (utf-8-sig)
- Groks `collect/mines/registry.json`, `registry_explorers.json`, `registry_mcap.json`
- Site JSON: `trades.json`, `tickers.json`, `bios.json`, `insider-trades.json`, `insider-companies.json`, `market-caps.json`

Title rows come from local extracts (geometry discarded). If a file is missing, rebuild still succeeds for the others.

| Jurisdiction | Files |
| --- | --- |
| Quebec GESTIM | `claims/<id>.geojson` (no `-ontario`/`-bc` suffix). Includes neighbor titles. |
| Ontario MLAS | `claims/<id>-ontario.geojson` |
| British Columbia MTA | `claims/<id>-bc.geojson` |

Refresh ON/BC from a checkout that has `claims/companies.json` (origin/main):

```
python build_on_bc_extracts.py
```

Quebec GESTIM extracts ship on `origin/main` under `claims/`. Copy them into this tree’s `claims/` if the checkout does not have them (this dirty UX tree did not). Do not rewrite collectors.

Local Groks `companies.json` may lag live Pages; rebuild overlays Quebec/ON/BC counts from the extract files (Quebec `quebec_count` is **focus** titles only).

## Paper / insiders

- `paper.py` reads the root `backtest.json` (built only by `build-backtest.py`) and stores its legs in `paper_*`. Jev scores the 12 largest |return| legs as artifact vs ordinary; flags stay in sqlite. It never writes site JSON.
- `insider_boards.py` runs the root `build-insider-repeatable.py` and `build-insider-follow.py`, ingests the collector's `insider-analysis.json` as-is, stores JSON in sqlite, and Jev-gates the follow list into sqlite and `export/insider-follow.json` only (the committed `insider-follow.json` is left as the builder wrote it).
- Form 4 sidecar: ingest `insider-form4.json` (do not re-scrape SEC from rebuild). Refresh with `collect/form4_enrich.py` separately.
- Canada mine production: `python3 scripts/ingest_canada_mine_production.py --sqlite qc.sqlite --apply` (monthly; cited filings only). Full `rebuild.py` reloads `canada_*` tables from the curated sources book without fetching IR. Export of `canada/producer-join.json` + existing `beta/<issuer>.json` is the ingest `--apply` / `--export-only` job. Never commit `qc.sqlite`.

## Full-province claims

`fetch_province_claims.py` pulls Ontario MLAS and BC MTA with `where=1=1`, `returnGeometry=false`, into packs `province:ontario` / `province:bc`. BC walks owner prefixes A–Z and 0–9. Resume with `--resume`. A fetch or API error exits 1. A complete non-resumed walk prunes titles it no longer sees (lapsed). Quebec GESTIM has no open REST here — producer extracts only.

Tests: `python3 scripts/qc_sqlite/test_qc_sqlite.py` (temp DBs, no network).
