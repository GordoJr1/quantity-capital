# Filed

Filed lists politician prints that just landed on the tape, grouped by drop time, newest first. The user switches the window between the last 3, 7, and 14 days.

## Sub-features

- `filed-ready` loads `landed.json`, shows an as-of line, and replaces the "Reading the tape…" placeholder.
- `filed-window` switches to the last 7 days and writes `?h=168`.
- `filed-empty` is a valid ready state when the window has no drops. The load-error line is not.

## How to get to it (user POV)

- On a wide window, choose **Filed** in the navigation named `Politician tape`.
- On a phone, choose **Filed** in the bottom tab bar named `Politician tape`.
- Open `landed.html` directly.
- Choose **Last 3 days**, **Last 7 days**, or **Last 14 days**.

## Driving it with Playwright

Preconditions:

- Doctor passed for `http://127.0.0.1:<port>`.
- `landed.json` is the committed file. Do not rebuild it.
- Evidence directory is outside the repo.

- **Both viewports.** Windows: `python .cursor/skills/verify-quantity-capital/qc_verify.py drive filed --base http://127.0.0.1:<port> --evidence <evidence-dir>`. Linux and the QC box: the same command with `python3` after `source <venv>/bin/activate` when a venv is required. Exit code 0. Stop the server with `Stop-Process -Id <pid> -Force` on Windows or `kill <pid>` on Linux. Never kill by process name.
- **Ready.** Wait until `#asof` is non-empty and `#board` does not contain `Reading the tape` or `Could not load the tape`. Either drop sections or the sentence `Nothing new has landed in this window` is success.
- **Last 7 days.** Choose the button named `Last 7 days`. The URL contains `h=168`. `#board` still does not say `Could not load the tape`. `local-after.png` shows the board.

## Gotchas

- Insider Filed is `insider-landed.html` and is not this page. The heading is also `Filed`. Check the navigation label `Politician tape` or the path `landed.html`.
- An empty window is a successful load. Only `Could not load the tape` is a failure.
- The chips are `72`, `168`, and `336` hours (`Last 3 days`, `Last 7 days`, `Last 14 days`). Assert the URL `h=168`, not the button's CSS class alone.
- Drops are keyed in America/New_York. Do not assert a UTC clock string.
- This recipe does not open a filer card. Returning to the tape is a separate entry point on the politician tape map.
