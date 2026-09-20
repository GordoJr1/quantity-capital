# Quantity Capital

Official STOCK Act / OGE 278-T tape from Senate eFD, House Clerk PTRs, and White House disclosures.

Amounts are ranges, not share counts. Not investment advice.

The live site is GitHub Pages from `main`: https://gordojr1.github.io/quantity-capital/

`claims.html` is the Quebec GESTIM / Ontario MLAS / BC MTA map (all committed company claims on load; search focuses one company plus nearby catalog claims). `beta.html` has a desktop-only mines inset beside Pipeline (same width as the price chart; hidden on phone with the Claims tab). Per-company extracts in `claims/` are a few MB each — do not commit nationwide PMTiles or shapefiles.

`paper.html` is a filed-date copy backtest (buy when the public filing lands). Rebuild with `python3 build-backtest.py`.

`insider-board.html?b=repeatable` ranks officers by equal-weight 30/90/180-day open-market copy returns from the public print date. Rebuild with `python3 build-insider-repeatable.py`.

`insiders.html` tracks officer and director open-market trades on the mining watchlist (defunct names out). U.S. domestic issuers use SEC Form 4. TSX / TSX-V names use public SEDI prints via CEO.CA. The full tape still arrives from an off-repo job. This repo now collects **Form 4 10b5-1 / plan footnotes and Table I end holdings** from those public EDGAR XML links and writes `insider-form4.json`. The follow list down-ranks scheduled-plan prints and uses size-versus-remaining-stake as conviction.

```
python3 fetch-prices.py              # append new Yahoo daily closes into prices/
python3 collect/form4_enrich.py      # Form 4 10b5-1 + Table I sidecar (SEC public)
python3 build-backtest.py            # rebuild backtest.json from trades-lite.json + prices/
python3 build-insider-repeatable.py  # 90-day open-market copy ranks (plan buys dropped)
python3 build-insider-follow.py      # Follow / Best officers + new-print alerts
```

`collect/form4_enrich.py` needs a SEC fair-access User-Agent: company name + email (default `Quantity Capital gordojr@proton.me`). Mozilla-style UAs get HTTP 403. Cap is 10 requests/second; the script sleeps 0.12s and reuses accessions already in `insider-form4.json`. No secrets. Do not point it at Senate eFD.

GitHub Action `daily-update` runs Form 4 enrich plus backtest / Repeatable / follow rebuilds daily at 23:30 UTC and commits those artifacts to `main` when data changed. It does not fetch or commit `prices/` — Yahoo daily closes stay morning-only from the off-repo collect bats. `follow-alerts.yml` does the Form 4 + follow rebuild when the tape is pushed. `morning-check` fails ~07:40 ET if today's off-repo politician (~07:22 ET) or insiders (~07:05 ET) tape stamp is missing. New Senate/House/OGE/SEDI filings still depend on the off-repo collector; do not mix those JSON files into feature commits.
