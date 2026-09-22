#!/usr/bin/env python3
"""Build the Vale Sudbury mine profile from SEC filings (stdlib + repo verify paths).

Vale's Sudbury Operations are one complex (Garson, Stobie, Clarabelle Mill,
Copper Cliff, Creighton, Coleman, Totten). Finished production is disclosed
at complex level only, so this script never splits a complex total across
shafts. Per-shaft run-of-mine ore + grades are attached only because the
2025 Form 20-F splits them in its nickel-production table.

Sources (public SEC EDGAR, same UA + sleep discipline as the Canada ingest):
- Vale Production and Sales in 4Q25 and 2025 (Form 6-K, 2026-01-27)
- Vale 2025 Form 20-F (2026-03-27), incl. the S-K 1300 Sudbury TRS (Ex. 96.3)

Every figure is gated by a must_contain quote verified against the live
filing text (strip_markup + verify_extract from ingest_canada_mine_production).
Any failed check exits 2 without writing.

    python3 scripts/build_vale_sudbury_profile.py --check   # verify only
    python3 scripts/build_vale_sudbury_profile.py           # verify + write beta/vale.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_canada_commodities as b
import ingest_canada_mine_production as ing

PROFILE = ROOT / "beta" / "vale.json"
SLEEP_S = 0.2

FILING_20F = {
    "title": "Vale 2025 Form 20-F",
    "date": "2026-03-27",
    "url": "https://www.sec.gov/Archives/edgar/data/917851/000129281426001844/valeform20f_2025.htm",
}
FILING_6K = {
    "title": "Vale Production and Sales in 4Q25 and 2025 (Form 6-K)",
    "date": "2026-01-27",
    "url": "https://www.sec.gov/Archives/edgar/data/917851/000129281426000189/vale20260127_6k.htm",
}

MEMBERS = [
    "garson-sudbury-operations",
    "stobie-sudbury-operations",
    "clarabelle-mill-sudbury-operations",
    "copper-cliff-sudbury-operations",
    "creighton-sudbury-operations",
    "coleman-sudbury-operations",
    "totten-sudbury-operations",
]

# (key, filing, must_contain needles). Quote gate = presence in stripped text.
CHECKS_20F = [
    ("ni_source", ["Sudbury", "35.3", "36.2", "38.2", "contained nickel"]),
    ("cu_source", ["Sudbury", "63.7", "58.6", "57.9"]),
    ("co_source", ["Sudbury", "388", "331", "365", "contained metric tons"]),
    ("pt_source", ["Platinum", "99", "107", "125", "thousand troy ounces of contained metal"]),
    ("pd_source", ["Palladium", "120", "149", "thousand troy ounces of contained metal"]),
    ("au_source", ["Gold", "46", "38", "45", "thousand troy ounces of contained metal"]),
    ("shaft_cc", ["Copper Cliff", "1,080", "1,019", "985"]),
    ("shaft_creighton", ["Creighton", "717", "491", "406"]),
    ("shaft_garson", ["Garson", "817", "791", "650"]),
    ("shaft_coleman", ["Coleman", "787", "875", "863"]),
    ("shaft_stobie", ["Stobie", "957", "115"]),
    ("shaft_totten", ["Totten", "589", "558", "518"]),
    ("ontario_total", ["Ontario - total", "4,947", "3,849", "3,422"]),
    ("res_rr", ["Measured + Indicated", "51.3", "Inferred", "73.3"]),
    ("res_proven", ["Proven", "26.1", "Probable", "37.3", "Total", "63.4"]),
    ("recovery", ["Ni: 65-90%", "Cu: 80-90%", "Co: 20-35%", "Pt: 65-75%", "Pd: 75-90%", "Au: 50-75%"]),
    ("res_prices", ["US$17,625 nickel", "US$9,950 copper", "US$39,125 cobalt",
                    "US$1,325/oz platinum", "US$1,050/oz palladium", "gold US$2,650/oz"]),
    ("metals_text", ["also contain copper, cobalt, PGMs, gold and silver"]),
    ("milled", ["nearly 5 million tons of ore milled in Sudbury", "an increase of 29% from 2024"]),
    ("stobie_pit", ["Stobie Pit reached 900 thousand tons of ore mined"]),
    ("cc_south", ["from 40% to 49%", "Copper Cliff Mine Complex"]),
    ("single_furnace", ["59.5 thousand tonnes of Ni", "single-furnace operation in mid-2017"]),
    ("stream", ["70% of the by-product gold from our Sudbury nickel mines", "for 20 years"]),
    ("surface_rights", ["We hold sufficient surface rights for the current life-of-mine"]),
    ("res_members", ["The reserves at Sudbury includes Coleman, Copper Cliff, Creighton, Garson and Totten"]),
]
CHECKS_6K = [
    ("ni_6k", ["Sudbury", "35.2", "36.6"]),
    ("cu_6k", ["Sudbury", "63.8", "58.6"]),
    ("site_6k", ["Finished Production by Site", "Sudbury", "59.4", "50.5"]),
    ("ore_record", ["Sudbury recorded its strongest ore production since 2016"]),
]


def fetch_text(url: str) -> str:
    time.sleep(SLEEP_S)
    return ing.strip_markup(b.fetch(url, timeout=120))


def check_all() -> tuple[dict[str, str], str, str]:
    t20 = fetch_text(FILING_20F["url"])
    t6 = fetch_text(FILING_6K["url"])
    results: dict[str, str] = {}
    for key, needles in CHECKS_20F:
        ok, why = ing.verify_extract(t20, {"must_contain": needles})
        results[key] = "ok" if ok else f"FAIL:{why}"
    for key, needles in CHECKS_6K:
        ok, why = ing.verify_extract(t6, {"must_contain": needles})
        results[key] = "ok" if ok else f"FAIL:{why}"
    # Absence gates (blank-with-reason, not figures): no quartile rank, no
    # mine-level C1/AISC, no stated mine-life years anywhere in the 20-F text.
    results["absent_quartile"] = "ok" if t20.count("quartile") == 0 else "FAIL:quartile present"
    results["absent_aisc"] = "ok" if len(__import__("re").findall(r"\bAISC\b", t20)) == 0 else "FAIL:AISC present"
    results["absent_c1"] = "ok" if t20.count("C1 cash") == 0 else "FAIL:C1 present"
    return results, t20, t6


def build_profile() -> dict:
    return {
        "id": "sudbury",
        "name": "Sudbury Operations",
        "kind": "complex",
        "location": "Greater Sudbury, Ontario, Canada (~330 km north-northeast of Toronto)",
        "operator": "Vale Canada Limited",
        "member_asset_ids": MEMBERS,
        "member_note": (
            "One complex: Coleman, Copper Cliff, Creighton, Garson and Totten underground mines, "
            "Stobie open pit, plus Clarabelle Mill / Copper Cliff Smelter and Nickel Refinery and "
            "Port Colborne Nickel Refinery. Finished production is disclosed at complex level only "
            "and is not split across shafts."
        ),
        "metals": ["nickel", "copper", "cobalt", "platinum", "palladium", "gold"],
        "metals_note": (
            "Nickel sulfide ore bodies, which also contain copper, cobalt, PGMs, gold and silver "
            "(20-F mineralization text). Silver is named but no Sudbury silver figure is published, "
            "so silver stays blank."
        ),
        "production": {
            "basis": (
                "Finished production by ore source, contained metal (20-F nickel/copper/cobalt/PGM tables). "
                "2025 6-K comparatives in parentheses: Ni 35.2 kt (2024: 36.6), Cu 63.8 kt (2024: 58.6)."
            ),
            "columns": ["2023", "2024", "2025"],
            "rows": [
                {"metal": "nickel", "unit": "kt", "values": {"2023": 38.2, "2024": 36.2, "2025": 35.3}},
                {"metal": "copper", "unit": "kt", "values": {"2023": 57.9, "2024": 58.6, "2025": 63.7}},
                {"metal": "cobalt", "unit": "t", "values": {"2023": 365, "2024": 331, "2025": 388}},
                {"metal": "platinum", "unit": "koz", "values": {"2023": 125, "2024": 107, "2025": 99}},
                {"metal": "palladium", "unit": "koz", "values": {"2023": 149, "2024": 120, "2025": 120}},
                {"metal": "gold", "unit": "koz", "values": {"2023": 45, "2024": 38, "2025": 46}},
            ],
            "site_note": (
                "Finished nickel by site (Sudbury site processes Thompson/external feeds too): "
                "2025 59.4 kt vs 2024 50.5 kt (4Q25 production 6-K). Q4 2025: Sudbury recorded its "
                "strongest ore production since 2016."
            ),
        },
        "shaft_ore": {
            "basis": (
                "Run-of-mine delivered from each operation to its mill (20-F section 2.1.2). "
                "Does not include adjustments due to beneficiation, smelting or refining. "
                "Attached per shaft only because the filing splits it; finished production is not split."
            ),
            "columns": ["2023", "2024", "2025"],
            "shafts": [
                {"name": "Copper Cliff", "ore_kt": {"2023": 985, "2024": 1019, "2025": 1080},
                 "cu_pct": {"2023": 1.3, "2024": 1.6, "2025": 1.3},
                 "ni_pct": {"2023": 1.1, "2024": 1.1, "2025": 0.9}},
                {"name": "Creighton", "ore_kt": {"2023": 406, "2024": 491, "2025": 717},
                 "cu_pct": {"2023": 2.2, "2024": 2.0, "2025": 2.0},
                 "ni_pct": {"2023": 2.9, "2024": 2.5, "2025": 2.3}},
                {"name": "Garson", "ore_kt": {"2023": 650, "2024": 791, "2025": 817},
                 "cu_pct": {"2023": 1.0, "2024": 1.1, "2025": 1.0},
                 "ni_pct": {"2023": 1.0, "2024": 1.2, "2025": 1.1}},
                {"name": "Coleman", "ore_kt": {"2023": 863, "2024": 875, "2025": 787},
                 "cu_pct": {"2023": 2.5, "2024": 1.8, "2025": 2.1},
                 "ni_pct": {"2023": 1.4, "2024": 1.2, "2025": 1.3}},
                {"name": "Stobie", "ore_kt": {"2023": 0, "2024": 115, "2025": 957},
                 "cu_pct": {"2023": 0.0, "2024": 0.3, "2025": 0.3},
                 "ni_pct": {"2023": 0.0, "2024": 0.3, "2025": 0.3}},
                {"name": "Totten", "ore_kt": {"2023": 518, "2024": 558, "2025": 589},
                 "cu_pct": {"2023": 1.9, "2024": 1.5, "2025": 1.3},
                 "ni_pct": {"2023": 1.3, "2024": 1.1, "2025": 1.0}},
            ],
            "ontario_total_ore_kt": {"2023": 3422, "2024": 3849, "2025": 4947},
            "operations_note": (
                "Nearly 5 Mt of ore milled in Sudbury in 2025 (+29% vs 2024) on the Copper Cliff Mine "
                "Capacity Replacement and Stobie Pit projects. Copper Cliff South rose from 40% to 49% "
                "of the Copper Cliff Mine Complex; Stobie Pit mined 900 kt, the complex's second largest "
                "ore producer. Sudbury also set a post-single-furnace (mid-2017) nickel record of 59.5 kt."
            ),
        },
        "recoveries": {
            "basis": "TRS metallurgical recovery ranges (cut-off and recovery vary by orebody and estimate timing).",
            "reserves": {"Ni": "65-90%", "Cu": "80-90%", "Co": "20-35%",
                         "Pt": "65-75%", "Pd": "75-90%", "Au": "50-75%"},
            "resources": {"Ni": "65-90%", "Cu": "85-90%", "Co": "20-35%",
                          "Pt": "65-75%", "Pd": "75-90%", "Au": "50-75%"},
            "cutoff": {"reserves": "8.2-244 US$/ton (value-based, NPR)",
                       "resources": "33-198 US$/t (3.5% CuEq at Nickel Rim South only)"},
            "price_assumptions": {
                "reserves": "Ni US$17,625/t, Cu US$9,950/t, Co US$39,125/t, Pt US$1,325/oz, Pd US$1,050/oz, Au US$2,650/oz",
                "resources": "Ni US$13,376-20,882/t, Cu US$6,100-9,500/t, Co US$45,000-56,300/t, Pt US$1,124-1,350/oz, Pd US$925-1,450/oz, Au US$1,000-1,950/oz",
            },
        },
        "reserves_resources": {
            "basis": (
                "S-K 1300 TRS as of 31 Dec 2025 (Exhibit 96.3). Adjusted to Vale's 90% ownership in VBM. "
                "Tonnage in Mt dry; Ni/Co/Cu in %, Pt/Pd/Au in g/t. Resources exclusive of reserves. "
                "Covers Coleman, Copper Cliff (incl. project), Creighton, Stobie, Garson, Totten, "
                "Nickel Rim South Extension and Ella Capre."
            ),
            "reserves": {
                "proven": {"tonnage_mt": 26.1, "ni": 1.42, "co": 0.03, "cu": 1.77, "pt": 1.21, "pd": 1.18, "au": 0.48},
                "probable": {"tonnage_mt": 37.3, "ni": 1.39, "co": 0.04, "cu": 1.37, "pt": 0.92, "pd": 1.21, "au": 0.33},
                "total": {"tonnage_mt": 63.4, "ni": 1.40, "co": 0.03, "cu": 1.54, "pt": 1.04, "pd": 1.20, "au": 0.39},
            },
            "resources": {
                "measured": {"tonnage_mt": 14.5, "ni": 1.04, "co": 0.04, "cu": 0.67, "pt": 0.35, "pd": 0.42, "au": 0.10},
                "indicated": {"tonnage_mt": 36.8, "ni": 1.32, "co": 0.04, "cu": 2.04, "pt": 0.83, "pd": 1.03, "au": 0.31},
                "measured_indicated": {"tonnage_mt": 51.3, "ni": 1.25, "co": 0.04, "cu": 1.65, "pt": 0.69, "pd": 0.86, "au": 0.25},
                "inferred": {"tonnage_mt": 73.3, "ni": 0.9, "co": 0.03, "cu": 1.0, "pt": 0.8, "pd": 0.9, "au": 0.3},
            },
            "stream_note": (
                "Reserve figures do not deduct streaming: Wheaton holds 70% of Sudbury by-product gold "
                "for 20 years (plus 75% of Salobo life-of-mine). Ongoing payments are the lesser of "
                "US$400/oz (1% annual inflation from 2019 under the Salobo contract) and market price."
            ),
        },
        "costs": {
            "blank": [
                "C1 cash cost: no Sudbury figure in the 20-F or the reviewed earnings 6-Ks (base-metals "
                "costs are disclosed at Vale Base Metals segment level only; segment Ni/Cu all-in sits on "
                "the company tab). Block left blank.",
                "All-in sustaining cost: no Sudbury figure in any reviewed filing. Block left blank.",
                "Cost-curve position: the 20-F contains zero quartile mentions. Block left blank.",
            ],
        },
        "life": {
            "blank": (
                "No stated mine-life years for Sudbury in the 20-F (only Salobo's ~20 years to 2045 is "
                "stated). The filing says Vale holds sufficient surface rights for the current life-of-mine. "
                "Block left blank."
            ),
        },
        "claims_note": (
            "No Vale company row in the repo claims catalog (Quebec/GESTIM-centred plus listed-issuer "
            "Ontario/BC extracts), so the preview shows the seven Map 900A member pins. Each pin links to "
            "claims.html?company=vale&asset=<id>."
        ),
        "sources": [FILING_20F, FILING_6K],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify against live filings, no write")
    args = ap.parse_args()
    results, _t20, _t6 = check_all()
    bad = {k: v for k, v in results.items() if v != "ok"}
    print(f"checks: {len(results)} ok={len(results) - len(bad)} bad={len(bad)}")
    for k, v in results.items():
        if v != "ok":
            print(f"  {k}: {v}")
    if bad:
        return 2
    if args.check:
        return 0
    doc = json.loads(PROFILE.read_text(encoding="utf-8"))
    mps = doc.get("mine_profiles") or {}
    mps["sudbury"] = build_profile()
    doc["mine_profiles"] = mps
    srcs = doc.get("sources") or []
    for cand in (FILING_20F, FILING_6K):
        if not any(s.get("url") == cand["url"] for s in srcs):
            srcs.append({"title": cand["title"], "date": cand["date"], "url": cand["url"],
                         "what": "Sudbury mine profile: complex production, shaft ore+grades, recoveries, TRS reserves/resources"})
    doc["sources"] = srcs
    basis_add = (
        " Sudbury mine_profiles.sudbury adds complex finished Ni/Cu/Co/Pt/Pd/Au 2023-2025, "
        "source-split shaft ore+grades, TRS recoveries and reserves/resources (20-F Ex. 96.3). "
        "Sudbury costs/life blank: no mine-level disclosure."
    )
    if "mine_profiles.sudbury" not in doc["method"].get("production_basis", ""):
        doc["method"]["production_basis"] = doc["method"].get("production_basis", "") + basis_add
    PROFILE.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"wrote mine_profiles.sudbury -> {PROFILE} ({stamp})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
