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

if __name__ == '__main__':
    unittest.main(verbosity=2)
