#!/usr/bin/env python3
"""Jev review gate: rate fixes before publishing, review after publishing.

Stdlib only. Uses TypeSafe System One (model ``jev-latest``) for the
judgments; all workflow, thresholds, and gating live in this script.

Requires ``TYPESAFE_API_KEY`` in the environment for live calls.
``--dry-run`` prints the exact request payload without sending anything,
so question design can be inspected (and the gate tested) without a key.

Usage (pre-publish: rate the fixes)::

    TYPESAFE_API_KEY=... python3 jev-review.py rate [--base main]

Usage (post-publish: review what shipped)::

    TYPESAFE_API_KEY=... python3 jev-review.py review [--base main]

``rate`` exits 0 when Jev says the change is ready AND correct enough,
2 when it fails the gate, 1 on usage/API errors. ``review`` is advisory
and always exits 0 on a successful call.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
MAX_DIFF_CHARS = 15000
RETRIES = 3

# Off-repo collector outputs. A feature change should not ship these;
# this is a deterministic code-owned check, not a model judgment.
COLLECTOR_FILES = (
    "trades.json",
    "trades-lite.json",
    "analysis.json",
    "tells.json",
    "traders.json",
    "tickers.json",
    "backtest.json",
    "insider-trades.json",
    "insider-trades-lite.json",
)


def run_git(*args):
    p = subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__))
    )
    return p.stdout.strip() if p.returncode == 0 else ""


def collect_state(mode, base):
    """Build the Jev `state`: branch summary plus the diff under review."""
    branch = run_git("branch", "--show-current")
    committed = run_git("log", f"{base}..HEAD", "--oneline")
    committed_diff = run_git("diff", f"{base}...HEAD")
    uncommitted = run_git("status", "-sb")
    working_diff = run_git("diff", "HEAD", "--", ".")
    diff = ""
    if committed_diff:
        diff += "# committed vs %s\n%s\n" % (base, committed_diff)
    if working_diff:
        diff += "# uncommitted working tree\n%s\n" % working_diff
    truncated = len(diff) > MAX_DIFF_CHARS
    if truncated:
        diff = diff[:MAX_DIFF_CHARS] + "\n# ... diff truncated ..."
    state = {
        "mode": mode,
        "branch": branch,
        "base": base,
        "commits": committed,
        "working_tree": uncommitted,
        "diff": diff,
        "diff_truncated": truncated,
    }
    changed = run_git("diff", "--name-only", f"{base}...HEAD") + "\n" + run_git(
        "diff", "--name-only", "HEAD"
    )
    state["collector_files_touched"] = sorted(
        {f for f in (line.strip() for line in changed.splitlines()) if f in COLLECTOR_FILES}
    )
    return state


def rate_questions():
    """Pre-publish judgments: correctness, risk, publish-readiness."""
    return {
        "correctness": {
            "type": "score",
            "instructions": (
                "Will this change achieve its stated goal without breaking "
                "existing behavior? Judge the diff on `diff`, with commit "
                "messages in `commits` as the stated goal."
            ),
            "criteria": [
                "Breaks behavior or misses the goal",
                "Partially works or has unresolved gaps",
                "Achieves the goal cleanly with no regressions",
            ],
        },
        "risk": {
            "type": "score",
            "instructions": (
                "If this change is wrong, how bad is the impact on the "
                "published site or its committed data?"
            ),
            "criteria": [
                "Cosmetic or trivially reversible",
                "Visibly wrong data or a broken page until reverted",
                "Corrupt data, outage, or loss of user trust",
            ],
        },
        "ready": {
            "type": "noul",
            "instructions": "Is this change ready to publish as-is?",
            "criteria": {
                "true": "Complete, verified, and safe to ship as-is",
                "false": "Needs more work, testing, or fixes before shipping",
            },
        },
    }


def review_questions():
    """Post-publish judgments: did it land clean, and what follow-up remains."""
    return {
        "shipped_clean": {
            "type": "noul",
            "instructions": (
                "Did the published change land as intended, with no defects, "
                "regressions, or unfinished work left behind? Judge the "
                "shipped diff on `diff`."
            ),
            "criteria": {
                "true": "Shipped as intended, nothing outstanding",
                "false": "Defects, regressions, or unfinished work remain",
            },
        },
        "followup": {
            "type": "score",
            "instructions": "What follow-up does the shipped change need?",
            "criteria": [
                "None - done",
                "Minor polish worth scheduling",
                "Prompt fix required",
            ],
        },
    }


def call_jev(state, questions, api_key):
    body = json.dumps(
        {"state": state, "model": MODEL, "questions": questions}
    ).encode()
    last_err = None
    for attempt in range(RETRIES):
        req = urllib.request.Request(
            API_URL,
            data=body,
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:  # noqa: F821 (imported below)
            payload = e.read().decode(errors="replace")
            if e.code == 401:
                raise SystemExit(
                    "error: TypeSafe rejected the API key (401). "
                    "Check TYPESAFE_API_KEY."
                )
            if e.code in (429, 529) and attempt < RETRIES - 1:
                last_err = "%s %s" % (e.code, payload)
                time.sleep(2 * (2**attempt))
                continue
            raise SystemExit("error: TypeSafe HTTP %s: %s" % (e.code, payload))
        except urllib.error.URLError as e:  # noqa: F821
            last_err = str(e)
            if attempt < RETRIES - 1:
                time.sleep(2 * (2**attempt))
                continue
            raise SystemExit("error: cannot reach TypeSafe API: %s" % last_err)
    raise SystemExit("error: TypeSafe call failed after retries: %s" % last_err)


import urllib.error  # noqa: E402  (kept late so helpers read top-down)


def fmt_score(ans):
    return "score=%.2f confidence=%.2f probs=%s" % (
        ans.get("score"),
        ans.get("confidence"),
        json.dumps(ans.get("probabilities")),
    )


def report(mode, answers):
    print("Jev %s verdict (model jev-latest):" % mode)
    for qid, ans in answers.items():
        if ans.get("type") == "noul":
            print("  %s: noul=%.2f" % (qid, ans.get("noul")))
        elif ans.get("type") == "choice":
            print(
                "  %s: choice=%s confidence=%.2f probs=%s"
                % (qid, ans.get("choice"), ans.get("confidence"),
                   json.dumps(ans.get("probabilities")))
            )
        else:
            print("  %s: %s" % (qid, fmt_score(ans)))


def evaluate_gate(answers, min_ready, min_correct):
    """Pure gate logic (testable without a key). Returns (ok, reasons)."""
    reasons = []
    ready = answers.get("ready", {}).get("noul", 0)
    correct = answers.get("correctness", {}).get("score", 0)
    if ready < min_ready:
        reasons.append("ready %.2f < %.2f" % (ready, min_ready))
    if correct < min_correct:
        reasons.append("correctness %.2f < %.2f" % (correct, min_correct))
    risk = answers.get("risk", {}).get("score")
    return (not reasons, reasons, ready, correct, risk)


def cmd_rate(args):
    state = collect_state("rate-fixes-before-publish", args.base)
    questions = rate_questions()
    if state["collector_files_touched"]:
        print(
            "warning: change touches collector-owned files: %s "
            "(do not ship these in a feature change)"
            % ", ".join(state["collector_files_touched"])
        )
    if args.dry_run:
        print(json.dumps({"state": state, "model": MODEL, "questions": questions}, indent=2))
        return 0
    resp = call_jev(state, questions, args.api_key)
    answers = resp.get("answers", {})
    report("pre-publish rating", answers)
    ok, reasons, ready, correct, risk = evaluate_gate(answers, args.min_ready, args.min_correct)
    if risk is not None and risk >= 1.5:
        print("note: risk score %.2f is high; publish carefully" % risk)
    if ok:
        print("GATE PASS: ready=%.2f correctness=%.2f" % (ready, correct))
        return 0
    print("GATE FAIL: %s" % "; ".join(reasons))
    return 2


def cmd_review(args):
    state = collect_state("review-after-publish", args.base)
    questions = review_questions()
    if args.dry_run:
        print(json.dumps({"state": state, "model": MODEL, "questions": questions}, indent=2))
        return 0
    resp = call_jev(state, questions, args.api_key)
    answers = resp.get("answers", {})
    report("post-publish review", answers)
    shipped = answers.get("shipped_clean", {}).get("noul", 0)
    followup = answers.get("followup", {}).get("score", 0)
    if shipped >= 0.7 and followup < 1.0:
        print("REVIEW: shipped clean, no follow-up needed")
    elif followup >= 1.5 or shipped < 0.4:
        print("REVIEW: prompt fix required - schedule follow-up work")
    else:
        print("REVIEW: landed with minor polish worth scheduling")
    return 0


def self_test():
    """Exercise gate logic on canned Jev answers. No key, no network."""
    good = {
        "correctness": {"type": "score", "score": 1.8, "confidence": 0.8},
        "risk": {"type": "score", "score": 0.2, "confidence": 0.9},
        "ready": {"type": "noul", "noul": 0.92},
    }
    bad = {
        "correctness": {"type": "score", "score": 0.6, "confidence": 0.7},
        "risk": {"type": "score", "score": 1.7, "confidence": 0.6},
        "ready": {"type": "noul", "noul": 0.3},
    }
    ok, _, ready, correct, _ = evaluate_gate(good, 0.7, 1.2)
    assert ok and ready == 0.92 and correct == 1.8, "good case should pass"
    ok, reasons, _, _, _ = evaluate_gate(bad, 0.7, 1.2)
    assert not ok and len(reasons) == 2, "bad case should fail both checks"
    for mode, qs in (("rate", rate_questions()), ("review", review_questions())):
        assert set(qs) >= ({"correctness", "ready"} if mode == "rate" else {"shipped_clean", "followup"})
        for q in qs.values():
            assert q["type"] in ("choice", "score", "noul") and q["instructions"]
    print("self-test: gate logic and question shapes OK")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("rate", "review"):
        p = sub.add_parser(name, help="%s with Jev" % name)
        p.add_argument("--base", default="main", help="base ref for the diff (default: main)")
        p.add_argument("--dry-run", action="store_true", help="print request payload, send nothing")
        p.add_argument("--api-key", default=os.environ.get("TYPESAFE_API_KEY", ""),
                       help="TypeSafe key (default: $TYPESAFE_API_KEY)")
        if name == "rate":
            p.add_argument("--min-ready", type=float, default=0.7)
            p.add_argument("--min-correct", type=float, default=1.2)
    sub.add_parser("self-test", help="validate gate logic without a key or network")
    args = ap.parse_args(argv)
    if args.cmd == "self-test":
        return self_test()
    if not args.dry_run and not args.api_key:
        print("error: set TYPESAFE_API_KEY or use --dry-run / --api-key", file=sys.stderr)
        return 1
    if args.cmd == "rate":
        return cmd_rate(args)
    return cmd_review(args)


if __name__ == "__main__":
    sys.exit(main())
