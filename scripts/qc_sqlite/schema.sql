-- Quantity Capital SQLite brain (pilot v1)
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

-- Per-title attributes from Ontario MLAS / BC MTA GeoJSON. No geometry.
CREATE TABLE IF NOT EXISTS claim_titles (
  title_pk TEXT PRIMARY KEY,
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
  source TEXT
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
