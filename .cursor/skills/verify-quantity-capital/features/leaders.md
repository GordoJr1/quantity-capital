# Leaders

Leaders ranks insider officers. The page opens on Performers. Repeatable is the filing-date copy board with 30, 90, and 180 day horizons.

## Sub-features

- `leaders-performers` lists priced filers as cards from `insider-analysis.json`.
- `leaders-repeatable` switches to the Repeatable table from `insider-repeatable.json`.
- `leaders-horizon` changes that table to the 30 day horizon and writes `?h=30`.

## How to get to it (user POV)

- From an insider page, choose **Leaders** in the navigation named `Insider tape`, or the same label on the phone tab bar.
- Open `insider-board.html` directly.
- Choose the **Repeatable** tab, then **30 day**, **90 day**, or **180 day**.

## Driving it with Playwright

Preconditions:

- Doctor passed for `http://127.0.0.1:<port>`.
- `insider-analysis.json` and `insider-repeatable.json` are the committed files. Do not rebuild them.
- Evidence directory is outside the repo.

- **Both viewports.** Windows: `python .cursor/skills/verify-quantity-capital/qc_verify.py drive leaders --base http://127.0.0.1:<port> --evidence <evidence-dir>`. Linux and the QC box: `python3 .cursor/skills/verify-quantity-capital/qc_verify.py drive leaders --base http://127.0.0.1:<port> --evidence <evidence-dir>` after `source <venv>/bin/activate` when a venv is required. Exit code 0. Stop the server with `Stop-Process -Id <pid> -Force` on Windows or `kill <pid>` on Linux. Never kill by process name.
- **Performers.** Wait until `#board article.card a.who` exists. The heading is `Leaders`.
- **Repeatable.** Choose the tab named `Repeatable` (`#board-tab-repeatable`). The URL contains `b=repeatable`. `table.rep tbody tr` has at least one row.
- **30 day horizon.** Choose `#rep-windows button[data-h="30"]`. The URL contains `h=30`. Any `table.rep th` contains `30d` (the first cell is `#`, so do not read only that one). `local-after.png` shows the table.

## Gotchas

- Performers and Repeatable load from different JSON files. A performers card is not proof of the Repeatable table.
- The Repeatable tab reveals `#rep-windows`. The horizon buttons are not in the DOM as a visible control before that click; they exist but the container starts `hidden`.
- Follow and Active traders / Active names are other tabs. This recipe does not verify them.
- Phone width hides `.desk-firm` columns. Assert the officer link and the `30d` header, not the desktop firm column.
- `?b=repeatable&h=90` is the direct URL for the default horizon. The recipe still clicks so the tab control is what changed the board.
