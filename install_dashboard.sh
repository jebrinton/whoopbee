#!/bin/bash
# Wires up the WHOOP dashboard from this checkout: first fetch, hourly launchd job, SwiftBar plugin folder, desktop widget.
# Usage: bash install_dashboard.sh   (after python3 whoop_bee.py setup)
set -euo pipefail
cd "$(dirname "$0")"
test -f data/config.json || { echo "no data/config.json: run python3 whoop_bee.py setup first" >&2; exit 1; }
[ "$PWD" = "$HOME/code/whoopbee" ] || echo "note: the desktop widget expects ~/code/whoopbee; see README-dashboard.md" >&2
mkdir -p data/img
python3 whoop_dash.py fetch
python3 whoop_dash.py schedule
osascript -e 'quit app "SwiftBar"' >/dev/null 2>&1 || true
defaults write com.ameba.SwiftBar PluginDirectory "$PWD/menubar"
open -g -a SwiftBar 2>/dev/null || echo "Install SwiftBar (brew install --cask swiftbar) for the menu bar."
if xcodebuild -version >/dev/null 2>&1; then
  bash WhoopWidget/scripts/build.sh --install
else
  echo "Desktop widget: install Xcode from the App Store, then run: bash WhoopWidget/scripts/build.sh --install"
fi
