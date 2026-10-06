# Quantity Capital verification map

This directory is the maintained source for verifying the user-facing behavior of Quantity Capital. Read this index, then use the matching feature file as the recipe. Launch, doctor, evidence paths, the shell-cache bump, and the `pr-check` / Jev rules live in `../SKILL.md`.

## Baseline preconditions

- Serve the repo root. Windows: `python -m http.server <port> --bind 127.0.0.1`. Linux and the QC box: `python3 -m http.server <port> --bind 127.0.0.1`.
- Stop only that server's PID. Windows: `Stop-Process -Id <pid> -Force`. Linux and the QC box: `kill <pid>`. Never kill by process name.
- Run `qc_verify.py doctor` and require a loopback base, your server PID on that port, a non-empty `tape/page.json`, and a `qc-shell-v` cache name.
- Drive only that instance. The live site `https://gordojr1.github.io/quantity-capital/` is a screenshot comparison, not a second app to mutate.
- Use the JSON already in the clone. Do not rebuild it and do not run `publish.py`.
- Pass an evidence directory outside the repo. Cleanup must leave those files in place.
- Run every recipe at `390x844` and `1280x800` unless the feature file says a viewport is impossible.

## Driving conventions

- Start from the baseline state unless the feature file says otherwise.
- Prefer accessible names and stable ids (`#filter-company`, `#wire`, `#q`, `role="tab"`) over coordinates.
- The harness is `python .cursor/skills/verify-quantity-capital/qc_verify.py` on Windows and `python3 .cursor/skills/verify-quantity-capital/qc_verify.py` on Linux and the QC box. If imports are missing, create the venv in `../SKILL.md` and `source <venv>/bin/activate` before `python3`.
- Treat commands as literal. Keep quoted names, ids, and flags unchanged.
- Phone width hides `nav.qc-nav` at `max-width: 820px` and shows `nav.tabbar`. Wire row markup changes at `900px` (`li.w-row` above, `li.w1` below). Those are different breakpoints.
- Below 820px the politician tape's `details.filter-fold` starts closed. Open **Search & filters** before typing.
- Record the feature id and viewport on every artifact. A path you did not drive is not verified.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the last screen.
- UI proof includes the URL the page wrote, the visible rows or heading, a screenshot, and `console.json`.
- `politician-tape` also stitches the local filtered wire next to the same filter on the live site. Pixel inequality is allowed. A missing row or a load error is not.
- Report an unreachable path with the command you ran and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 and one paragraph, then exactly these H2 sections, in order:

1. `Sub-features`
2. `How to get to it (user POV)`
3. `Driving it with Playwright`
4. `Gotchas`

## Features

- [Politician tape](./politician-tape.md) covers the homepage wire, company filter, empty filter, and the politician lookup that opens a member page.
- [Insiders tape](./insiders-tape.md) covers officer/ticker search and the empty window.
- [Leaders](./leaders.md) covers the insider leaderboard, including Repeatable horizons.
- [Ticker chart](./ticker-chart.md) covers the politician ticker page and its range control.
- [Filed](./filed.md) covers the politician Filed drop list and its day window.

Not mapped yet: Heat (`signals.html`, `insider-signals.html`), Patterns (`tells.html`), Paper (`paper.html`), Overlap (`overlap.html`), insider Filed (`insider-landed.html`), insider ticker (`insider-ticker.html`), Checks, Contracts, Beta / Mines, Claims, Canada, Research. Do not claim those from a tape proof.
