"""DoD contract announcement parser copied from the research scraper.

Stdlib only. No network and no historical scrape.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from html import unescape

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
LIST_URL = "https://www.war.gov/News/Contracts/?Page={page}"
LIST_URL_1 = "https://www.war.gov/News/Contracts/"

FETCH_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
MON_ALT = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?"
)
DATE_RE = re.compile(
    rf"\b({MON_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
    re.I,
)
SAME_MONTH_RANGE_RE = re.compile(
    rf"\b({MON_ALT})\.?\s+(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}}),?\s+(\d{{4}})\b",
    re.I,
)
COMPLETION_RE = re.compile(
    rf"(?:expected to be completed|to be completed|completion date of|"
    rf"expected completion date of|estimated completion date of|"
    rf"completed by|completed on|completed in|will be completed by|"
    rf"scheduled to be completed(?: by)?|performance completion date of|"
    rf"complete no later than|completed no later than|no later than|"
    rf"not later than)\s+"
    rf"(?:by\s+|on\s+|in\s+)?"
    rf"(({MON_ALT})\.?\s+(?:\d{{1,2}},?\s+)?\d{{4}})",
    re.I,
)
COMPLETION_BEFORE_RE = re.compile(
    rf"(({MON_ALT})\.?\s+\d{{1,2}},?\s+\d{{4}}),?\s+"
    rf"(?:performance\s+)?completion date",
    re.I,
)
AMOUNT_RE = re.compile(
    r"\$\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(million|billion|thousand)?",
    re.I,
)
CLAUSE_END_RE = re.compile(
    r"\b(?:is|are|was|were|has been|have been|will be|is being|are being|"
    r"was being)(?:\s+(?:[a-z]+ly|hereby|each|also|now)){0,3}\s+awarded\b"
    r"|\b(?:is|are|was|were|has been|have been|will be|is being|are being)"
    r"(?:\s+(?:[a-z]+ly|hereby|each|also|now)){0,3}\s+issued\b"
    r"|\b(?:has|have)\s+received\b"
    r"|\bwill compete\b"
    r"|\bare competing\b",
    re.I,
)
MOD_RE = re.compile(
    r"\bmodif(?:y|ies|ied|ication|ications)\b"
    r"|\boption exercise\b"
    r"|\bexercise of (?:an |the )?options?\b"
    r"|\bexercis(?:e|es|ed|ing) (?:an |the )?option\b",
    re.I,
)
FMS_RE = re.compile(r"\bFMS\b|Foreign Military Sales", re.I)
DASHED_CN_RE = re.compile(
    r"\b[A-Z][A-Z0-9]{2,14}-\d{2}-[A-Z]{1,4}-[A-Z0-9]{2,10}\b"
)
UNDASHED_CN_RE = re.compile(
    r"\b[A-Z]{1,6}\d{5,9}[CDFGPQR]\d{3,6}\b"
)
TYPE_RE = re.compile(
    r"firm-fixed-price"
    r"|fixed-price-incentive(?:-firm(?:-target)?)?(?:-fee)?"
    r"|fixed-price-award-fee"
    r"|fixed-price with economic price adjustment"
    r"|fixed-price"
    r"|cost-plus-fixed-fee"
    r"|cost-plus-incentive-fee"
    r"|cost-plus-incentive"
    r"|cost-plus-award-fee"
    r"|cost-plus-award"
    r"|cost-reimbursable"
    r"|cost-plus"
    r"|time-and-materials"
    r"|time and materials"
    r"|labor-hour"
    r"|indefinite-delivery\s*/\s*indefinite-quantity"
    r"|indefinite-delivery,?\s+indefinite-quantity"
    r"|\bIDIQ\b"
    r"|undefinitized"
    r"|letter contract"
    r"|basic ordering agreement"
    r"|blanket purchase agreement",
    re.I,
)
PLACES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia", "d.c.", "d.c", "dc", "puerto rico", "guam",
    "virgin islands", "american samoa", "northern mariana islands",
    "norway", "australia", "united kingdom", "uk", "england", "scotland",
    "wales", "germany", "japan", "korea", "south korea", "republic of korea",
    "canada", "italy", "israel", "france", "spain", "netherlands",
    "the netherlands", "sweden", "finland", "poland", "turkey", "turkiye",
    "singapore", "united arab emirates", "uae", "qatar", "kuwait", "bahrain",
    "saudi arabia", "india", "taiwan", "romania", "greece", "denmark",
    "belgium", "switzerland", "austria", "brazil", "colombia", "egypt",
    "jordan", "ukraine", "new zealand", "ireland", "portugal",
    "czech republic", "czechia", "hungary", "slovakia", "lithuania", "latvia",
    "estonia", "luxembourg", "iceland", "mexico", "chile", "argentina", "peru",
    "philippines", "thailand", "indonesia", "malaysia", "vietnam", "pakistan",
    "iraq", "afghanistan", "kenya", "south africa", "morocco", "oman",
    "croatia", "bulgaria", "slovenia", "serbia", "georgia", "moldova",
    "kazakhstan", "nigeria", "tunisia", "bahrain",
}
BAD_CITY = {
    "inc", "inc.", "llc", "l.l.c.", "corp", "corp.", "co", "co.", "ltd", "ltd.",
    "lp", "l.p.", "llp", "plc", "gmbh", "company", "incorporated", "corporation",
    "limited", "sa", "ag", "bv", "nv", "as", "ab", "oy", "jv", "jv1", "jv2",
}

ARTICLE_FIELDS = [
    "article_id", "url", "publish_date", "n_paragraphs", "source_route",
]
AWARD_FIELDS = [
    "article_id", "publish_date", "branch", "company", "company_raw",
    "city_state", "amount_usd", "is_modification", "is_multiple_award",
    "contract_type", "contract_number", "completion_date",
    "is_foreign_military_sales", "text",
]
def month_num(token: str) -> int:
    t = token.lower().replace(".", "")
    if t.startswith("sept"):
        return 9
    return MONTHS[t[:3]]


def iso_date(mon: str, day: str, year: str) -> str:
    return f"{int(year):04d}-{month_num(mon):02d}-{int(day):02d}"


def dates_in_title(title: str) -> list[str]:
    found: list[tuple[int, str]] = []
    for m in SAME_MONTH_RANGE_RE.finditer(title):
        y = m.group(4)
        mon = m.group(1)
        d1, d2 = int(m.group(2)), int(m.group(3))
        for d in range(min(d1, d2), max(d1, d2) + 1):
            found.append((m.start(), f"{int(y):04d}-{month_num(mon):02d}-{d:02d}"))
    if found:
        # Prefer an explicit same-month range over a partial single-day hit.
        return [d for _, d in sorted(found)]
    for m in DATE_RE.finditer(title):
        found.append((m.start(), iso_date(m.group(1), m.group(2), m.group(3))))
    out = [d for _, d in found]
    if re.search(r"\bthrough\b|\bthru\b", title, re.I) and len(out) >= 2:
        start = datetime.strptime(out[0], "%Y-%m-%d").date()
        end = datetime.strptime(out[-1], "%Y-%m-%d").date()
        if start > end:
            start, end = end, start
        filled = []
        cur = start
        while cur <= end:
            filled.append(cur.isoformat())
            cur += timedelta(days=1)
        return filled
    return out


def publish_date_from_title(title: str) -> str:
    ds = dates_in_title(title)
    if not ds:
        return ""
    # Announcement day is the last calendar date named (or the end of a range).
    return max(ds)


def normalize_dashes(s: str) -> str:
    for ch in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212", "\ufe58", "\ufe63", "\uff0d"):
        s = s.replace(ch, "-")
    return s


def strip_html(fragment: str) -> str:
    s = normalize_dashes(fragment)
    s = re.sub(r"(?i)<br\s*/?>", " ", s)
    s = re.sub(r"(?i)</p>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = unescape(s)
    s = s.replace("\xa0", " ").replace("\u200b", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def article_region(html: str) -> str:
    i = html.find('class="article-view"')
    if i < 0:
        i = html.find('class="adetail')
    region = html[i if i >= 0 else 0 :]
    j = region.find('class="footer')
    if j > 0:
        region = region[:j]
    return region


def extract_title(html: str) -> str:
    m = re.search(r'(?is)<h1[^>]*class="maintitle"[^>]*>(.*?)</h1>', html)
    if not m:
        m = re.search(r"(?is)<title>(.*?)</title>", html)
        if not m:
            return ""
        return strip_html(m.group(1)).split(">")[0].strip()
    return strip_html(m.group(1))


def is_heading(raw_p: str, text: str) -> bool:
    if not text or "$" in text or len(text) > 90:
        return False
    if re.search(r"\b(awarded|award|contract)\b", text, re.I):
        return False
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) >= 0.8:
        return True
    if re.search(r"(?i)<strong>", raw_p) and len(text) < 70 and text.count(" ") <= 8:
        return True
    return False


def is_skippable(text: str) -> bool:
    low = text.lower().strip()
    if not low:
        return True
    if "provides the military forces" in low:
        return True
    if "delivered to your inbox" in low or "products you want delivered" in low:
        return True
    if re.match(r"^[\*\s]*small business\b", low) and len(low) < 500:
        return True
    if "million or more are announced" in low or (
        "$7.5 million or more" in low and "awarded" not in low and len(low) < 400
    ):
        return True
    return False


def is_continuation(text: str) -> bool:
    if re.match(r"^\(?\s*awarded\b", text, re.I) and len(text) < 220:
        return True
    if text and text[0].islower() and len(text) < 400 and "$" not in text[:15]:
        return True
    return False


def looks_like_place(part: str) -> bool:
    p = part.strip(" .").lower()
    p = re.sub(r"\s+", " ", p)
    return p in PLACES


def looks_like_city(part: str) -> bool:
    p = part.strip()
    if len(p) < 2:
        return False
    if p.lower().strip(".") in BAD_CITY or p.lower() in BAD_CITY:
        return False
    return True


def split_company(company_raw: str) -> tuple[str, str]:
    seg = re.split(r"\s*;\s*", company_raw, maxsplit=1)[0]
    seg = re.split(r"\s*\((?=[A-Z][A-Z0-9-]{4,})", seg, maxsplit=1)[0]
    seg = seg.strip(" ,;")
    parts = [p.strip() for p in seg.split(",") if p.strip()]
    city_state = ""
    company = seg
    if len(parts) >= 3 and looks_like_place(parts[-1]) and looks_like_city(parts[-2]):
        city_state = f"{parts[-2]}, {parts[-1]}"
        company = ", ".join(parts[:-2]).strip(" ,")
    elif len(parts) >= 2 and looks_like_place(parts[-1]) and looks_like_city(parts[-2] if len(parts) > 1 else ""):
        # two parts only when the first is not a bare suffix — still try
        if len(parts) == 2 and looks_like_city(parts[0]) and not re.search(
            r"\b(Inc|LLC|Corp|Co|Ltd)\b", parts[0]
        ):
            pass
        elif len(parts) >= 2 and looks_like_place(parts[-1]) and looks_like_city(parts[0]):
            # "City, State" without a company would be odd; leave company as full seg
            pass
    company = re.split(
        r",\s+(?:a|an|doing business as|d\.?b\.?a\.?|d/b/a)\s+",
        company,
        maxsplit=1,
        flags=re.I,
    )[0]
    company = company.strip(" ,;*")
    return company, city_state


def parse_amount(text: str) -> float | None:
    m = AMOUNT_RE.search(text)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    mag = (m.group(2) or "").lower()
    if mag == "million":
        num *= 1_000_000
    elif mag == "billion":
        num *= 1_000_000_000
    elif mag == "thousand":
        num *= 1_000
    return num


def contract_numbers(text: str) -> list[str]:
    hits: list[tuple[int, str]] = []
    for rx in (DASHED_CN_RE, UNDASHED_CN_RE):
        for m in rx.finditer(text):
            hits.append((m.start(), m.group(0)))
    hits.sort()
    out = []
    seen = set()
    for _, n in hits:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def contract_types(text: str) -> str:
    found = []
    seen = set()
    for m in TYPE_RE.finditer(text):
        s = re.sub(r"\s+", " ", m.group(0)).strip().lower()
        s = re.sub(r"\s*/\s*", "/", s)
        s = re.sub(r",\s*", " ", s)
        if s in seen:
            continue
        seen.add(s)
        found.append(s)
    return " / ".join(found)


def parse_completion(text: str) -> str:
    m = COMPLETION_RE.search(text) or COMPLETION_BEFORE_RE.search(text)
    if not m:
        return ""
    chunk = m.group(1)
    md = re.match(
        rf"({MON_ALT})\.?\s+(?:(\d{{1,2}}),?\s+)?(\d{{4}})",
        chunk,
        re.I,
    )
    if not md:
        return ""
    mon = month_num(md.group(1))
    year = int(md.group(3))
    if md.group(2):
        return f"{year:04d}-{mon:02d}-{int(md.group(2)):02d}"
    return f"{year:04d}-{mon:02d}"


def parse_award_paragraph(text: str, branch: str) -> dict:
    text = normalize_dashes(text)
    text = re.sub(
        r"\s*Choose which (?:War|Defense)\.gov products you want delivered to your inbox\.?",
        "",
        text,
        flags=re.I,
    ).strip()
    clean = text.replace("*", "")
    clean = re.sub(r"\s+", " ", clean).strip()
    m = CLAUSE_END_RE.search(clean)
    if m:
        company_raw = clean[: m.start()].strip(" ,;")
    else:
        company_raw = re.split(r"(?<=\.)\s+", clean, maxsplit=1)[0][:500].strip()
    company, city_state = split_company(company_raw)
    cnums = contract_numbers(clean)
    in_clause = contract_numbers(company_raw)
    plural = re.search(
        r"\b(were|are|have been)\s+(?:each\s+)?awarded\b|\bwill compete\b",
        clean,
        re.I,
    )
    is_multiple = 1 if (
        len(in_clause) >= 2
        or (company_raw.count(";") >= 1 and plural)
        or re.search(r"multiple[ -]award", clean, re.I)
    ) else 0
    amount = parse_amount(clean)
    return {
        "branch": branch,
        "company": company,
        "company_raw": company_raw,
        "city_state": city_state,
        "amount_usd": amount,
        "is_modification": 1 if MOD_RE.search(clean) else 0,
        "is_multiple_award": is_multiple,
        "contract_type": contract_types(clean),
        "contract_number": cnums[0] if cnums else "",
        "completion_date": parse_completion(clean),
        "is_foreign_military_sales": 1 if FMS_RE.search(clean) else 0,
        "text": text,
    }


def parse_article_html(html: str) -> tuple[str, str, list[dict]]:
    title = extract_title(html)
    pub = publish_date_from_title(title)
    region = article_region(html)
    ps = re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", region)
    branch = ""
    awards: list[dict] = []
    pending_text = ""
    for raw_p in ps:
        text = strip_html(raw_p)
        if not text or is_skippable(text):
            continue
        if is_heading(raw_p, text):
            if pending_text:
                awards.append(parse_award_paragraph(pending_text, branch))
                pending_text = ""
            branch = text.strip()
            continue
        if is_continuation(text) and pending_text:
            pending_text = (pending_text + " " + text).strip()
            continue
        if "$" not in text and not re.search(r"\b(awarded|award|contract)\b", text, re.I):
            if pending_text and len(text) < 280:
                pending_text = (pending_text + " " + text).strip()
            continue
        if pending_text:
            awards.append(parse_award_paragraph(pending_text, branch))
        pending_text = text
    if pending_text:
        awards.append(parse_award_paragraph(pending_text, branch))
    return title, pub, awards
# --- self test ---------------------------------------------------------------

SELF_TESTS = [
    {
        "name": "single award, undashed order number, option sentence is not a mod",
        "branch": "NAVY",
        "text": (
            "Raytheon Co., Marlborough, Massachusetts, is being awarded $9,347,391 "
            "for cost-plus-fixed-fee, firm-fixed-price order N6339419F0002 under a "
            "previously awarded basic ordering agreement (N6339417G5103) for engineering "
            "services in support of the Aegis SPY-1 radar. This contract includes options "
            "which, if exercised, would bring the cumulative value of this order to "
            "$19,497,003. Work will be performed in Yorktown, Virginia, and is expected "
            "to be completed by January 2021."
        ),
        "expect": {
            "company": "Raytheon Co.",
            "city_state": "Marlborough, Massachusetts",
            "amount_usd": 9347391.0,
            "is_modification": 0,
            "is_multiple_award": 0,
            "contract_number": "N6339419F0002",
            "completion_date": "2021-01",
            "is_foreign_military_sales": 0,
            "contract_type": "cost-plus-fixed-fee / firm-fixed-price / basic ordering agreement",
        },
    },
    {
        "name": "modification uses headline dollars not cumulative face value",
        "branch": "AIR FORCE",
        "text": (
            "PTC Inc., Boston, Massachusetts, has been awarded a $99,767,830 modification "
            "(P00013) to a previously awarded contract (FA8109-21-C-0001) for Air Force "
            "Enterprise Supply Chain Analysis, Planning, and Execution. The modification "
            "brings the total cumulative face value of the contract to $194,830,400 from "
            "$95,062,570. Work is expected to be completed by Sept. 30, 2028. Foreign "
            "Military Sales funds in the amount of $1,000 will be used."
        ),
        "expect": {
            "company": "PTC Inc.",
            "city_state": "Boston, Massachusetts",
            "amount_usd": 99767830.0,
            "is_modification": 1,
            "is_multiple_award": 0,
            "contract_number": "FA8109-21-C-0001",
            "completion_date": "2028-09-30",
            "is_foreign_military_sales": 1,
        },
    },
    {
        "name": "multiple award ceiling and first company",
        "branch": "AIR FORCE",
        "text": (
            "LATA-CTI JV LLC,* Albuquerque, New Mexico (FA8903-26-D-0062); "
            "Los Alamos Technical Associates Inc.,* Albuquerque, New Mexico "
            "(FA8903-26-D-0063) were awarded a maximum $3,500,000,000 firm-fixed-price, "
            "cost-plus-fixed-fee, indefinite-delivery/indefinite-quantity, multiple award "
            "task order contract for environmental services. Work is expected to be "
            "completed by March 2031."
        ),
        "expect": {
            "company": "LATA-CTI JV LLC",
            "city_state": "Albuquerque, New Mexico",
            "amount_usd": 3500000000.0,
            "is_modification": 0,
            "is_multiple_award": 1,
            "contract_number": "FA8903-26-D-0062",
            "completion_date": "2031-03",
            "contract_type": (
                "firm-fixed-price / cost-plus-fixed-fee / "
                "indefinite-delivery/indefinite-quantity"
            ),
        },
    },
    {
        "name": "option exercise modification",
        "branch": "NAVY",
        "text": (
            "The Boeing Co., St. Louis, Missouri, is awarded a $12,000,000 modification "
            "to previously awarded contract N00019-19-C-0001 that exercises an option "
            "for spare parts. Work is expected to be completed by Dec. 15, 2025."
        ),
        "expect": {
            "company": "The Boeing Co.",
            "city_state": "St. Louis, Missouri",
            "amount_usd": 12000000.0,
            "is_modification": 1,
            "is_multiple_award": 0,
            "contract_number": "N00019-19-C-0001",
            "completion_date": "2025-12-15",
        },
    },
    {
        "name": "foreign place, FMS phrase, IDIQ",
        "branch": "NAVY",
        "text": (
            "Kongsberg Defence & Aerospace AS, Kongsberg, Norway, is awarded a "
            "$404,354,590 firm-fixed-price, indefinite-delivery/indefinite-quantity "
            "contract for missiles. This contract involves Foreign Military Sales to "
            "Australia. Work is expected to be completed by Sept. 24, 2034."
        ),
        "expect": {
            "company": "Kongsberg Defence & Aerospace AS",
            "city_state": "Kongsberg, Norway",
            "amount_usd": 404354590.0,
            "is_modification": 0,
            "is_multiple_award": 0,
            "contract_number": "",
            "completion_date": "2034-09-24",
            "is_foreign_military_sales": 1,
            "contract_type": "firm-fixed-price / indefinite-delivery/indefinite-quantity",
        },
    },
    {
        "name": "dollar amount written with the word million; subsidiary clause",
        "branch": "NAVY",
        "text": (
            "Sikorsky Aircraft Corp., a Lockheed Martin Co., Stratford, Connecticut, "
            "is awarded a $21.6 million firm-fixed-price contract (N00019-24-C-0010) "
            "for maintenance. Work is expected to be completed by June 30, 2027."
        ),
        "expect": {
            "company": "Sikorsky Aircraft Corp.",
            "city_state": "Stratford, Connecticut",
            "amount_usd": 21600000.0,
            "is_modification": 0,
            "contract_number": "N00019-24-C-0010",
            "completion_date": "2027-06-30",
            "contract_type": "firm-fixed-price",
        },
    },
    {
        "name": "unicode hyphens in type and contract number; date before completion",
        "branch": "DEFENSE LOGISTICS AGENCY",
        "text": (
            "GE Medical Systems Information Technologies Inc., Wauwatosa, Wisconsin, "
            "has been awarded a maximum $450,000,000 firm\u2010fixed\u2010price, "
            "indefinite\u2010delivery/indefinite\u2010quantity contract for patient monitoring "
            "systems. Location of performance is Wisconsin, with a Jan. 10, 2024, "
            "performance completion date. The contracting activity is the Defense "
            "Logistics Agency Troop Support, Philadelphia, Pennsylvania "
            "(SPE2D1\u201019\u2010D\u20100010)."
        ),
        "expect": {
            "company": "GE Medical Systems Information Technologies Inc.",
            "city_state": "Wauwatosa, Wisconsin",
            "amount_usd": 450000000.0,
            "is_modification": 0,
            "is_multiple_award": 0,
            "contract_number": "SPE2D1-19-D-0010",
            "completion_date": "2024-01-10",
            "contract_type": "firm-fixed-price / indefinite-delivery/indefinite-quantity",
        },
    },
    {
        "name": "adverb between auxiliary and awarded",
        "branch": "DEFENSE LOGISTICS AGENCY",
        "text": (
            "CACI NSS Inc., Chantilly, Virginia, was competitively awarded a "
            "firm-fixed-price contract for $8,582,382 on Jan. 11, 2019. The "
            "contracting activity is the Defense Health Agency (HT0015-19-F-0018)."
        ),
        "expect": {
            "company": "CACI NSS Inc.",
            "city_state": "Chantilly, Virginia",
            "amount_usd": 8582382.0,
            "is_modification": 0,
            "is_multiple_award": 0,
            "contract_number": "HT0015-19-F-0018",
            "contract_type": "firm-fixed-price",
        },
    },
    {
        "name": "completion phrased as no later than a month and year",
        "branch": "NAVY",
        "text": (
            "Advanced Technology Construction Co., Tacoma, Washington, are awarded a "
            "combined-maximum-value $1,000,000,000 indefinite-delivery/indefinite-quantity "
            "contract (N44255-26-D-1600). Work will complete no later than September 2028."
        ),
        "expect": {
            "company": "Advanced Technology Construction Co.",
            "city_state": "Tacoma, Washington",
            "amount_usd": 1000000000.0,
            "contract_number": "N44255-26-D-1600",
            "completion_date": "2028-09",
            "contract_type": "indefinite-delivery/indefinite-quantity",
        },
    },
    {
        "name": "title dates: single day, range, and same-month span",
        "titles": {
            "Contracts for Sept. 25, 2026": ("2026-09-25", ["2026-09-25"]),
            "Contracts For Jan. 11, 2019": ("2019-01-11", ["2019-01-11"]),
            "Contracts for Feb. 2, 2026, Through Feb. 4, 2026": (
                "2026-02-04",
                ["2026-02-02", "2026-02-03", "2026-02-04"],
            ),
            "Contracts For March 9-10, 2019": (
                "2019-03-10",
                ["2019-03-09", "2019-03-10"],
            ),
        },
    },
]


def run_self_test() -> tuple[int, int, list[str]]:
    lines = []
    passed = 0
    failed = 0
    for case in SELF_TESTS:
        if "titles" in case:
            ok = True
            details = []
            for title, (pub, covered) in case["titles"].items():
                got_pub = publish_date_from_title(title)
                got_cov = dates_in_title(title)
                if got_pub != pub or got_cov != covered:
                    ok = False
                    details.append(
                        f"    title {title!r}: pub {got_pub} expected {pub}; "
                        f"covered {got_cov} expected {covered}"
                    )
            if ok:
                passed += 1
                lines.append(f"PASS  {case['name']}")
            else:
                failed += 1
                lines.append(f"FAIL  {case['name']}")
                lines.extend(details)
            continue
        got = parse_award_paragraph(case["text"], case["branch"])
        bad = []
        for key, exp in case["expect"].items():
            g = got[key]
            if isinstance(exp, float):
                if g is None or abs(g - exp) > 0.01:
                    bad.append(f"    {key}: got {g!r} expected {exp!r}")
            elif g != exp:
                bad.append(f"    {key}: got {g!r} expected {exp!r}")
        if bad:
            failed += 1
            lines.append(f"FAIL  {case['name']}")
            lines.extend(bad)
        else:
            passed += 1
            lines.append(f"PASS  {case['name']}")
    return passed, failed, lines


def parse_listing(html: str) -> list[dict]:
    rows = re.findall(
        r'article-id="(\d+)"\s+article-title="([^"]*)"\s+'
        r'article-alt="[^"]*"\s+article-url="([^"]+)"',
        html,
    )
    out = []
    seen = set()
    for aid, title, url in rows:
        if aid in seen:
            continue
        seen.add(aid)
        title = unescape(title)
        covered = dates_in_title(title)
        pub = max(covered) if covered else ""
        out.append({
            "article_id": aid,
            "title": title,
            "url": url,
            "publish_date": pub,
            "covered_dates": covered,
        })
    return out


OBLIGATED_RE = re.compile(
    r"\$([\d,]+)\s*(?:\([^)]*\)\s*)?(?:[^.$]{0,160}?)obligated at (?:the )?time of (?:the )?award",
    re.I,
)
NO_FUNDS_RE = re.compile(r"no funds (?:will be|are being) obligated", re.I)
IDIQ_RE = re.compile(r"indefinite-delivery|\bIDIQ\b", re.I)


def clean_paragraph(text: str) -> str:
    text = normalize_dashes(text or "")
    text = re.sub(
        r"\s*Choose which (?:War|Defense)\.gov products you want delivered to your inbox\.?",
        "",
        text,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", text.replace("*", "")).strip()


def obligated_amount(text: str, is_multi: bool):
    """Headline obligated dollars. None when the paragraph does not say."""
    if is_multi:
        return None
    clean = clean_paragraph(text)
    if NO_FUNDS_RE.search(clean):
        return 0
    found = OBLIGATED_RE.findall(clean)
    if not found:
        return None
    total = 0
    for raw in found:
        total += int(raw.replace(",", ""))
    return total


def is_idiq_text(text: str, contract_type: str = "") -> bool:
    return bool(IDIQ_RE.search(f"{contract_type or ''} {text or ''}"))


def award_blurb(text: str) -> str:
    clean = clean_paragraph(text)
    match = CLAUSE_END_RE.search(clean)
    if not match:
        return clean
    return clean[match.start():].strip()


def split_awardees(text: str, branch: str) -> list[dict]:
    """One dict per awardee. Multi-award paragraphs keep the shared ceiling."""
    base = parse_award_paragraph(text, branch)
    clean = clean_paragraph(text)
    match = CLAUSE_END_RE.search(clean)
    head = clean[: match.start()] if match else clean
    parts = [part.strip(" ;") for part in head.split(";") if part.strip(" ;")]
    rows = [base]
    if base.get("is_multiple_award") and len(parts) >= 2:
        split_rows = []
        for part in parts:
            part = re.sub(r"^(?:and|or)\s+", "", part, flags=re.I).strip()
            company, city_state = split_company(part)
            numbers = contract_numbers(part)
            if not company:
                continue
            row = dict(base)
            row["company"] = company
            row["city_state"] = city_state
            row["company_raw"] = part
            row["contract_number"] = numbers[0] if numbers else ""
            row["is_multiple_award"] = 1
            split_rows.append(row)
        if split_rows:
            rows = split_rows
    n = len(rows) if base.get("is_multiple_award") else 1
    for row in rows:
        row["n_awardees"] = n if base.get("is_multiple_award") else 1
        if base.get("is_multiple_award"):
            row["is_multiple_award"] = 1
    return rows
