# Claims map sources

Committed GeoJSON extracts for the [claims map](../claims.html). **Not legal title.** Confirm on the provincial registry before relying on a cell.

Rebuild Ontario/BC extracts with `python3 build_on_bc_extracts.py` (stdlib only; hits the official REST endpoints). Quebec GESTIM extracts are the existing `claims/<id>.geojson` files.

## Quebec — GESTIM

- Product: active mineral titles (GESTIM).
- Files: `claims/{iamgold,agnico-eagle,alamos-gold,gold-fields,eldorado-gold,barrick,wesdome-gold-mines}.geojson`
- Neighbors: titles within ~2 km of the company cells (Quebec only).
- As of: catalog `as_of` on `companies.json`.

## Ontario — MLAS operational claims

- REST (GeoJSON, paginated): [MLAS MapServer layer 1 (HOLDER)](https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/MLAS/mlas_op/MapServer/1)
- Provincial note: unofficial viewing data, not legal title.
- Files: `claims/<id>-ontario.geojson` (company focus tenures plus neighboring MLAS titles in a ~3 km pad, `role=neighbor`).
- Holder match (`UPPER(HOLDER) LIKE`):

| Company | Needles | Titles (2026-09-17) |
| --- | --- | ---: |
| IAMGOLD | IAMGOLD | 3550 |
| Agnico Eagle | AGNICO | 9227 |
| Alamos Gold | ALAMOS | 5115 |
| Wesdome | WESDOME | 2270 |
| Evolution | EVOLUTION | 3691 |
| Equinox Gold | GREENSTONE, MUSSELWHITE | 2714 |
| Vale | VALE CANADA LIMITED | 189 (2026-09-23) |

`area_ha` is not mapped from this layer.

Vale holder strings are Jev-gated (`--sweep-holders --judge-holders`): the
distinct MLAS HOLDER values matching `%VALE%` are swept attributes-only, then
TypeSafe Jev (Noul: is this holder Vale Canada Limited) approves each string
once. Live sweep 2026-09-23: `(100) VALE CANADA LIMITED VALE CANADA LIMITEE`
(98 titles, noul 0.88) and the 50/50
`(50) GLENCORE CANADA CORPORATION, (50) VALE CANADA LIMITED VALE CANADA LIMITEE`
(91 titles, noul 0.86, include-as-focus). Judgments cache to
`claims/.cache/mlas/vale-holder-judgments.json` (gitignored) so reruns spend
no tokens; Jev never invents tenures.

## Ontario — full MLAS set (cache + holder index)

- Build: `python3 build_on_bc_extracts.py --full-ontario` (stdlib only; OBJECTID
  keyset resumption). The full download lives in the gitignored cache
  `claims/.cache/mlas/tiles/` (41 tiles, `full-progress.json`); a rerun with a
  complete cache downloads nothing. Tiles are never committed.
- Committed: `claims/mlas/holders.json` — holder, tenure count, and bounding
  box for every holder, built offline with
  `python3 build_on_bc_extracts.py --holders-index` (no network).
  No second UI reads it — it is the small committed index behind the
  per-company extracts.
- Full set 2026-09-23: **403,789** titles, **1,382** holders, tiles **244.6 MB**
  in cache (layer `count` agrees: 403789). Vale-holder titles: 189, matching
  `claims/vale-ontario.geojson` focus.

## British Columbia — MTA tenure

- REST (GeoJSON, paginated): [Mineral, Placer and Coal Tenure Spatial View, MapServer/36](https://delivery.maps.gov.bc.ca/arcgis/rest/services/whse/bcgw_pub_whse_mineral_tenure/MapServer/36)
- Licence: Open Government Licence – British Columbia.
- Files: `claims/<id>-bc.geojson` (company tenures only; no neighbors).
- Owner match (`UPPER(OWNER_NAME) LIKE`):

| Company | Needles | Titles (2026-09-17) |
| --- | --- | ---: |
| Newmont | NEWMONT, PRETIUM | 34 |
| Centerra | THOMPSON CREEK, CENTERRA | 112 |
| Artemis Gold | BW GOLD, ARTEMIS | 7 |

## Gaps

- **Kinross Great Bear** (Ontario): no extract. Holder string did not match the ON needle set.
- **Equinox**: matched via Greenstone / Musselwhite, not the word EQUINOX.
- **Centerra / Artemis**: matched operating subsidiaries (Thompson Creek, BW Gold), not only the parent name.
- **Newmont Quebec**: Éléonore was sold; no GESTIM extract. Red Chris / Brucejack / Galore Creek are BC only.
- **Ontario/BC neighbors** are not drawn (payload).
- **Nationwide shapefiles** are not in git. Live ArcGIS from the browser is not used (static Pages + CORS).
- Other Beta producers with no QC/ON/BC titles stay at zero until a holder match exists.

## Provincial claims database

The Claims nav stays on `claims.html`. Quebec and British Columbia checkboxes draw `claims/tiles/qc.pmtiles.png` and `claims/tiles/bc.pmtiles.png`. Ontario stays on the company extracts. Yukon, Nunavut, and Newfoundland and Labrador use the same tile checkboxes. `claims.html?company=` still loads the company extract. The `.png` suffix keeps GitHub Pages from gzip-slicing Range requests. `claims-db.html` remains the full-registry viewer.

Monthly, from the Windows desktop:

```
py -3 scripts/claims_db.py --all --publish
```

That ingests Quebec, British Columbia, Ontario, Yukon, Newfoundland and Labrador, and Nunavut into gitignored `claims/.db/claims.sqlite`, links holders, and commits one PMTiles file per province under `claims/tiles/` plus the search, link, and mine-radius JSON. A province that would pass 100 MiB is split into `qc.pmtiles.png` and `qc-2.pmtiles.png` (same for BC). Quebec is the GESTIM weekly active-titles shapefile (`TITRES_ACTIFS_ACTIVE_TITLES.zip` on Données Québec / MRNF); only `STI_CODE` A is kept, with the holder from `DET_NOM`. British Columbia is the DataBC WFS layer `WHSE_MINERAL_TENURE.MTA_ACQUIRED_TENURE_SVW` (not the 10,000-row MapServer), paged with `startIndex` and `sortBy=OBJECTID`, keeping mineral claims and leases and `OWNER_NAME`. `python3` on Windows is the Store stub; use `py -3` or `python`. Tiles use `tippecanoe` on PATH, then `wsl tippecanoe` (paths via `wslpath`). The build stops if neither is available. Draft viewer: `claims-db.html`.
