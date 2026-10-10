#!/usr/bin/env python3
"""Build locked shells and plain-text JSON from the hand-built reference issues.

Run once when the locked design changes. fill_brief.py is what the builder runs.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fill_brief import render_value, tags_from_plain, to_plain

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
TEMPLATES = HERE / "templates"


class Walker:
    def __init__(self, html: str):
        self.html = html
        self.pos = 0
        self.spans: list[tuple[int, int, str]] = []

    def take(self, pattern: str, token: str, group: int = 1) -> str:
        match = re.search(pattern, self.html[self.pos :], re.S)
        if not match:
            raise SystemExit(
                f"no match for {token} at {self.pos}: {pattern[:100]}"
            )
        start = self.pos + match.start(group)
        end = self.pos + match.end(group)
        if end < start:
            raise SystemExit(f"empty span {token}")
        self.spans.append((start, end, token))
        self.pos = end
        return match.group(group)


def apply_tokens(html: str, spans: list[tuple[int, int, str]]) -> str:
    ordered = sorted(spans, key=lambda item: item[0])
    for left, right in zip(ordered, ordered[1:]):
        if left[1] > right[0]:
            raise SystemExit(f"overlap {left[2]} and {right[2]}")
    out = html
    for start, end, token in reversed(ordered):
        out = out[:start] + "{{" + token + "}}" + out[end:]
    return out


def assign(root: dict, token: str, value):
    parts = token.split(".")
    cur = root
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        nxt_list = (not last) and parts[i + 1].isdigit()
        if part.isdigit():
            idx = int(part)
            if not isinstance(cur, list):
                raise SystemExit(f"{token}: expected a list at {part}")
            while len(cur) <= idx:
                cur.append(None)
            if last:
                if cur[idx] not in (None, value):
                    raise SystemExit(f"mismatch {token}")
                cur[idx] = value
            else:
                if cur[idx] is None:
                    cur[idx] = [] if nxt_list else {}
                cur = cur[idx]
            continue
        if not isinstance(cur, dict):
            raise SystemExit(f"{token}: expected an object at {part}")
        if last:
            if part in cur and cur[part] != value:
                raise SystemExit(f"mismatch {token}: {cur[part]!r} vs {value!r}")
            cur[part] = value
        else:
            if part not in cur:
                cur[part] = [] if nxt_list else {}
            cur = cur[part]


def remember(bag: dict, token: str, fragment: str):
    if token == "header_tags":
        value = tags_from_plain(to_plain(fragment))
    else:
        value = to_plain(fragment)
    rendered = render_value(value)
    # The hand-built exec notes encode a dollar as &#36;. Plain text keeps "$".
    dollar_form = rendered.replace("$", "&#36;")
    if rendered != fragment and dollar_form != fragment:
        raise SystemExit(
            "round-trip mismatch for "
            + token
            + "\n  got: "
            + rendered[:240]
            + "\n  src: "
            + fragment[:240]
        )
    assign(bag, token, value)


def build(html: str, takes: list[tuple[str, str]]) -> tuple[str, dict]:
    walker = Walker(html)
    bag: dict = {}
    for pattern, token in takes:
        fragment = walker.take(pattern, token)
        remember(bag, token, fragment)
    template = apply_tokens(html, walker.spans)
    return template, bag


TAG = (
    r'style="font:bold 12px/16px Arial;letter-spacing:1\.1px;text-transform:uppercase;'
    r'color:#334155;background:transparent;padding:0">([^<]*)</span>'
)
HREF = r'href="([^"]+)"'


def daily_takes() -> list[tuple[str, str]]:
    takes = [
        (r"<title>Super Intelligence: ([^<]*)</title>", "date_line"),
        (r'class="si-accent">([^<]*)</div>', "date_line"),
        (
            r'class="si-tag-dark" style="font:bold 12px/16px Arial;letter-spacing:1\.1px;'
            r'text-transform:uppercase;color:#99f6e4;background:transparent;padding:0">([^<]*)</span>',
            "header_tags",
        ),
    ]
    text_td = (
        r'class="si-text si-text-col si-bg" style="padding:5px 0;font-size:15px;'
        r'line-height:22px;color:#1A1A1A;vertical-align:top" bgcolor="#FAF8F3">([^<]*(?:<span class="si-code".*?</span>[^<]*)*)</td>'
    )
    for n in range(5):
        p = f"tips.{n}"
        takes.extend(
            [
                (TAG, f"{p}.tag"),
                (
                    r'line-height:28px;vertical-align:top" bgcolor="#FAF8F3">([^<]*)</td>',
                    f"{p}.emoji",
                ),
                (
                    r'class="si-title" style="font:bold 20px/25px Arial;color:#0f1f3d">([^<]*)</div>',
                    f"{p}.headline",
                ),
                (
                    r'font-size:12px;color:#6b7280;margin-top:3px">([^<]*)</div>',
                    f"{p}.credit",
                ),
                (text_td, f"{p}.what"),
                (text_td, f"{p}.how"),
                (text_td, f"{p}.why"),
                (
                    HREF
                    + r' style="font-size:14px;line-height:22px;font-weight:bold;color:#1e40af;text-decoration:underline"',
                    f"{p}.read_url",
                ),
                (
                    HREF
                    + r' style="color:#475569;font-weight:bold;font-size:14px;line-height:22px"',
                    f"{p}.post_url",
                ),
            ]
        )
    for n, section in enumerate(("ai", "tool", "jev")):
        count = {"ai": 3, "tool": 3, "jev": 2}[section]
        for i in range(count):
            p = f"news.{section}.{i}"
            takes.extend(
                [
                    (
                        r'class="si-news-icon si-bg" width="36" valign="top" style="width:36px;padding:12px 8px 12px 14px;font-size:20px;line-height:22px;vertical-align:top" bgcolor="#FAF8F3">([^<]*)</td>',
                        f"{p}.emoji",
                    ),
                    (TAG, f"{p}.tag"),
                    (
                        r'font-size:15px;font-weight:bold;color:#1A1A1A">&nbsp;([^<]*)</span>',
                        f"{p}.title",
                    ),
                    (
                        r'style="font-size:15px;line-height:22px;color:#475569;margin-top:4px">([^<]*(?:<span class="si-code".*?</span>[^<]*)*)</div>',
                        f"{p}.body",
                    ),
                    (HREF + r' style="color:#[0-9a-fA-F]+;font-weight:bold"', f"{p}.link_url"),
                    (r'font-weight:bold">([^<]*)</a>&nbsp;&rarr;', f"{p}.link_label"),
                ]
            )
    return takes


def exec_takes() -> list[tuple[str, str]]:
    takes = [
        (r"<title>Super Intelligence Executive Brief: ([^<]*)</title>", "date_line"),
        (r'class="si-date-desk">([^<]*?) &middot; 2-minute read', "date_line"),
        (
            r'class="si-date-phone" style="display:none;mso-hide:all">([^<]*?) &middot; 2-MIN READ',
            "date_phone",
        ),
    ]
    for n in range(3):
        p = f"big3.{n}"
        takes.extend(
            [
                (
                    r'line-height:28px;vertical-align:top" bgcolor="#FAF8F3">([^<]*)</td>',
                    f"{p}.emoji",
                ),
                (TAG, f"{p}.tag"),
                (
                    r'class="si-title" style="font:bold 20px/25px Arial;color:#0f1f3d;margin-top:6px">([^<]*)</div>',
                    f"{p}.headline",
                ),
                (r">WHAT HAPPENED</td><td[^>]*>(.*?)</td>", f"{p}.what_happened"),
                (r">WHY IT MATTERS</td><td[^>]*>(.*?)</td>", f"{p}.why_it_matters"),
                (
                    HREF
                    + r' style="color:#[0-9a-fA-F]+;font-weight:bold;font-size:14px;line-height:22px"',
                    f"{p}.link_url",
                ),
                (r'line-height:22px">([^<]*)</a>', f"{p}.link_label"),
            ]
        )
    for n in range(3):
        p = f"projects.{n}"
        takes.extend(
            [
                (r"&#127482;&#127480; ([^<]*)</div>", f"{p}.name"),
                (
                    r'class="si-sub" style="font-size:12px;line-height:18px;color:#6b7280;margin-top:2px">([^<]*)</div>',
                    f"{p}.place_line",
                ),
                (
                    r'style="font-size:15px;line-height:22px;color:#374151;margin-top:6px">([^<]*)</div>',
                    f"{p}.summary",
                ),
                (r">Size</td><td[^>]*>([^<]*)</td>", f"{p}.size"),
                (r">Cost</td><td[^>]*>([^<]*)</td>", f"{p}.cost"),
                (r">Power</td><td[^>]*>([^<]*)</td>", f"{p}.power"),
                (r">Who builds</td><td[^>]*>([^<]*)</td>", f"{p}.who"),
                (r">Jobs</td><td[^>]*>([^<]*)</td>", f"{p}.jobs"),
                (r">Timing</td><td[^>]*>([^<]*)</td>", f"{p}.timing"),
                (
                    HREF
                    + r' style="color:#[0-9a-fA-F]+;font-weight:bold;font-size:14px;line-height:22px"',
                    f"{p}.link_url",
                ),
                (r'line-height:22px">([^<]*)</a>', f"{p}.link_label"),
            ]
        )
    takes.extend(
        [
            (
                r'style="font-size:15px;line-height:22px;color:#cbd5e1;margin-top:4px">([^<]*)</div>',
                "scoreboard.intro",
            ),
            (
                r">&nbsp;&middot;&nbsp;<span class=\"si-tag\" style=\"font:bold 12px/16px Arial;letter-spacing:1\.1px;text-transform:uppercase;color:#334155;background:transparent;padding:0\">([^<]*)</span>",
                "headline.status",
            ),
            (
                r'class="si-title" style="font:bold 22px/28px Arial;color:#0f1f3d;margin-top:10px">([^<]*)</div>',
                "headline.title",
            ),
            (
                r'style="font-size:15px;line-height:22px;color:#374151;margin-top:6px">([^<]*)<a href="',
                "headline.before",
            ),
            (HREF + r' style="color:#b45309;font-weight:bold"', "headline.link_url"),
            (r'style="color:#b45309;font-weight:bold">([^<]*)</a>', "headline.link_label"),
            (r"</a>([^<]*)</div>", "headline.after"),
            (
                r'style="font-size:15px;line-height:22px;color:#374151;margin-top:6px">([^<]*)</div>',
                "headline.detail",
            ),
            (r'color:#6b7280">([^<]*)</span>', "headline.updated"),
            (
                r'style="font:bold 44px/48px Arial;color:#FAF8F3;letter-spacing:-1px">([^<]*)</div>',
                "headline.score",
            ),
            (
                r'color:#fcd34d;background:transparent;padding:0">([^<]*)</span>',
                "headline.badge",
            ),
        ]
    )
    for n in range(6):
        p = f"cards.{n}"
        takes.extend(
            [
                (TAG, f"{p}.category"),
                (TAG, f"{p}.status"),
                (HREF + r' style="font:bold 16px/20px Arial;color:#0f1f3d;text-decoration:none"', f"{p}.link_url"),
                (
                    r'style="font:bold 16px/20px Arial;color:#0f1f3d;text-decoration:none">([^<]*) <span',
                    f"{p}.name",
                ),
                (
                    r'white-space:nowrap(?:;letter-spacing:-\.2px)?">([^<]*)</div>',
                    f"{p}.explainer",
                ),
                (r'letter-spacing:-1px">([^<]*)</span>', f"{p}.score"),
                (
                    r'font-size:14px;line-height:14px(?:;color:#[0-9a-fA-F]+)?">([^<]*)</span>',
                    f"{p}.unit",
                ),
            ]
        )
        for row in range(3):
            takes.extend(
                [
                    (
                        r'padding-left:6px;white-space:nowrap" bgcolor="#FAF8F3">([^<]*)</td>',
                        f"{p}.rows.{row}.name",
                    ),
                    (
                        r'class="si-val si-bg" align="right"(?: style="color:#[0-9a-fA-F]+")? bgcolor="#FAF8F3">([^<]*)</td>',
                        f"{p}.rows.{row}.score",
                    ),
                    (r'style="width:([0-9.]+)%;height:8px;', f"{p}.rows.{row}.bar"),
                ]
            )
        takes.extend(
            [
                (
                    r'class="si-fit" style="font-size:15px;line-height:22px;color:#6b7280;margin-top:7px;height:44px;overflow:hidden">([^<]*)</div>',
                    f"{p}.note",
                ),
                (
                    r'<b style="color:#0f1f3d">This week:</b> ([^<]*)</div>',
                    f"{p}.this_week",
                ),
            ]
        )
    takes.append((r"Executive Brief &middot; ([^<]*)</p>", "date_line"))
    return takes


def attach_sources(product: str, data: dict) -> None:
    data["product"] = product
    if product == "daily":
        data["subject"] = "Daily Brief: " + data["date_line"]
        for tip in data["tips"]:
            tip["source_id"] = tip["read_url"]
        for section in data["news"].values():
            for item in section:
                item["source_id"] = item["link_url"]
        return
    data["subject"] = "Executive Brief: " + data["date_line"]
    for item in data["big3"]:
        item["source_id"] = item["link_url"]
    for item in data["projects"]:
        item["source_id"] = item["link_url"]
    data["headline"]["source_id"] = data["headline"]["link_url"]
    for card in data["cards"]:
        card["source_id"] = card["link_url"]


def main() -> int:
    daily_html = (FIXTURES / "newsletter-20261009.html").read_text(encoding="utf-8")
    exec_html = (FIXTURES / "exec-brief-20261005-reference.html").read_text(encoding="utf-8")
    daily_template, daily = build(daily_html, daily_takes())
    exec_template, exec_data = build(exec_html, exec_takes())
    attach_sources("daily", daily)
    attach_sources("exec", exec_data)
    (TEMPLATES / "daily-locked.html").write_text(daily_template, encoding="utf-8")
    (TEMPLATES / "exec-locked.html").write_text(exec_template, encoding="utf-8")
    (FIXTURES / "daily-20261009.json").write_text(
        json.dumps(daily, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (FIXTURES / "exec-20261005.json").write_text(
        json.dumps(exec_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"daily slots {daily_template.count('{{')}")
    print(f"exec slots {exec_template.count('{{')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
