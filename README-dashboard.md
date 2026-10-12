# WHOOP dashboard: menu bar + desktop widget

Sits next to the Beeminder bridge (`whoop_bee.py`) and shares its WHOOP app, refresh token and lock.
Inputs first: the always-visible mark is bedtime regularity; HRV and resting HR only as 7-night averages inside your
own 60-night band; no recovery score, strain, stages or efficiency anywhere.

## Install

    bash install_dashboard.sh

What it does: pulls the last 90 days, renders the charts, installs an hourly launchd job (`com.whoopdash.fetch`),
points SwiftBar at `menubar/`, and builds and installs the desktop widget if Xcode is present.

## Two things only you can do

1. HRV / resting HR need one more WHOOP scope. At https://developer-dashboard.whoop.com edit the app, add
   `read:recovery` (and `read:workout` if you want late workouts auto-tagged), save, then run
   `python3 whoop_bee.py auth`. The dashboard notices on its next fetch; nothing else to change.
2. The desktop widget is a WidgetKit app and needs Xcode (App Store, free, ~15 GB). After it is installed:
   `bash WhoopWidget/scripts/build.sh --install`, then right-click the desktop → Edit Widgets → "WHOOP".

## Pieces

- `whoop_dash.py` — fetch / render / log / menubar / schedule. `python3 whoop_dash.py status` for a one-liner.
- `menubar/whoop.10m.py` — SwiftBar plugin (SwiftBar's plugin folder is `menubar/`). Menu bar: 14-night fall-asleep
  sparkline (template image, so it follows the menu bar's light/dark). Dropdown: nights-in-window count, SRI, median
  fall-asleep, sleep timeline chart, HRV/RHR band charts, one-click behavior log (alcohol, caffeine after 2 pm, ate
  after 8 pm, hard workout <2 h before bed), the Beeminder readout (`menubar/bedtime.10m.py`) folded in as a
  submenu, Sync now.
- `WhoopWidget/` — Xcode project (macOS 15+). Small / medium / large widget reading `~/code/whoopbee/data/dash.json`
  every 15 min via a read-only sandbox exception for `~/code/whoopbee/data/`. If the repo lives elsewhere, change the
  path in `Shared/WhoopModels.swift` and `WhoopWidgetExtension/WhoopWidgetExtension.entitlements`, then rebuild.
  Structure adapted from ClaudeUsageWidget (MIT).
- Data files it writes, all in `data/`: `dash_cache.json` (raw WHOOP records), `dash.json` (everything the views
  need), `journal.json` (your logged behaviors), `img/*.png`, `dash_log.txt`.

## Definitions

- Night = the evening date; a sleep that starts before 15:00 belongs to the previous night (same rule as whoop_bee.py).
- "Asleep by 23:30: 7 of last 10 nights" = nights whose fall-asleep time was within `grace_min` (default 30) of the
  target. Rolling window, so it cannot "break". Change `grace_min` in `data/dash_config.json`.
- SRI = Sleep Regularity Index (Phillips et al. 2017): 200 × P(same sleep/wake state at the same minute on consecutive
  days) − 100, over the last 14 days, naps included. 100 = identical pattern every day.
- HRV / RHR band = 10th–90th percentile of the previous 60 nights; line = trailing 7-night mean. The number is shown
  only when the mean leaves the band. Needs 14 nights of baseline before it appears.
- Behavior log: a click before 15:00 tags last night; after 15:00 it tags tonight. Click again to undo.
