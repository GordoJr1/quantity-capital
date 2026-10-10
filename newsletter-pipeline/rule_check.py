#!/usr/bin/env python3
"""Rule check for a Daily Brief and an Executive Brief. Runs before Reviewer Bot.

Checks, in order: 20 words per sentence, locked-template match, links,
repeats across the two briefs, and a source id on every claim.
A failed check exits 1 and prints DO NOT CALL REVIEWER BOT.
This script never messages Reviewer Bot and never sends mail.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
MAX_WORDS = 20
BOT_BLOCK_HOSTS = (
    "bls.gov",
    "www.bls.gov",
    "data.bls.gov",
    "sec.gov",
    "www.sec.gov",
    "edgar.sec.gov",
)
STYLE_RE = re.compile(r"(?is)<style\b.*?</style>")
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)

DAILY_PROSE = ("headline", "credit", "what", "how", "why")
NEWS_PROSE = ("title", "body", "link_label")
BIG3_PROSE = ("headline", "what_happened", "why_it_matters", "link_label")
PROJECT_PROSE = ("name", "place_line", "summary", "size", "cost", "power", "who", "jobs", "timing", "link_label")
HEADLINE_PROSE = ("title", "before", "after", "detail", "updated", "link_label", "badge")
CARD_PROSE = ("name", "explainer", "note", "this_week", "score", "unit", "status", "category")


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"FAIL json: {path} root must be an object")
    return data


def split_sentences(text: str) -> list[str]:
    text = text.replace("\u00a0", " ").replace("`", "")
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    protected = re.sub(r"\bU\.S\.", "U<dot>S<dot>", text)
    protected = re.sub(r"(\d)\.(\d)", r"\1<dot>\2", protected)
    protected = re.sub(
        r"\b(No|Mr|Mrs|Ms|Dr|St|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.",
        r"\1<dot>",
        protected,
    )
    chunks = re.split(r"(?<=[.!?])\s+", protected.strip())
    out = []
    for chunk in chunks:
        sentence = chunk.replace("<dot>", ".").strip()
        if sentence:
            out.append(sentence)
    return out


def word_count(sentence: str) -> int:
    count = 0
    for raw in sentence.split():
        token = raw.strip(".,;:!?()[]\"“”‘’")
        if token:
            count += 1
    return count


def norm(text: str) -> str:
    text = text.lower()
    text = text.replace("\u00a0", " ").replace("\u2011", "-").replace("\u2010", "-")
    text = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def check_words(label: str, text: str, errors: list[str]) -> None:
    if not isinstance(text, str) or not text.strip():
        return
    for nth, sentence in enumerate(split_sentences(text), start=1):
        count = word_count(sentence)
        if count > MAX_WORDS:
            errors.append(
                f"FAIL words: {label} sentence {nth} has {count} words (max {MAX_WORDS}): {sentence}"
            )


def require_source(label: str, item: dict, links: list[str], errors: list[str]) -> None:
    source = item.get("source_id")
    if not isinstance(source, str) or not source.strip():
        errors.append(f"FAIL source: {label} has no source_id")
        return
    if not re.match(r"https?://", source):
        errors.append(f"FAIL source: {label} source_id is not an http(s) URL")
        return
    if source not in links:
        errors.append(f"FAIL source: {label} source_id is not one of its links")


def prose_of(item: dict, fields: tuple[str, ...], label: str, errors: list[str]) -> list[str]:
    found = []
    for field in fields:
        if field not in item:
            continue
        value = item[field]
        if not isinstance(value, str):
            errors.append(f"FAIL words: {label}.{field} is not text")
            continue
        check_words(f"{label}.{field}", value, errors)
        found.append(value)
    return found


def walk_daily(data: dict, errors: list[str]) -> tuple[list[str], list[str]]:
    sentences: list[str] = []
    links: list[str] = []
    tips = data.get("tips")
    if not isinstance(tips, list) or len(tips) != 5:
        errors.append("FAIL template: daily JSON needs exactly 5 tips")
        tips = tips if isinstance(tips, list) else []
    for i, tip in enumerate(tips):
        if not isinstance(tip, dict):
            errors.append(f"FAIL source: tips[{i}] is not an object")
            continue
        label = f"tips[{i}]"
        sentences.extend(prose_of(tip, DAILY_PROSE, label, errors))
        claim_links = [tip.get("read_url", ""), tip.get("post_url", "")]
        links.extend(u for u in claim_links if isinstance(u, str))
        require_source(label, tip, [u for u in claim_links if isinstance(u, str)], errors)
    news = data.get("news") if isinstance(data.get("news"), dict) else {}
    for section in ("ai", "tool", "jev"):
        items = news.get(section, [])
        if not isinstance(items, list):
            errors.append(f"FAIL template: news.{section} must be a list")
            continue
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"FAIL source: news.{section}[{i}] is not an object")
                continue
            label = f"news.{section}[{i}]"
            sentences.extend(prose_of(item, NEWS_PROSE, label, errors))
            url = item.get("link_url", "")
            if isinstance(url, str):
                links.append(url)
            require_source(label, item, [url] if isinstance(url, str) else [], errors)
    return sentences, links


def card_sentences(card: dict, label: str, errors: list[str]) -> list[str]:
    found = prose_of(card, CARD_PROSE, label, errors)
    rows = card.get("rows") if isinstance(card.get("rows"), list) else []
    for i, row in enumerate(rows):
        if isinstance(row, dict):
            found.extend(prose_of(row, ("name", "score"), f"{label}.rows[{i}]", errors))
    return found


def walk_exec(data: dict, errors: list[str]) -> tuple[list[str], list[str]]:
    sentences: list[str] = []
    links: list[str] = []
    for key, fields in (("big3", BIG3_PROSE), ("projects", PROJECT_PROSE)):
        items = data.get(key)
        if not isinstance(items, list):
            errors.append(f"FAIL template: exec JSON missing {key}")
            continue
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"FAIL source: {key}[{i}] is not an object")
                continue
            label = f"{key}[{i}]"
            sentences.extend(prose_of(item, fields, label, errors))
            url = item.get("link_url", "")
            if isinstance(url, str):
                links.append(url)
            require_source(label, item, [url] if isinstance(url, str) else [], errors)
    headline = data.get("headline") if isinstance(data.get("headline"), dict) else None
    if headline is None:
        errors.append("FAIL template: exec JSON missing headline")
    else:
        sentences.extend(prose_of(headline, HEADLINE_PROSE, "headline", errors))
        url = headline.get("link_url", "")
        if isinstance(url, str):
            links.append(url)
        require_source("headline", headline, [url] if isinstance(url, str) else [], errors)
    intro = data.get("scoreboard", {}).get("intro") if isinstance(data.get("scoreboard"), dict) else None
    if isinstance(intro, str):
        check_words("scoreboard.intro", intro, errors)
        sentences.append(intro)
    cards = data.get("cards")
    if not isinstance(cards, list) or len(cards) != 6:
        errors.append("FAIL template: exec JSON needs 6 scoreboard cards")
        cards = cards if isinstance(cards, list) else []
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            errors.append(f"FAIL source: cards[{i}] is not an object")
            continue
        label = f"cards[{i}]"
        sentences.extend(card_sentences(card, label, errors))
        url = card.get("link_url", "")
        if isinstance(url, str):
            links.append(url)
        require_source(label, card, [url] if isinstance(url, str) else [], errors)
    return sentences, links


def check_repeats(daily_bits: list[str], exec_bits: list[str], daily_links: list[str], exec_links: list[str], errors: list[str]) -> None:
    daily_sentences = {}
    for text in daily_bits:
        for sentence in split_sentences(text):
            key = norm(sentence)
            if word_count(sentence) >= 6:
                daily_sentences.setdefault(key, sentence)
    for text in exec_bits:
        for sentence in split_sentences(text):
            key = norm(sentence)
            if key in daily_sentences and word_count(sentence) >= 6:
                errors.append("FAIL repeats: same sentence in both briefs: " + sentence)
                daily_sentences.pop(key, None)
    shared = sorted(set(daily_links) & set(exec_links))
    for url in shared:
        errors.append("FAIL repeats: same link in both briefs: " + url)


def design_for(product: str) -> dict:
    path = TEMPLATES / f"{product}-design.json"
    return json.loads(path.read_text(encoding="utf-8"))


SLOT_RE = re.compile(r"\{\{[A-Za-z0-9_.]+\}\}")


def check_template(product: str, html: str, errors: list[str]) -> None:
    leftover = SLOT_RE.findall(html)
    if leftover:
        errors.append(f"FAIL template: {product} HTML still has slots: " + ", ".join(leftover[:8]))
    design = design_for(product)
    required = list(design.get("required_substrings", []))
    if product == "exec":
        layout = "big-story" if "<!-- layout: big-story -->" in html or 'data-layout="big-story"' in html else "standard"
        required.extend(design.get("layouts", {}).get(layout, {}).get("required_substrings", []))
        for banned in design.get("forbidden_substrings", []):
            if banned in html:
                errors.append(f"FAIL template: forbidden text present: {banned}")
    missing = [item for item in required if item not in html]
    if missing:
        shown = "; ".join(missing[:8])
        errors.append(f"FAIL template: {product} missing locked markers: {shown}")
    locked = TEMPLATES / ("daily-locked.html" if product == "daily" else "exec-locked.html")
    if product == "exec" and "<!-- layout: big-story -->" in html:
        locked = TEMPLATES / "exec-bigstory-template.html"
    if locked.is_file():
        shell = locked.read_text(encoding="utf-8")
        shell_style = STYLE_RE.search(shell)
        page_style = STYLE_RE.search(html)
        if not shell_style or not page_style or shell_style.group(0) != page_style.group(0):
            errors.append(f"FAIL template: {product} <style> does not match the locked shell")
    markers = TEMPLATES / f"{product}-design-markers.txt"
    if markers.is_file():
        absent = [line for line in markers.read_text(encoding="utf-8").splitlines() if line and line not in html]
        if absent:
            errors.append(f"FAIL template: {product} missing design markers: " + "; ".join(absent[:8]))


def html_links(html: str) -> set[str]:
    found = set()
    for href in HREF_RE.findall(html):
        if href.startswith("http://") or href.startswith("https://"):
            found.add(href)
    return found


def check_link_presence(label: str, links: list[str], html: str, errors: list[str]) -> list[str]:
    present = html_links(html)
    clean = []
    for url in links:
        if not isinstance(url, str) or not re.match(r"https?://\S+$", url):
            errors.append(f"FAIL links: {label} has a bad URL: {url!r}")
            continue
        if url not in present:
            errors.append(f"FAIL links: {label} URL is not in the HTML: {url}")
        clean.append(url)
    return clean


def host_of(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")


def bot_blocked(host: str) -> bool:
    for blocked in BOT_BLOCK_HOSTS:
        if host == blocked or host.endswith("." + blocked):
            return True
    return False


def probe(url: str, timeout: float) -> tuple[str, int | None, str]:
    headers = {"User-Agent": "Quantity Capital gordojr@proton.me"}
    last = (url, None, "error")
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = int(getattr(resp, "status", None) or resp.getcode())
                if method == "GET":
                    resp.read(256)
                return url, code, ""
        except urllib.error.HTTPError as exc:
            last = (url, int(exc.code), f"HTTP {exc.code}")
            if method == "HEAD":
                continue
            return last
        except Exception as exc:
            last = (url, None, type(exc).__name__)
            if method == "HEAD":
                continue
            return last
    return last


def check_live(urls: list[str], timeout: float, errors: list[str], warnings: list[str]) -> None:
    unique = []
    seen = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    if not unique:
        errors.append("FAIL links: no http(s) links to check")
        return
    with ThreadPoolExecutor(max_workers=min(8, len(unique))) as pool:
        futures = [pool.submit(probe, url, timeout) for url in unique]
        for future in as_completed(futures):
            url, code, err = future.result()
            if code is not None and 200 <= code < 400:
                continue
            host = host_of(url)
            if bot_blocked(host) or code in (401, 403, 429):
                warnings.append(f"WARN links: {url} ({err or code}) not counted (host blocks scripts)")
                continue
            if code in (404, 410):
                errors.append(f"FAIL links: {url} ({err})")
                continue
            errors.append(f"FAIL links: {url} ({err or code})")


def finish(errors: list[str], warnings: list[str]) -> int:
    for line in warnings:
        print(line)
    if errors:
        for line in errors:
            print(line)
        print("DO NOT CALL REVIEWER BOT")
        return 1
    print("PASS rule-check")
    print("Reviewer Bot may run")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rule check before Reviewer Bot")
    parser.add_argument("--daily-json", required=True)
    parser.add_argument("--daily-html", required=True)
    parser.add_argument("--exec-json", required=True)
    parser.add_argument("--exec-html", required=True)
    parser.add_argument("--skip-live", action="store_true")
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args(argv)

    errors: list[str] = []
    warnings: list[str] = []
    daily = load_json(Path(args.daily_json))
    exec_data = load_json(Path(args.exec_json))
    daily_html_path = Path(args.daily_html)
    exec_html_path = Path(args.exec_html)
    if not daily_html_path.is_file() or not exec_html_path.is_file():
        print("FAIL template: HTML file missing")
        print("DO NOT CALL REVIEWER BOT")
        return 1
    daily_html = daily_html_path.read_text(encoding="utf-8")
    exec_html = exec_html_path.read_text(encoding="utf-8")

    daily_bits, daily_links = walk_daily(daily, errors)
    exec_bits, exec_links = walk_exec(exec_data, errors)
    check_words("daily.date_line", str(daily.get("date_line", "")), errors)
    check_repeats(daily_bits, exec_bits, daily_links, exec_links, errors)
    check_template("daily", daily_html, errors)
    check_template("exec", exec_html, errors)
    daily_links = check_link_presence("daily", daily_links, daily_html, errors)
    exec_links = check_link_presence("exec", exec_links, exec_html, errors)
    if not args.skip_live:
        check_live(daily_links + exec_links, args.timeout, errors, warnings)
    return finish(errors, warnings)


if __name__ == "__main__":
    sys.exit(main())
