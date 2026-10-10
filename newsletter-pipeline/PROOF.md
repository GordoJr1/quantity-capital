# Proof: Oct 9 Daily Brief and the Oct 5 Exec reference

No email was sent. AgentMail was not called.

## Commands

```
python3 newsletter-pipeline/fill_brief.py --product daily --json newsletter-pipeline/fixtures/daily-20261009.json --out /tmp/qc-daily-20261009.html
python3 newsletter-pipeline/fill_brief.py --product exec --json newsletter-pipeline/fixtures/exec-20261005.json --out /tmp/qc-exec-20261005.html
python3 newsletter-pipeline/rule_check.py --daily-json newsletter-pipeline/fixtures/daily-20261009.json --daily-html /tmp/qc-daily-20261009.html --exec-json newsletter-pipeline/fixtures/exec-20261005.json --exec-html /tmp/qc-exec-20261005.html
```

## Match

Daily filled HTML is byte-identical to `fixtures/newsletter-20261009.html` (36,973 bytes).

Exec filled HTML is not byte-identical to `fixtures/exec-brief-20261005-reference.html` (60,037 vs 60,049). The hand-built notes encode three dollar signs as `&#36;`. The fill script writes `$`. After those three entities are read as `$`, the files match. Nothing else differs.

Locked design markers from `templates/daily-design-markers.txt` and `templates/exec-design-markers.txt` are all present. Every `required_substrings` entry for the daily shell and the exec standard layout is present. Exec forbidden markers are absent. The `<style>` block matches the locked shell for both briefs.

## Rule check

Exit 1. The script printed `DO NOT CALL REVIEWER BOT`.

Three hand-built exec sentences are over the 20-word cap:

- `big3[0].why_it_matters` sentence 2 has 21 words.
- `projects[0].summary` sentence 1 has 23 words.
- `projects[1].power` sentence 1 has 22 words.

Template match, source ids, cross-brief repeats, and link presence passed. The live link check warned once: `https://openai.com/index/introducing-gpt-6-1-sol` returned HTTP 403. That host blocks scripts, so it was not a failure. No link returned 404.

A big-story fill (`--product big-story`) clears the bracket placeholders and keeps `<!-- layout: big-story -->`, `BIG STORY`, `WHAT HAPPENED`, `WHY IT MATTERS`, `WHAT TO WATCH`, and `Benchmark Scoreboard`.
