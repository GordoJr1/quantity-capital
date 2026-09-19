# Quantity Capital SQLite brain (pilot)

SQLite sits between the collectors’ JSON and the static PWA. This pilot wires **claims ↔ companies ↔ tickers**, then one politician trade calc on top. Ontario and BC **per-title** rows are ingested from local GeoJSON (properties only). Polygons stay in those files. Collectors are not rewritten.

Last local rebuild (2026-09-19): **26,720** title rows — Ontario 26,567 / British Columbia 153 — plus 31,434 title parties.

## Rebuild

From `C:\Users\gordo\Desktop\quantity-capital`:

```
python scripts/qc_sqlite/rebuild.py
python scripts/qc_sqlite/rebuild.py --skip-jev
```

Needs `pip install typesafe-sdk` for Jev calls. `TYPESAFE_API_KEY` is read from the process env, or from `%USERPROFILE%\.grok\typesafe.env`. The key is never written to the repo.

Outputs:

- `qc.sqlite` (repo root) — primary DB
- `C:\Users\gordo\Desktop\Groks folder\qc.sqlite` — optional collect-side mirror
- `scripts/qc_sqlite/export/*.json` — PWA-sized stubs (attributes/links + top calc rows)
- `scripts/qc_sqlite/.cache/jev.json` — local Jev answer cache (not the API key)

Excel export is not in this pilot.

## Schema (v2)

| Table | What |
| --- | --- |
| `companies` | Union of mines registry / explorers / mcap / claims-map / insider-companies, keyed by slug |
| `tickers` | `tickers.json` + `market-caps.json` |
| `company_tickers` | company ↔ ticker (first ticker per source is `is_primary`) |
| `claim_packs` | Focus/neighbor packs from the claims-map catalog, plus `{id}:ontario` / `{id}:bc` packs when extracts exist |
| `claim_company_links` | pack ↔ company with `source` (`catalog`, `name_match`, `extract_alias`, `jev`) and Jev score/outcome |
| `claim_titles` | One row per Ontario MLAS / BC MTA title (properties only — no polygons) |
| `claim_title_parties` | Parsed `(pct, holder)` parties on a title |
| `mines` | Pin lat/lon from the claims-map catalog |
| `people` | `bios.json` |
| `politician_trades` | PTR rows with parsed amount band low/high/mid |
| `insider_trades` | Form 4 / SEDI rows |
| `trade_size_vs_cap` | **Pilot calc** (see below) |
| `jev_decisions` | Raw Choice/Noul/Score answers |
| `v_trade_size_vs_cap` | The calc as a view; the table is the materialized copy plus Jev flags |

## Pilot calc: `size_vs_cap`

```
size_bps = 10000 * amount_mid / market_cap
```

`amount_mid` is the midpoint of the STOCK Act band (e.g. `$1,001–$15,000` → `8000.5`). Join is `politician_trades.ticker → tickers → company_tickers (primary) → companies`. Jev reviews the 20 largest `size_bps` prints and stores `jev_flag` / `jev_anomaly_noul`.

This is an estimate: PTR amounts are bands, not fills.

## What Jev decides

| Call | Primitive | Used for |
| --- | --- | --- |
| Schema once per rebuild | Choice ×3 | claims grain, company key, which calc |
| Each catalog producer + fuzzy neighbor holders | Score (same / related / different) + Noul `same_name`, `holder_is_vehicle` | claim ↔ company link confidence; round Score to `leave_unlinked` / `curator` / `same_entity` |
| Unique ON/BC title holders that fuzzy-match one catalog issuer or share distinctive tokens with the extract issuer | same Score + Nouls | JV co-holders and title vehicles (`jv_holder` / `title_vehicle`). Unmatched JV names (e.g. SMM Gold Côté) stay unlinked on purpose. |
| Top 20 `size_bps` trades | Choice flag + Noul `is_anomaly` | calc flags |

Code still owns exact slug matches, amount-band parsing, and the bps formula. `--skip-jev` ingests everything and leaves Jev columns null.

If Jev prefers a different calc than `size_vs_cap`, the rebuild still ships `size_vs_cap` this session and records the Choice in `meta.jev_schema`.

## Pinned inputs (read-only)

- Groks `collect/_jev_review/claims-map/companies.json` (utf-8-sig)
- Groks `collect/mines/registry.json`, `registry_explorers.json`, `registry_mcap.json`
- Site JSON: `trades.json`, `tickers.json`, `bios.json`, `insider-trades.json`, `insider-companies.json`, `market-caps.json`

Ontario/BC title rows come from local `claims/*-ontario.geojson` and `claims/*-bc.geojson`. Geometry is discarded on ingest. If those files are missing, rebuild still succeeds (packs + Quebec catalog only).

Refresh extracts from a checkout that has `claims/companies.json` (origin/main):

```
python build_on_bc_extracts.py
```

That script hits Ontario MLAS and BC MTA REST and rewrites `claims/<id>-ontario.geojson` / `claims/<id>-bc.geojson`. Copy those files into this repo’s `claims/` folder if you are on a checkout that does not have them (this dirty UX tree did not). Quebec per-title GeoJSON is still map-only.

Local Groks `companies.json` may lag live Pages; rebuild overlays ON/BC counts from the extract files.

## Next

- Quebec GESTIM per-title rows (same properties grain; extracts are large)
- Excel workbook of links + calc
- Second calc (insider vs politician same ticker/day, or mining purchase clusters)
- PWA pages reading export JSON instead of ad-hoc joins
