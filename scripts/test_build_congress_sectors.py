"""Stdlib tests for scripts/build_congress_sectors.py.  Run: python scripts/test_build_congress_sectors.py"""
import datetime as dt, gzip, json, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_congress_sectors as w

class T(unittest.TestCase):
    def test_sic_and_overrides(self):
        self.assertEqual(w.sic_sector('7372'), 'Tech')
        self.assertEqual(w.sic_sector('6022'), 'Financials')
        self.assertEqual(w.sic_sector(None), None)
        self.assertEqual(w.classify('GOOGL', 'Alphabet Inc', 'Stock', lambda t: w.OVERRIDE.get(t), {}), 'Comm. Services')
        self.assertEqual(w.classify(None, 'City of Austin TX GO 4.0% due 2031', 'Municipal Security', lambda t: None, {}), 'Bonds')
        self.assertEqual(w.classify('SPY', 'SPDR S&P 500 ETF', 'Stock', lambda t: 'Funds' if t in w.KNOWN_FUNDS else None, {}), 'Funds')

    def test_sentence_templates(self):
        meta = {'longrun_years': [2014, 2025], 'cutoff': '2026-08-08'}
        up = dict(sector='Tech', window='3y', longrun=17.1, recent=22.2, diff=5.1)
        dn = dict(sector='Energy', window='12m', longrun=7.6, recent=2.2, diff=-5.4)
        self.assertEqual(w.sentence(up, meta), 'Rising: Tech, from 17% of purchases (2014\u20132025 average) to 22% in the 3 years to August 8, 2026.')
        self.assertEqual(w.sentence(dn, meta), 'Falling: Energy, from 8% of purchases (2014\u20132025 average) to 2% in the 12 months to August 8, 2026.')
        self.assertEqual(w.NONE_TEXT, "No clear shift right now: no sector's change passes all our checks.")

    def test_advice_words_absent(self):
        import re
        meta = {'longrun_years': [2014, 2025], 'cutoff': '2026-08-08'}
        txt = ' '.join([w.NONE_TEXT] + [w.sentence(dict(sector=s, window=win, longrun=10, recent=14, diff=4), meta) for s in w.SECTORS for win in ('3y', '12m')])
        self.assertIsNone(re.search(r'(?i)\b(buy|sell|should|recommend|avoid|signal|opportunit|bullish|bearish)', txt))

    def test_dedupe_one_to_one(self):
        members = [['house_jane_doe', 'Jane Doe', 'house']]
        rows = [dict(m=0, t=100, f=120, a=1001, k='AAPL', b='Tech', x='apple'),
                dict(m=0, t=100, f=120, a=1001, k='AAPL', b='Tech', x='apple')]
        T_ = [dict(fid='jane-doe', filer='Jane Doe', chamber='house', t=100, a=1001, k='AAPL', asset='Apple Inc'),
              dict(fid='jane-doe', filer='Jane Doe', chamber='house', t=100, a=1001, k='AAPL', asset='Apple Inc'),
              dict(fid='jane-doe', filer='Jane Doe', chamber='house', t=100, a=1001, k='AAPL', asset='Apple Inc'),
              dict(fid='jane-doe', filer='Jane Doe', chamber='house', t=101, a=1001, k='AAPL', asset='Apple Inc')]
        dup, pairs = w.dedupe(T_, rows, members)
        self.assertEqual(dup, [True, True, False, False])

    def test_fail_soft_keeps_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'research' / 'sectors.json'; out.parent.mkdir()
            out.write_text('{"keep": 1}')
            sys.argv = ['x', '--tape', str(Path(d) / 'missing.json'), '--out', str(out), '--base', str(Path(d) / 'nobase.json.gz')]
            self.assertEqual(w.main(), 0)
            self.assertEqual(out.read_text(), '{"keep": 1}')

    def test_mondays_only_skips_other_days(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'sectors.json'
            sys.argv = ['x', '--tape', 'none', '--out', str(out), '--mondays-only']
            now = dt.datetime.now()
            if now.weekday() != 0 or now.hour >= 12:
                self.assertEqual(w.main(), 0); self.assertFalse(out.exists())

    def test_quarter_windows_roll(self):
        def ends(d):
            return [(lab, w.iso(a), w.iso(b)) for lab, a, b in w.quarter_windows(d)]
        sep = ends(dt.date(2026, 9, 22))
        self.assertEqual([x[0] for x in sep],
                         ['Q4 2024', 'Q1 2025', 'Q2 2025', 'Q3 2025', 'Q4 2025', 'Q1 2026', 'Q2 2026', 'Q3 2026'])
        self.assertEqual(sep[0][1:], ('2024-10-01', '2024-12-31'))
        self.assertEqual(sep[1][1:], ('2025-01-01', '2025-03-31'))
        self.assertEqual(sep[2][1:], ('2025-04-01', '2025-06-30'))
        self.assertEqual(sep[3][1:], ('2025-07-01', '2025-09-30'))
        self.assertEqual(sep[4][1:], ('2025-10-01', '2025-12-31'))
        self.assertEqual(sep[5][1:], ('2026-01-01', '2026-03-31'))
        self.assertEqual(sep[6][1:], ('2026-04-01', '2026-06-30'))
        self.assertEqual(sep[7][1:], ('2026-07-01', '2026-09-30'))
        octo = ends(dt.date(2026, 10, 5))
        self.assertEqual([x[0] for x in octo],
                         ['Q1 2025', 'Q2 2025', 'Q3 2025', 'Q4 2025', 'Q1 2026', 'Q2 2026', 'Q3 2026', 'Q4 2026'])
        self.assertEqual(octo[0][1:], ('2025-01-01', '2025-03-31'))
        self.assertEqual(octo[-1][1:], ('2026-10-01', '2026-12-31'))

    def test_quarter_shade_partial(self):
        def marks(d):
            return [(q['label'], q['shade'], q['partial']) for q in w.quarter_marks(d)]
        sep = marks(dt.date(2026, 9, 22))
        self.assertEqual([m for m in sep if m[2]], [('Q3 2026', 'incomplete', True)])
        self.assertEqual(dict((m[0], m[1]) for m in sep)['Q2 2026'], None)
        aug = marks(dt.date(2026, 8, 20))
        by = {m[0]: (m[1], m[2]) for m in aug}
        self.assertEqual(by['Q2 2026'], ('filling', True))
        self.assertEqual(by['Q3 2026'], ('incomplete', True))
        self.assertEqual([m[0] for m in aug if m[2]], ['Q2 2026', 'Q3 2026'])

    def test_quarter_counts_shares_and_null(self):
        as_of = dt.date(2026, 9, 22)
        def P(day, sector, n):
            o = dt.date.fromisoformat(day).toordinal()
            return [dict(t=o, b=sector) for _ in range(n)]
        S = []
        S += P('2024-09-30', 'Energy', 7)          # day before Q4 2024
        S += P('2024-10-01', 'Tech', 30)           # first day of Q4 2024
        S += P('2024-11-15', 'Health Care', 10)
        S += P('2024-12-31', 'Financials', 20)     # last day of Q4 2024
        S += P('2025-03-31', 'Tech', 29)           # Q1 2025, under MIN_N
        S += P('2025-04-01', 'Tech', 10)           # Q2 2025, rounding
        S += P('2025-05-01', 'Financials', 10)
        S += P('2025-06-30', 'Health Care', 11)
        S += P('2014-06-01', 'Energy', 100)        # outside the eight quarters
        S += P('2026-09-22', 'Tech', 5)            # Q3 2026, under MIN_N
        cols, shares = w.quarter_share_table(S, as_of)
        by = {q['label']: q for q in cols}
        sh = {q['label']: shares[i] for i, q in enumerate(cols)}
        si = {s: i for i, s in enumerate(w.SECTORS)}
        self.assertEqual(by['Q4 2024']['purchases'], 60)
        self.assertFalse(by['Q4 2024']['partial'])
        self.assertEqual(sh['Q4 2024'][si['Tech']], 50.0)
        self.assertEqual(sh['Q4 2024'][si['Financials']], 33.3)
        self.assertEqual(sh['Q4 2024'][si['Health Care']], 16.7)
        self.assertEqual(sh['Q4 2024'][si['Energy']], 0.0)
        self.assertAlmostEqual(sum(sh['Q4 2024']), 100.0, delta=0.1 * 11)
        self.assertEqual(by['Q1 2025']['purchases'], 29)
        self.assertTrue(all(v is None for v in sh['Q1 2025']))
        self.assertEqual(by['Q2 2025']['purchases'], 31)
        self.assertEqual(sh['Q2 2025'][si['Tech']], round(10 / 31 * 100, 1))
        self.assertEqual(sh['Q2 2025'][si['Financials']], round(10 / 31 * 100, 1))
        self.assertEqual(sh['Q2 2025'][si['Health Care']], round(11 / 31 * 100, 1))
        self.assertAlmostEqual(sum(v for v in sh['Q2 2025'] if v is not None), 100.0, delta=0.1 * 11)
        self.assertEqual(by['Q3 2026']['purchases'], 5)
        self.assertEqual(by['Q3 2026']['shade'], 'incomplete')
        self.assertTrue(all(v is None for v in sh['Q3 2026']))
        self.assertEqual(sum(q['purchases'] for q in cols), 60 + 29 + 31 + 5)

    def test_current_matches_series_last_point(self):
        as_of = dt.date(2026, 9, 22)
        ao = as_of.toordinal()
        start = w.monday(ao) - 84
        self.assertEqual(w.iso(start), '2026-06-29')
        self.assertEqual(start, w.monday(ao) - 7 * (w.ROLL - 1))
        def add(day, sector, n):
            o = dt.date.fromisoformat(day).toordinal()
            return [dict(t=o, b=sector) for _ in range(n)]
        S = []
        S += add('2026-06-28', 'Energy', 50)       # Sunday before the window
        S += add('2026-06-29', 'Tech', 40)         # first day
        S += add('2026-08-01', 'Financials', 30)
        S += add('2026-09-22', 'Health Care', 30)  # as_of
        weeks, share, counts, total = w.series(S, ao)
        cur = w.current_column(ao, share, total)
        self.assertEqual(cur['from'], '2026-06-29')
        self.assertEqual(cur['to'], '2026-09-22')
        self.assertEqual(cur['weeks'], 13)
        self.assertEqual(cur['purchases'], 100)
        self.assertEqual(sum(counts), 100)
        self.assertTrue(cur['partial'])
        self.assertEqual(cur['shade'], 'incomplete')
        for s in w.SECTORS:
            self.assertEqual(cur['shares'][s], share[s][-1])
        self.assertEqual(cur['shares']['Tech'], 40.0)
        self.assertEqual(cur['shares']['Financials'], 30.0)
        self.assertEqual(cur['shares']['Health Care'], 30.0)
        self.assertEqual(cur['shares']['Energy'], 0.0)
        thin = add('2026-01-05', 'Materials', 1) + add('2026-09-01', 'Tech', 10)
        _w, sh2, counts2, tot2 = w.series(thin, ao)
        cur2 = w.current_column(ao, sh2, tot2)
        self.assertEqual(cur2['purchases'], 10)
        self.assertEqual(sum(counts2), 10)
        self.assertTrue(all(cur2['shares'][s] is None for s in w.SECTORS))
        self.assertTrue(all(sh2[s][-1] is None for s in w.SECTORS))

if __name__ == '__main__':
    unittest.main(verbosity=2)
