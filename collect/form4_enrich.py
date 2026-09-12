#!/usr/bin/env python3
"""Collect Form 4 10b5-1 / plan footnotes and Table I end holdings.

Reads insider-trades-lite.json (origin=form4 source URLs), fetches each
unique EDGAR ownership XML once, and writes insider-form4.json.

Stdlib only. SEC fair-access: identify the requester in User-Agent
(company name + contact email) and stay under 10 requests/second.
See AGENTS.md / README. Does not touch Senate eFD.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
TAPE = ROOT / "insider-trades-lite.json"
DEST = ROOT / "insider-form4.json"

# SEC.gov rejects Mozilla-style UAs ("undeclared automated tool").
# Fair-access wants a human-readable name and a contact email.
SEC_UA = "Quantity Capital gordojr@proton.me"
SEC_SLEEP = 0.12  # ~8 req/s; SEC cap is 10/s
SEC_RETRIES = 4

PLAN_POS = re.compile(
    r"10\s*b5\s*[-–—]?\s*1|rule\s*10b5|trading plan|written plan|"
    r"sell(?:ing)? plan|purchase plan|pre-?arranged",
    re.I,
)
PLAN_NEG = re.compile(
    r"\bnot\b.{0,40}pursuant|\bnot\b.{0,40}10\s*b5|does not (?:constitute|represent)",
    re.I,
)
COVER_RE = re.compile(
    r"sell to cover|sell-to-cover|tax withhold|withholding obligation|"
    r"to (?:satisfy|cover|funds*satisfy).{0,40}tax|net settle",
    re.I,
)
ID_ACCN = re.compile(r"^form4-(\d{10}-\d{2}-\d{6})-")
ACCN_DASH = re.compile(r"^\d{10}-\d{2}-\d{6}$")


def local(tag: str) -> str:
    if not tag:
        return ""
    return tag.rsplit("}", 1)[-1]


def child(el, name: str):
    if el is None:
        return None
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def children(el, name: str):
    if el is None:
        return []
    return [c for c in el if local(c.tag) == name]


def text_of(el) -> str:
    if el is None:
        return ""
    v = child(el, "value")
    if v is not None and (v.text or "").strip():
        return v.text.strip()
    if el.text and el.text.strip():
        return el.text.strip()
    return ""


def find_first(el, name: str):
    if el is None:
        return None
    if local(el.tag) == name:
        return el
    for c in el:
        hit = find_first(c, name)
        if hit is not None:
            return hit
    return None


def footnote_ids(el) -> list[str]:
    ids: list[str] = []
    if el is None:
        return ids
    if local(el.tag) == "footnoteId":
        fid = el.get("id")
        if fid:
            ids.append(fid)
    for c in el:
        ids.extend(footnote_ids(c))
    return ids


def parse_num(raw) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def truthy(raw) -> bool:
    s = str(raw or "").strip().lower()
    return s in {"1", "true", "yes", "y"}


def accession_from_url(url: str) -> str:
    path = urlparse(url or "").path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return ""
    raw = parts[-2]
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 18:
        return f"{digits[:10]}-{digits[10:12]}-{digits[12:18]}"
    return raw


def canonical_form4_url(url: str, accn: str = "") -> str:
    """Rebuild /Archives/edgar/data/{cik}/{accn}/{file} when the tape smashed CIK+accession."""
    parsed = urlparse(url or "")
    path = parsed.path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    filename = parts[-1] if parts else "primary_doc.xml"
    accn = accn or accession_from_url(url)
    digits = re.sub(r"\D", "", accn)
    if len(digits) < 18:
        return url
    cik = digits[:10].lstrip("0") or "0"
    accn_nodash = digits[:18]
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{filename}"


def accession_from_trade_id(trade_id: str) -> str:
    m = ID_ACCN.match(trade_id or "")
    return m.group(1) if m else ""


def classify_text(text: str) -> str | None:
    """Return '10b5-1', 'cover', or None."""
    if not text or not text.strip():
        return None
    if PLAN_NEG.search(text):
        if COVER_RE.search(text) and not PLAN_POS.search(text):
            return "cover"
        return None
    cover = bool(COVER_RE.search(text))
    plan = bool(PLAN_POS.search(text))
    if plan:
        return "10b5-1"
    if cover:
        return "cover"
    return None


def classify_notes(texts: list[str], aff: bool) -> tuple[bool, str]:
    """(is_scheduled_plan, why). Cover is not a scheduled plan."""
    kinds = [classify_text(t) for t in texts if t]
    if "10b5-1" in kinds:
        return True, "10b5-1"
    if "cover" in kinds:
        return False, "cover"
    if aff:
        return True, "aff10b5One"
    return False, ""


def parse_ownership_xml(xml_text: str, url: str = "") -> dict:
    root = ET.fromstring(xml_text)
    if local(root.tag) != "ownershipDocument":
        raise ValueError("not an ownershipDocument")

    footnotes: dict[str, str] = {}
    fn_block = find_first(root, "footnotes")
    for fn in children(fn_block if fn_block is not None else root, "footnote"):
        fid = fn.get("id") or ""
        if fid:
            footnotes[fid] = "".join(fn.itertext()).strip()

    remarks = text_of(find_first(root, "remarks"))
    aff = truthy(text_of(find_first(root, "aff10b5One")))
    issuer = find_first(root, "issuer")
    owner = find_first(root, "reportingOwnerId")
    sig = find_first(root, "ownerSignature")
    period = text_of(find_first(root, "periodOfReport"))
    filed = text_of(child(sig, "signatureDate")) or period

    table = find_first(root, "nonDerivativeTable")
    txns: list[dict] = []
    holdings: list[dict] = []

    def own_flag(node) -> str:
        nature = child(node, "ownershipNature")
        if nature is None:
            nature = node
        raw = text_of(find_first(nature, "directOrIndirectOwnership")).upper()
        return "I" if raw.startswith("I") else "D"

    for node in children(table, "nonDerivativeTransaction"):
        code = text_of(find_first(child(node, "transactionCoding"), "transactionCode")).upper()
        shares = parse_num(text_of(find_first(node, "transactionShares")))
        price = parse_num(text_of(find_first(node, "transactionPricePerShare")))
        after = parse_num(text_of(find_first(node, "sharesOwnedFollowingTransaction")))
        acquired = text_of(find_first(node, "transactionAcquiredDisposedCode")).upper()
        date = text_of(find_first(node, "transactionDate"))
        title = text_of(find_first(node, "securityTitle"))
        ids = footnote_ids(node)
        notes = [footnotes[i] for i in ids if i in footnotes]
        if remarks:
            notes.append(remarks)
        plan, why = classify_notes(notes, aff)
        txns.append({
            "date": date,
            "code": code,
            "shares": shares,
            "price": price,
            "after": after,
            "acquired": acquired,
            "title": title,
            "own": own_flag(node),
            "plan": plan,
            "why": why,
        })

    for node in children(table, "nonDerivativeHolding"):
        after = parse_num(text_of(find_first(node, "sharesOwnedFollowingTransaction")))
        holdings.append({
            "title": text_of(find_first(node, "securityTitle")),
            "after": after,
            "own": own_flag(node),
        })

    filing_plan = any(t.get("plan") for t in txns) or (
        aff and classify_text(" ".join(footnotes.values()) + " " + remarks) == "10b5-1"
    )
    return {
        "url": url,
        "accn": accession_from_url(url),
        "symbol": text_of(child(issuer, "issuerTradingSymbol")),
        "issuer": text_of(child(issuer, "issuerName")),
        "owner": text_of(child(owner, "rptOwnerName")),
        "cik": text_of(child(owner, "rptOwnerCik")),
        "period": period,
        "filed": filed,
        "aff": aff,
        "plan": filing_plan,
        "tx": txns,
        "hold": holdings,
    }


def vs_stake(shares: float | None, after: float | None, acquired: bool) -> float | None:
    """Size versus remaining (end) stake. Buys use after; sales use prior."""
    if shares is None or shares < 0 or after is None or after < 0:
        return None
    if acquired:
        if after <= 0:
            return None
        return shares / after
    prior = after + shares
    if prior <= 0:
        return 1.0 if shares else None
    return shares / prior


def compact_filing(parsed: dict) -> dict:
    out = {
        "url": parsed.get("url") or "",
        "symbol": parsed.get("symbol") or "",
        "owner": parsed.get("owner") or "",
        "filed": parsed.get("filed") or "",
        "aff": bool(parsed.get("aff")),
        "plan": bool(parsed.get("plan")),
        "tx": [],
        "hold": [],
    }
    for t in parsed.get("tx") or []:
        row = {
            "d": t.get("date") or "",
            "c": t.get("code") or "",
            "s": t.get("shares"),
            "a": t.get("after"),
            "p": 1 if t.get("plan") else 0,
        }
        if t.get("why"):
            row["w"] = t["why"]
        if t.get("own"):
            row["o"] = t["own"]
        if t.get("acquired"):
            row["ad"] = t["acquired"]
        out["tx"].append(row)
    for h in parsed.get("hold") or []:
        if h.get("after") is None and not h.get("title"):
            continue
        hold = {"a": h.get("after")}
        if h.get("title"):
            hold["t"] = h["title"]
        if h.get("own"):
            hold["o"] = h["own"]
        out["hold"].append(hold)
    return out


def expand_tx(row: dict) -> dict:
    acquired = str(row.get("ad") or "").upper() == "A"
    shares = row.get("s")
    after = row.get("a")
    return {
        "date": row.get("d") or "",
        "code": row.get("c") or "",
        "shares": shares,
        "after": after,
        "plan": bool(row.get("p")),
        "why": row.get("w") or "",
        "own": row.get("o") or "",
        "acquired": "A" if acquired else str(row.get("ad") or ""),
        "vs": vs_stake(shares, after, acquired),
    }


def match_trade(trade: dict, filing: dict) -> dict | None:
    txs = [expand_tx(x) for x in (filing.get("tx") or [])]
    date = str(trade.get("trade_date") or "")[:10]
    code = str(trade.get("code") or "").upper()
    shares = parse_num(trade.get("shares"))
    best = None
    best_gap = 1e18
    for tx in txs:
        if date and tx["date"] and tx["date"] != date:
            continue
        if code and tx["code"] and tx["code"] != code:
            continue
        if shares is not None and tx["shares"] is not None:
            gap = abs(tx["shares"] - shares)
            if gap > max(0.51, shares * 0.001):
                continue
        else:
            gap = 0 if shares is None or tx["shares"] is None else 1e9
        if gap < best_gap:
            best, best_gap = tx, gap
    return best


def trade_overlay(trade: dict, filing: dict, tx: dict | None) -> dict:
    acquired = (trade.get("side") == "purchase") or str((tx or {}).get("acquired") or "") == "A"
    shares = parse_num(trade.get("shares"))
    after = (tx or {}).get("after")
    if after is None:
        after = parse_num(trade.get("shares_after"))
    plan = bool((tx or {}).get("plan") or filing.get("plan") and tx is None)
    why = (tx or {}).get("why") or ("10b5-1" if filing.get("plan") and plan else "")
    # Filing-level plan with a matched non-plan txn (cover) stays non-plan.
    if tx is not None:
        plan = bool(tx.get("plan"))
        why = tx.get("why") or ""
    if shares is None and tx is not None:
        shares = parse_num(tx.get("shares"))
    vs = vs_stake(shares, after, acquired)
    hold_sum = 0.0
    hold_n = 0
    for h in filing.get("hold") or []:
        val = h.get("a")
        if val is not None:
            hold_sum += float(val)
            hold_n += 1
    if tx and tx.get("after") is not None:
        hold_sum += float(tx["after"])
        hold_n += 1
    # Table I end holdings for this security. Alias names the table agent reads.
    shares_held = after
    pct_held = parse_num(trade.get("pct_held"))
    if pct_held is None:
        pct_held = parse_num(trade.get("held_pct"))
    out = {
        "accn": filing.get("accn") or accession_from_url(filing.get("url") or ""),
        "plan": plan,
        "why": why,
        "shares": shares,
        "after": after,
        "shares_held": shares_held,
        "pct_held": None if pct_held is None else round(pct_held, 6),
        "vs": None if vs is None else round(vs, 4),
        "held": None if hold_n == 0 else round(hold_sum, 4),
        "aff": bool(filing.get("aff")),
    }
    if tx and tx.get("own"):
        out["own"] = tx["own"]
    return out


def load_json(path: Path):
    with path.open() as f:
        return json.load(f)


def form4_urls(trades: list[dict]) -> dict[str, str]:
    """accession -> first source URL."""
    out: dict[str, str] = {}
    for t in trades:
        if str(t.get("origin") or "").lower() != "form4":
            continue
        url = str(t.get("source") or "")
        if "sec.gov" not in url or not url.endswith(".xml"):
            continue
        accn = accession_from_url(url) or accession_from_trade_id(t.get("id") or "")
        if accn and accn not in out:
            out[accn] = canonical_form4_url(url, accn)
    return out


def sec_request(url: str, ua: str, timeout: float = 30.0) -> bytes:
    # Prefer www.sec.gov Archives paths; data.sec.gov also serves the same files.
    headers = {
        "User-Agent": ua,
        "Accept": "application/xml,text/xml,*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def filing_dir(url: str) -> str:
    canon = canonical_form4_url(url)
    if "/" not in canon:
        return ""
    return canon.rsplit("/", 1)[0]


def filing_url_candidates(url: str) -> list[str]:
    canon = canonical_form4_url(url)
    seen = []
    for u in (canon, url):
        if u and u not in seen:
            seen.append(u)
    # Some issuers name the XML oddly; primary_doc.xml is the ownership form.
    base = filing_dir(canon)
    if base:
        for name in ("primary_doc.xml", "form4.xml"):
            alt = base + "/" + name
            if alt not in seen:
                seen.append(alt)
    return seen


def xmls_from_index(index: dict, base: str) -> list[str]:
    items = ((index.get("directory") or {}).get("item")) or []
    if isinstance(items, dict):
        items = [items]
    out = []
    for item in items:
        name = str((item or {}).get("name") or "")
        if name.lower().endswith(".xml") and "index" not in name.lower():
            out.append(base.rstrip("/") + "/" + name)
    return out


def resolve_from_index(url: str, ua: str) -> list[str]:
    base = filing_dir(url)
    if not base:
        return []
    raw = sec_request(base + "/index.json", ua)
    try:
        index = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    return xmls_from_index(index, base)


def fetch_bytes(url: str, ua: str, sleep_s: float) -> bytes:
    last_err: Exception | None = None
    delay = sleep_s
    for attempt in range(SEC_RETRIES):
        try:
            raw = sec_request(url, ua)
            time.sleep(sleep_s)
            return raw
        except urllib.error.HTTPError as err:
            last_err = err
            if err.code == 404:
                raise
            if err.code in {403, 429, 500, 502, 503}:
                time.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as err:
            last_err = err
            time.sleep(delay)
            delay = min(delay * 2, 8.0)
    raise RuntimeError(f"fetch failed {url}: {last_err}")


def efts_ciks(accn: str, ua: str) -> list[str]:
    """Issuer / owner CIKs for an accession. Filing-agent accessions live under these."""
    if not accn:
        return []
    q = urllib.parse.quote(f'"{accn}"')
    url = f"https://efts.sec.gov/LATEST/search-index?q={q}&forms=4"
    raw = sec_request(url, ua)
    data = json.loads(raw.decode("utf-8", errors="replace"))
    out: list[str] = []
    for hit in ((data.get("hits") or {}).get("hits") or []):
        for cik in ((hit.get("_source") or {}).get("ciks") or []):
            c = str(cik).lstrip("0") or "0"
            if c not in out:
                out.append(c)
    return out


def urls_under_ciks(accn: str, ciks: list[str], filename: str) -> list[str]:
    digits = re.sub(r"\D", "", accn)
    if len(digits) < 18:
        return []
    accn_nodash = digits[:18]
    name = filename or "primary_doc.xml"
    out = []
    for cik in ciks:
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}"
        for fn in (name, "primary_doc.xml", "form4.xml"):
            u = f"{base}/{fn}"
            if u not in out:
                out.append(u)
    return out


def fetch_filing(url: str, ua: str, sleep_s: float) -> dict:
    last_err: Exception | None = None
    tried = []
    candidates = filing_url_candidates(url)
    idx = 0
    efts_tried = False
    while True:
        if idx >= len(candidates):
            if efts_tried:
                break
            # Filing-agent accessions (e.g. 0001062993-*) live under the issuer CIK.
            efts_tried = True
            try:
                accn = accession_from_url(url)
                filename = (urlparse(url).path.rstrip("/").split("/") or [""])[-1]
                for extra in urls_under_ciks(accn, efts_ciks(accn, ua), filename):
                    if extra not in candidates:
                        candidates.append(extra)
                time.sleep(sleep_s)
            except Exception as efts_err:
                last_err = efts_err
            continue
        candidate = candidates[idx]
        idx += 1
        if candidate in tried:
            continue
        tried.append(candidate)
        try:
            raw = fetch_bytes(candidate, ua, sleep_s)
            text = raw.decode("utf-8", errors="replace")
            return parse_ownership_xml(text, candidate)
        except urllib.error.HTTPError as err:
            last_err = err
            if err.code == 404 and "/index.json" not in candidate:
                try:
                    for extra in resolve_from_index(candidate, ua):
                        if extra not in candidates:
                            candidates.append(extra)
                except Exception as idx_err:
                    last_err = idx_err
                continue
            if err.code == 404:
                continue
            raise
        except (ET.ParseError, ValueError, RuntimeError) as err:
            last_err = err
            continue
    raise RuntimeError(f"fetch failed {url}: {last_err}")


def rollup_filers(trades: list[dict], overlays: dict[str, dict]) -> dict[str, dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        if str(t.get("origin") or "").lower() != "form4":
            continue
        fid = t.get("filer_id") or ""
        if not fid:
            continue
        ov = overlays.get(t.get("id") or "")
        shares = parse_num(t.get("shares"))
        after = (ov or {}).get("after")
        if after is None:
            after = parse_num(t.get("shares_after"))
        acquired = t.get("side") == "purchase"
        vs = (ov or {}).get("vs")
        if vs is None:
            vs = vs_stake(shares, after, acquired)
        buckets[fid].append({
            "name": t.get("filer") or fid,
            "ticker": t.get("ticker") or "",
            "title": t.get("title") or "",
            "filed": str(t.get("filed_date") or "")[:10],
            "side": t.get("side") or "",
            "code": str(t.get("code") or "").upper(),
            "plan": bool((ov or {}).get("plan")),
            "why": (ov or {}).get("why") or "",
            "after": after,
            "held": (ov or {}).get("held"),
            "vs": vs,
            "aff": bool((ov or {}).get("aff")),
        })

    out: dict[str, dict] = {}
    for fid, rows in buckets.items():
        rows.sort(key=lambda r: r.get("filed") or "")
        latest = rows[-1]
        market = [
            r for r in rows
            if r["code"] in {"P", "S"} or r["side"] in {"purchase", "sale", "sale_post"}
        ]
        plan_n = sum(1 for r in market if r["plan"])
        disc_n = len(market) - plan_n
        conv_vals = [
            r["vs"] for r in market
            if not r["plan"] and r.get("vs") is not None and r["side"] == "purchase"
        ]
        if not conv_vals:
            conv_vals = [r["vs"] for r in market if not r["plan"] and r.get("vs") is not None]
        out[fid] = {
            "name": latest.get("name") or fid,
            "ticker": latest.get("ticker") or "",
            "title": latest.get("title") or "",
            "prints": len(rows),
            "market": len(market),
            "plan": plan_n,
            "disc": disc_n,
            "planShare": round(plan_n / len(market), 4) if market else 0.0,
            "conv": None if not conv_vals else round(float(median(conv_vals)), 4),
            "after": latest.get("after"),
            "held": latest.get("held"),
            "lastFiled": latest.get("filed") or "",
            "lastWhy": latest.get("why") or "",
            "lastPlan": bool(latest.get("plan")),
        }
    return out


def checkpoint_filings(dest: Path, prev: dict, filings: dict[str, dict]) -> None:
    """Write filings mid-run so a kill does not lose the night."""
    payload = dict(prev or {})
    payload["filings"] = filings
    payload["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, separators=(",", ":"))
        f.write("\n")
    tmp.replace(dest)


def build_payload(
    tape: dict,
    filings: dict[str, dict],
    overlays: dict[str, dict],
    filers: dict[str, dict],
    fetched: int,
    skipped: int,
    failed: list[str],
    ua: str,
) -> dict:
    plan_trades = sum(1 for v in overlays.values() if v.get("plan"))
    cover_trades = sum(1 for v in overlays.values() if v.get("why") == "cover")
    with_after = sum(1 for v in overlays.values() if v.get("shares_held") is not None or v.get("after") is not None)
    with_pct = sum(1 for v in overlays.values() if v.get("pct_held") is not None)
    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00",
        "tapeCollected": tape.get("collected") or "",
        "source": "SEC EDGAR Form 4 ownership XML (Table I + aff10b5One / footnotes)",
        "userAgent": ua,
        "rateLimit": f"{SEC_SLEEP:.2f}s between requests (~{1 / SEC_SLEEP:.0f}/s; SEC cap 10/s)",
        "method": (
            "Fetch each unique Form 4 XML from the tape source URL. "
            "A print is a scheduled plan when a footnote (or remarks) names Rule 10b5-1 / "
            "a trading plan, or the filing-level aff10b5One box is set and the lot is not "
            "a sell-to-cover / tax-withholding. Table I end holdings are "
            "sharesOwnedFollowingTransaction plus nonDerivativeHolding rows. "
            "Each tape overlay carries transaction shares, shares_held (Table I end), "
            "and pct_held when the tape already has held_pct (Form 4 XML has no "
            "shares-outstanding field). Conviction is trade size ÷ remaining stake "
            "(sharesAfter on buys, sharesAfter+shares on sales)."
        ),
        "disclaimer": "Not investment advice. Plan flags are parsed from public Form 4 text.",
        "stats": {
            "filings": len(filings),
            "trades": len(overlays),
            "filers": len(filers),
            "planTrades": plan_trades,
            "coverTrades": cover_trades,
            "withAfter": with_after,
            "withPctHeld": with_pct,
            "fetched": fetched,
            "cached": skipped,
            "failed": len(failed),
        },
        "failed": failed,
        "filings": filings,
        "trades": overlays,
        "filers": filers,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tape", type=Path, default=TAPE)
    p.add_argument("--dest", type=Path, default=DEST)
    p.add_argument("--prev", type=Path, default=None, help="Previous sidecar (default: dest)")
    p.add_argument("--ua", default=SEC_UA, help="SEC User-Agent (name + email)")
    p.add_argument("--sleep", type=float, default=SEC_SLEEP, help="Seconds between SEC requests")
    p.add_argument("--limit", type=int, default=0, help="Max new filings to fetch (0 = all)")
    p.add_argument("--refresh", action="store_true", help="Re-fetch accessions already in dest")
    p.add_argument("--offline", action="store_true", help="Do not hit the network; rematch cache")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.tape.is_file():
        print(f"missing {args.tape}", file=sys.stderr)
        return 1
    tape = load_json(args.tape)
    trades = tape.get("trades") or []
    wanted = form4_urls(trades)
    prev_path = args.prev or args.dest
    prev = load_json(prev_path) if prev_path.is_file() else {}
    filings = dict(prev.get("filings") or {}) if not args.refresh else {}
    if args.refresh:
        filings = {}

    to_fetch = []
    for accn, url in wanted.items():
        if accn in filings and not args.refresh:
            # Keep URL current if the tape renamed the xml file.
            if url and not filings[accn].get("url"):
                filings[accn]["url"] = url
            continue
        to_fetch.append((accn, url))
    if args.limit:
        to_fetch = to_fetch[: args.limit]

    fetched = 0
    failed: list[str] = []
    if args.offline:
        to_fetch = []
    total = len(to_fetch)
    if total:
        print(f"fetching {total} Form 4s (cached {len(filings)}) UA={args.ua!r}", flush=True)
    for accn, url in to_fetch:
        try:
            parsed = fetch_filing(url, args.ua, args.sleep)
            parsed["accn"] = accn
            filings[accn] = compact_filing(parsed)
            filings[accn]["accn"] = accn
            fetched += 1
            if fetched % 50 == 0 or fetched == total:
                print(f"  fetched {fetched}/{total}", flush=True)
            if fetched % 50 == 0:
                checkpoint_filings(args.dest, prev, filings)
        except Exception as err:
            failed.append(f"{accn} {err}")
            print(f"warn {accn}: {err}", file=sys.stderr)

    overlays: dict[str, dict] = {}
    for t in trades:
        if str(t.get("origin") or "").lower() != "form4":
            continue
        tid = t.get("id") or ""
        accn = accession_from_trade_id(tid) or accession_from_url(t.get("source") or "")
        filing = filings.get(accn)
        if not filing:
            continue
        filing = dict(filing)
        filing["accn"] = accn
        tx = match_trade(t, filing)
        overlays[tid] = trade_overlay(t, filing, tx)

    filers = rollup_filers(trades, overlays)
    skipped = max(0, len(wanted) - fetched - len([a for a, _ in to_fetch if a not in filings]))
    cached = len(wanted) - fetched - len([a for a, _ in to_fetch])
    if cached < 0:
        cached = len(filings) - fetched
    payload = build_payload(
        tape, filings, overlays, filers, fetched, max(0, cached), failed, args.ua
    )
    args.dest.parent.mkdir(parents=True, exist_ok=True)
    with args.dest.open("w") as f:
        json.dump(payload, f, separators=(",", ":"))
        f.write("\n")
    st = payload["stats"]
    print(
        "wrote {path} ({kb:.1f} KB)  filings={f}  trades={t}  filers={p}  "
        "plan={plan}  cover={cover}  fetched={n}  failed={x}".format(
            path=args.dest.name,
            kb=args.dest.stat().st_size / 1024,
            f=st["filings"],
            t=st["trades"],
            p=st["filers"],
            plan=st["planTrades"],
            cover=st["coverTrades"],
            n=st["fetched"],
            x=st["failed"],
        )
    )
    return 0 if not failed or fetched or overlays else 1


if __name__ == "__main__":
    raise SystemExit(main())
