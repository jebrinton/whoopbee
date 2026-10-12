#!/bin/bash
# Builds WhoopWidget.app with ad-hoc signing and installs it to /Applications. Needs full Xcode (not just CLT).
set -euo pipefail
cd "$(dirname "$0")/.."
if ! xcodebuild -version >/dev/null 2>&1; then
  echo "xcodebuild needs Xcode. Install it from the App Store, then: sudo xcode-select -s /Applications/Xcode.app && sudo xcodebuild -license accept" >&2
  exit 1
fi
python3 scripts/make_icon.py >/dev/null 2>&1 || true
build_dir=$(mktemp -d "${TMPDIR:-/tmp}/whoopwidget-build.XXXXXX")
trap 'rm -rf "$build_dir"' EXIT
xcodebuild -project WhoopWidget.xcodeproj -scheme WhoopWidget -configuration Release -destination 'platform=macOS' \
  -derivedDataPath "$build_dir" ONLY_ACTIVE_ARCH=YES \
  CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM= build | tail -5
app="$build_dir/Build/Products/Release/WhoopWidget.app"
codesign --verify --deep --strict "$app"
if [ "${1:-}" = "--install" ]; then
  osascript -e 'tell application "WhoopWidget" to quit' >/dev/null 2>&1 || true
  rm -rf /Applications/WhoopWidget.app
  ditto "$app" /Applications/WhoopWidget.app
  open /Applications/WhoopWidget.app
  echo "Installed /Applications/WhoopWidget.app. Right-click the desktop → Edit Widgets → search WHOOP."
else
  mkdir -p build && ditto -c -k --norsrc --noextattr --keepParent "$app" build/WhoopWidget.zip
  echo "Built build/WhoopWidget.zip"
fi
