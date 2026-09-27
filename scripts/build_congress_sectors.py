#!/usr/bin/env python3
"""Weekly builder for research/sectors.json: what sectors members of Congress purchase (counting trades, not dollars).

Inputs
  * scripts/congress_sectors_base.json.gz  frozen history, built once from the Kadoa congress-trading-monitor snapshot
    (MIT; House Clerk + Senate eFD PTRs, filings through 2026-09-22).  Congress purchases only, already sector-classified.
  * trades.json   the live Quantity Capital politician tape (same file the site publishes).  House + Senate rows only.
  * tickers.json  the site ticker file (SIC codes), used only for tickers the base has never seen.
Output
  * research/sectors.json  (compact; weekly 13-week sector shares, yearly table, recent-vs-long-run table,
    eight calendar-quarter columns, the chart's latest 13-week column, auto-computed "biggest shifts" list,
    incomplete-period markers).

Dedupe with the tape: a tape purchase is dropped as a duplicate of a base (Kadoa) row when chamber, trade date and the
floor of the amount range are equal, the filer names share a word, and the tickers are equal (or, when either ticker
is missing, the asset names share >= 2 words).  One-to-one greedy match, the same rule as the study's build_sectors.py.
Every other tape purchase is added.  Nothing else is read, so the weekly job needs only the live tape.

Usage
  python scripts/build_congress_sectors.py --tape trades.json --tickers tickers.json --out research/sectors.json
         [--base scripts/congress_sectors_base.json.gz] [--mondays-only] [--force] [--boot 5000]
  --mondays-only : do nothing unless today (local time) is Monday before 12:00 and the output was not already built today.
Fail-soft: any error prints "sectors: skipped (...)" and exits 0 without touching the existing output.
Needs Python 3.10+ and numpy.
"""
import argparse, datetime as dt, gzip, json, os, re, sys, tempfile, time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECTORS = ['Tech', 'Financials', 'Health Care', 'Cons. Discretionary', 'Comm. Services', 'Industrials',
           'Cons. Staples', 'Energy', 'Utilities', 'Real Estate', 'Materials']
LABELS = {'Tech': 'Tech', 'Financials': 'Financials', 'Health Care': 'Health care', 'Cons. Discretionary': 'Consumer discretionary',
          'Comm. Services': 'Communication services', 'Industrials': 'Industrials', 'Cons. Staples': 'Consumer staples',
          'Energy': 'Energy', 'Utilities': 'Utilities', 'Real Estate': 'Real estate', 'Materials': 'Materials'}
BUCKETS = SECTORS + ['Funds', 'Bonds', 'Other']
LAG_DAYS, FILL_DAYS = 45, 69       # dark shade: last 45 days (filing deadline); light: 45-69 days (90% of a month's filings in)
ROLL, MIN_N = 13, 30               # rolling window (weeks); a window needs >= 30 stock purchases to show a mix
SERIES_FROM = dt.date(2014, 1, 1)  # thin data before 2014
LONGRUN_FROM = 2014
# ---- shift rule (fixed in advance; same rules as the study's "breadth" test, Congress only, counting trades)
MIN_PP = 3.0          # count-share change vs long-run average, percentage points
SAME_PP = 1.5         # each-member-equal and without-top-5 changes must point the same way by at least this much
MIN_PER_MEMBER = 3    # a member needs >= 3 stock purchases in a period to count in the each-member-equal measure
N_TESTS = 22          # 11 sectors x 2 windows -> Bonferroni alpha = 0.05 / 22 per measure
PERSIST = 4           # a shift is listed only if it passes at this as-of and the previous 3 weekly as-ofs
SEED = 20260927
WINDOWS = [('3y', 1095, '3 years'), ('12m', 365, '12 months')]
NONE_TEXT = "No clear shift right now: no sector's change passes all our checks."
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

# ---------------------------------------------------------------------------------------------------------------- classify
RX_FUND = re.compile(r'\b(etf|etn|fund|funds|index|ishares|spdr|vanguard|proshares|powershares|direxion|wisdomtree|invesco qqq|qqq|'
                     r'select sector|mutual fund|529|target date|target retirement|money market|ultra ?short|s&p 500|nasdaq[- ]100|'
                     r'russell 2000|msci|ark (innovation|genomic)|global x|vaneck|ssga|sector spdr|trust series|unit investment|annuity)\b', re.I)
RX_BOND = re.compile(r'(\bmuni\b|municipal|\bbonds?\b|treasury|t-?bills?\b|\bnotes?\b.*\d{2,4}|coupon|matures|\bdue\s+\d|\b\d{1,2}(\.\d{1,5})?\s?%\s*(due|matur|\d{1,2}/|\d{4}|cpn|coupon|notes?|bonds?|sr\b|senior)|rate/coupon|'
                     r'\bgo\b.*\d{4}|\brev(enue)?\b.*\d{4}|\bmtn\b|debenture|zero cpn|\bcpn\b|school dist|sch dist|\bauth\b|\bcnty\b|'
                     r'\butil(ity)? sys|\bgen oblig|\bref(unding)?\b.*\d{4}|certificate of deposit|\bcd\b.*\d{4}|\bfdic\b|brokered cd)', re.I)
RX_TAG = re.compile(r'\[([A-Za-z]{2})\]\s*$')
RX_MMF = re.compile(r'(money market|money fund|fedfund|treasury fund|gov(ernmen)?t (obligations? )?fund|cash reserves|liquidity fund|\b[A-Z]{3}XX\b|sweep)', re.I)
RX_BOND_STRONG = re.compile(r'(\b\d{1,2}\.\d{1,5}\s?%|rate/coupon|matures:?|\bdtd\s?\d|\bnts\b|\bb/e\b|\bdue\s+\d{1,2}/|\bmuni(cipal)?\b|\btreas(ury)?\b.*(note|bill|bond)|\bbonds?\b|\bdebentures?\b|\bnotes? due\b|structured note|buffer(ed)? note|autocallable|auto callable|certificate of deposit)', re.I)
RX_FUND_STRONG = re.compile(r'\b(etf|etfs|fund|index fund|money market|mutual fund)\b', re.I)
RX_CRYPTO = re.compile(r'\b(bitcoin|btc|ethereum|\beth\b|crypto|cryptocurrency|solana|dogecoin|litecoin|cardano|ripple|xrp|usdc|tether)\b', re.I)
RX_OPTION = re.compile(r'\b(call|put)s?\b|\boption(s)?\b|\bstrike\b|@\s*\$?\d', re.I)
RX_CUT = re.compile(r'\b(see endnote|unsolicited|solicited|description:|company:|average unit price|your broker).*$', re.I)
BOND_TYPES = {'GS', 'CS', 'Corporate Bond', 'Municipal Security'}
FUND_TYPES = {'ET', 'MF', 'EF', 'HN', 'AB'}
PRIV_TYPES = {'PS', 'Non-Public Stock', 'OL', 'RS', 'VA', 'SA', 'OI', 'OT', 'Other', 'Commodities/Futures Contract'}
OVERRIDE = {**{t: 'Comm. Services' for t in 'GOOGL GOOG META FB NFLX DIS EA ATVI TTWO RBLX MTCH SNAP PINS TWTR SPOT BIDU WBD PARA FOXA FOX NWSA NWS LYV ROKU IAC ZG Z TCEHY NTES SE IQ'.split()},
            **{t: 'Financials' for t in 'V MA PYPL FI FISV FIS GPN XYZ SQ AFRM COIN HOOD SOFI TOST FOUR'.split()},
            **{t: 'Industrials' for t in 'ADP PAYX UBER LYFT BR CPRT CTAS VRSK EFX'.split()},
            **{t: 'Cons. Discretionary' for t in 'ABNB EBAY BKNG EXPE DASH ETSY CVNA'.split()},
            **{t: 'Health Care' for t in 'CVS DHR TMO A IQV'.split()},
            **{t: 'Tech' for t in 'ACN IBM CTSH IT EPAM'.split()},
            **{t: 'Cons. Staples' for t in 'WBA'.split()}, **{t: 'Materials' for t in 'SHW'.split()}, 'MMM': 'Industrials', 'CEG': 'Utilities'}
KNOWN_FUNDS = set('SPY IVV VOO VTI QQQ DIA IWM EFA EEM GLD SLV USO UNG TLT IEF SHY BIL SGOV AGG BND LQD HYG JNK VEA VWO VNQ XLK XLF XLE XLV XLY XLP XLI XLB XLU XLRE XLC SMH SOXX ARKK IBIT FBTC GBTC ETHE BITO SCHD VIG VYM JEPI JEPQ RSP MDY IJH IJR VB VO VXUS IEMG MUB SPLG SPYG SPYV IWF IWD QQQM TQQQ SQQQ SPXL UPRO SSO SDS'.split())

def sic_sector(sic):
    """SIC (4-digit) -> GICS-style sector (same table as the study's build_sectors.py)."""
    try: s = int(sic)
    except (TypeError, ValueError): return None
    R = lambda a, b: a <= s <= b
    if s in (6722, 6726, 6221): return 'Funds'
    if R(100, 299) or R(700, 799) or R(900, 999): return 'Cons. Staples'
    if R(800, 899) or R(1000, 1099) or R(1400, 1499): return 'Materials'
    if R(1200, 1399): return 'Energy'
    if s in (1520, 1521, 1531): return 'Cons. Discretionary'
    if R(1500, 1799): return 'Industrials'
    if R(2000, 2199): return 'Cons. Staples'
    if R(2200, 2399) or R(2500, 2599) or s in (2451, 2452): return 'Cons. Discretionary'
    if R(2400, 2499) or R(2600, 2699): return 'Materials'
    if R(2700, 2749) or s == 2741: return 'Comm. Services'
    if R(2750, 2799): return 'Industrials'
    if R(2833, 2836): return 'Health Care'
    if R(2840, 2844): return 'Cons. Staples'
    if R(2800, 2899): return 'Materials'
    if R(2900, 2999): return 'Energy'
    if s in (3011, 3021) or R(3100, 3199): return 'Cons. Discretionary'
    if R(3000, 3099) or R(3200, 3399): return 'Materials'
    if R(3400, 3499): return 'Industrials'
    if R(3570, 3579): return 'Tech'
    if R(3500, 3599): return 'Industrials'
    if R(3630, 3639) or s == 3651: return 'Cons. Discretionary'
    if s == 3652: return 'Comm. Services'
    if R(3660, 3679): return 'Tech'
    if R(3600, 3699): return 'Industrials'
    if R(3710, 3716) or R(3750, 3751) or R(3790, 3799): return 'Cons. Discretionary'
    if R(3700, 3799) or s == 3812: return 'Industrials'
    if s == 3826 or R(3840, 3851): return 'Health Care'
    if R(3820, 3829) or R(3860, 3869): return 'Tech'
    if s == 3873: return 'Cons. Discretionary'
    if R(3800, 3899): return 'Industrials'
    if R(3910, 3915) or R(3940, 3949): return 'Cons. Discretionary'
    if R(3900, 3999): return 'Industrials'
    if R(4610, 4619) or s == 4922: return 'Energy'
    if R(4000, 4799): return 'Industrials'
    if R(4800, 4899): return 'Comm. Services'
    if R(4950, 4959): return 'Industrials'
    if R(4900, 4999): return 'Utilities'
    if s in (5122, 5047): return 'Health Care'
    if R(5140, 5149) or s == 5180: return 'Cons. Staples'
    if s == 5045: return 'Tech'
    if R(5171, 5172): return 'Energy'
    if R(5000, 5199): return 'Industrials'
    if R(5400, 5499) or s in (5331, 5399, 5912): return 'Cons. Staples'
    if R(5200, 5999): return 'Cons. Discretionary'
    if s == 6324: return 'Health Care'
    if R(6500, 6553) or s == 6798: return 'Real Estate'
    if R(6000, 6799): return 'Financials'
    if R(7000, 7099) or R(7200, 7299): return 'Cons. Discretionary'
    if R(7310, 7319): return 'Comm. Services'
    if R(7370, 7379): return 'Tech'
    if R(7300, 7399) or R(7500, 7599): return 'Industrials'
    if R(7800, 7899): return 'Comm. Services'
    if R(7900, 7999): return 'Cons. Discretionary'
    if R(8000, 8099) or s == 8731: return 'Health Care'
    if R(8200, 8299): return 'Cons. Discretionary'
    if R(8700, 8799) or R(8100, 8199) or R(8900, 8999): return 'Industrials'
    return None

def norm_name(s):
    s = re.sub(r'\[[a-z]{2}\]', ' ', str(s or ''), flags=re.I).lower()
    s = re.sub(r'\([^)]*\)', ' ', s)
    s = re.sub(r'\b(the|inc|incorporated|corp|corporation|co|company|ltd|limited|plc|nv|n v|sa|ag|se|holdings?|group|class [a-c]|cl [a-c]|'
               r'common stock|common|stock|shares?|ordinary|adr|ads|sponsored|new|com|cmn|del|de)\b', ' ', s)
    return ' '.join(re.findall(r'[a-z0-9]+', s))

def norm_co(s):
    s = RX_CUT.sub(' ', str(s or ''))
    s = re.sub(r'\[[a-z]{2}\]', ' ', s, flags=re.I)
    s = re.sub(r'^.*>\s*', ' ', s)
    return norm_name(s)

def clean_ticker(t):
    if not isinstance(t, str): return None
    t = t.strip().upper().replace('.', '-').replace('/', '-').lstrip('$')
    return t if re.fullmatch(r'[A-Z][A-Z0-9-]{0,6}', t) and t not in {'N-A', 'NA', 'NONE', 'NULL'} else None

def class_key(ticker, asset): return (ticker or '') + '||' + norm_co(asset)

def classify(ticker, asset, at, tsec, overrides):
    """-> bucket (11 sectors, Funds, Bonds or Other).  Frozen study result first (class_override), then the study's rules."""
    k = class_key(ticker, asset)
    if k in overrides: return overrides[k]
    if not at:
        mt = RX_TAG.search(asset or ''); at = mt.group(1).upper() if mt else ''
    txt = f'{asset} '
    sec = tsec(ticker) if ticker else None
    if at in ('CT', 'Cryptocurrency') or (RX_CRYPTO.search(txt) and not RX_FUND.search(txt) and sec is None): return 'Other'
    if at in FUND_TYPES or sec == 'Funds' or ((RX_FUND.search(txt) or RX_MMF.search(txt)) and sec is None): return 'Funds'
    if at in BOND_TYPES or (sec is None and RX_BOND.search(txt)): return 'Bonds'
    if at in ('OP', 'Stock Option') or RX_OPTION.search(txt): return sec or 'Other'
    if at in PRIV_TYPES and sec is None: return 'Other'
    if RX_BOND_STRONG.search(txt) and sec != 'Funds': return 'Bonds'
    if RX_FUND_STRONG.search(txt): return 'Funds'
    return sec or 'Other'

STOP_NAME = {'jr', 'sr', 'ii', 'iii', 'iv', 'hon', 'the', 'mr', 'mrs', 'ms', 'dr'}
STOP_ASSET = {'inc', 'corp', 'common', 'stock', 'the', 'class', 'company', 'shares', 'and', 'com', 'ltd', 'plc', 'group', 'holdings'}
def name_toks(n): return {t for t in re.findall(r'[a-z]+', str(n or '').lower()) if len(t) >= 3 and t not in STOP_NAME}
def asset_toks(a): return {t for t in re.findall(r'[a-z]{3,}', str(a or '').lower()) if t not in STOP_ASSET}
def name_key(n):
    t = re.findall(r'[a-z]+', str(n).lower().replace('jr.', '').replace('jr', ''))
    return (t[-1], t[0][0]) if t else None

def amt_lo(s):
    n = [float(x.replace(',', '')) for x in re.findall(r'\d[\d,]*', s or '')]
    return int(n[0]) if n else -1

def pdate(s):
    try: return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError): return None

# ---------------------------------------------------------------------------------------------------------------- data
def load_base(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f: B = json.load(f)
    ep = dt.date.fromisoformat(B['epoch']).toordinal()
    r = B['rows']
    rows = [dict(m=m, t=ep + t, f=ep + f, a=a, k=(B['tickers'][k] if k >= 0 else None), b=B['buckets'][b], x=B['assets'][x])
            for m, t, f, a, k, b, x in zip(r['m'], r['t'], r['f'], r['a'], r['k'], r['b'], r['x'])]
    return B, rows

def load_tape(path):
    j = json.load(open(path, encoding='utf-8'))
    T = []
    for r in j.get('trades', []):
        ch = str(r.get('chamber') or '').lower()
        if ch not in ('house', 'senate') or r.get('side') != 'purchase': continue
        td, fd = pdate(r.get('trade_date')), pdate(r.get('filed_date'))
        if td is None or fd is None: continue
        T.append(dict(id=r.get('id'), fid=str(r.get('filer_id') or ''), filer=r.get('filer') or '', chamber=ch, t=td.toordinal(), f=fd.toordinal(),
                      a=amt_lo(r.get('amount')), k=clean_ticker(r.get('ticker')), asset=str(r.get('asset') or ''), at=str(r.get('asset_type') or '')))
    return T, j.get('collected')

def dedupe(T, rows, members):
    """Tape purchase is a duplicate of a base row: same chamber + trade date + amount floor, filer names share a word, same ticker
    (or, when either ticker is missing, asset names share >= 2 words).  One-to-one, greedy in tape order."""
    idx = defaultdict(list)
    for i, r in enumerate(rows): idx[(members[r['m']][2], r['t'], r['a'])].append(i)
    mtok = [name_toks(m[1]) for m in members]
    used, dup, pairs = set(), [False] * len(T), []
    for j, g in enumerate(T):
        gt = name_toks(g['filer']); ga = None
        for i in idx.get((g['chamber'], g['t'], g['a']), ()):
            if i in used: continue
            r = rows[i]
            if not (gt & mtok[r['m']]): continue
            if g['k'] and r['k']:
                if g['k'] != r['k']: continue
            else:
                if ga is None: ga = asset_toks(g['asset'])
                if len(ga & set(r['x'].split())) < 2: continue
            used.add(i); dup[j] = True; pairs.append((g['fid'], r['m'])); break
    return dup, pairs

def assemble(base_path, tape_path, tickers_path):
    B, rows = load_base(base_path)
    members = B['members']                       # [kadoa_id, name, chamber]
    T, collected = load_tape(tape_path)
    dup, pairs = dedupe(T, rows, members)
    # member identity: frozen tape->base map, then this run's matched pairs, then a unique (last name, first initial) match
    mm = dict(B['member_map'])
    votes = defaultdict(lambda: defaultdict(int))
    for fid, m in pairs: votes[fid][m] += 1
    for fid, v in votes.items(): mm.setdefault(fid, max(v, key=v.get))
    kk = defaultdict(set)
    for i, m in enumerate(members):
        k = name_key(m[1])
        if k: kk[k].add(i)
    site_t = {}
    if tickers_path and Path(tickers_path).exists():
        site_t = (json.load(open(tickers_path, encoding='utf-8')) or {}).get('tickers', {}) or {}
    tmap = B['ticker_sector']
    def tsec(t):
        if t in tmap: return tmap[t]
        if t in OVERRIDE: return OVERRIDE[t]
        if t in KNOWN_FUNDS: return 'Funds'
        return sic_sector((site_t.get(t) or {}).get('sic'))
    ov = B['class_override']
    P = [dict(m=r['m'], t=r['t'], f=r['f'], b=r['b']) for r in rows]
    added = 0; new_rule = 0
    for g, d in zip(T, dup):
        if d: continue
        m = mm.get(g['fid'])
        if m is None:
            c = kk.get(name_key(g['filer']))
            m = next(iter(c)) if c and len(c) == 1 else 'tape:' + g['fid']
        if class_key(g['k'], g['asset']) not in ov: new_rule += 1
        P.append(dict(m=m, t=g['t'], f=g['f'], b=classify(g['k'], g['asset'], g['at'], tsec, ov))); added += 1
    lim = dt.date.fromisoformat(B['kadoa_snapshot']['filed_through']).toordinal()
    old = [d for g, d in zip(T, dup) if g['f'] <= lim]
    as_of = max(p['f'] for p in P)
    lo = dt.date(2012, 1, 1).toordinal()
    P = [p for p in P if p['t'] >= lo and p['f'] >= p['t'] and p['t'] <= as_of]
    info = dict(base_rows=len(rows), tape_purchases=len(T), tape_duplicates=int(sum(dup)), tape_added=added,
                tape_added_rule_classified=new_rule, tape_collected=collected,
                tape_old_match_pct=round(sum(old) / len(old) * 100, 1) if old else None, base_filed_through=B['kadoa_snapshot']['filed_through'])
    return P, as_of, info

# ---------------------------------------------------------------------------------------------------------------- stats
def monday(o): return o - dt.date.fromordinal(o).weekday()
def iso(o): return dt.date.fromordinal(o).isoformat()

def _as_ord(as_of):
    return as_of.toordinal() if isinstance(as_of, dt.date) else int(as_of)

def quarter_windows(as_of):
    """8 calendar quarters ending with the quarter that contains as_of, oldest first.

    as_of is a datetime.date or a date ordinal. Returns (label, start_ordinal, end_ordinal).
    """
    d = as_of if isinstance(as_of, dt.date) else dt.date.fromordinal(int(as_of))
    idx = d.year * 4 + (d.month - 1) // 3          # newest quarter, 0 = Q1
    out = []
    for i in range(idx - 7, idx + 1):
        y, qq = divmod(i, 4)
        start = dt.date(y, qq * 3 + 1, 1)
        end = dt.date(y, 12, 31) if qq == 3 else dt.date(y, qq * 3 + 4, 1) - dt.timedelta(days=1)
        out.append((f'Q{qq + 1} {y}', start.toordinal(), end.toordinal()))
    return out

def quarter_shade(end_ord, as_of):
    """Same two levels as the chart: quarter last day vs as_of−45 / as_of−69."""
    ao = _as_ord(as_of)
    if end_ord >= ao - LAG_DAYS: return 'incomplete'
    if end_ord >= ao - FILL_DAYS: return 'filling'
    return None

def quarter_marks(as_of):
    """Quarter labels and shade, without counts. Each item is label, shade, partial, start, end."""
    rows = []
    for label, a, b in quarter_windows(as_of):
        shade = quarter_shade(b, as_of)
        rows.append(dict(label=label, start=iso(a), end=iso(b), shade=shade, partial=shade is not None))
    return rows

def quarter_share_table(purchases, as_of):
    """Counts and sector shares for quarter_windows. purchases are {t: ordinal, b: sector}."""
    wins = quarter_windows(as_of)
    si = {s: i for i, s in enumerate(SECTORS)}
    counts = [[0] * len(SECTORS) for _ in wins]
    totals = [0] * len(wins)
    spans = [(a, b) for _, a, b in wins]
    lo, hi = spans[0][0], spans[-1][1]
    for p in purchases:
        t = p['t']
        if t < lo or t > hi: continue
        for i, (a, b) in enumerate(spans):
            if a <= t <= b:
                counts[i][si[p['b']]] += 1
                totals[i] += 1
                break
    quarters, shares = [], []
    for i, (label, a, b) in enumerate(wins):
        shade = quarter_shade(b, as_of)
        tot = totals[i]
        quarters.append(dict(label=label, start=iso(a), end=iso(b), purchases=tot, partial=shade is not None, shade=shade))
        if tot < MIN_N:
            shares.append([None] * len(SECTORS))
        else:
            shares.append([round(float(counts[i][j] / tot * 100), 1) for j in range(len(SECTORS))])
    return quarters, shares

def current_column(as_of, share, total):
    """Chart's latest point: 13 weeks ending the week of as_of. share/total come from series()."""
    ao = _as_ord(as_of)
    start = monday(ao) - 7 * (ROLL - 1)
    if share[SECTORS[0]]:
        shares = {s: share[s][-1] for s in SECTORS}
    else:
        shares = {s: None for s in SECTORS}
        total = 0
    return {
        'from': iso(start), 'to': iso(ao), 'weeks': ROLL, 'purchases': int(total),
        'partial': True, 'shade': 'incomplete', 'shares': shares,
    }

def series(S, as_of):
    import numpy as np
    w0, w1 = monday(min(p['t'] for p in S)), monday(as_of)
    nW = (w1 - w0) // 7 + 1
    W = np.zeros((nW, len(SECTORS)))
    si = {s: i for i, s in enumerate(SECTORS)}
    for p in S: W[(monday(p['t']) - w0) // 7, si[p['b']]] += 1
    cs = np.cumsum(np.vstack([np.zeros((1, len(SECTORS))), W]), axis=0)
    R = cs[ROLL:] - cs[:-ROLL]                  # R[i] = window ending at week i + ROLL - 1
    weeks, share = [], {s: [] for s in SECTORS}
    start = SERIES_FROM.toordinal()
    last_counts = [0] * len(SECTORS)
    last_total = 0
    for i in range(len(R)):
        wk = w0 + 7 * (i + ROLL - 1)
        if wk < start: continue
        tot = R[i].sum(); weeks.append(iso(wk))
        for j, s in enumerate(SECTORS): share[s].append(round(float(R[i, j] / tot * 100), 1) if tot >= MIN_N else None)
        last_counts = [int(R[i, j]) for j in range(len(SECTORS))]
        last_total = int(tot)
    return weeks, share, last_counts, last_total

def shares_of(counts):
    tot = sum(counts.values())
    return {s: (counts.get(s, 0) / tot * 100 if tot else float('nan')) for s in SECTORS}

def yearly(S, as_of):
    by = defaultdict(lambda: defaultdict(int)); mem = defaultdict(set)
    for p in S:
        y = dt.date.fromordinal(p['t']).year; by[y][p['b']] += 1; mem[y].add(p['m'])
    ys = sorted(by)
    cut = as_of - LAG_DAYS
    return {'years': ys, 'purchases': [sum(by[y].values()) for y in ys], 'members': [len(mem[y]) for y in ys],
            'partial': [y >= dt.date.fromordinal(cut).year for y in ys],
            'share': {s: [round(shares_of(by[y])[s], 1) for y in ys] for s in SECTORS}}

def shift_tests(S, as_of, n_boot):
    """Count-share change vs the long-run average (mean of calendar-year shares, 2014 .. last full year before the cutoff),
    for the 12 months and 3 years ending 45 days before as_of.  Member-cluster bootstrap; Bonferroni over 22 tests per measure."""
    import numpy as np
    cut = as_of - LAG_DAYS
    years = list(range(LONGRUN_FROM, dt.date.fromordinal(cut).year))
    S = [p for p in S if p['f'] <= as_of]
    mem = sorted({p['m'] for p in S}, key=str); mi = {m: i for i, m in enumerate(mem)}
    si = {s: i for i, s in enumerate(SECTORS)}
    nm, ns = len(mem), len(SECTORS)
    tot_by_m = defaultdict(int)
    for p in S: tot_by_m[p['m']] += 1
    top5 = set(sorted(tot_by_m, key=lambda m: (-tot_by_m[m], str(m)))[:5])      # most active members, whole history
    def mat(sel):
        N = np.zeros((nm, ns))
        for p in sel: N[mi[p['m']], si[p['b']]] += 1
        return N
    per = {}
    for y in years:
        per[y] = mat([p for p in S if dt.date.fromordinal(p['t']).year == y])
    win = {w: mat([p for p in S if cut - d < p['t'] <= cut]) for w, d, _ in WINDOWS}
    def count_share(N): t = N.sum(); return N.sum(0) / t * 100 if t else np.full(ns, np.nan)
    def pm_share(N):
        n = N.sum(1); ok = n >= MIN_PER_MEMBER
        return (N[ok] / n[ok, None]).mean(0) * 100 if ok.any() else np.full(ns, np.nan)
    def ex5_share(N):
        keep = np.array([m not in top5 for m in mem]); X = N[keep]
        strip = np.argsort(-X.sum(1), kind='stable')[:5]; X = np.delete(X, strip, axis=0)      # also the 5 most active in the period
        return count_share(X)
    L = {f.__name__: np.nanmean([f(per[y]) for y in years], axis=0) for f in (count_share, pm_share, ex5_share)}
    rng = np.random.default_rng(SEED)
    Wt = rng.multinomial(nm, np.ones(nm) / nm, size=n_boot).astype(float)
    def bcount(N): a = Wt @ N; return a / a.sum(1, keepdims=True) * 100
    def bpm(N):
        n = N.sum(1); ok = (n >= MIN_PER_MEMBER).astype(float)
        P = np.where(ok[:, None] > 0, N / np.where(n > 0, n, 1)[:, None], 0.0)
        return (Wt @ P) / (Wt @ ok)[:, None] * 100
    alpha = 0.05 / N_TESTS
    alpha_fw = 0.05 / (2 * N_TESTS)      # info only: both measures x 22 tests
    out = []
    with np.errstate(invalid='ignore', divide='ignore'):
        lb = {k: np.nanmean(np.stack([f(per[y]) for y in years]), axis=0) for k, f in (('count', bcount), ('pm', bpm))}
        for w, d, wl in WINDOWS:
            N = win[w]
            sh = dict(count=count_share(N), pm=pm_share(N), ex5=ex5_share(N))
            ci = {}
            for k, f in (('count', bcount), ('pm', bpm)):
                dd = f(N) - lb[k]
                ci[k] = (np.nanpercentile(dd, 100 * alpha / 2, axis=0), np.nanpercentile(dd, 100 * (1 - alpha / 2), axis=0),
                         np.nanpercentile(dd, 100 * alpha_fw / 2, axis=0), np.nanpercentile(dd, 100 * (1 - alpha_fw / 2), axis=0))
            for s in SECTORS:
                j = si[s]
                dc = sh['count'][j] - L['count_share'][j]; dp = sh['pm'][j] - L['pm_share'][j]; dx = sh['ex5'][j] - L['ex5_share'][j]
                sgn = np.sign(dc)
                cl, ch = ci['count'][0][j], ci['count'][1][j]; pl, ph = ci['pm'][0][j], ci['pm'][1][j]
                passes = bool(abs(dc) >= MIN_PP and (cl > 0 or ch < 0) and (pl > 0 or ph < 0)
                              and np.sign(dp) == sgn and abs(dp) >= SAME_PP and np.sign(dx) == sgn and abs(dx) >= SAME_PP)
                fw = bool(passes and (ci['count'][2][j] > 0 or ci['count'][3][j] < 0) and (ci['pm'][2][j] > 0 or ci['pm'][3][j] < 0))
                out.append(dict(sector=s, window=w, longrun=round(float(L['count_share'][j]), 2), recent=round(float(sh['count'][j]), 2),
                                diff=round(float(dc), 2), diff_each_member=round(float(dp), 2), diff_ex_top5=round(float(dx), 2),
                                ci_count=[round(float(cl), 2), round(float(ch), 2)], ci_each_member=[round(float(pl), 2), round(float(ph), 2)],
                                purchases=int(N.sum()), passes=passes, passes_strict_44=fw))
    meta = dict(cutoff=iso(cut), longrun_years=[years[0], years[-1]], windows={w: [iso(cut - d + 1), iso(cut)] for w, d, _ in WINDOWS})
    return out, meta

def long_date(o):
    d = dt.date.fromordinal(o); return f'{MONTHS[d.month - 1]} {d.day}, {d.year}'

def sentence(r, meta):
    """e.g. 'Rising: Tech, from 17% of purchases (2014–2025 average) to 22% in the 3 years to August 8, 2026.'"""
    wl = dict((w, l) for w, _, l in WINDOWS)[r['window']]
    y0, y1 = meta['longrun_years']
    end = long_date(dt.date.fromisoformat(meta['cutoff']).toordinal())
    return (f"{'Rising' if r['diff'] > 0 else 'Falling'}: {LABELS[r['sector']]}, from {round(r['longrun'])}% of purchases "
            f"({y0}\u2013{y1} average) to {round(r['recent'])}% in the {wl} to {end}.")

def build(base, tape, tickers, n_boot):
    t0 = time.time()
    P, as_of, info = assemble(base, tape, tickers)
    S = [p for p in P if p['b'] in SECTORS]
    stock_other = sum(1 for p in P if p['b'] == 'Other')
    weeks, share, _last_counts, last_total = series(S, as_of)
    tests, meta = shift_tests(S, as_of, n_boot)
    hist = [tests]
    for k in range(1, PERSIST):
        hist.append(shift_tests(S, as_of - 7 * k, n_boot)[0])
    passed = [{(r['sector'], r['window']) for r in h if r['passes']} for h in hist]
    shown = [r for r in tests if all((r['sector'], r['window']) in ps for ps in passed)]
    order = {w: i for i, (w, _, _) in enumerate(WINDOWS)}
    shown.sort(key=lambda r: (order[r['window']], -abs(r['diff'])))
    lines = [sentence(r, meta) for r in shown] or [NONE_TEXT]
    ym = {(r['sector'], r['window']): r for r in tests}
    lo_series = SERIES_FROM.toordinal()
    S2 = [p for p in S if p['t'] >= lo_series]
    q_cols, q_shares = quarter_share_table(S2, as_of)
    cur = current_column(as_of, share, last_total)
    compare = [dict(sector=s, label=LABELS[s], longrun=round(ym[(s, '3y')]['longrun'], 1), last3y=round(ym[(s, '3y')]['recent'], 1),
                    last12m=round(ym[(s, '12m')]['recent'], 1),
                    quarters=[q_shares[i][j] for i in range(len(q_cols))], current=cur['shares'][s])
               for j, s in enumerate(SECTORS)]
    out = {
        'version': 1,
        'generated_at': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
        'as_of': iso(as_of), 'incomplete_from': iso(as_of - LAG_DAYS), 'filling_in_from': iso(as_of - FILL_DAYS),
        'coverage': {'from': iso(min(p['t'] for p in S2)), 'to': iso(max(p['t'] for p in S2)), 'purchases': len(S2),
                     'members': len({p['m'] for p in S2}), 'other_pct': round(stock_other / (len(S) + stock_other) * 100, 1),   # Other bucket (private, crypto, unidentified) vs 11 sectors + Other
                     'sources': 'House and Senate disclosures: Kadoa congress-trading-monitor snapshot (filings through ' + info['base_filed_through'] + ') plus the Quantity Capital tape',
                     **{k: info[k] for k in ('tape_added', 'tape_duplicates', 'tape_old_match_pct', 'tape_added_rule_classified', 'tape_collected')}},
        'measure': 'Share of stock purchases by members of Congress (House and Senate), counting trades, 11 sectors',
        'window_weeks': ROLL, 'min_purchases': MIN_N,
        'sectors': SECTORS, 'labels': LABELS,
        'weeks': weeks, 'share': share,
        'yearly': yearly(S2, as_of),
        'compare': {'longrun_years': meta['longrun_years'], 'cutoff': meta['cutoff'], 'windows': meta['windows'],
                    'quarters': q_cols, 'current': {k: cur[k] for k in ('from', 'to', 'weeks', 'purchases', 'partial', 'shade')},
                    'rows': compare},
        'shifts': {'lines': lines, 'items': [{k: r[k] for k in ('sector', 'window', 'longrun', 'recent', 'diff')} for r in shown],
                   'rule': (f'Count-share change of at least {MIN_PP:g} points against the long-run average; member-cluster bootstrap interval excludes zero '
                            f'(Bonferroni over {N_TESTS} tests) both counting trades and counting each member equally; same direction by at least '
                            f'{SAME_PP:g} points each member equal and without the 5 most active members; passes at this as-of and the previous '
                            f'{PERSIST - 1} weekly as-ofs.'),
                   'checks': tests},
        'runtime_sec': round(time.time() - t0, 1),
    }
    return out

def sane(o):
    c = o['coverage']
    probs = []
    if c['purchases'] < 20000: probs.append(f"only {c['purchases']} purchases")
    if c['other_pct'] > 10: probs.append(f"Other bucket {c['other_pct']}%")
    if len(o['weeks']) < 500: probs.append(f"only {len(o['weeks'])} weeks")
    m = c.get('tape_old_match_pct')
    if m is not None and m < 75: probs.append(f"only {m}% of older tape purchases matched the base (possible double counting)")
    return probs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tape', required=True); ap.add_argument('--tickers', default=None); ap.add_argument('--out', required=True)
    ap.add_argument('--base', default=str(HERE / 'congress_sectors_base.json.gz'))
    ap.add_argument('--mondays-only', action='store_true'); ap.add_argument('--force', action='store_true')
    ap.add_argument('--boot', type=int, default=5000)
    a = ap.parse_args()
    try: sys.stdout.reconfigure(errors='replace')
    except Exception: pass
    out = Path(a.out)
    try:
        now = dt.datetime.now()
        if a.mondays_only and not a.force:
            if now.weekday() != 0 or now.hour >= 12:
                print('sectors: not Monday morning; skip'); return 0
            if out.exists():
                try:
                    g = json.load(open(out, encoding='utf-8')).get('generated_at', '')
                    if g[:10] == now.date().isoformat(): print('sectors: already built today; skip'); return 0
                except Exception: pass
        o = build(a.base, a.tape, a.tickers, a.boot)
        probs = sane(o)
        if probs:
            print('sectors: skipped (sanity: ' + '; '.join(probs) + '); kept existing file'); return 0
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(out.parent), suffix='.tmp')
        with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump(o, f, separators=(',', ':'), ensure_ascii=False)
        os.replace(tmp, out)
        try: os.chmod(out, 0o644)
        except OSError: pass
        c = o['coverage']
        print(f"sectors: wrote {out} as_of={o['as_of']} purchases={c['purchases']} members={c['members']} tape_added={c['tape_added']} "
              f"bytes={out.stat().st_size} shifts={o['shifts']['lines']} {o['runtime_sec']}s")
    except Exception as e:
        print(f'sectors: skipped ({type(e).__name__}: {e}); kept existing file')
    return 0

if __name__ == '__main__':
    sys.exit(main())
