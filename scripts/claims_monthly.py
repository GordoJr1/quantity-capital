r"""One-command monthly claims snapshot. Manual only — not part of the morning or evening bats.

Do not wire this into update-trades.bat or any scheduled task. Run it by hand
about once a month. It is not called from any .bat.

Exact command, from the desktop copy (the default snapshot folder is
C:\Users\gordo\Desktop\Groks folder\claims_snapshots):

    py -3 "C:\Users\gordo\Desktop\Groks folder\collect\claims_monthly.py"

From a checkout of this repo:

    py -3 scripts/claims_monthly.py

Either command shallow-clones origin/main and, inside that clone, runs:

    py -3 scripts/claims_db.py --all --refresh --no-jev --skip-tiles

A single-province trial that does not use the real snapshot folder:

    py -3 scripts/claims_monthly.py --provinces nunavut --snapshots %TEMP%\qc-claims-monthly-test

claims_db.py is not modified. The pipeline writes its gitignored
claims/.db/claims.sqlite (and claims/links, claims/search, claims/around).
This script copies the database and holders.json out to a dated snapshot.
It does not pass --publish and it never writes qc.sqlite.

This file imports claims_snapshot_diff from the same folder, so the two scripts
can be copied together into the Groks collect folder and run from there.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if not sys.path or sys.path[0] != _HERE:
    sys.path.insert(0, _HERE)

import claims_snapshot_diff

REMOTE = "https://github.com/GordoJr1/quantity-capital"
FORBIDDEN = r"C:\Users\gordo\Desktop\quantity-capital"
DEFAULT_SNAPSHOTS = r"C:\Users\gordo\Desktop\Groks folder\claims_snapshots"
PIPELINE_FLAGS = ["--refresh", "--no-jev", "--skip-tiles"]
_SNAP_NAME = re.compile(r"^claims-(\d{8})(?:-(\d+))?\.sqlite$")


class MonthlyError(Exception):
    """User-facing failure. main() prints it and exits non-zero."""


class StepTimer:
    def __init__(self) -> None:
        self.started = time.perf_counter()
        self._last = self.started

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        print(f"elapsed {name}: {now - self._last:.1f}s", flush=True)
        self._last = now

    def total(self) -> None:
        print(f"elapsed total: {time.perf_counter() - self.started:.1f}s", flush=True)


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))


def _is_forbidden(path: Path) -> bool:
    """True for the protected desktop checkout, without listing or git-reading it."""
    target = _norm(path)
    banned = os.path.normcase(os.path.normpath(os.path.abspath(FORBIDDEN)))
    if target == banned:
        return True
    prefix = banned if banned.endswith(os.sep) else banned + os.sep
    return target.startswith(prefix)


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1


def _porcelain(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git status failed").strip()
        raise MonthlyError(f"git status failed in {repo}: {detail}")
    return proc.stdout


def prepare_repo(repo_arg: Path | None, stamp: str) -> Path:
    if repo_arg is not None:
        if _is_forbidden(repo_arg):
            raise MonthlyError(
                r"refusing C:\Users\gordo\Desktop\quantity-capital "
                r"(protected working copy; this runner will not touch it)"
            )
        repo = Path(repo_arg)
        if not repo.is_dir():
            raise MonthlyError(f"repo not found: {repo}")
        script = repo / "scripts" / "claims_db.py"
        if not script.is_file():
            raise MonthlyError(f"missing {script}")
        dirty = _porcelain(repo)
        if dirty.strip():
            preview = "\n".join(dirty.splitlines()[:40])
            raise MonthlyError(f"refusing dirty repo {repo}:\n{preview}")
        print(f"repo: {repo}", flush=True)
        return repo

    dest = Path(tempfile.gettempdir()) / f"qc-claims-monthly-{stamp}"
    if dest.exists():
        dest = _unique_path(dest)
    print(f"cloning {REMOTE} -> {dest}", flush=True)
    proc = subprocess.run(
        ["git", "clone", "--depth", "1", REMOTE, str(dest)],
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
    )
    script = dest / "scripts" / "claims_db.py"
    if proc.returncode != 0 or not script.is_file():
        raise MonthlyError(f"clone failed ({proc.returncode}): {REMOTE}")
    print(f"repo: {dest}", flush=True)
    return dest


def pipeline_command(repo: Path, provinces: list[str] | None) -> list[str]:
    script = repo / "scripts" / "claims_db.py"
    cmd = [sys.executable, "-u", str(script)]
    if provinces:
        for name in provinces:
            cmd.extend(["--province", name])
    else:
        cmd.append("--all")
    cmd.extend(PIPELINE_FLAGS)
    if "--publish" in cmd or any("qc.sqlite" in part for part in cmd):
        raise MonthlyError("refusing to publish or to touch qc.sqlite")
    return cmd


def stream_pipeline(cmd: list[str], repo: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"log: {log_path}", flush=True)
    print("command: " + subprocess.list2cmdline(cmd), flush=True)
    with log_path.open("w", encoding="utf-8", errors="replace", newline="\n") as log:
        log.write("command: " + subprocess.list2cmdline(cmd) + "\n")
        log.write(f"cwd: {repo}\n\n")
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=_child_env(),
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            log.flush()
            print(line, end="", flush=True)
        return proc.wait()


def allocate_snapshot(directory: Path, day: str) -> tuple[Path, Path, Path]:
    """claims-YYYYMMDD.sqlite, or claims-YYYYMMDD-2.sqlite, never an existing name."""
    directory.mkdir(parents=True, exist_ok=True)
    suffix: int | None = None
    while True:
        stem = f"claims-{day}" if suffix is None else f"claims-{day}-{suffix}"
        sqlite_path = directory / f"{stem}.sqlite"
        holders_path = directory / f"{stem}.holders.json"
        counts_path = directory / f"{stem}.counts.json"
        if not sqlite_path.exists() and not holders_path.exists() and not counts_path.exists():
            return sqlite_path, holders_path, counts_path
        suffix = 2 if suffix is None else suffix + 1


def _snapshot_key(path: Path) -> tuple[str, int, str] | None:
    match = _SNAP_NAME.match(path.name)
    if match is None:
        return None
    # Unsuffixed claims-YYYYMMDD.sqlite is the first file that day. The next
    # file is claims-YYYYMMDD-2.sqlite. The suffix is numeric so -10 sorts
    # after -2. Byte order would not, because "-" sorts before ".".
    suffix = int(match.group(2)) if match.group(2) else 0
    return (match.group(1), suffix, path.name)


def previous_snapshot(directory: Path, current: Path) -> Path | None:
    current_key = _snapshot_key(current)
    if current_key is None or not directory.is_dir():
        return None
    earlier: list[Path] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        key = _snapshot_key(path)
        if key is not None and key < current_key and path != current:
            earlier.append(path)
    if not earlier:
        return None
    earlier.sort(key=lambda path: _snapshot_key(path) or ("", 0, path.name))
    return earlier[-1]


def checkpoint_and_copy(src: Path, dst: Path) -> None:
    """Fold the WAL into the main file, then copy. Never touches qc.sqlite."""
    if src.name != "claims.sqlite" or src.parent.name != ".db":
        raise MonthlyError(f"refusing to copy unexpected database {src}")
    if not src.is_file():
        raise MonthlyError(f"missing claims database: {src}")
    if dst.exists():
        raise MonthlyError(f"refusing to overwrite {dst}")
    uri = src.resolve().as_uri() + "?mode=rw"
    con = sqlite3.connect(uri, uri=True, timeout=120)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    src_con = sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst_con = sqlite3.connect(dst)
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()


def write_counts(sqlite_path: Path, counts_path: Path) -> None:
    if counts_path.exists():
        raise MonthlyError(f"refusing to overwrite {counts_path}")
    uri = sqlite_path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        con.execute("PRAGMA query_only = ON")
        rows = con.execute(
            """
            SELECT province,
                   COUNT(*) AS rows,
                   COUNT(DISTINCT COALESCE(holder, '')) AS distinct_holders
            FROM titles
            GROUP BY province
            ORDER BY province
            """
        ).fetchall()
        total = con.execute("SELECT COUNT(*) FROM titles").fetchone()[0]
    finally:
        con.close()
    provinces = {
        province: {"rows": int(count), "distinct_holders": int(holders)}
        for province, count, holders in rows
    }
    payload = {
        "snapshot": str(sqlite_path),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "rows": int(total),
        "provinces": provinces,
    }
    counts_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_diff(snapshots: Path, current: Path, holders_path: Path) -> int:
    previous = previous_snapshot(snapshots, current)
    if previous is None:
        print("first snapshot, no diff", flush=True)
        return 0
    diff_dir = snapshots / "diffs"
    diff_dir.mkdir(parents=True, exist_ok=True)
    argv = [str(previous), str(current), "--out-dir", str(diff_dir), "--format", "both"]
    previous_holders = previous.with_suffix(".holders.json")
    if previous_holders.is_file():
        argv.extend(["--holders-old", str(previous_holders)])
    else:
        print(
            f"warning: no holders file for {previous.name}; old company names will be raw holders",
            flush=True,
        )
    if holders_path.is_file():
        argv.extend(["--holders-new", str(holders_path)])
    else:
        print(
            f"warning: no holders file for {current.name}; new company names will be raw holders",
            flush=True,
        )
    print(f"diff: {previous.name} -> {current.name}", flush=True)
    return claims_snapshot_diff.main(argv)


def execute(args: argparse.Namespace, timer: StepTimer) -> int:
    started = datetime.now()
    stamp = started.strftime("%Y%m%d-%H%M%S")
    day = started.strftime("%Y%m%d")
    snapshots = Path(args.snapshots)
    snapshots.mkdir(parents=True, exist_ok=True)
    log_path = _unique_path(snapshots / "logs" / f"claims-monthly-{stamp}.log")

    repo = prepare_repo(args.repo, stamp)
    timer.mark("repo")

    cmd = pipeline_command(repo, args.provinces)
    code = stream_pipeline(cmd, repo, log_path)
    timer.mark("pipeline")
    if code != 0:
        print(f"pipeline failed ({code}). log: {log_path}", file=sys.stderr)
        return code or 1

    source_db = repo / "claims" / ".db" / "claims.sqlite"
    source_holders = repo / "claims" / "links" / "holders.json"
    sqlite_path, holders_path, counts_path = allocate_snapshot(snapshots, day)
    checkpoint_and_copy(source_db, sqlite_path)
    if not source_holders.is_file():
        raise MonthlyError(f"missing {source_holders}")
    if holders_path.exists():
        raise MonthlyError(f"refusing to overwrite {holders_path}")
    shutil.copy2(source_holders, holders_path)
    timer.mark("snapshot")
    print(f"snapshot: {sqlite_path}", flush=True)
    print(f"holders: {holders_path}", flush=True)

    write_counts(sqlite_path, counts_path)
    timer.mark("counts")
    print(f"counts: {counts_path}", flush=True)

    if args.no_diff:
        print("diff skipped (--no-diff)", flush=True)
        timer.mark("diff")
        return 0
    diff_code = run_diff(snapshots, sqlite_path, holders_path)
    timer.mark("diff")
    if diff_code != 0:
        print(f"diff failed ({diff_code})", file=sys.stderr)
        return diff_code
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Monthly claims snapshot (manual only, not part of the morning or evening bats). "
            "Clones a clean origin/main, refreshes claims, and writes a dated snapshot."
        )
    )
    parser.add_argument(
        "--snapshots",
        type=Path,
        default=Path(DEFAULT_SNAPSHOTS),
        help=f"Where dated snapshots go (default: {DEFAULT_SNAPSHOTS})",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Use this clean checkout instead of cloning. Refused when dirty, and the desktop working copy is always refused.",
    )
    parser.add_argument(
        "--provinces",
        nargs="+",
        default=None,
        help="Province ids to refresh (passed as repeated --province). Default is --all.",
    )
    parser.add_argument(
        "--no-diff",
        action="store_true",
        help="Save the snapshot but do not diff it against the previous one.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    timer = StepTimer()
    code = 1
    try:
        args = parse_args(argv)
        code = execute(args, timer)
    except MonthlyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    finally:
        timer.total()
    return code


if __name__ == "__main__":
    sys.exit(main())
