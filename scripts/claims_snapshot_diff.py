"""Diff two claims snapshots and write a company rollup, a title detail file, and a short report.

Standalone. Standard library only. Safe to copy into the Groks collect folder
and run on its own:

    py -3 claims_snapshot_diff.py OLD.sqlite NEW.sqlite --out-dir DIR
        [--holders-old HOLDERS.json] [--holders-new HOLDERS.json]
        [--format csv|md|both]

Opens both databases read-only (SQLite URI mode=ro). Never writes to them.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import string
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Registry rows are stored as "(100) NAME" (see format_registry_holder). Other
# feeds use a trailing "NAME (100%)". Drop both so a percent tag is not a transfer.
_LEAD_PCT = re.compile(r"^\s*\(\s*\d+(?:\.\d+)?\s*%?\s*\)\s*")
_TRAIL_PCT = re.compile(r"\s*\(\s*\d+(?:\.\d+)?\s*%?\s*\)\s*$")
_CHANGE_ORDER = {"new": 0, "lapsed": 1, "transferred": 2}


class DiffError(Exception):
    """User-facing diff failure. main() prints it and exits non-zero."""


@dataclass
class Title:
    province: str
    objectid: object
    title_id: str
    holder: str
    area_ha: float | None


@dataclass
class Side:
    path: Path
    by_key: dict[tuple[str, str], Title]
    raw_rows: Counter
    dup_extras: Counter

    @property
    def rows(self) -> int:
        return sum(self.raw_rows.values())

    @property
    def unique(self) -> int:
        return len(self.by_key)

    @property
    def dupes(self) -> int:
        return sum(self.dup_extras.values())


@dataclass
class Detail:
    change: str
    province: str
    title_id: str
    old_holder: str
    new_holder: str
    company_old: str
    company_new: str
    area_ha: float | None
    ticker_old: str = ""
    ticker_new: str = ""


@dataclass
class CompanyTotals:
    new: int = 0
    lapsed: int = 0
    transferred_in: int = 0
    transferred_out: int = 0
    new_area_ha: float = 0.0
    lapsed_area_ha: float = 0.0


@dataclass
class DiffResult:
    old: Side
    new: Side
    details: list[Detail]
    companies: dict[tuple[str, str, str], CompanyTotals]
    outputs: list[Path]
    holders_old_matched: int = 0
    holders_old_total: int = 0
    holders_new_matched: int = 0
    holders_new_total: int = 0
    used_holders_old: bool = False
    used_holders_new: bool = False
    pair_counts: Counter = field(default_factory=Counter)
    pair_provinces: dict[tuple[str, str, str, str], Counter] = field(default_factory=dict)


def normalize_holder(value: str | None) -> str:
    """Casefold, strip punctuation and extra spaces, drop percent tags.

    A trailing tag looks like ``(100%)``. A leading tag looks like ``(100)``,
    which is how the claims database stores registry interest.
    """
    text = "" if value is None else str(value).strip()
    previous = None
    while previous != text:
        previous = text
        text = _LEAD_PCT.sub("", text)
        text = _TRAIL_PCT.sub("", text)
        text = text.strip()
    folded = text.casefold()
    cleaned = "".join(" " if ch in string.punctuation else ch for ch in folded)
    return " ".join(cleaned.split())


def _oid_key(value: object) -> tuple:
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value)
    try:
        return (0, int(str(value)))
    except (TypeError, ValueError):
        return (1, "" if value is None else str(value))


def _area(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _connect_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise DiffError(f"database not found: {path}")
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise DiffError(f"could not open {path} read-only: {exc}") from exc
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def load_side(path: Path) -> Side:
    """Read titles, deduped on (province, title_id), keeping the max objectid."""
    con = _connect_ro(path)
    try:
        present = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'titles'"
        ).fetchone()
        if present is None:
            raise DiffError(f"{path} has no titles table")
        best: dict[tuple[str, str], Title] = {}
        seen: Counter = Counter()
        raw_rows: Counter = Counter()
        try:
            cursor = con.execute(
                "SELECT province, objectid, title_id, holder, area_ha FROM titles"
            )
        except sqlite3.Error as exc:
            raise DiffError(f"could not read titles from {path}: {exc}") from exc
        for row in cursor:
            province = "" if row["province"] is None else str(row["province"])
            title_id = "" if row["title_id"] is None else str(row["title_id"])
            holder = "" if row["holder"] is None else str(row["holder"])
            key = (province, title_id)
            raw_rows[province] += 1
            seen[key] += 1
            title = Title(
                province=province,
                objectid=row["objectid"],
                title_id=title_id,
                holder=holder,
                area_ha=_area(row["area_ha"]),
            )
            current = best.get(key)
            if current is None or _oid_key(title.objectid) >= _oid_key(current.objectid):
                best[key] = title
    finally:
        con.close()
    dup_extras: Counter = Counter()
    for (province, _title_id), count in seen.items():
        if count > 1:
            dup_extras[province] += count - 1
    return Side(path=path, by_key=best, raw_rows=raw_rows, dup_extras=dup_extras)


def _primary_company(row: dict) -> tuple[str, str]:
    for company in row.get("companies") or []:
        if not isinstance(company, dict):
            continue
        name = str(company.get("company") or "").strip()
        ticker = str(company.get("ticker") or "").strip()
        if not ticker:
            tickers = company.get("tickers") or []
            if isinstance(tickers, list) and tickers:
                ticker = str(tickers[0] or "").strip()
        if not name and not ticker:
            continue
        if not name:
            name = str(company.get("company_id") or "").strip()
        return name, ticker
    return "", ""


def load_holders(path: Path | None) -> dict[tuple[str, str], tuple[str, str]] | None:
    """Map (province, holder) to (company, ticker) from write_links() JSON.

    The file is ``{"rows": [{"province", "holder", "companies": [{"company", "ticker", ...}]}]}``.
    Holder keys match ontario_claims_db.holder_key: stripped text, or "(blank)".
    """
    if path is None:
        return None
    if not path.is_file():
        raise DiffError(f"holders file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiffError(f"could not read holders file {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        raise DiffError(f"{path} is not a holders.json written by claims_db.py (missing rows)")
    index: dict[tuple[str, str], tuple[str, str]] = {}
    for row in data["rows"]:
        if not isinstance(row, dict):
            continue
        province = str(row.get("province") or "").strip()
        holder = str(row.get("holder") or "").strip() or "(blank)"
        name, ticker = _primary_company(row)
        key = (province, holder)
        # Keep a linked company if a later duplicate row has none.
        if key in index and index[key][0] and not name:
            continue
        index[key] = (name, ticker)
    return index


def _lookup_key(holder: str) -> str:
    return holder.strip() or "(blank)"


def company_for(
    index: dict[tuple[str, str], tuple[str, str]] | None,
    province: str,
    holder: str,
) -> tuple[str, str]:
    """Company and ticker from holders.json, or the raw holder when unmapped."""
    raw = holder.strip()
    if index is None:
        return raw, ""
    found = index.get((province, _lookup_key(holder)))
    if found and found[0]:
        return found
    return raw, ""


def _area_add(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def build_diff(
    old: Side,
    new: Side,
    holders_old: dict[tuple[str, str], tuple[str, str]] | None,
    holders_new: dict[tuple[str, str], tuple[str, str]] | None,
) -> DiffResult:
    details: list[Detail] = []
    companies: dict[tuple[str, str, str], CompanyTotals] = defaultdict(CompanyTotals)
    pair_counts: Counter = Counter()
    pair_provinces: dict[tuple[str, str, str, str], Counter] = defaultdict(Counter)

    for key in sorted(set(new.by_key) - set(old.by_key)):
        title = new.by_key[key]
        company, ticker = company_for(holders_new, title.province, title.holder)
        details.append(Detail(
            change="new",
            province=title.province,
            title_id=title.title_id,
            old_holder="",
            new_holder=title.holder,
            company_old="",
            company_new=company,
            area_ha=title.area_ha,
            ticker_new=ticker,
        ))
        slot = companies[(company, ticker, title.province)]
        slot.new += 1
        slot.new_area_ha += _area_add(title.area_ha)

    for key in sorted(set(old.by_key) - set(new.by_key)):
        title = old.by_key[key]
        company, ticker = company_for(holders_old, title.province, title.holder)
        details.append(Detail(
            change="lapsed",
            province=title.province,
            title_id=title.title_id,
            old_holder=title.holder,
            new_holder="",
            company_old=company,
            company_new="",
            area_ha=title.area_ha,
            ticker_old=ticker,
        ))
        slot = companies[(company, ticker, title.province)]
        slot.lapsed += 1
        slot.lapsed_area_ha += _area_add(title.area_ha)

    for key in sorted(set(old.by_key) & set(new.by_key)):
        before = old.by_key[key]
        after = new.by_key[key]
        if normalize_holder(before.holder) == normalize_holder(after.holder):
            continue
        company_old, ticker_old = company_for(holders_old, before.province, before.holder)
        company_new, ticker_new = company_for(holders_new, after.province, after.holder)
        details.append(Detail(
            change="transferred",
            province=after.province,
            title_id=after.title_id,
            old_holder=before.holder,
            new_holder=after.holder,
            company_old=company_old,
            company_new=company_new,
            area_ha=after.area_ha if after.area_ha is not None else before.area_ha,
            ticker_old=ticker_old,
            ticker_new=ticker_new,
        ))
        companies[(company_old, ticker_old, before.province)].transferred_out += 1
        companies[(company_new, ticker_new, after.province)].transferred_in += 1
        pair = (company_old, ticker_old, company_new, ticker_new)
        pair_counts[pair] += 1
        pair_provinces[pair][after.province] += 1

    details.sort(key=lambda row: (_CHANGE_ORDER[row.change], row.province, row.title_id))
    matched_old, total_old = _match_stats(old, holders_old)
    matched_new, total_new = _match_stats(new, holders_new)
    return DiffResult(
        old=old,
        new=new,
        details=details,
        companies=dict(companies),
        outputs=[],
        holders_old_matched=matched_old,
        holders_old_total=total_old,
        holders_new_matched=matched_new,
        holders_new_total=total_new,
        used_holders_old=holders_old is not None,
        used_holders_new=holders_new is not None,
        pair_counts=pair_counts,
        pair_provinces=dict(pair_provinces),
    )


def _match_stats(
    side: Side,
    index: dict[tuple[str, str], tuple[str, str]] | None,
) -> tuple[int, int]:
    holders = {(title.province, _lookup_key(title.holder)) for title in side.by_key.values()}
    if index is None:
        return 0, len(holders)
    matched = sum(1 for key in holders if index.get(key) and index[key][0])
    return matched, len(holders)


def _format_area(value: float | None) -> str:
    if value is None:
        return ""
    text = f"{float(value):.4f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def _output_names(old: Path, new: Path, fmt: str) -> list[str]:
    base = f"claims-diff-{old.stem}-to-{new.stem}"
    names: list[str] = []
    if fmt in {"csv", "both"}:
        names.append(f"{base}-by-company.csv")
        names.append(f"{base}-detail.csv")
    if fmt in {"md", "both"}:
        names.append(f"{base}.md")
    return names


def allocate_outputs(out_dir: Path, names: list[str]) -> list[Path]:
    """Return paths that do not exist yet. An existing name gets -2, then -3, and so on.

    Every file from one run shares the same suffix so the set stays together.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix: int | None = None
    while True:
        paths: list[Path] = []
        for name in names:
            path = Path(name)
            if suffix is None:
                paths.append(out_dir / name)
            else:
                paths.append(out_dir / f"{path.stem}-{suffix}{path.suffix}")
        if not any(path.exists() for path in paths):
            return paths
        suffix = 2 if suffix is None else suffix + 1


def _write_company_csv(path: Path, companies: dict[tuple[str, str, str], CompanyTotals]) -> None:
    rows = sorted(companies.items(), key=lambda item: (item[0][0].casefold(), item[0][1].casefold(), item[0][2].casefold()))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([
            "company", "ticker", "province", "new", "lapsed",
            "transferred_in", "transferred_out", "new_area_ha", "lapsed_area_ha",
        ])
        for (company, ticker, province), totals in rows:
            writer.writerow([
                company,
                ticker,
                province,
                totals.new,
                totals.lapsed,
                totals.transferred_in,
                totals.transferred_out,
                _format_area(totals.new_area_ha),
                _format_area(totals.lapsed_area_ha),
            ])


def _write_detail_csv(path: Path, details: list[Detail]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([
            "change", "province", "title_id", "old_holder", "new_holder",
            "company_old", "company_new", "area_ha",
        ])
        for row in details:
            writer.writerow([
                row.change,
                row.province,
                row.title_id,
                row.old_holder,
                row.new_holder,
                row.company_old,
                row.company_new,
                _format_area(row.area_ha),
            ])


def _md_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "/").replace("\n", " ").replace("\r", " ")


def _change_counts(details: list[Detail]) -> Counter:
    counts: Counter = Counter()
    for row in details:
        counts[row.change] += 1
    return counts


def _province_counts(details: list[Detail], change: str) -> Counter:
    counts: Counter = Counter()
    for row in details:
        if row.change == change:
            counts[row.province] += 1
    return counts


def _dup_phrase(count: int) -> str:
    noun = "duplicate extra" if count == 1 else "duplicate extras"
    return f"{count} {noun} dropped"


def _top_companies(result: DiffResult, field_name: str, area_name: str, limit: int = 25) -> tuple[list[tuple[tuple[str, str], int, float]], int]:
    rolled: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0.0])
    for (company, ticker, _province), totals in result.companies.items():
        count = getattr(totals, field_name)
        area = getattr(totals, area_name)
        if not count:
            continue
        slot = rolled[(company, ticker)]
        slot[0] += count
        slot[1] += area
    ranked = sorted(
        rolled.items(),
        key=lambda item: (-item[1][0], -item[1][1], item[0][0].casefold(), item[0][1].casefold()),
    )
    top = ranked[:limit]
    return [(key, int(slot[0]), float(slot[1])) for key, slot in top], len(ranked)


def _write_markdown(path: Path, result: DiffResult) -> None:
    counts = _change_counts(result.details)
    unchanged = len(set(result.old.by_key) & set(result.new.by_key)) - counts["transferred"]
    provinces = sorted(set(result.old.raw_rows) | set(result.new.raw_rows))
    new_by_prov = _province_counts(result.details, "new")
    lapsed_by_prov = _province_counts(result.details, "lapsed")
    xfer_by_prov = _province_counts(result.details, "transferred")
    lines: list[str] = []
    lines.append(f"# Claims diff: {result.old.path.stem} to {result.new.path.stem}")
    lines.append("")
    lines.append(
        f"This compares the old snapshot `{result.old.path.name}` with the new snapshot "
        f"`{result.new.path.name}`. One title is one distinct province plus title id. "
        "When the same title appears more than once, the row with the highest objectid is kept "
        "and the extra copies are counted as duplicates."
    )
    lines.append("")
    lines.append(
        "A title is new when it is only in the new snapshot, lapsed when it is only in the old "
        "snapshot, and transferred when it is in both but the holder name changed. "
        "Holder names are compared after case is folded, punctuation and extra spaces are removed, "
        "and a percent tag such as `(100%)` at the end or `(100)` at the start is ignored. "
        "That keeps a restated interest from looking like a sale."
    )
    lines.append("")
    lines.append(
        f"Sample size: the old snapshot has {result.old.unique} distinct titles "
        f"({result.old.rows} rows read, {_dup_phrase(result.old.dupes)}). "
        f"The new snapshot has {result.new.unique} distinct titles "
        f"({result.new.rows} rows read, {_dup_phrase(result.new.dupes)})."
    )
    lines.append("")
    if result.used_holders_old or result.used_holders_new:
        lines.append(
            "Company names come from the holders files (the company and ticker on the first "
            "linked company for that province and holder). A holder with no link keeps its raw name."
        )
        if result.used_holders_old:
            lines.append(
                f"Old holders file matched a company for {result.holders_old_matched} of "
                f"{result.holders_old_total} distinct holders."
            )
        if result.used_holders_new:
            lines.append(
                f"New holders file matched a company for {result.holders_new_matched} of "
                f"{result.holders_new_total} distinct holders."
            )
    else:
        lines.append("No holders files were provided, so the company column is the raw holder name.")
    lines.append("")
    lines.append("## Per-province row counts")
    lines.append("")
    lines.append("| Province | Old rows | New rows | Old distinct | New distinct | Old duplicate extras | New duplicate extras | New | Lapsed | Transferred |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for province in provinces:
        old_distinct = sum(1 for key in result.old.by_key if key[0] == province)
        new_distinct = sum(1 for key in result.new.by_key if key[0] == province)
        lines.append(
            f"| {_md_cell(province)} | {result.old.raw_rows[province]} | {result.new.raw_rows[province]} | "
            f"{old_distinct} | {new_distinct} | {result.old.dup_extras[province]} | {result.new.dup_extras[province]} | "
            f"{new_by_prov[province]} | {lapsed_by_prov[province]} | {xfer_by_prov[province]} |"
        )
    lines.append("")
    lines.append("## Change totals")
    lines.append("")
    lines.append(
        f"Change totals: {counts['new']} new, {counts['lapsed']} lapsed, "
        f"{counts['transferred']} transferred, {unchanged} unchanged."
    )
    lines.append("")
    lines.append("These totals are distinct titles after duplicate rows were removed, not raw table rows.")
    lines.append("")
    _append_top(lines, result, "new", "new_area_ha", "new claims", "New area (ha)")
    _append_top(lines, result, "lapsed", "lapsed_area_ha", "lapsed claims", "Lapsed area (ha)")
    _append_transfers(lines, result)
    lines.append("## Files")
    lines.append("")
    lines.append("The title-by-title list is the detail CSV. This note only groups transfers.")
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_top(lines: list[str], result: DiffResult, field_name: str, area_name: str, label: str, area_label: str) -> None:
    top, total = _top_companies(result, field_name, area_name, 25)
    lines.append(f"## Top 25 companies by {label}")
    lines.append("")
    if not top:
        lines.append(f"No {label}.")
        lines.append("")
        return
    shown = len(top)
    lines.append(
        f"Showing {shown} of {total} companies with {label}, rolled up across provinces "
        f"(25 maximum). Sample size for this list is the {sum(count for _key, count, _area in top)} "
        f"{label} on the companies shown, out of {total} companies that had at least one."
    )
    lines.append("")
    lines.append(f"| Rank | Company | Ticker | {label.capitalize()} | {area_label} |")
    lines.append("| ---: | --- | --- | ---: | ---: |")
    for rank, ((company, ticker), count, area) in enumerate(top, start=1):
        lines.append(
            f"| {rank} | {_md_cell(company)} | {_md_cell(ticker)} | {count} | {_format_area(area)} |"
        )
    lines.append("")


def _append_transfers(lines: list[str], result: DiffResult) -> None:
    lines.append("## Transfers by company pair")
    lines.append("")
    total = sum(result.pair_counts.values())
    if not total:
        lines.append("No transfers.")
        lines.append("")
        return
    pairs = sorted(
        result.pair_counts.items(),
        key=lambda item: (-item[1], item[0][0].casefold(), item[0][2].casefold(), item[0][1].casefold(), item[0][3].casefold()),
    )
    lines.append(
        f"All {total} transfers, grouped into {len(pairs)} company pairs. "
        "Nothing is cut off in this section."
    )
    lines.append("")
    for (company_old, ticker_old, company_new, ticker_new), count in pairs:
        provinces = result.pair_provinces[(company_old, ticker_old, company_new, ticker_new)]
        bits = ", ".join(f"{name} {provinces[name]}" for name in sorted(provinces))
        old_label = _md_cell(company_old) or "(blank)"
        new_label = _md_cell(company_new) or "(blank)"
        old_tick = f" ({_md_cell(ticker_old)})" if ticker_old else ""
        new_tick = f" ({_md_cell(ticker_new)})" if ticker_new else ""
        noun = "title" if count == 1 else "titles"
        lines.append(f"- {old_label}{old_tick} -> {new_label}{new_tick}: {count} {noun} ({bits})")
    lines.append("")


def write_outputs(result: DiffResult, out_dir: Path, fmt: str) -> list[Path]:
    names = _output_names(result.old.path, result.new.path, fmt)
    paths = allocate_outputs(out_dir, names)
    written: list[Path] = []
    index = 0
    if fmt in {"csv", "both"}:
        _write_company_csv(paths[index], result.companies)
        written.append(paths[index])
        index += 1
        _write_detail_csv(paths[index], result.details)
        written.append(paths[index])
        index += 1
    if fmt in {"md", "both"}:
        _write_markdown(paths[index], result)
        written.append(paths[index])
    result.outputs = written
    return written


def print_summary(result: DiffResult) -> None:
    print(
        f"old: {result.old.path} rows={result.old.rows} unique={result.old.unique} dup_extras={result.old.dupes}"
    )
    print(
        f"new: {result.new.path} rows={result.new.rows} unique={result.new.unique} dup_extras={result.new.dupes}"
    )
    counts = _change_counts(result.details)
    new_by_prov = _province_counts(result.details, "new")
    lapsed_by_prov = _province_counts(result.details, "lapsed")
    xfer_by_prov = _province_counts(result.details, "transferred")
    provinces = sorted(
        set(result.old.raw_rows) | set(result.new.raw_rows) | set(new_by_prov) | set(lapsed_by_prov) | set(xfer_by_prov)
    )
    header = (
        f"{'province':<22} {'old_rows':>8} {'old_unique':>10} {'old_dupes':>9} "
        f"{'new_rows':>8} {'new_unique':>10} {'new_dupes':>9} "
        f"{'new':>6} {'lapsed':>7} {'transferred':>12}"
    )
    print(header)
    sum_old_unique = 0
    sum_new_unique = 0
    for province in provinces:
        old_unique = sum(1 for key in result.old.by_key if key[0] == province)
        new_unique = sum(1 for key in result.new.by_key if key[0] == province)
        sum_old_unique += old_unique
        sum_new_unique += new_unique
        print(
            f"{province:<22} {result.old.raw_rows[province]:>8} {old_unique:>10} {result.old.dup_extras[province]:>9} "
            f"{result.new.raw_rows[province]:>8} {new_unique:>10} {result.new.dup_extras[province]:>9} "
            f"{new_by_prov[province]:>6} {lapsed_by_prov[province]:>7} {xfer_by_prov[province]:>12}"
        )
    print(
        f"{'TOTAL':<22} {result.old.rows:>8} {sum_old_unique:>10} {result.old.dupes:>9} "
        f"{result.new.rows:>8} {sum_new_unique:>10} {result.new.dupes:>9} "
        f"{counts['new']:>6} {counts['lapsed']:>7} {counts['transferred']:>12}"
    )
    unchanged = len(set(result.old.by_key) & set(result.new.by_key)) - counts["transferred"]
    print(
        f"changes: new={counts['new']} lapsed={counts['lapsed']} "
        f"transferred={counts['transferred']} unchanged={unchanged}"
    )
    for path in result.outputs:
        print(f"wrote: {path}")


def run(
    old_path: Path,
    new_path: Path,
    out_dir: Path,
    holders_old: Path | None = None,
    holders_new: Path | None = None,
    fmt: str = "both",
) -> DiffResult:
    if fmt not in {"csv", "md", "both"}:
        raise DiffError(f"unknown format {fmt!r}")
    old = load_side(old_path)
    new = load_side(new_path)
    old_index = load_holders(holders_old)
    new_index = load_holders(holders_new)
    result = build_diff(old, new, old_index, new_index)
    write_outputs(result, out_dir, fmt)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diff two read-only claims snapshots into CSV and a plain-English report."
    )
    parser.add_argument("old", type=Path, help="Older claims.sqlite (opened read-only)")
    parser.add_argument("new", type=Path, help="Newer claims.sqlite (opened read-only)")
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory for the report files")
    parser.add_argument("--holders-old", type=Path, default=None, help="holders.json matching the old snapshot")
    parser.add_argument("--holders-new", type=Path, default=None, help="holders.json matching the new snapshot")
    parser.add_argument("--format", choices=("csv", "md", "both"), default="both")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        result = run(
            args.old,
            args.new,
            args.out_dir,
            holders_old=args.holders_old,
            holders_new=args.holders_new,
            fmt=args.format,
        )
    except DiffError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print_summary(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
