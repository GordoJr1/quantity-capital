#!/usr/bin/env python3
"""Fail if today's America/New_York morning tapes are not on this checkout.

Off-repo Windows jobs normally push:
  ~07:05 ET  insider-trades-lite.json on this repo   ("Insiders update …")
  ~07:22 ET  trades-lite.json on GordoJr1/stock-act ("Politician tape update …")

This does not fetch Senate eFD / House / SEDI. It only reads committed stamps.
Before 07:30 ET the slot has not closed, so the check exits 0.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
READY_MINUTE = 7 * 60 + 30  # 07:30 ET — after both morning collectors
ROOT = Path(__file__).resolve().parent
POLITICIAN_URL = "https://raw.githubusercontent.com/GordoJr1/stock-act/main/trades-lite.json"
INSIDER_FILE = "insider-trades-lite.json"


def collected_et_date(payload: dict, label: str):
    stamp = payload.get("collected")
    if not stamp:
        raise ValueError(f"{label}: missing collected")
    dt = datetime.fromisoformat(stamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).date(), dt.astimezone(timezone.utc)


def load_json_url(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def report(label: str, payload: dict, today) -> bool:
    day, utc = collected_et_date(payload, label)
    ok = day == today
    print(
        f"{'OK' if ok else 'FAIL'} {label}  collected={utc.isoformat()}  "
        f"et_date={day.isoformat()}  want={today.isoformat()}"
    )
    return ok


def main() -> int:
    now = datetime.now(ET)
    today = now.date()
    minute = now.hour * 60 + now.minute
    print(f"now {now.isoformat()}  today={today.isoformat()}")
    if minute < READY_MINUTE:
        print(f"before 07:30 ET morning window ({minute} < {READY_MINUTE}); skip")
        return 0

    failed = 0
    try:
        if not report("politician tape", load_json_url(POLITICIAN_URL), today):
            failed += 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL politician tape: {exc}")
        failed += 1
    try:
        insider = json.loads((ROOT / INSIDER_FILE).read_text(encoding="utf-8"))
        if not report("insiders tape", insider, today):
            failed += 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL insiders tape: {exc}")
        failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
