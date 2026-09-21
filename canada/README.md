# Canadian commodity production

Static book for `canada.html` (beta → **Canada**). National aggregates plus Map 900A principal mines. Mine-level tonnes from StatCan/NRCan are confidential and are never stored. Optional **2025 production** is company-disclosed actuals on the matching `beta/<issuer>.json` producer page (Newmont shape). The Canada table joins that book. Blank / — when not disclosed.

## Monthly refresh

Not a morning/evening tape bat. Not `daily-update`. Run on the desktop or a Cloud Agent when NRCan/StatCan publish a new annual year (typically late February) or after a Map 900A roll.

```
python3 scripts/build_canada_commodities.py
python3 scripts/build_canada_commodities.py --sqlite /path/to/qc.sqlite
python3 scripts/build_canada_commodities.py --jev
python3 scripts/build_canada_commodities.py --offline
python3 scripts/build_canada_commodities.py --check
python3 scripts/ingest_canada_mine_production.py              # fetch IR / EDGAR; write beta pages + join
python3 scripts/ingest_canada_mine_production.py --apply      # also strip canada-only figure overlay
python3 scripts/ingest_canada_mine_production.py --apply-only # curated quotes, no fetch; write beta + join
python3 scripts/ingest_canada_mine_production.py --offline
python3 scripts/ingest_canada_mine_production.py --check
```

`--sqlite` is optional (`qc.sqlite` is gitignored). The monthly ingest is deterministic. Jev is only for leftover fuzzy owner→claims-company pairs, via a **one-shot** merge gate (not during every ingest, not in morning/evening bats):

```
python3 scripts/run_canada_commodities_gate.py --packet-only   # CI / Cloud Agent
# QC box, once:
set -a && source /home/box/shared/typesafe/env && set +a
python3 scripts/run_canada_commodities_gate.py --require-jev
```

Do not re-run `--require-jev` unless the packet calls change (no repeated safe_to_apply burns). Without a key the PR stays green.

TypeSafe key locations (never commit or paste the key):

- Environment: `TYPESAFE_API_KEY`
- Desktop: `C:\Users\gordo\.grok\typesafe.env` (also `~/.grok/typesafe.env`)
- Box: `source /home/box/shared/typesafe/env`

Writes `canada/commodities.json` (national + Map 900A roster). Mine-level 2025 ounces are written to `beta/<issuer>.json` and joined via `canada/producer-join.json`. User-Agent: `Quantity Capital gordojr@proton.me`. Sleeps 0.15s between NRCan year pages.

## Sources

- StatCan table 16-10-0022 quantities (Canada + provinces, 2019–present): https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1610002201
- CSV zip: https://www150.statcan.gc.ca/n1/tbl/csv/16100022-eng.zip
- NRCan annual HTML for earlier years in the 20-year window: https://mmsd.nrcan-rncan.gc.ca/prod-prod/ann-ann-eng.aspx?FileT=YYYY&Lang=en
- Map 900A REST (Metals=3, Nonmetals=4, Coal=5, Oil sands=6): https://maps-cartes.services.geo.ca/server_serveur/rest/services/NRCan/900A_and_top_100_en/MapServer

If a live source is blocked, the builder records a `blockers` entry and can finish from `scripts/fixtures/canada/` (`--offline`, or automatic fallback when both production sources fail). Refill on a desktop that can reach StatCan/NRCan.

## Troy ounces

Gold, silver, platinum, palladium, rhodium, and platinum-group national (and provincial) totals are converted at ingest to troy ounces. Factor: **1 troy ounce = 31.1034768 grams**. Each year uses that year’s published StatCan/NRCan mass unit (NRCan silver is tonnes through 2018, then kilograms; gold is kilograms; PGMs are kilograms on NRCan through 2018 and grams on StatCan). Other commodities keep their source units. `--convert-existing` applies the same conversion to an already-built `canada/commodities.json` without refetching Map 900A. Do not invent mine-level ounces.

## Mine-level 2025 production

`scripts/canada-mine-production-sources.json` is the curated issuer list (IR news, MD&A, AIF, annual, ops update, NI 43-101 **actuals**). The ingest fetches each URL (Quantity Capital User-Agent, 0.2s sleep) and keeps a figure only when the stored quote / `must_contain` strings appear on the page. Coverage is patchy on purpose.

**Source of truth is the beta producer page**, same shape as `beta/newmont.json`: `production[]` with `period: "2025"`, `kind: "annual"`, `attr_koz`, and `by_asset` per mine id, plus `assets[]`. Examples: Blackwater → Artemis Gold; Brucejack → Newmont; Copper Mountain → Hudbay; Dome Mountain → Blue Lagoon (asset only — no 2025 ounces disclosed); Elk → Gold Mountain (FY ≠ calendar 2025, no by_asset).

`canada/producer-join.json` maps Map 900A `mine_id` → `beta_id` / `asset_id`. `canada.html` reads the 2025 column from those producer files. Do not hand-maintain a parallel canada-only ounce store.

- Precious-metal **oz / ounces** in company reports are troy ounces (mining ounce = troy ounce). Beta pages keep each file's `units.gold` convention (**koz** on Newmont and most producers). The Canada table converts koz → troy oz (×1,000). kg uses `1 troy oz = 31.1034768 g`.
- Other commodities keep the source unit on by_asset (`copper_t`, `copper_mlb`, `silver_koz`) and the table labels it.
- Gold is blank when the report only gives AuEq / GEO, or only a complex total (Island Gold District, Canadian Malartic, Porcupine, Snow Lake, Timmins). Complex totals already on a producer page (e.g. Alamos `island-gold-district`) are not copied onto Map 900A pit rows.
- Existing filing-backed by_asset rows are not overwritten. Missing Canadian assets / 2025 slots are added.
- SEDAR+ is paywalled — do not point this job at it. Use the issuer IR page or an EDGAR exhibit.
- Jev is optional and only for leftover owner→claims matches / filing-text→mine; deterministic quotes first.
- Do not commit `qc.sqlite` or secrets. Claims crawls stay out of daily bats.

Typical blockers (also on source rows): paywalled SEDAR+, no mine breakout, pre-production / ramp-up without ounces, fiscal year ≠ calendar 2025, private operator, PGM concentrate without payable gold.

## Claims links

Owners and mine names are matched to `claims/companies.json` (and beta issuer catalogs). Linked rows use the existing overview-first map:

`claims.html?company=<id>&asset=<mine-id>`

Unlinked Map 900A rows stay visible without a claims href. The Canada tab does not load claim polygons.
