"""Shared stdlib helpers for the root builders (backtest, repeatable, follow)."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRICES = ROOT / "prices"

# Refuse to overwrite a board when its input shrinks below this share of the previous build.
SHRINK_FLOOR = 0.5

AWARD_CODES = {"A", "30", "45", "46"}
EXERCISE_CODES = {"M", "X", "51", "54", "57", "59", "71"}
NON_MARKET_CODES = {"G", "W", "D", "J", "U"}
AWARD_NATURE = re.compile(r"^(30|45|46)\b")
EXERCISE_NATURE = re.compile(r"^(51|54|57|59|71)\b")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00"


def first_on_or_after(closes: list, date: str):
    lo, hi = 0, len(closes)
    while lo < hi:
        mid = (lo + hi) // 2
        if closes[mid][0] < date:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(closes):
        return None
    return closes[lo]


def first_after(closes: list, date: str):
    """First close strictly after `date` (a filing can land after that day's close)."""
    lo, hi = 0, len(closes)
    while lo < hi:
        mid = (lo + hi) // 2
        if closes[mid][0] <= date:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(closes):
        return None
    return closes[lo]


def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def median(xs: list[float]) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    mid = len(ys) // 2
    if len(ys) % 2:
        return ys[mid]
    return (ys[mid - 1] + ys[mid]) / 2.0


def round_ret(n) -> float:
    return round(float(n), 4)


def load_prices(code: str, cache: dict, prices: Path = PRICES):
    """Cleaned [[date, close], ...] for a prices/ ticker, or None."""
    if code in cache:
        return cache[code]
    path = prices / (code + ".json")
    if not path.is_file():
        cache[code] = None
        return None
    try:
        with path.open() as f:
            data = json.load(f)
        cleaned = []
        for row in data.get("c") or []:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            d, px = row[0], row[1]
            if not d or px is None:
                continue
            try:
                px = float(px)
            except (TypeError, ValueError):
                continue
            if px <= 0:
                continue
            cleaned.append([str(d)[:10], px])
        cache[code] = cleaned or None
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        cache[code] = None
    return cache[code]


def atomic_write_json(path: Path, data, *, compact: bool = True) -> None:
    """Write via a temp file + rename so a killed run never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            if compact:
                json.dump(data, f, separators=(",", ":"))
            else:
                json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_previous(path: Path) -> tuple[dict | None, str]:
    """Previous build output. A truncated or corrupt file is reported, not fatal."""
    if not path.is_file():
        return None, ""
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as err:
        return None, f"previous {path.name} unreadable ({type(err).__name__}); treating as first build"
    if not isinstance(data, dict):
        return None, f"previous {path.name} is not an object; treating as first build"
    return data, ""


def load_form4(path: Path) -> tuple[dict, str]:
    """Form 4 plan sidecar. Missing -> ({}, warning). Corrupt -> ValueError (fail loudly)."""
    if not path.is_file():
        return {}, f"{path.name} missing; 10b5-1 plan filter is off"
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as err:
        raise ValueError(f"{path.name} unreadable ({type(err).__name__}: {err}); refusing to build without the 10b5-1 filter") from err
    if not isinstance(data, dict) or not isinstance(data.get("trades"), dict):
        raise ValueError(f"{path.name} has no trades map; refusing to build without the 10b5-1 filter")
    return data, ""


def shrink_problems(prev: dict | None, new_stats: dict, keys: tuple[str, ...], floor: float = SHRINK_FLOOR) -> list[str]:
    """Stats that fell below `floor` of the previous build (a broken or empty input)."""
    if not prev:
        return []
    old_stats = prev.get("stats") or {}
    out = []
    for k in keys:
        old = old_stats.get(k)
        new = new_stats.get(k)
        if not isinstance(old, (int, float)) or old <= 0 or not isinstance(new, (int, float)):
            continue
        if new < old * floor:
            out.append(f"{k} {old} -> {new}")
    return out


def payload_key(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "generated"}


def write_if_changed(path: Path, data: dict, prev: dict | None) -> bool:
    """Skip the write when only the generated timestamp would change."""
    if prev is not None and payload_key(prev) == payload_key(data):
        return False
    atomic_write_json(path, data)
    return True


def is_award(t: dict) -> bool:
    if t.get("side") == "award":
        return True
    code = str(t.get("code") or "").upper()
    nature = str(t.get("nature") or "")
    return code in AWARD_CODES or bool(AWARD_NATURE.match(nature))


def is_exercise(t: dict) -> bool:
    if t.get("side") == "exercise":
        return True
    code = str(t.get("code") or "").upper()
    nature = str(t.get("nature") or "")
    return code in EXERCISE_CODES or bool(EXERCISE_NATURE.match(nature))


def is_market_purchase(t: dict) -> bool:
    """Form 4 code P or SEDI public-market nature 10. No awards, exercises, gifts, or other codes."""
    if not t or t.get("side") != "purchase":
        return False
    if is_award(t) or is_exercise(t):
        return False
    code = str(t.get("code") or "").upper()
    if code in NON_MARKET_CODES:
        return False
    if str(t.get("origin") or "").lower() == "sedi":
        return str(t.get("nature") or "").startswith("10")
    return code == "P"
