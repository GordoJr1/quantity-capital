# Claims vs insider buys — 7 Oct 2026 research

Research only. Not investment advice. Not legal title. This folder is not linked from the live site nav.

After merge, GitHub Pages will also serve `viewer.html` at:

`https://gordojr1.github.io/quantity-capital/research/claims_insider_20261007/viewer.html`

## What each file is

| File | What it is |
|---|---|
| `NOTE.md` | The research note. Plain English. Numbers come from the CSVs below. |
| `comparison.csv` | One row per company per province, plus a `province=all` total. New snapshot vs OLD A holder counts. OLD B title-level `added`/`dropped` where coverage exists. 11,098 rows. |
| `comparison.json` | Same rows as `comparison.csv`, columnar JSON for the viewer. |
| `viewer.html` | Standalone browser for the comparison. No site shell. No CDN. |
| `new-to-data.csv` | Unlinked holders that appear in the new snapshot only (39 names). No linked company is new. |
| `missing-from-data.csv` | Unlinked holders that appear in OLD A only (40 names). No linked company is missing. |
| `clusters.csv` | Claim clusters under the main rule (10+ new claims and 2x the company mean). 545 rows. |
| `rate-by-company.csv` | Buy-day rate test, one row per company per buy kind. |
| `rate-summary.csv` | Rolled-up rate test (all / open market / placements). |
| `rate-robustness.csv` | Drop top 1 and top 3 companies. |
| `buy-events.csv` | Claim-linked insider buy events with 30/90/180-day returns. 1,348 rows. |
| `forward-summary.csv` | Median returns for after-cluster vs other vs no-claims. |
| `forward-by-company.csv` | One median per company, then the median of those. |
| `sensitivity.csv` | 10+/2x, 25+/2x, and 50+/3x cluster rules. |
| `juniors.csv` | Juniors within 5 km of a major, all six provinces. One row per junior × province × major. 181 rows. |
| `sanity.json` | Row-count checks vs `counts.json` and OLD A. |
| `lib.py` | Pure helpers (change type, distance, cluster rule). |
| `run_research.py` | Rebuilds the CSVs and JSON from the pinned inputs. |
| `test_claims_insider.py` | Tiny pytest fixtures for comparison math, change type, and distance. |

## Inputs (pinned; do not rediscover)

New snapshot (six provinces), ingested 2026-10-02 about 5:10–5:14 PM ET:

- `C:\Users\gordo\Desktop\Groks folder\claims_snapshots\claims-20261002.sqlite` table `titles`, 898,104 rows. Ontario 404,707; Quebec 253,955; Yukon 168,961; Nunavut 34,558; British Columbia 31,219; Newfoundland 4,704.
- `C:\Users\gordo\Desktop\Groks folder\claims_snapshots\claims-20261002.holders.json` (2026-10-02T21:13:56Z; 898,104 titles; 5,840 holder rows).
- `C:\Users\gordo\Desktop\Groks folder\claims_snapshots\claims-20261002.counts.json`

OLD A, holder level, all six provinces:

- Repo file `claims/links/holders.json` at commit `397823c4bf` (2026-09-24T19:24:25Z; 898,668 titles; 5,842 rows). Ontario 403,782; Quebec 255,285; Yukon 168,961; Nunavut 34,551; British Columbia 31,395; Newfoundland 4,694.

OLD B, title level, partial:

- `C:\Users\gordo\Desktop\Groks folder\qc.sqlite` table `claim_titles`, 475,727 rows (Ontario 430,557; Quebec 35,077; British Columbia 10,093). Loaded 2026-09-16..20. Opened read-only (`mode=ro`). Never written.

Insider and company data, same `qc.sqlite`:

- `insider_trades` 21,895 rows; purchases 6,778; last `trade_date` 2026-10-07.
- `companies`, `company_tickers` (`is_primary`), `mines` (182 mines).

Prices: repo `prices/*.json` at clone commit `e22911357e`.

Scripts open sqlite with URI `mode=ro` plus `PRAGMA query_only=ON`. They never write `qc.sqlite` or the snapshot.

## Rules

**Cluster (main):** a month with at least 10 new claims and at least 2x that company's mean monthly count (Jul 2023 – Sep 2026, zeros included). Cluster date = last issue date in that month. After-cluster window = 90 days. A buy counts only if the cluster date is on or before the trade date.

**Junior / major / distance (for `juniors.csv`):**

- Major = `company_type` Producer, Major or Producer, Mid-tier, or a company in `mines`.
- Junior = Explorer (any), Developer (any), Producer, Junior, Land Banks.
- Out = Royalty and Other. A mine owner is a major even if typed junior.
- Distance = nearest edge-to-edge between claim boxes (`minx,miny,maxx,maxy`) in the same province, in km. Lon degrees scale by cos(latitude). Lat degrees use 110.574 km.
- Keep rows with min distance ≤ 5 km. Also count that junior's claims within 2 km of that major.
- Grain = one row per junior × province × major.

**Comparison `change_type`:** `new_to_data` (old 0, new > 0), `missing_from_data` (old > 0, new 0), `added` (both > 0 and new > old), `dropped` (both > 0 and new < old), `unchanged`.

## How to re-run

From the repo root of this clone:

```
py -3 -m pytest research/claims_insider_20261007/test_claims_insider.py scripts/test_claims_snapshot_diff.py -q
py -3 research/claims_insider_20261007/run_research.py
```

`run_research.py` rebuilds the CSVs, `comparison.json`, and `sanity.json`. It does not overwrite `NOTE.md` or `viewer.html`. After a re-run, check every number in `NOTE.md` against the new CSVs.

Needs the pinned snapshot paths on this machine, `qc.sqlite`, and git access to `397823c4bf:claims/links/holders.json`. No network crawl. Do not run `claims_db.py`, `claims_monthly.py`, or `publish.py`.

## How to open the viewer

From the repo root:

```
py -3 -m http.server 8000
```

Then open:

`http://localhost:8000/research/claims_insider_20261007/viewer.html`

Filters: province, company search, change type, minimum |change|, linked vs raw holders. Sort by clicking column headers (change and change % work both ways). Toggle flat / group by company / group by province. The status line shows how many rows are on screen.

`file://` will fail to fetch `comparison.json` in most browsers. Use the local server.
