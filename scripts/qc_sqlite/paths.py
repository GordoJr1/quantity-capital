"""Pinned input / output paths for the QC SQLite brain."""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
QC_ROOT = HERE.parents[1]
GROKS = Path(r"C:\Users\gordo\Desktop\Groks folder")

CLAIMS_COMPANIES = GROKS / "collect" / "_jev_review" / "claims-map" / "companies.json"
MINES_REGISTRY = GROKS / "collect" / "mines" / "registry.json"
MINES_EXPLORERS = GROKS / "collect" / "mines" / "registry_explorers.json"
MINES_MCAP = GROKS / "collect" / "mines" / "registry_mcap.json"

TRADES = QC_ROOT / "trades.json"
TRADES_LITE = QC_ROOT / "trades-lite.json"
TICKERS = QC_ROOT / "tickers.json"
BIOS = QC_ROOT / "bios.json"
INSIDER_TRADES = QC_ROOT / "insider-trades.json"
INSIDER_COMPANIES = QC_ROOT / "insider-companies.json"
MARKET_CAPS = QC_ROOT / "market-caps.json"

CLAIMS_DIR = QC_ROOT / "claims"
PRICES = QC_ROOT / "prices"
GROKS_PRICES = GROKS / "prices"
TELLS_JSON = QC_ROOT / "tells.json"
ANALYSIS_JSON = QC_ROOT / "analysis.json"

DB_PATH = QC_ROOT / "qc.sqlite"
DB_MIRROR = GROKS / "qc.sqlite"
SCHEMA_SQL = HERE / "schema.sql"
JEV_CACHE = HERE / ".cache" / "jev.json"
EXPORT_DIR = HERE / "export"
TYPESAFE_ENV = Path.home() / ".grok" / "typesafe.env"
