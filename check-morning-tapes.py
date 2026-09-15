#!/usr/bin/env python3
"""Fail if today's America/New_York morning tapes are not on this checkout.

Off-repo Windows jobs normally push:
  ~07:05 ET  insider-trades-lite.json  ("Insiders update …")
  ~07:22 ET  trades-lite.json          ("Politician tape update …")

This does not fetch Senate eFD / House / SEDI. It only reads committed stamps.
Before 07:30 ET the slot has not closed, so the check exits 0.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
READY_MINUTE = 7 * 60 + 30  # 07:30 ET — after both morning collectors
ROOT = Path(__file__).resolve().parent
CHECKS = (
    ("trades-lite.json", "politician tape"),
    ("insider-trades-lite.json", "insiders tape"),
)


def collected_et_date(path: Path):
    raw = json.loads(path.read_text())
    stamp = raw.get("collected")
    if not stamp:
        raise ValueError(f"{path.name}: missing collected")
    dt = datetime.fromisoformat(stamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).date(), dt.astimezone(timezone.utc)


def main() -> int:
    now = datetime.now(ET)
    today = now.date()
    minute = now.hour * 60 + now.minute
    print(f"now {now.isoformat()}  today={today.isoformat()}")
    if minute < READY_MINUTE:
        print(f"before 07:30 ET morning window ({minute} < {READY_MINUTE}); skip")
        return 0

    failed = 0
    for rel, label in CHECKS:
        path = ROOT / rel
        try:
            day, utc = collected_et_date(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"FAIL {label}: {exc}")
            failed += 1
            continue
        ok = day == today
        print(
            f"{'OK' if ok else 'FAIL'} {label}  collected={utc.isoformat()}  "
            f"et_date={day.isoformat()}  want={today.isoformat()}"
        )
        if not ok:
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
