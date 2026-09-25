#!/usr/bin/env python3
"""Build the split tape the politician pages read.

The off-repo collector keeps writing trades.json and trades-lite.json.
This script runs in follow-alerts and daily-update after those pushes and
writes the derived files Pages serves. Python stays on the standard library.
Row enrichment, the tape slice, and the heat score go through qc.js so the
pages keep the names and the order they have today.

Outputs:
  tape/page.json              default (stock) first page, filers, boards
  tape/kind/{option,bond,all}.json
  tape/all.json               full lite tape, used only after a filter
  tape/politicians/<id>.json  one filer, from trades.json
  tape/tickers/<symbol>.json  one symbol, from trades.json
  tape/filers.json            id list for the politician search
  landed.json                 prints added in the last 14 days
  heat.json                   precomputed heat list for 14, 30, and 90 days
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("node is required to run qc.js for the tape split", file=sys.stderr)
        return 1
    script = ROOT / "scripts" / "tape-split.mjs"
    proc = subprocess.run([node, str(script)], cwd=ROOT)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
