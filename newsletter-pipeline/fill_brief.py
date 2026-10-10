#!/usr/bin/env python3
"""Fill a locked brief template from JSON. Does not write HTML from scratch.

The model supplies plain text. This script inserts it into the locked shell
and refuses raw tags. It does not send mail and does not call Reviewer Bot.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"

TOKEN_RE = re.compile(r"\{\{([A-Za-z0-9_.]+)\}\}")
CODE_RE = re.compile(r"`([^`]*)`")
CODE_SPAN = (
    '<span class="si-code" style="font-family:Consolas,\'Courier New\',monospace;'
    "font-size:14px;background-color:#eef2f7;color:#1A1A1A;padding:2px 4px;"
    'white-space:nowrap">{0}</span>'
)
CODE_SPAN_RE = re.compile(
    r'<span class="si-code" style="font-family:Consolas,\'Courier New\',monospace;'
    r'font-size:14px;background-color:#eef2f7;color:#1A1A1A;padding:2px 4px;'
    r'white-space:nowrap">(.*?)</span>'
)

# Characters the locked issues already encode with named entities.
NAMED = {
    "\u00a0": "&nbsp;",
    "\u00b7": "&middot;",
    "\u201c": "&ldquo;",
    "\u201d": "&rdquo;",
    "\u2018": "&lsquo;",
    "\u2019": "&rsquo;",
    "\u2014": "&mdash;",
    "\u2013": "&ndash;",
    "\u2011": "&#8209;",
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
}


def escape_text(text: str) -> str:
    """Encode plain text the way the locked shells encode it. Leave '$' as '$'."""
    out = []
    for ch in text:
        if ch in NAMED:
            out.append(NAMED[ch])
        elif ord(ch) > 127:
            out.append(f"&#{ord(ch)};")
        else:
            out.append(ch)
    return "".join(out)


def render_text(plain: str) -> str:
    """Turn plain text into locked-shell HTML. Backticks become code spans."""
    if "<" in plain or ">" in plain:
        raise ValueError("field contains HTML tags; pass plain text only")
    if "{{" in plain or "}}" in plain:
        raise ValueError("field contains template tokens")
    parts = CODE_RE.split(plain)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            if "<" in part or ">" in part or "`" in part:
                raise ValueError("code span contains HTML")
            out.append(CODE_SPAN.format(escape_text(part)))
        else:
            out.append(escape_text(part))
    return "".join(out)


def to_plain(fragment: str) -> str:
    """Inverse of render_text for text copied out of a locked issue."""
    text = CODE_SPAN_RE.sub(lambda m: "`" + m.group(1) + "`", fragment)
    if "<" in text or ">" in text:
        raise ValueError("unconverted tag in fragment: " + text[:180])
    return html.unescape(text)


def render_tags(tags: list) -> str:
    rendered = []
    for tag in tags:
        if not isinstance(tag, str) or not tag.strip():
            raise ValueError("header tag must be a non-empty string")
        rendered.append(escape_text(tag.replace(" ", "\u00a0")))
    return "&nbsp;&middot; ".join(rendered)


def tags_from_plain(plain: str) -> list[str]:
    parts = re.split(r"\u00a0\u00b7 ", plain)
    return [part.replace("\u00a0", " ") for part in parts]


def render_value(value):
    if isinstance(value, list):
        return render_tags(value)
    if not isinstance(value, str):
        raise TypeError(f"slot value must be str or tag list, got {type(value).__name__}")
    return render_text(value)


def lookup(data, token: str):
    cur = data
    for part in token.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def fill_template(template: str, data: dict) -> str:
    missing = []
    bad = []

    def repl(match: re.Match) -> str:
        token = match.group(1)
        try:
            value = lookup(data, token)
        except (KeyError, IndexError, TypeError, ValueError):
            missing.append(token)
            return match.group(0)
        try:
            return render_value(value)
        except (TypeError, ValueError) as exc:
            bad.append(f"{token}: {exc}")
            return match.group(0)

    filled = TOKEN_RE.sub(repl, template)
    if missing or bad:
        lines = [f"missing {token}" for token in missing] + bad
        raise SystemExit("FAIL fill: " + "; ".join(lines[:24]))
    leftover = TOKEN_RE.findall(filled)
    if leftover:
        raise SystemExit("FAIL fill: unfilled tokens: " + ", ".join(leftover[:12]))
    return filled


# Big-story placeholders, in document order. Repeated names are consumed in order.
# MSO conditionals ([if mso], [endif], [if !mso]) are not content.
BIG_STORY_QUEUE = [
    "weekday",
    "mon",
    "day",
    "year",
    "weekday",
    "mon",
    "day",
    "year",
    "phone_wday",
    "phone_mon",
    "phone_day",
    "category",
    "headline",
    "summary",
    "what_happened",
    "what_facts",
    "why_it_matters",
    "why_builder",
    "watch_1",
    "watch_2",
    "watch_3",
    "source_1_url",
    "source_1_name",
    "source_2_url",
    "source_2_name",
    "source_3_url",
    "source_3_name",
    "project_1_name",
    "project_1_place",
    "project_1_when",
    "project_1_summary",
    "project_1_size",
    "project_1_cost",
    "project_1_power",
    "project_1_who",
    "project_1_jobs",
    "project_1_timing",
    "project_1_url",
    "project_1_source",
    "project_2_name",
    "project_2_place",
    "project_2_when",
    "project_2_summary",
    "project_2_size",
    "project_2_cost",
    "project_2_power",
    "project_2_who",
    "project_2_jobs",
    "project_2_timing",
    "project_2_url",
    "project_2_source",
    "mon",
    "day",
    "year",
    "weekday",
    "mon",
    "day",
    "year",
]

# Exact placeholder text in exec-bigstory-template.html, same order as BIG_STORY_QUEUE.
BIG_STORY_PLACEHOLDERS = [
    "[Weekday]",
    "[Mon]",
    "[day]",
    "[year]",
    "[Weekday]",
    "[Mon]",
    "[day]",
    "[year]",
    "[WKD]",
    "[MON]",
    "[day]",
    "[CATEGORY: POLICY, MODELS, DEALS OR OUTAGE]",
    "[Lead headline: plain words, max 12 words]",
    "[One-sentence summary of the story for a busy leader]",
    "[What happened: who did what, and when. 2 or 3 short sentences.]",
    "[Key facts and numbers from the primary source.]",
    "[Why it matters for construction, business or policy. 2 or 3 short sentences.]",
    "[What it can change for a builder: cost, bids, schedules, jobs or rules.]",
    "[Next date, decision or deadline]",
    "[Second thing to watch]",
    "[Optional third thing to watch]",
    "[Source 1 URL]",
    "[Source 1 name: publisher and title]",
    "[Source 2 URL]",
    "[Source 2 name: publisher and title]",
    "[Source 3 URL]",
    "[Source 3 name: publisher and title]",
    "[Project 1 name]",
    "[City, State]",
    "[Mon day]",
    "[One or two plain sentences about the project.]",
    "[Size from the source]",
    "[Cost from the source]",
    "[Power from the source]",
    "[Who builds from the source]",
    "[Jobs from the source]",
    "[Timing from the source]",
    "[Project 1 source URL]",
    "[Project 1 source name]",
    "[Project 2 name]",
    "[City, State]",
    "[Mon day]",
    "[One or two plain sentences about the project.]",
    "[Size from the source]",
    "[Cost from the source]",
    "[Power from the source]",
    "[Who builds from the source]",
    "[Jobs from the source]",
    "[Timing from the source]",
    "[Project 2 source URL]",
    "[Project 2 source name]",
    "[Mon]",
    "[day]",
    "[year]",
    "[Weekday]",
    "[Mon]",
    "[day]",
    "[year]",
]


def fill_big_story(template: str, data: dict) -> str:
    if len(BIG_STORY_QUEUE) != len(BIG_STORY_PLACEHOLDERS):
        raise SystemExit("FAIL fill: big-story placeholder map is uneven")
    used = {key: 0 for key in BIG_STORY_QUEUE}
    out = template
    for key, placeholder in zip(BIG_STORY_QUEUE, BIG_STORY_PLACEHOLDERS):
        idx = out.find(placeholder)
        if idx < 0:
            raise SystemExit(f"FAIL fill: locked big-story shell missing {placeholder}")
        try:
            value = data[key]
        except KeyError:
            raise SystemExit(f"FAIL fill: JSON missing {key}") from None
        if isinstance(value, list):
            n = used[key]
            if n >= len(value):
                raise SystemExit(f"FAIL fill: not enough values for {key}")
            piece = value[n]
            used[key] = n + 1
        else:
            piece = value
        if not isinstance(piece, str):
            raise SystemExit(f"FAIL fill: {key} must be a string")
        rendered = render_text(piece) if key not in _URL_KEYS else _render_url(piece)
        out = out[:idx] + rendered + out[idx + len(placeholder) :]
    leftover = re.findall(r"\[[^\[\]\n]*[A-Za-z][^\[\]\n]*\]", _visible(out))
    if leftover:
        raise SystemExit("FAIL fill: placeholders left: " + "; ".join(leftover[:8]))
    return out


_URL_KEYS = {
    "source_1_url",
    "source_2_url",
    "source_3_url",
    "project_1_url",
    "project_2_url",
}


def _render_url(url: str) -> str:
    if not re.match(r"https?://", url):
        raise SystemExit(f"FAIL fill: URL must start with http:// or https:// ({url[:80]})")
    if "<" in url or ">" in url or '"' in url or " " in url:
        raise SystemExit("FAIL fill: URL has a space or a quote")
    return url


def _visible(page: str) -> str:
    body = re.sub(r"(?is)<(style|script)\b.*?</\1\s*>", " ", page)
    body = re.sub(r"(?s)<!--.*?-->", " ", body)
    return body


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("FAIL fill: JSON root must be an object")
    return data


def template_for(product: str) -> Path:
    names = {
        "daily": "daily-locked.html",
        "exec": "exec-locked.html",
        "big-story": "exec-bigstory-template.html",
    }
    if product not in names:
        raise SystemExit("FAIL fill: product must be daily, exec, or big-story")
    path = TEMPLATES / names[product]
    if not path.is_file():
        raise SystemExit(f"FAIL fill: missing template {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill a locked newsletter shell from JSON")
    parser.add_argument("--product", required=True, choices=["daily", "exec", "big-story"])
    parser.add_argument("--json", required=True, dest="json_path")
    parser.add_argument("--out", required=True)
    parser.add_argument("--template", help="Override the locked HTML shell")
    args = parser.parse_args(argv)

    data = load_json(Path(args.json_path))
    template = Path(args.template).read_text(encoding="utf-8") if args.template else template_for(args.product).read_text(encoding="utf-8")
    if args.product == "big-story":
        filled = fill_big_story(template, data)
    else:
        filled = fill_template(template, data)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(filled, encoding="utf-8")
    print(f"WROTE {out} bytes={len(filled.encode('utf-8'))} product={args.product}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
