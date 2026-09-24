"""Stdlib file helpers shared by the scripts/ builders and gates."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write via a sibling temp file + os.replace so a killed run never leaves a torn file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, payload: Any, *, indent: int | None = 2, compact: bool = False) -> None:
    if compact:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=indent)
    atomic_write_text(path, text + "\n")
