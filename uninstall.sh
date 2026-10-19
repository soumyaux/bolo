#!/bin/bash
# Removes Bolo. Ollama and its models are left alone, and so is the speech model in
# ~/.cache/huggingface (delete models--mlx-community--whisper-large-v3-turbo there to free 1.5 GB).
#   curl -fsSL https://raw.githubusercontent.com/soumyaux/bolo/main/uninstall.sh | bash
set -uo pipefail

SUPPORT="$HOME/Library/Application Support/Bolo"

ask() {
  local answer=""
  if [ -r /dev/tty ]; then read -r -p "$1 " answer </dev/tty || true; fi
  printf '%s' "$answer"
}

echo "Quitting Bolo"
pkill -f "bolo.app" 2>/dev/null || true
launchctl remove local.bolo 2>/dev/null || true

rm -rf "$HOME/Applications/Bolo.app"
rm -f "$HOME/Library/LaunchAgents/local.bolo.plist" "$HOME/Library/Logs/Bolo.log"
echo "Removed Bolo.app, the login item and the log."

if [ -d "$SUPPORT" ]; then
  reply="$(ask "Also delete your settings, Groq key, learned words and saved dictations in $SUPPORT? [y/N]")"
  if [[ "$reply" =~ ^[Yy] ]]; then
    rm -rf "$SUPPORT"
    echo "Deleted $SUPPORT"
  else
    rm -rf "$SUPPORT/app"
    echo "Kept your data in $SUPPORT (the app code was removed)."
  fi
fi
echo "Bolo is uninstalled. In System Settings › Privacy & Security you can remove Bolo from"
echo "Microphone, Accessibility and Input Monitoring."
