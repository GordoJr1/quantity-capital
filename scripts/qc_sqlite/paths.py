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
TICKERS = QC_ROOT / "tickers.json"
BIOS = QC_ROOT / "bios.json"
INSIDER_TRADES = QC_ROOT / "insider-trades.json"
INSIDER_COMPANIES = QC_ROOT / "insider-companies.json"
MARKET_CAPS = QC_ROOT / "market-caps.json"

CLAIMS_DIR = QC_ROOT / "claims"

DB_PATH = QC_ROOT / "qc.sqlite"
DB_MIRROR = GROKS / "qc.sqlite"
SCHEMA_SQL = HERE / "schema.sql"
JEV_CACHE = HERE / ".cache" / "jev.json"
EXPORT_DIR = HERE / "export"
TYPESAFE_ENV = Path.home() / ".grok" / "typesafe.env"
