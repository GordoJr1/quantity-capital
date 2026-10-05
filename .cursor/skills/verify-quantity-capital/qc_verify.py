#!/usr/bin/env python3
"""Drive the Quantity Capital static site and write proof evidence.

Sync Playwright (Chromium) plus Pillow. Serves nothing itself: point --base
at an http.server you started. Does not rebuild JSON, publish, or open sqlite.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

LIVE_ORIGIN = "https://gordojr1.github.io/quantity-capital"
VIEWPORTS = ((390, 844), (1280, 800))
NVDA = "NVDA"
EMPTY_QUERY = "zzzz-no-such-ticker"
FILER_ID = "abigail-spanberger"
FILER_QUERY = "Spanberger"


def repo_root() -> Path:
    # .cursor/skills/verify-quantity-capital/qc_verify.py
    return Path(__file__).resolve().parents[3]


def default_evidence() -> Path:
    env = os.environ.get("QC_VERIFY_EVIDENCE")
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / "qc-verify-evidence"


def evidence_dir(path: Path | None) -> Path:
    dest = (path or default_evidence()).resolve()
    root = repo_root()
    if dest == root or root in dest.parents:
        raise SystemExit(f"evidence directory must sit outside the repo: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def fetch(url: str, timeout: float = 20) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def pid_owns_loopback(pid: int, port: int) -> tuple[bool, str]:
    """Return whether `pid` is LISTENING on 127.0.0.1:`port`. Never matches by process name."""
    out = subprocess.check_output(["netstat", "-ano", "-p", "tcp"], text=True, errors="replace")
    owners: list[str] = []
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[1]
        owner = parts[-1]
        if local.startswith("127.0.0.1:") and local.endswith(f":{port}"):
            owners.append(owner)
    if not owners:
        return False, "no LISTENING socket on 127.0.0.1:%s" % port
    ok = any(owner == str(pid) for owner in owners)
    return ok, "listening pid(s) %s" % ",".join(owners)


def doctor(base: str, pid: int | None) -> dict:
    base = base.rstrip("/")
    report: dict = {"base": base, "ok": False, "checks": []}

    def add(name: str, ok: bool, detail: str) -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        import playwright.sync_api  # noqa: F401
        add("playwright", True, "playwright.sync_api imports")
    except Exception as exc:  # noqa: BLE001
        add("playwright", False, "import failed: %s" % exc)
    try:
        import PIL  # noqa: F401
        add("pillow", True, "PIL imports")
    except Exception as exc:  # noqa: BLE001
        add("pillow", False, "import failed: %s" % exc)

    if not base.startswith("http://127.0.0.1:"):
        add("loopback", False, "verification base must be http://127.0.0.1:<port> (got %s)" % base)
    else:
        add("loopback", True, base)
        port = int(base.rsplit(":", 1)[-1])
        if pid is None:
            add("port-owner", False, "pass --pid of the http.server you started")
        else:
            try:
                owned, detail = pid_owns_loopback(pid, port)
            except Exception as exc:  # noqa: BLE001
                owned, detail = False, str(exc)
            add("port-owner", owned, "pid %s %s" % (pid, detail))

    status, body = fetch(base + "/index.html")
    text = body.decode("utf-8", "replace")
    add("index", status == 200 and 'id="wire"' in text and "Quantity" in text,
        "HTTP %s, %s bytes" % (status, len(body)))

    status, body = fetch(base + "/tape/page.json")
    try:
        page = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        page = {}
    trades = page.get("trades") if isinstance(page, dict) else None
    add("tape-page", status == 200 and isinstance(trades, list) and len(trades) > 0 and bool(page.get("collected")),
        "HTTP %s collected=%s trades=%s" % (status, page.get("collected") if isinstance(page, dict) else None,
                                             len(trades) if isinstance(trades, list) else None))

    status, body = fetch(base + "/sw.js")
    sw = body.decode("utf-8", "replace")
    cache_line = next((line.strip() for line in sw.splitlines() if line.strip().startswith("const CACHE")), "")
    add("shell-cache", status == 200 and cache_line.startswith('const CACHE = "qc-shell-v'),
        cache_line or "HTTP %s" % status)
    report["cache"] = cache_line

    report["ok"] = all(item["ok"] for item in report["checks"])
    return report


class PageLog:
    def __init__(self) -> None:
        self.console: list[dict] = []
        self.pageerrors: list[str] = []
        self.failed: list[dict] = []
        self.urls: list[str] = []

    def attach(self, page) -> None:
        def on_console(msg) -> None:
            if msg.type in ("error", "warning"):
                self.console.append({"type": msg.type, "text": msg.text})

        def on_fail(req) -> None:
            self.failed.append({"url": req.url, "error": req.failure})

        page.on("console", on_console)
        page.on("pageerror", lambda err: self.pageerrors.append(str(err)))
        page.on("requestfailed", on_fail)
        page.on("response", lambda res: self.urls.append(res.url))

    def dump(self) -> dict:
        return {
            "console": self.console,
            "pageerrors": self.pageerrors,
            "failed_requests": self.failed,
        }


def open_browser():
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    return pw, browser


def new_page(browser, width: int, height: int):
    kwargs = dict(viewport={"width": width, "height": height}, locale="en-US", device_scale_factor=1)
    try:
        context = browser.new_context(service_workers="block", **kwargs)
    except TypeError:
        context = browser.new_context(**kwargs)
    page = context.new_page()
    page.set_default_timeout(60000)
    log = PageLog()
    log.attach(page)
    return context, page, log


def wait_tape_ready(page) -> None:
    page.wait_for_function(
        """() => {
          const asof = (document.querySelector('#asof')?.textContent || '').trim();
          const wire = document.querySelector('#wire');
          if (!asof || !wire) return false;
          if ((wire.innerText || '').includes('Could not load trades.json')) return false;
          const rows = wire.querySelectorAll('li.w-row, li.w1');
          return document.body.classList.contains('is-ready') && rows.length > 0;
        }"""
    )


def open_search_fold(page) -> None:
    fold = page.locator("details.filter-fold")
    fold.wait_for(state="attached")
    if not fold.evaluate("el => el.open"):
        page.locator("details.filter-fold > summary").click()
        page.wait_for_function("() => document.querySelector('details.filter-fold').open")


def assert_nav(page, width: int) -> None:
    header = page.locator("nav.qc-nav[aria-label='Politician tape']")
    tabs = page.locator("nav.tabbar[aria-label='Politician tape']")
    if width >= 821:
        header.wait_for(state="visible")
        tabs.wait_for(state="hidden")
    else:
        header.wait_for(state="hidden")
        tabs.wait_for(state="visible")
    mark = page.locator("a.mark").first
    mark.wait_for(state="visible")
    text = mark.inner_text()
    if "quantity" not in text.casefold():
        raise AssertionError("mark text %r" % text)


def filter_nvda(page) -> None:
    open_search_fold(page)
    company = page.locator("#filter-company")
    company.wait_for(state="visible")
    company.fill(NVDA)
    page.wait_for_function(
        """() => {
          if (!location.search.includes('q=NVDA')) return false;
          const wire = document.querySelector('#wire');
          const text = wire ? wire.innerText : '';
          if (!text.includes('NVDA') || text.includes('No trades match these filters.')) return false;
          const symbols = [...document.querySelectorAll('#wire .w-tk, #wire .w1-tk b')];
          return symbols.some((n) => (n.textContent || '').trim().toUpperCase() === 'NVDA');
        }"""
    )


def filter_empty(page) -> None:
    open_search_fold(page)
    page.locator("#filter-company").fill(EMPTY_QUERY)
    page.wait_for_function(
        """() => (document.querySelector('#wire')?.innerText || '').includes('No trades match these filters.')"""
    )


def open_filer(page) -> None:
    open_search_fold(page)
    who = page.locator("#who-search")
    who.fill(FILER_QUERY)
    button = page.locator("#who-menu.open button[data-id='%s']" % FILER_ID)
    button.wait_for(state="visible")
    button.click()
    page.wait_for_url("**/politician.html?id=%s*" % FILER_ID)
    page.wait_for_function(
        """() => (document.querySelector('#who')?.textContent || '').includes('Spanberger')
              && !(document.querySelector('#rows')?.innerText || '').includes('Could not load')"""
    )


def shot(page, path: Path, selector: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if selector:
        loc = page.locator(selector).first
        loc.wait_for(state="attached")
        loc.scroll_into_view_if_needed()
    page.screenshot(path=str(path), full_page=False)


def wire_text(page) -> str:
    return page.locator("#wire").inner_text()


def stitch(left: Path, right: Path, out: Path, label_left: str = "local", label_right: str = "live") -> None:
    from PIL import Image, ImageDraw

    a = Image.open(left).convert("RGB")
    b = Image.open(right).convert("RGB")
    height = max(a.height, b.height)

    def fit(im: Image.Image) -> Image.Image:
        if im.height == height:
            return im
        width = max(1, int(im.width * height / im.height))
        return im.resize((width, height), Image.Resampling.LANCZOS)

    a, b = fit(a), fit(b)
    pad, gap = 28, 12
    canvas = Image.new("RGB", (a.width + b.width + gap, height + pad), (18, 20, 26))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 6), label_left, fill=(216, 221, 227))
    draw.text((a.width + gap + 8, 6), label_right, fill=(216, 221, 227))
    canvas.paste(a, (0, pad))
    canvas.paste(b, (a.width + gap, pad))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)


def drive_politician_tape(base: str, live: str, evidence: Path, viewports: list[tuple[int, int]]) -> dict:
    base = base.rstrip("/")
    live = live.rstrip("/")
    root = evidence / "politician-tape"
    root.mkdir(parents=True, exist_ok=True)
    pw, browser = open_browser()
    report: dict = {
        "feature": "politician-tape",
        "base": base,
        "live": live,
        "query": NVDA,
        "filer_id": FILER_ID,
        "viewports": [],
        "ok": False,
    }
    try:
        for width, height in viewports:
            name = "%sx%s" % (width, height)
            folder = root / name
            folder.mkdir(parents=True, exist_ok=True)
            entry: dict = {"viewport": name, "ok": False}
            context, page, log = new_page(browser, width, height)
            try:
                page.goto(base + "/index.html", wait_until="domcontentloaded")
                wait_tape_ready(page)
                assert_nav(page, width)
                shot(page, folder / "local-before.png", "#wire")
                filter_nvda(page)
                if not any(url.endswith("/tape/all.json") for url in log.urls):
                    raise AssertionError("company filter did not request tape/all.json")
                shot(page, folder / "local-action.png", "#filter-company")
                shot(page, folder / "local-after.png", "#wire")
                (folder / "wire-after.txt").write_text(
                    page.url + "\n" + wire_text(page)[:4000], encoding="utf-8"
                )
                filter_empty(page)
                shot(page, folder / "local-empty.png", "#wire")
                open_filer(page)
                shot(page, folder / "local-politician.png", "#who")
                entry["local_url_after_filter"] = "q=%s" % NVDA
                entry["politician_url"] = page.url
                entry["politician_heading"] = page.locator("#who").inner_text()
            finally:
                context.close()

            context, page, live_log = new_page(browser, width, height)
            try:
                page.goto(live + "/index.html", wait_until="domcontentloaded")
                wait_tape_ready(page)
                assert_nav(page, width)
                filter_nvda(page)
                shot(page, folder / "live-after.png", "#wire")
                (folder / "wire-live.txt").write_text(
                    page.url + "\n" + wire_text(page)[:4000], encoding="utf-8"
                )
                entry["live_url"] = page.url
            finally:
                context.close()

            stitch(folder / "local-after.png", folder / "live-after.png", folder / "local-vs-live.png")
            console = {"local": log.dump(), "live": live_log.dump()}
            (folder / "console.json").write_text(json.dumps(console, indent=2), encoding="utf-8")
            entry["console_errors"] = [row["text"] for row in log.console if row["type"] == "error"]
            entry["live_console_errors"] = [row["text"] for row in live_log.console if row["type"] == "error"]
            entry["pageerrors"] = log.pageerrors + live_log.pageerrors
            entry["ok"] = True
            entry["stitch"] = str(folder / "local-vs-live.png")
            report["viewports"].append(entry)
        report["ok"] = all(item["ok"] for item in report["viewports"])
    except Exception as exc:
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        raise
    finally:
        browser.close()
        pw.stop()
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def wait_insider_ready(page) -> None:
    page.wait_for_function(
        """() => {
          const asof = (document.querySelector('#asof')?.textContent || '').trim();
          const rows = document.querySelectorAll('#rows li.qc-txn-tape');
          const text = document.querySelector('#rows')?.innerText || '';
          return !!asof && rows.length > 0 && !text.includes('Could not load insider tape');
        }"""
    )


def drive_insiders_tape(base: str, evidence: Path, viewports: list[tuple[int, int]]) -> dict:
    base = base.rstrip("/")
    root = evidence / "insiders-tape"
    pw, browser = open_browser()
    report: dict = {"feature": "insiders-tape", "base": base, "viewports": [], "ok": False}
    try:
        for width, height in viewports:
            folder = root / ("%sx%s" % (width, height))
            folder.mkdir(parents=True, exist_ok=True)
            context, page, log = new_page(browser, width, height)
            try:
                page.goto(base + "/insiders.html", wait_until="domcontentloaded")
                wait_insider_ready(page)
                ticker = page.locator("#rows .qc-txn-tk").first.inner_text().strip().split()[0]
                page.locator("#q").fill(ticker)
                page.wait_for_function(
                    """(ticker) => {
                      const q = new URLSearchParams(location.search).get('q') || '';
                      if (q.toUpperCase() !== ticker.toUpperCase()) return false;
                      const nodes = [...document.querySelectorAll('#rows .qc-txn-tk')];
                      return nodes.length > 0 && nodes.every((n) => (n.textContent || '').toUpperCase().includes(ticker.toUpperCase()));
                    }""",
                    arg=ticker,
                )
                shot(page, folder / "local-after.png", "#rows")
                page.locator("#q").fill(EMPTY_QUERY)
                page.wait_for_function(
                    """() => (document.querySelector('#rows')?.innerText || '').includes('No insider prints in this window.')"""
                )
                shot(page, folder / "local-empty.png", "#rows")
                entry = {"viewport": "%sx%s" % (width, height), "ticker": ticker, "ok": True,
                         "console_errors": [row["text"] for row in log.console if row["type"] == "error"],
                         "pageerrors": log.pageerrors}
            finally:
                context.close()
            (folder / "console.json").write_text(json.dumps(log.dump(), indent=2), encoding="utf-8")
            report["viewports"].append(entry)
        report["ok"] = all(item["ok"] for item in report["viewports"])
    finally:
        browser.close()
        pw.stop()
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def drive_leaders(base: str, evidence: Path, viewports: list[tuple[int, int]]) -> dict:
    base = base.rstrip("/")
    root = evidence / "leaders"
    pw, browser = open_browser()
    report: dict = {"feature": "leaders", "base": base, "viewports": [], "ok": False}
    try:
        for width, height in viewports:
            folder = root / ("%sx%s" % (width, height))
            folder.mkdir(parents=True, exist_ok=True)
            context, page, log = new_page(browser, width, height)
            try:
                page.goto(base + "/insider-board.html", wait_until="domcontentloaded")
                page.wait_for_selector("#board article.card a.who")
                page.get_by_role("tab", name="Repeatable").click()
                page.wait_for_function(
                    """() => location.search.includes('b=repeatable') && document.querySelectorAll('table.rep tbody tr').length > 0"""
                )
                page.locator("#rep-windows button[data-h='30']").click()
                page.wait_for_function(
                    """() => location.search.includes('h=30') && document.querySelector('table.rep th')?.textContent.includes('30d')"""
                )
                shot(page, folder / "local-after.png", "table.rep")
                entry = {"viewport": "%sx%s" % (width, height), "url": page.url, "ok": True,
                         "console_errors": [row["text"] for row in log.console if row["type"] == "error"],
                         "pageerrors": log.pageerrors}
            finally:
                context.close()
            (folder / "console.json").write_text(json.dumps(log.dump(), indent=2), encoding="utf-8")
            report["viewports"].append(entry)
        report["ok"] = all(item["ok"] for item in report["viewports"])
    finally:
        browser.close()
        pw.stop()
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def drive_ticker(base: str, evidence: Path, viewports: list[tuple[int, int]]) -> dict:
    base = base.rstrip("/")
    root = evidence / "ticker-chart"
    pw, browser = open_browser()
    report: dict = {"feature": "ticker-chart", "base": base, "ticker": NVDA, "viewports": [], "ok": False}
    try:
        for width, height in viewports:
            folder = root / ("%sx%s" % (width, height))
            folder.mkdir(parents=True, exist_ok=True)
            context, page, log = new_page(browser, width, height)
            try:
                page.goto(base + "/ticker.html?t=NVDA", wait_until="domcontentloaded")
                page.wait_for_function(
                    """() => {
                      const who = (document.querySelector('#who')?.textContent || '').trim();
                      const svg = document.querySelector('#chart-svg');
                      const px = (document.querySelector('#last-px')?.textContent || '').trim();
                      return who && who !== '\\u2014' && who !== '—' && svg && svg.childElementCount > 0 && px && px !== '—' && px !== '\\u2014';
                    }"""
                )
                page.locator("#range button[data-r='1y']").click()
                page.wait_for_function(
                    """() => document.querySelector('#range button[data-r=\"1y\"]')?.getAttribute('aria-selected') === 'true'"""
                )
                page.wait_for_selector("#marks li.qc-txn-tape")
                shot(page, folder / "local-after.png", "#chart-svg")
                entry = {"viewport": "%sx%s" % (width, height), "heading": page.locator("#who").inner_text(), "ok": True,
                         "console_errors": [row["text"] for row in log.console if row["type"] == "error"],
                         "pageerrors": log.pageerrors}
            finally:
                context.close()
            (folder / "console.json").write_text(json.dumps(log.dump(), indent=2), encoding="utf-8")
            report["viewports"].append(entry)
        report["ok"] = all(item["ok"] for item in report["viewports"])
    finally:
        browser.close()
        pw.stop()
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def drive_filed(base: str, evidence: Path, viewports: list[tuple[int, int]]) -> dict:
    base = base.rstrip("/")
    root = evidence / "filed"
    pw, browser = open_browser()
    report: dict = {"feature": "filed", "base": base, "viewports": [], "ok": False}
    try:
        for width, height in viewports:
            folder = root / ("%sx%s" % (width, height))
            folder.mkdir(parents=True, exist_ok=True)
            context, page, log = new_page(browser, width, height)
            try:
                page.goto(base + "/landed.html", wait_until="domcontentloaded")
                page.wait_for_function(
                    """() => {
                      const board = document.querySelector('#board')?.innerText || '';
                      const asof = (document.querySelector('#asof')?.textContent || '').trim();
                      return !!asof && !board.includes('Reading the tape') && !board.includes('Could not load the tape');
                    }"""
                )
                page.get_by_role("button", name="Last 7 days").click()
                page.wait_for_function(
                    """() => location.search.includes('h=168') &&
                      !(document.querySelector('#board')?.innerText || '').includes('Could not load the tape')"""
                )
                shot(page, folder / "local-after.png", "#board")
                entry = {"viewport": "%sx%s" % (width, height), "url": page.url, "ok": True,
                         "board": page.locator("#board").inner_text()[:500],
                         "console_errors": [row["text"] for row in log.console if row["type"] == "error"],
                         "pageerrors": log.pageerrors}
            finally:
                context.close()
            (folder / "console.json").write_text(json.dumps(log.dump(), indent=2), encoding="utf-8")
            report["viewports"].append(entry)
        report["ok"] = all(item["ok"] for item in report["viewports"])
    finally:
        browser.close()
        pw.stop()
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


DRIVES = {
    "politician-tape": drive_politician_tape,
    "insiders-tape": drive_insiders_tape,
    "leaders": drive_leaders,
    "ticker-chart": drive_ticker,
    "filed": drive_filed,
}


def parse_viewports(values: list[str] | None) -> list[tuple[int, int]]:
    if not values:
        return list(VIEWPORTS)
    out = []
    for raw in values:
        w, h = raw.lower().split("x")
        out.append((int(w), int(h)))
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Quantity Capital verification harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    doc = sub.add_parser("doctor")
    doc.add_argument("--base", required=True)
    doc.add_argument("--pid", type=int)
    doc.add_argument("--evidence")

    drv = sub.add_parser("drive")
    drv.add_argument("feature", choices=sorted(DRIVES))
    drv.add_argument("--base", required=True)
    drv.add_argument("--live", default=LIVE_ORIGIN)
    drv.add_argument("--evidence")
    drv.add_argument("--viewport", action="append", help="WxH. Repeatable. Default: 390x844 and 1280x800.")

    sti = sub.add_parser("stitch")
    sti.add_argument("--left", required=True)
    sti.add_argument("--right", required=True)
    sti.add_argument("--out", required=True)
    sti.add_argument("--label-left", default="local")
    sti.add_argument("--label-right", default="live")

    args = parser.parse_args(argv)
    if args.cmd == "stitch":
        stitch(Path(args.left), Path(args.right), Path(args.out), args.label_left, args.label_right)
        print(args.out)
        return 0

    evidence = evidence_dir(Path(args.evidence) if args.evidence else None)
    if args.cmd == "doctor":
        report = doctor(args.base, args.pid)
        dest = evidence / "doctor.json"
        dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    viewports = parse_viewports(args.viewport)
    feature = args.feature
    if feature == "politician-tape":
        report = drive_politician_tape(args.base, args.live, evidence, viewports)
    else:
        report = DRIVES[feature](args.base, evidence, viewports)
    print(json.dumps({"ok": report["ok"], "feature": feature, "evidence": str(evidence)}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except AssertionError as exc:
        print("assertion failed: %s" % exc, file=sys.stderr)
        raise SystemExit(1)
