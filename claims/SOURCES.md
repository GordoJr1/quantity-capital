# Claims map sources

Committed GeoJSON extracts for the [claims map](../claims.html). **Not legal title.** Confirm on the provincial registry before relying on a cell.

Rebuild Ontario/BC extracts with `python3 build_on_bc_extracts.py` (stdlib only; hits the official REST endpoints). Quebec GESTIM extracts are the existing `claims/<id>.geojson` files.

The map’s default “all companies” view loads `claims/overview.geojson` (not the per-company extracts). After adding or refreshing extracts, regenerate it:

```
python3 scripts/build_claims_overview.py
```

That walk is stdlib-only, reads committed `claims/*.geojson` + `companies.json`, and writes claim-block footprints (merged ~0.02° title cells) plus `claims/extract-bytes.json` (per-company extract sizes for nearby loading). One-off / publish hook — not part of morning bats or `daily-update`. Those footprints are the default all-companies view only; a searched holder paints real claim polygons.

## Quebec — GESTIM

- Product: active mineral titles (GESTIM).
- Files: `claims/{iamgold,agnico-eagle,alamos-gold,gold-fields,eldorado-gold,barrick,wesdome-gold-mines}.geojson`
- Neighbors: titles within ~2 km of the company cells (Quebec only).
- As of: catalog `as_of` on `companies.json`.

## Ontario — MLAS operational claims

- REST (GeoJSON, paginated): [MLAS MapServer layer 1 (HOLDER)](https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/MLAS/mlas_op/MapServer/1)
- Provincial note: unofficial viewing data, not legal title.
- Files: `claims/<id>-ontario.geojson` (featured producers include neighboring MLAS titles within ~3 km; other companies are tenure-only).
- Holder match (`UPPER(HOLDER) LIKE`):

| Company | Needles | Titles (2026-09-17) |
| --- | --- | ---: |
| IAMGOLD | IAMGOLD | 3550 |
| Agnico Eagle | AGNICO | 9227 |
| Alamos Gold | ALAMOS | 5115 |
| Wesdome | WESDOME | 2270 |
| Evolution | EVOLUTION | 3691 |
| Equinox Gold | GREENSTONE, MUSSELWHITE | 2714 |

`area_ha` is not mapped from this layer.

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
- **Ontario neighbors** around featured producers (IAMGOLD, Agnico, Alamos, Wesdome, Evolution, Equinox) are MLAS titles within ~3 km of that company’s cells. Rebuild with `python3 build_on_bc_extracts.py --neighbors-only`.
- **BC neighbors** are not drawn (payload).
- **Nationwide shapefiles** are not in git. Live ArcGIS from the browser is not used (static Pages + CORS).
- Other Beta producers with no QC/ON/BC titles stay at zero until a holder match exists.
