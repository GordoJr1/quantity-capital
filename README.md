# Quantity Capital

Official STOCK Act / OGE 278-T tape from Senate eFD, House Clerk PTRs, and White House disclosures.

Amounts are ranges, not share counts. Not investment advice.

The live site is GitHub Pages from `main`: https://gordojr1.github.io/quantity-capital/

`paper.html` is a filed-date copy backtest (buy when the public filing lands). Rebuild with `python3 build-backtest.py`.

`insider-board.html?b=repeatable` ranks officers by equal-weight 30/90/180-day open-market copy returns from the public print date. Rebuild with `python3 build-insider-repeatable.py`.

`insiders.html` tracks officer and director open-market trades on the mining watchlist (defunct names out). U.S. domestic issuers use SEC Form 4. TSX / TSX-V names use public SEDI prints via CEO.CA. Rebuild with `python collect/insider_collect.py`, then `python collect/fetch_insider_prices.py` and `python collect/build_insider_analysis.py`. Those collect scripts are **not in this repo** — an off-repo job still pushes tape JSON here. This repo keeps charts and the paper backtest current on its own:

```
python3 fetch-prices.py      # append new Yahoo daily closes into prices/
python3 build-backtest.py    # rebuild backtest.json from trades-lite.json + prices/
```

GitHub Action `daily-update` runs that pair weekdays at 23:30 UTC and commits to `main` when prices changed (Actions tab → Run workflow to fire it by hand). New Senate/House/OGE/Form 4/SEDI filings still depend on the off-repo collector.
