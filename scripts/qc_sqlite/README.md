# Quantity Capital SQLite brain (pilot)

SQLite sits between the collectors’ JSON and the static PWA. This pilot wires **claims ↔ companies ↔ tickers**, then one politician trade calc on top. Polygons stay in GeoJSON extracts (not in the DB). Collectors are not rewritten.

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

## Schema (v1)

| Table | What |
| --- | --- |
| `companies` | Union of mines registry / explorers / mcap / claims-map / insider-companies, keyed by slug |
| `tickers` | `tickers.json` + `market-caps.json` |
| `company_tickers` | company ↔ ticker (first ticker per source is `is_primary`) |
| `claim_packs` | One **focus** row per producer extract + one **neighbor** row per GESTIM neighbor holder. Counts, bbox, extract path. No polygons. |
| `claim_company_links` | pack ↔ company with `source` (`catalog`, `name_match`, `jev`) and Jev score/outcome |
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
| Top 20 `size_bps` trades | Choice flag + Noul `is_anomaly` | calc flags |

Code still owns exact slug matches, amount-band parsing, and the bps formula. `--skip-jev` ingests everything and leaves Jev columns null.

If Jev prefers a different calc than `size_vs_cap`, the rebuild still ships `size_vs_cap` this session and records the Choice in `meta.jev_schema`.

## Pinned inputs (read-only)

- Groks `collect/_jev_review/claims-map/companies.json` (utf-8-sig)
- Groks `collect/mines/registry.json`, `registry_explorers.json`, `registry_mcap.json`
- Site JSON: `trades.json`, `tickers.json`, `bios.json`, `insider-trades.json`, `insider-companies.json`, `market-caps.json`

GeoJSON extracts (`claims/*.geojson`) are not ingested. They are large; the map keeps them. Local Groks catalog may lag live Pages (Ontario/BC counts).

## Next

- Per-title claim rows if extracts are local (still no polygon blobs)
- Excel workbook of links + calc
- Second calc (insider vs politician same ticker/day, or mining purchase clusters)
- PWA pages reading export JSON instead of ad-hoc joins
