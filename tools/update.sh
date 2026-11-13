#!/bin/zsh
# Refresh the filter lists, write the converted rules where the installed extension looks
# for them, and ask Safari to reload. No rebuild needed. Run by hand (`make update`) or by
# the weekly launchd job (`make schedule`).
set -euo pipefail
cd "$(dirname "$0")/.."
PROFILE="${PROFILE:-${1:-lean}}"
APP=/Applications/Muffle.app
BUNDLE_ID=$(defaults read "$APP/Contents/Info.plist" CFBundleIdentifier)
EXT_DIR="$HOME/Library/Containers/$BUNDLE_ID.blocker/Data/Library/Application Support/Muffle"
YT_DIR="$HOME/Library/Containers/$BUNDLE_ID.youtube/Data/Library/Application Support/Muffle"
APP_DIR="$HOME/Library/Containers/$BUNDLE_ID/Data/Library/Application Support/Muffle"
LOG="$HOME/Library/Logs/Muffle-update.log"

mkdir -p "$EXT_DIR" "$YT_DIR" "$APP_DIR" "$(dirname "$LOG")"
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') profile=$PROFILE"
  tmp=$(mktemp -d)

  # 1. YouTube ad-removal data from uBlock Origin's list. Also writes
  #    tools/ubo-youtube-rules.txt, which the rules build below picks up.
  #    Exit 2 = fine, but uBlock added a rule the parser doesn't understand.
  set +e
  python3 tools/build_yt_config.py --refresh --out "$tmp/yt-config.json"
  yt_rc=$?
  set -e
  if [[ $yt_rc -eq 0 || $yt_rc -eq 2 ]]; then
    mv -f "$tmp/yt-config.json" "$YT_DIR/yt-config.json"
    cp -f "$YT_DIR/yt-config.json" "$APP_DIR/yt-config.json"
  else
    echo "youtube config: FAILED (exit $yt_rc); extension keeps its previous data"
  fi
  if [[ $yt_rc -eq 2 ]]; then
    osascript -e 'display notification "uBlock added a YouTube rule Muffle cannot interpret. See ~/Library/Logs/Muffle-update.log." with title "Muffle"' || true
  fi

  # 2. Content blocker rules.
  python3 tools/build_rules.py --refresh --profile "$PROFILE" --out "$tmp/blockerList.json" | tail -6
  mv -f "$tmp/blockerList.json" "$EXT_DIR/blockerList.json"
  mv -f "$tmp/blockerList-meta.json" "$EXT_DIR/rules-meta.json"
  cp -f "$EXT_DIR/rules-meta.json" "$APP_DIR/rules-meta.json"
  rmdir "$tmp"
  # 3. Tell Safari. The YouTube extension picks up its new data at the next Safari
  #    launch, or within a day on an open Safari (yt-guard.js asks background.js).
  "$APP/Contents/MacOS/Muffle" --reload
} 2>&1 | tee -a "$LOG"
