# Mining claims vs insider buys — research note (2026-10-07)

Not investment advice. Claims data is provincial viewing data, not legal title.

## Bottom line

1. Insiders did not buy more often after a claims cluster. After-cluster buy-days were 0.90 times the company's own baseline (128 vs 142.4 expected). The sample is 580 buy-days in 161 companies. 24 companies sat above their baseline. 37 sat below.
2. Buys after a cluster did not earn more. At 90 days the median beat SPY by 1.7 points (86 events, 30 companies). Other buys in claim-holding companies beat SPY by 10.4 points (1,094 events, 172 companies; p = 0.08).
3. 86 junior companies have a claim within 5 km of a major in the same province. The list has 181 junior-province-major rows. 144 of those rows also have a claim within 2 km.

A cluster is a month with a large batch of new claims. The rule is 10 or more new claims, and at least twice that company's own monthly mean.

## Data

| Item | Source |
|---|---|
| New claims snapshot | `claims_snapshots/claims-20261002.sqlite` table `titles`, 898,104 rows. Ingested 2026-10-02 about 5:10–5:14 PM ET. Six provinces. |
| New holder links | `claims_snapshots/claims-20261002.holders.json` (2026-10-02T21:13:56Z; 5,840 holder rows). |
| Old holder counts (OLD A) | `claims/links/holders.json` at commit `397823c4bf` (2026-09-24T19:24:25Z; 898,668 titles; 5,842 rows). |
| Old titles, partial (OLD B) | `qc.sqlite` table `claim_titles`, 475,727 rows. Ontario 430,557. Quebec 35,077 (extracts plus neighbors). British Columbia 10,093 (capped). Loaded 2026-09-16..20. |
| Companies, tickers, insider trades, mines | Same `qc.sqlite`, opened read-only. 21,895 insider rows. 6,778 purchases. Last trade date 2026-10-07. |
| Prices | Repo `prices/*.json` at commit `e22911357e` (clone of origin/main, 2026-10-07). |
| SPY | `prices/SPY.json`. |
| Gold miners stand-in | Equal-weight basket of the 33 gold producers in the claims catalog. No GDX or XGD file exists. |

OLD A and the new snapshot share the same holder-count shape. Company-by-company counts are direct for all six provinces.

OLD B has title ids and some area. Use it only where it covers the land. Ontario is full. Quebec and British Columbia are partial.

Yukon, Nunavut, and Newfoundland have no title-level old snapshot. A change there versus OLD B is coverage, not staking.

Quebec versus OLD B is the same trap. OLD B has 35,077 Quebec rows. The new snapshot has 253,955.

**Province title counts (OLD A vs new snapshot)**

| Province | OLD A titles | New titles | New minus OLD A |
|---|---:|---:|---:|
| ontario | 403,782 | 404,707 | +925 |
| quebec | 255,285 | 253,955 | -1,330 |
| british-columbia | 31,395 | 31,219 | -176 |
| yukon | 168,961 | 168,961 | +0 |
| nunavut | 34,551 | 34,558 | +7 |
| newfoundland | 4,694 | 4,704 | +10 |
| total | 898,668 | 898,104 | -564 |

## Method

1. **Company.** Use the holders.json company link when it exists. Else use the raw holder name. The `linked` flag records this.
2. **Claims cluster.** A month with at least 10 new claims, and at least 2x that company's mean monthly count. Months run Jul 2023 – Sep 2026. Months with zero claims stay in the mean. The cluster date is the last issue date in that month. Extra checks use 25+ at 2x, and 50+ at 3x.
3. **Issue dates.** Take them from the new snapshot for all six provinces. Drop Ontario 2018-04-10 conversion dates. Drop non-ISO dates, years before 1990, and dates after 2026-10-02.
4. **After-cluster window.** Count 90 days after the cluster date. A buy is after a cluster only if the cluster date is on or before the trade date. No lookahead.
5. **Insider buys.** Keep `side = purchase` from SEDI and Form 4. Open market means SEDI code 10 or Form 4 P. Placement means SEDI codes 11, 15, and 16.
6. **Rate test.** Use 1 Apr 2025 – 7 Oct 2026. Insider data is thin before Apr 2025. The unit is a buy-day (company plus date). Expected counts use each company's own share of days inside windows. The p-value uses 20,000 simulations under “no link”.
7. **Forward returns.** Use one event per company per filing date. Keep trades from 1 Apr 2025. Keep filings within 30 days of the trade. Enter at the first close on or after the filing date. Skip the event if that close is more than 5 days later. Horizons are 30, 90, and 180 calendar days.
8. **Eight-day window.** OLD A is 24 Sep 2026. The new snapshot is 2 Oct 2026. That gap is 8 days. It is too short for returns. It is context only.
9. **Juniors next to majors.** Major means type Producer, Major or Producer, Mid-tier, or a company in the mines table. Junior means Explorer (any subtype), Developer (any subtype), Producer, Junior, or Land Banks. Drop Royalty and Other. A mine owner counts as a major even if typed as a junior. Distance is nearest edge-to-edge between claim boxes in the same province, in km. Keep a row when min distance is 5 km or less. Also count that junior's claims within 2 km of that major. Grain is one row per junior × province × major.

## Part 1 — Company-by-company claims (new snapshot vs OLD A)

`comparison.csv` has 11,098 rows. That is one row per company per province, plus a total row.

Linked public companies: 562. Unlinked holders treated as their own name: 4,916.

No linked company is new to the data. No linked company is missing from the data. The 562 linked names persist from OLD A to the new snapshot.

Unlinked holders do churn. 39 unlinked names are new to the data. 40 unlinked names are missing. Some of that is a spelling change, not a new company. Example: “Lowel Schmidt” (40 old claims) vs “Lowell Schmidt” (143 new claims).

Much of any Quebec, Yukon, Nunavut, or Newfoundland change versus OLD B is coverage, not real staking. OLD B never held those full provinces at title level. Do not read title-level added or dropped there as new staking.

Holder-count changes versus OLD A are the fair six-province comparison.

**Largest linked-company count changes, 24 Sep to 2 Oct 2026 (8 days; context only)**

| Company | Ticker | Province | Old | New | Change |
|---|---|---|---:|---:|---:|
| Azimut Exploration | AZM.V | quebec | 9,813 | 9,711 | -102 |
| First Quantum Minerals | FM.TO | ontario | 443 | 493 | +50 |
| Freeport-McMoRan | FCX | british-columbia | 0 | 33 | +33 |
| Metals Creek Resources Corp. | MEK.V | ontario | 286 | 255 | -31 |
| Visible Gold Mines Inc. | VGD.V | quebec | 844 | 870 | +26 |
| Midland Exploration | MD.V | quebec | 10,076 | 10,058 | -18 |
| VR Resources | VRR.V | ontario | 83 | 65 | -18 |
| Electric Royalties | ELEC.V | ontario | 17 | 5 | -12 |
| Agnico Eagle Mines | AEM | ontario | 9,219 | 9,230 | +11 |
| Gold Fields | GFI | quebec | 4,385 | 4,374 | -11 |
| Rush Rare Metals Corp. | RSH.CN | quebec | 134 | 123 | -11 |
| Strategic Metals | SMD.V | british-columbia | 0 | 11 | +11 |

Eight days is too short to study stock returns after these changes. A new province row with old_claims = 0 can also be a new holder link, not new stakes. The cluster study below uses issue dates across years, not this 8-day gap.

## Part 2a — Do insider buys rise after a claims cluster?

Main rule (10+ claims, 2x mean): 545 clusters across 218 companies.

Issue dates kept for clustering: 738,486 titles. Dropped dates: 159,618. Those drops are conversion dates, junk dates, or dates after the snapshot.

Rate test on 161 companies with a window inside 1 Apr 2025 – 7 Oct 2026:

| Buys | Buy-days | After-cluster, observed | Expected | Ratio | p (one-sided) | Companies higher / lower |
|---|---:|---:|---:|---:|---:|---|
| All | 580 | 128 | 142.4 | 0.90 | 0.9341 | 24 / 37 |
| Open market | 510 | 114 | 124.8 | 0.91 | 0.8938 | 19 / 34 |
| Placement | 75 | 14 | 18.8 | 0.74 | 0.9346 | 9 / 31 |

- Three companies still buy more after clusters. Azimut Exploration: 23 vs 15.9 expected. Delta Resources: 17 vs 10.5. Renegade Gold: 11 vs 5.2.
- Without those three, the ratio falls to 0.69.
- Sign test on companies with buys: 24 higher vs 37 lower (p = 0.124).
- Placements are few (75 buy-days). Do not lean on that split.

The 2 Oct 2026 draft on a thinner extract found a 1.16x lift. This six-province snapshot does not repeat that lift.

## Part 2b — Forward returns of insider buys (claim-holding companies)

Medians are in percent. Hit means the share with a return above 0. Beat means the share above the benchmark.

| Group | Days | Events | Cos | Median return | Hit | Median vs SPY | Beat SPY | Median vs basket | Beat basket |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| After cluster | 30 | 101 | 31 | 2.1 | 50.5 | 1.2 | 50.5 | -0.9 | 47.5 |
| After cluster | 90 | 86 | 30 | 11.4 | 60.5 | 1.7 | 51.2 | -3.6 | 45.3 |
| After cluster | 180 | 60 | 23 | 7.7 | 56.7 | -3.8 | 48.3 | -4.5 | 40.0 |
| Other buys | 30 | 1,188 | 174 | 4.2 | 56.7 | 1.9 | 55.8 | -0.4 | 48.6 |
| Other buys | 90 | 1,094 | 172 | 16.7 | 66.6 | 10.4 | 63.3 | 1.3 | 52.1 |
| Other buys | 180 | 887 | 162 | 33.3 | 74.0 | 23.8 | 68.9 | 4.9 | 55.7 |
| No claims (context) | 30 | 2,235 | 310 | 4.0 | 58.5 | 2.1 | 54.6 | 0.7 | 51.9 |
| No claims (context) | 90 | 2,047 | 306 | 10.0 | 63.7 | 4.6 | 55.7 | -2.2 | 46.8 |
| No claims (context) | 180 | 1,590 | 284 | 19.7 | 67.3 | 8.2 | 58.4 | -2.5 | 47.7 |

- After-cluster vs other, excess over SPY (rank test p): 30 days 0.52, 90 days 0.08, 180 days 0.0095.
- At 180 days, after-cluster buys did worse (median vs SPY −3.8 vs +23.8 points).
- The after-cluster sample is small: 101 events in 31 companies at 30 days. It falls to 60 events in 23 companies at 180 days.
- One median per company gives the same story at 90 days: +9.6 vs +11.8 points over SPY (30 vs 172 companies).
- Open-market-only rows are in `forward-summary.csv`.

## Sensitivity — cluster size

| Rule | Clusters (cos) | Buy-rate ratio | Without top 3 | Cos higher/lower (p) | 90d vs SPY: after / other (p) | 180d vs SPY: after / other (p) |
|---|---|---:|---:|---|---|---|
| 10 claims, 2x mean (main) | 545 (218) | 0.90 | 0.69 | 24/37 (0.124) | 1.7 / 10.4 (0.08) | −3.8 / 23.8 (0.0095) |
| 25 claims, 2x mean | 370 (161) | 0.99 | 0.74 | 19/30 (0.152) | −1.7 / 10.7 (0.0032) | −13.5 / 24.2 (0.0002) |
| 50 claims, 3x mean | 234 (117) | 1.11 | 0.85 | 15/23 (0.256) | −6.3 / 10.7 (0.0016) | −13.5 / 24.2 (0.0002) |

Bigger batches show a slightly higher buy-rate ratio. The after-cluster returns then look worse. The 50+ sample is 62 events at 90 days and 43 events at 180 days.

These p-values treat each event as independent. They are not. Events bunch by company and by date. Read them as weak.

## Part 3 — Juniors next to majors (all six provinces)

Final rule used:

1. Major: `companies.company_type` is Producer, Major or Producer, Mid-tier, or the company owns a mine in `mines`.
2. Junior: Explorer (any subtype), Developer (any subtype), Producer, Junior, or Land Banks.
3. Out: Royalty and Other. A mine owner is a major even if typed as a junior or land bank.
4. Distance: nearest edge-to-edge between any junior claim box and any major claim box in the same province, in km.
5. Boxes are `minx, miny, maxx, maxy`. Lon degrees scale by cos(latitude). Lat degrees use 110.574 km.
6. Keep a row when min distance is 5 km or less. Also count that junior's claims within 2 km of that major.
7. Grain: one row per junior × province × major (not nearest-only).
8. A distance of 0 km means the boxes touch or overlap.

Result: 181 rows. 86 juniors. 28 majors. 6 provinces. 144 rows have at least one junior claim within 2 km. 71 juniors in the list have at least one insider buy-day since 1 Apr 2025.

Wesdome is typed Producer, Junior. It still counts as a major here because it is in the mines table.

**Closest 15 junior-major pairs (0 km means boxes overlap)**

| Junior | Ticker | Province | Major | Ticker | km | Claims ≤5 km | Claims ≤2 km | Buy-days |
|---|---|---|---|---|---:|---:|---:|---:|
| Abitibi Metals | AMQ.CN | ontario | Hemlo Mining Corp. | HMMC.V | 0.0 | 176 | 164 | 11 |
| Abitibi Metals | AMQ.CN | quebec | Glencore | GLNCY | 0.0 | 110 | 36 | 11 |
| Abitibi Metals | AMQ.CN | quebec | Gold Fields | GFI | 0.0 | 92 | 80 | 11 |
| Adamera Minerals | ADZ.V | british-columbia | Barrick Mining | B | 0.0 | 1 | 1 | 4 |
| Amex Exploration | AMX.V | ontario | Agnico Eagle Mines | AEM | 0.0 | 276 | 91 | 4 |
| Amex Exploration | AMX.V | quebec | Agnico Eagle Mines | AEM | 0.0 | 73 | 47 | 4 |
| Amex Exploration | AMX.V | quebec | Gold Fields | GFI | 0.0 | 43 | 19 | 4 |
| Apollo Silver | APGO.V | ontario | Agnico Eagle Mines | AEM | 0.0 | 94 | 94 | 30 |
| Apollo Silver | APGO.V | quebec | Agnico Eagle Mines | AEM | 0.0 | 28 | 24 | 30 |
| ATHA Energy Corp. | SASK.V | nunavut | Cameco | CCO.TO | 0.0 | 305 | 240 | 2 |
| ATHA Energy Corp. | SASK.V | nunavut | Rio Tinto | RIO | 0.0 | 28 | 21 | 2 |
| Aurelius Minerals | AUL.V | ontario | Agnico Eagle Mines | AEM | 0.0 | 68 | 68 | 0 |
| Azimut Exploration | AZM.V | quebec | Rio Tinto | RIO | 0.0 | 100 | 36 | 27 |
| Azimut Exploration | AZM.V | quebec | The Mosaic Company | MOS | 0.0 | 188 | 65 | 27 |
| Banyan Gold | BYN.V | yukon | Hecla Mining | HL | 0.0 | 554 | 298 | 0 |

Full list: `juniors.csv`. This is not the old Quebec ~2 km neighbor extract. It is box distance on the new snapshot.

## Limits

- **Not investment advice.** This is a research note on public filings and provincial viewing data.
- **No GDX or XGD file.** The producer basket is a stand-in. It is equal-weight and mixed currency.
- **Short insider history.** Dense SEDI data starts in Apr 2025. Form 4 covers few of these names.
- **One live snapshot.** Lapsed claims are gone, so older months look quieter than they were. Clusters lean toward 2025–2026.
- **Issue date is not a staking tape.** The snapshot has no transfer or lapse history.
- **Ontario conversion.** 127,149 titles carry issue date 2018-04-10. Those dates are not staking. They are dropped from clusters.
- **Yukon and Nunavut dates.** Some issue_date values are not ISO dates. They are dropped.
- **OLD B is partial.** Quebec is producer extracts plus neighbors. British Columbia is capped. Title-level added and dropped outside that coverage are blank.
- **Holder links are partial.** About 48% of new titles link to a public company. Unlinked holders are private or unmatched.
- **Eight-day window.** 24 Sep to 2 Oct 2026 is too short for returns.
- **Currency.** Many stock returns are CAD. SPY is USD. Excess returns include CAD/USD moves.
- **Price rounding.** Penny stocks round to 2 decimals, so their returns are coarse.
- **Placements are not open-market buys.** SEDI purchase includes private placements. They often fund staking. They are split out.
- **Overlap.** One buy can follow more than one cluster. One claim with two holders counts for both.
- **Weak p-values.** They assume independent events. Events bunch by company and by date.
- **Distance is box-to-box, not polygon-to-polygon.** A box can be larger than the claim. 5 km is approximate.
- **Mines table is the catalog, not a live operating flag.** A company in `mines` is treated as a major.

## Files

All in `research/claims_insider_20261007/`:

- `comparison.csv` / `comparison.json` — company × province vs OLD A, with OLD B title-level added/dropped where coverage exists
- `new-to-data.csv` / `missing-from-data.csv` — unlinked holders that appear or vanish (no linked company does)
- `clusters.csv` — every cluster under the main rule
- `rate-by-company.csv`, `rate-summary.csv`, `rate-robustness.csv` — Part 2a
- `buy-events.csv` — claim-linked buy events with returns
- `forward-summary.csv`, `forward-by-company.csv` — Part 2b
- `sensitivity.csv` — cluster-size checks
- `juniors.csv` — juniors within 5 km of a major, all six provinces
- `sanity.json` — row-count checks
- `viewer.html` — browse the comparison (local server; not in the site nav)
- `run_research.py`, `lib.py`, `test_claims_insider.py` — code and tests
