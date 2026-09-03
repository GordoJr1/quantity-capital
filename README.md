# Quantity Capital

Official STOCK Act / OGE 278-T tape from Senate eFD, House Clerk PTRs, and White House disclosures.

Amounts are ranges, not share counts. Not investment advice.

`paper.html` is a filed-date copy backtest (buy when the public filing lands). Rebuild with `python3 build-backtest.py`.

`insiders.html` tracks officer and director open-market trades on the mining watchlist (defunct names out). U.S. domestic issuers use SEC Form 4. TSX / TSX-V names use public SEDI prints via CEO.CA. Rebuild with `python collect/insider_collect.py`, then `python collect/fetch_insider_prices.py` and `python collect/build_insider_analysis.py`.
