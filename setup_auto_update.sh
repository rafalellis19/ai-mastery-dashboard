#!/bin/bash
# setup_auto_update.sh — one-time install of the local auto-updater (launchd).
# Makes dashboard.html update itself daily at 7:00 even if the Claude app is closed.
# Run once:  bash setup_auto_update.sh
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.rafa.dashboard-updater.plist"

# Pull the API key from the shell profile so launchd has it
KEY=$(zsh -ic 'echo $ANTHROPIC_API_KEY' 2>/dev/null | tail -1)
if [ -z "$KEY" ]; then
  echo "❌ ANTHROPIC_API_KEY not found in ~/.zshrc — add it first."; exit 1
fi

pip3 install --quiet anthropic requests 2>/dev/null || pip3 install --break-system-packages --quiet anthropic requests

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.rafa.dashboard-updater</string>
  <key>EnvironmentVariables</key>
  <dict><key>ANTHROPIC_API_KEY</key><string>${KEY}</string></dict>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>cd "${DIR}" &amp;&amp; if [ "\$(date +%u)" = "1" ]; then /usr/bin/python3 reading_updater.py --weekly; else /usr/bin/python3 reading_updater.py --daily; fi; /usr/bin/python3 health_check.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>${DIR}/updater.log</string>
  <key>StandardErrorPath</key><string>${DIR}/updater.log</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "✅ Installed. Runs daily at 07:00 (weekly mode on Mondays). Log: ${DIR}/updater.log"
echo "   Test now with:  launchctl start com.rafa.dashboard-updater"
