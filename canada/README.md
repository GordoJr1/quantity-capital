# Canadian commodity production

Static book for `canada.html` (beta → **Canada**). National aggregates plus Map 900A principal mines. Mine-level tonnes are confidential and are never stored.

## Monthly refresh

Not a morning/evening tape bat. Not `daily-update`. Run on the desktop or a Cloud Agent when NRCan/StatCan publish a new annual year (typically late February) or after a Map 900A roll.

```
python3 scripts/build_canada_commodities.py
python3 scripts/build_canada_commodities.py --sqlite /path/to/qc.sqlite
python3 scripts/build_canada_commodities.py --jev
python3 scripts/build_canada_commodities.py --offline
python3 scripts/build_canada_commodities.py --check
```

`--sqlite` is optional (`qc.sqlite` is gitignored). `--jev` is optional TypeSafe/Jev owner linking (one shot, cached). Without a key the deterministic linker still writes claims deep links — CI does not need secrets.

TypeSafe key locations (never commit or paste the key):

- Environment: `TYPESAFE_API_KEY`
- Desktop: `C:\Users\gordo\.grok\typesafe.env` (also `~/.grok/typesafe.env`)
- Box: `source /home/box/shared/typesafe/env`

Writes `canada/commodities.json`. User-Agent: `Quantity Capital gordojr@proton.me`. Sleeps 0.15s between NRCan year pages.

## Sources

- StatCan table 16-10-0022 quantities (Canada + provinces, 2019–present): https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1610002201
- CSV zip: https://www150.statcan.gc.ca/n1/tbl/csv/16100022-eng.zip
- NRCan annual HTML for earlier years in the 20-year window: https://mmsd.nrcan-rncan.gc.ca/prod-prod/ann-ann-eng.aspx?FileT=YYYY&Lang=en
- Map 900A REST (Metals=3, Nonmetals=4, Coal=5, Oil sands=6): https://maps-cartes.services.geo.ca/server_serveur/rest/services/NRCan/900A_and_top_100_en/MapServer

If a live source is blocked, the builder records a `blockers` entry and can finish from `scripts/fixtures/canada/` (`--offline`, or automatic fallback when both production sources fail). Refill on a desktop that can reach StatCan/NRCan.

## Claims links

Owners and mine names are matched to `claims/companies.json` (and beta issuer catalogs). Linked rows use the existing overview-first map:

`claims.html?company=<id>&asset=<mine-id>`

Unlinked Map 900A rows stay visible without a claims href. The Canada tab does not load claim polygons.
