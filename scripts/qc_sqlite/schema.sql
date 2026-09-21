-- Quantity Capital SQLite brain (pilot v3)
-- Claims attributes + company/ticker links + one politician trade calc.
-- Polygons stay on disk/CDN as GeoJSON extracts; this DB stores attributes/links only.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS jev_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  question_id TEXT NOT NULL,
  primitive TEXT NOT NULL,
  model TEXT,
  answer_json TEXT NOT NULL,
  input_tokens INTEGER,
  output_tokens INTEGER
);

-- Unified issuer. company_id is a stable slug (mines/mcap/claims-map id, else slugified name).
CREATE TABLE IF NOT EXISTS companies (
  company_id TEXT PRIMARY KEY,
  name TEXT,
  holder TEXT,
  universe TEXT,
  rank INTEGER,
  enabled INTEGER,
  filing_backed INTEGER,
  fiscal_year_end TEXT,
  ir_news TEXT,
  profile_file TEXT,
  commodity TEXT,
  company_type TEXT,
  country TEXT,
  claim_count INTEGER,
  neighbor_count INTEGER,
  quebec_count INTEGER,
  ontario_count INTEGER,
  bc_count INTEGER,
  extract_path TEXT,
  ontario_extract TEXT,
  bc_extract TEXT,
  bbox_json TEXT,
  color TEXT,
  sources_json TEXT NOT NULL DEFAULT '[]',
  names_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS company_names (
  company_id TEXT NOT NULL REFERENCES companies(company_id),
  name TEXT NOT NULL,
  kind TEXT,
  PRIMARY KEY (company_id, name)
);

CREATE TABLE IF NOT EXISTS tickers (
  ticker TEXT PRIMARY KEY,
  name TEXT,
  industry TEXT,
  sic TEXT,
  market_cap REAL,
  shares REAL,
  px REAL,
  cap_asof TEXT,
  cap_currency TEXT,
  cap_src TEXT
);

CREATE TABLE IF NOT EXISTS company_tickers (
  company_id TEXT NOT NULL REFERENCES companies(company_id),
  ticker TEXT NOT NULL REFERENCES tickers(ticker),
  source TEXT,
  is_primary INTEGER NOT NULL DEFAULT 0,
  jev_score REAL,
  jev_confidence REAL,
  jev_outcome TEXT,
  PRIMARY KEY (company_id, ticker)
);

-- Attribute rows: one focus pack per producer + one row per neighbor holder.
-- Not per-polygon. extract_path points at the GeoJSON if it exists elsewhere.
CREATE TABLE IF NOT EXISTS claim_packs (
  pack_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(company_id),
  role TEXT NOT NULL,
  holder TEXT,
  holder_company_id TEXT REFERENCES companies(company_id),
  claim_count INTEGER,
  jurisdiction TEXT,
  source TEXT,
  as_of TEXT,
  extract_path TEXT,
  bbox_json TEXT,
  color TEXT
);

CREATE TABLE IF NOT EXISTS claim_company_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pack_id TEXT NOT NULL REFERENCES claim_packs(pack_id),
  company_id TEXT NOT NULL REFERENCES companies(company_id),
  link_role TEXT NOT NULL,
  source TEXT NOT NULL,
  jev_score REAL,
  jev_confidence REAL,
  jev_outcome TEXT,
  same_name_noul REAL,
  holder_is_vehicle_noul REAL
);

-- Per-title attributes from Quebec GESTIM / Ontario MLAS / BC MTA GeoJSON. No geometry.
CREATE TABLE IF NOT EXISTS claim_titles (
  title_pk TEXT PRIMARY KEY, -- {pack_id}:{role}:{claim_id}; geometry duplicates collapse
  pack_id TEXT NOT NULL REFERENCES claim_packs(pack_id),
  company_id TEXT NOT NULL REFERENCES companies(company_id),
  holder_company_id TEXT REFERENCES companies(company_id),
  jurisdiction TEXT NOT NULL,
  claim_id TEXT,
  claim_name TEXT,
  holder_raw TEXT,
  status TEXT,
  recorded_date TEXT,
  anniversary_or_expiry TEXT,
  area_ha REAL,
  tenure_type TEXT,
  source TEXT,
  as_of TEXT,
  role TEXT,
  extract_path TEXT
);

-- Parsed (pct, holder) parties on a title. Ontario MLAS uses "(70) A, (30) B".
CREATE TABLE IF NOT EXISTS claim_title_parties (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title_pk TEXT NOT NULL REFERENCES claim_titles(title_pk),
  pack_id TEXT NOT NULL REFERENCES claim_packs(pack_id),
  holder_name TEXT NOT NULL,
  interest_pct REAL,
  holder_company_id TEXT REFERENCES companies(company_id),
  source TEXT,
  jev_score REAL,
  jev_confidence REAL,
  jev_outcome TEXT,
  same_name_noul REAL,
  holder_is_vehicle_noul REAL
);

CREATE TABLE IF NOT EXISTS mines (
  mine_id TEXT NOT NULL,
  company_id TEXT NOT NULL REFERENCES companies(company_id),
  name TEXT,
  lat REAL,
  lon REAL,
  region TEXT,
  country TEXT,
  gestim INTEGER,
  note TEXT,
  PRIMARY KEY (company_id, mine_id)
);

CREATE TABLE IF NOT EXISTS people (
  filer_id TEXT PRIMARY KEY,
  name TEXT,
  display TEXT,
  chamber TEXT,
  state TEXT,
  party TEXT,
  district TEXT,
  bioguide TEXT,
  opensecrets TEXT
);

CREATE TABLE IF NOT EXISTS politician_trades (
  trade_id TEXT PRIMARY KEY,
  filer TEXT,
  filer_id TEXT,
  chamber TEXT,
  ticker TEXT,
  asset TEXT,
  asset_type TEXT,
  side TEXT,
  amount_raw TEXT,
  amount_low REAL,
  amount_high REAL,
  amount_mid REAL,
  trade_date TEXT,
  filed_date TEXT,
  owner TEXT,
  source TEXT,
  added TEXT
);

CREATE TABLE IF NOT EXISTS insider_trades (
  trade_id TEXT PRIMARY KEY,
  filer TEXT,
  filer_id TEXT,
  title TEXT,
  ticker TEXT,
  company TEXT,
  company_type TEXT,
  commodity TEXT,
  side TEXT,
  shares REAL,
  price REAL,
  value REAL,
  amount_raw TEXT,
  trade_date TEXT,
  filed_date TEXT,
  origin TEXT,
  exchange TEXT
);

-- Pilot calc: STOCK Act amount-band midpoint as bps of issuer market cap.
CREATE TABLE IF NOT EXISTS trade_size_vs_cap (
  trade_id TEXT PRIMARY KEY REFERENCES politician_trades(trade_id),
  ticker TEXT,
  company_id TEXT,
  filer TEXT,
  filer_id TEXT,
  side TEXT,
  trade_date TEXT,
  amount_raw TEXT,
  amount_mid REAL,
  market_cap REAL,
  size_bps REAL,
  has_claims INTEGER,
  jev_flag TEXT,
  jev_confidence REAL,
  jev_anomaly_noul REAL,
  jev_model TEXT
);

CREATE INDEX IF NOT EXISTS idx_pol_ticker ON politician_trades(ticker);
CREATE INDEX IF NOT EXISTS idx_pol_filer ON politician_trades(filer_id);
CREATE INDEX IF NOT EXISTS idx_pol_date ON politician_trades(trade_date);
CREATE INDEX IF NOT EXISTS idx_ins_ticker ON insider_trades(ticker);
CREATE INDEX IF NOT EXISTS idx_ins_date ON insider_trades(trade_date);
CREATE INDEX IF NOT EXISTS idx_ct_ticker ON company_tickers(ticker);
CREATE INDEX IF NOT EXISTS idx_packs_company ON claim_packs(company_id);
CREATE INDEX IF NOT EXISTS idx_packs_holder ON claim_packs(holder_company_id);
CREATE INDEX IF NOT EXISTS idx_links_company ON claim_company_links(company_id);
CREATE INDEX IF NOT EXISTS idx_titles_company ON claim_titles(company_id);
CREATE INDEX IF NOT EXISTS idx_titles_jurisdiction ON claim_titles(jurisdiction);
CREATE INDEX IF NOT EXISTS idx_titles_pack ON claim_titles(pack_id);
CREATE INDEX IF NOT EXISTS idx_title_parties_holder ON claim_title_parties(holder_name);
CREATE INDEX IF NOT EXISTS idx_title_parties_company ON claim_title_parties(holder_company_id);
CREATE INDEX IF NOT EXISTS idx_title_parties_pack ON claim_title_parties(pack_id);
CREATE INDEX IF NOT EXISTS idx_calc_bps ON trade_size_vs_cap(size_bps);
CREATE INDEX IF NOT EXISTS idx_mines_company ON mines(company_id);

-- Tells board (build_tells.py rules). Official stock buys near a 20-session low
-- that then rose 20% within 15 sessions. Export reconstructs tells.json.
CREATE TABLE IF NOT EXISTS tell_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filer_id TEXT NOT NULL,
  ticker TEXT NOT NULL,
  name TEXT,
  day TEXT,
  buy REAL,
  high REAL,
  ret REAL,
  days INTEGER,
  amount_raw TEXT,
  high_end INTEGER
);

CREATE TABLE IF NOT EXISTS tell_hands (
  filer_id TEXT PRIMARY KEY,
  name TEXT,
  chamber TEXT,
  scored INTEGER,
  hits INTEGER,
  rate REAL,
  median REAL,
  score REAL,
  whale INTEGER,
  rank INTEGER
);

CREATE TABLE IF NOT EXISTS tell_hand_tells (
  filer_id TEXT NOT NULL REFERENCES tell_hands(filer_id),
  seq INTEGER NOT NULL,
  ticker TEXT,
  name TEXT,
  day TEXT,
  buy REAL,
  high REAL,
  ret REAL,
  days INTEGER,
  amount_raw TEXT,
  high_end INTEGER,
  PRIMARY KEY (filer_id, seq)
);

CREATE TABLE IF NOT EXISTS tell_now (
  ticker TEXT PRIMARY KEY,
  name TEXT,
  last REAL,
  px_asof TEXT,
  last_buy TEXT,
  buy_px REAL,
  chg REAL,
  near_low INTEGER,
  open INTEGER,
  high_end INTEGER,
  score REAL,
  why TEXT,
  rank INTEGER
);

CREATE TABLE IF NOT EXISTS tell_now_hands (
  ticker TEXT NOT NULL REFERENCES tell_now(ticker),
  filer_id TEXT NOT NULL,
  name TEXT,
  rate REAL,
  hits INTEGER,
  PRIMARY KEY (ticker, filer_id)
);

CREATE INDEX IF NOT EXISTS idx_tell_events_filer ON tell_events(filer_id);
CREATE INDEX IF NOT EXISTS idx_tell_events_ticker ON tell_events(ticker);
CREATE INDEX IF NOT EXISTS idx_tell_hands_rank ON tell_hands(rank);
CREATE INDEX IF NOT EXISTS idx_tell_now_rank ON tell_now(rank);

CREATE VIEW IF NOT EXISTS v_tell_hands AS
SELECT filer_id, name, chamber, scored, hits, rate, median, score, whale, rank
FROM tell_hands
ORDER BY rank;

CREATE VIEW IF NOT EXISTS v_tell_now AS
SELECT ticker, name, last, px_asof, last_buy, buy_px, chg, near_low, open, high_end, score, why, rank
FROM tell_now
ORDER BY rank;

-- Analysis / signals book (build_analysis.py rules + Jev ship/drop).
CREATE TABLE IF NOT EXISTS analysis_candidates (
  ticker TEXT PRIMARY KEY,
  name TEXT,
  rule_action TEXT,
  action TEXT,
  score REAL,
  why TEXT,
  last_px REAL,
  px_asof TEXT,
  buy_px REAL,
  chg_since_buy REAL,
  last_buy TEXT,
  n_buyers INTEGER,
  buy_high INTEGER,
  heat_rank INTEGER,
  heat REAL,
  landed INTEGER,
  landed_at TEXT,
  lag_days INTEGER,
  conflict INTEGER,
  conflict_why_json TEXT,
  whale INTEGER,
  cluster INTEGER,
  buyers_json TEXT,
  weekly_json TEXT,
  monthly_json TEXT,
  invalidation REAL,
  flags_json TEXT,
  jev_ship TEXT,
  jev_action TEXT,
  jev_action_confidence REAL,
  jev_borderline REAL,
  jev_conflict_noul REAL,
  jev_model TEXT,
  shipped INTEGER NOT NULL DEFAULT 0,
  list TEXT
);

CREATE INDEX IF NOT EXISTS idx_analysis_score ON analysis_candidates(score);
CREATE INDEX IF NOT EXISTS idx_analysis_list ON analysis_candidates(list, shipped);

CREATE VIEW IF NOT EXISTS v_analysis_book AS
SELECT ticker, name, action, score, why, heat_rank, list
FROM analysis_candidates
WHERE shipped = 1 AND list = 'book'
ORDER BY score DESC;

CREATE VIEW IF NOT EXISTS v_analysis_avoid AS
SELECT ticker, name, action, score, why, heat_rank, list
FROM analysis_candidates
WHERE shipped = 1 AND list = 'avoid'
ORDER BY score DESC;

CREATE VIEW IF NOT EXISTS v_trade_size_vs_cap AS
SELECT
  t.trade_id,
  t.ticker,
  ct.company_id,
  c.name AS company_name,
  t.filer,
  t.filer_id,
  t.side,
  t.trade_date,
  t.amount_raw,
  t.amount_mid,
  k.market_cap,
  CASE
    WHEN k.market_cap IS NOT NULL AND k.market_cap > 0 AND t.amount_mid IS NOT NULL
    THEN 10000.0 * t.amount_mid / k.market_cap
  END AS size_bps,
  CASE WHEN c.claim_count IS NOT NULL AND c.claim_count > 0 THEN 1 ELSE 0 END AS has_claims
FROM politician_trades t
LEFT JOIN tickers k ON k.ticker = t.ticker
LEFT JOIN company_tickers ct ON ct.ticker = t.ticker AND ct.is_primary = 1
LEFT JOIN companies c ON c.company_id = ct.company_id;

CREATE TABLE IF NOT EXISTS paper_legs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filer_id TEXT NOT NULL,
  ticker TEXT NOT NULL,
  filed TEXT,
  trade TEXT,
  amt TEXT,
  entry_px REAL,
  entry_date TEXT,
  exit_px REAL,
  exit_date TEXT,
  ret REAL,
  jev_artifact_noul REAL,
  jev_quality REAL,
  jev_model TEXT
);
CREATE TABLE IF NOT EXISTS paper_filers (
  filer_id TEXT PRIMARY KEY,
  name TEXT,
  chamber TEXT,
  n INTEGER,
  skip INTEGER,
  avg REAL,
  med REAL,
  win INTEGER
);
CREATE TABLE IF NOT EXISTS paper_tickers (
  ticker TEXT PRIMARY KEY,
  name TEXT,
  n INTEGER,
  people INTEGER,
  avg REAL,
  med REAL,
  win INTEGER
);
CREATE TABLE IF NOT EXISTS insider_form4 (
  trade_id TEXT PRIMARY KEY,
  plan INTEGER,
  payload_json TEXT
);
CREATE TABLE IF NOT EXISTS insider_repeatable_filers (
  filer_id TEXT PRIMARY KEY,
  payload_json TEXT
);
CREATE TABLE IF NOT EXISTS insider_follow (
  filer_id TEXT PRIMARY KEY,
  shipped INTEGER,
  jev_ship TEXT,
  jev_confidence REAL,
  payload_json TEXT
);
CREATE TABLE IF NOT EXISTS insider_analysis_book (
  ticker TEXT PRIMARY KEY,
  list TEXT,
  payload_json TEXT
);

-- Canadian Map 900A mine production (cited company filings only).
-- Standalone: no FK to companies so a monthly ingest can use a fresh qc.sqlite.
CREATE TABLE IF NOT EXISTS canada_mines (
  mine_id TEXT PRIMARY KEY,
  name TEXT,
  company_id TEXT,
  asset_id TEXT,
  region TEXT,
  country TEXT NOT NULL DEFAULT 'Canada',
  ownership_pct REAL,
  commodity TEXT,
  omit_figure INTEGER NOT NULL DEFAULT 0,
  map_900a INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS canada_mine_sources (
  mine_id TEXT NOT NULL,
  year INTEGER NOT NULL,
  url TEXT,
  title TEXT,
  as_of TEXT,
  kind TEXT,
  blocker TEXT,
  fetch_ok INTEGER,
  fetch_error TEXT,
  period TEXT,
  through TEXT,
  period_label TEXT,
  PRIMARY KEY (mine_id, year)
);
CREATE TABLE IF NOT EXISTS canada_mine_production (
  mine_id TEXT NOT NULL,
  year INTEGER NOT NULL,
  commodity TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT NOT NULL,
  source_value REAL,
  source_unit TEXT,
  quote TEXT,
  PRIMARY KEY (mine_id, year, commodity)
);
