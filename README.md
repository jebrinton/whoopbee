# whoopbee

A bedtime commitment built on WHOOP and Beeminder, plus a Mac sleep dashboard that leads with bedtime regularity.

- `whoop_bee.py` reads when WHOOP says you fell asleep and posts the minutes past your target to a Beeminder
  do-less goal. launchd runs it (`com.whoopbee.daily`). After you fly east, the target moves later for a few nights
  while your body clock catches up.
- `whoop_dash.py`, `menubar/` and `WhoopWidget/` make up the dashboard: a SwiftBar menu-bar sparkline and a macOS
  desktop widget. See [README-dashboard.md](README-dashboard.md).
- `widget/BedtimeWidget.js` is an iOS home-screen widget for [Scriptable](https://scriptable.app) that reads the
  Beeminder goal.

Python stdlib only. The launchd, SwiftBar and widget parts are macOS-only.

## Setup

1. At https://developer-dashboard.whoop.com create an app with scopes `read:sleep` and `offline` (add
   `read:recovery` and `read:workout` for the dashboard) and redirect URL `http://localhost:8765/callback`.
2. Clone to `~/code/whoopbee`. The desktop widget's sandbox exception hardcodes that path
   (`WhoopWidget/Shared/WhoopModels.swift` and the extension's `.entitlements`). Avoid Desktop, Documents and
   Downloads, which launchd jobs can't read.
3. `python3 whoop_bee.py setup` asks for the WHOOP app keys and your Beeminder username and token, creates the
   goal, authorizes WHOOP and schedules itself.
4. `bash install_dashboard.sh` for the dashboard.

## Private state

API keys, the rotating WHOOP refresh token, your nights, logs and caches all live in `data/`, which is gitignored.
`archive/` holds pre-git backups and is ignored too.
