#!/bin/bash
# Installs or updates Bolo. Safe to run again: it is also the updater.
#   curl -fsSL https://raw.githubusercontent.com/soumyaux/bolo/main/install.sh | bash
set -euo pipefail

REPO="https://github.com/soumyaux/bolo.git"
SUPPORT="$HOME/Library/Application Support/Bolo"
DIR="$SUPPORT/app"
OLLAMA_URL="http://127.0.0.1:11434"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
# Questions read from the terminal, so they work under curl | bash too.
ask() {
  local answer=""
  if [ -r /dev/tty ]; then read -r -p "$1 " answer </dev/tty || true; fi
  printf '%s' "$answer"
}

# 1. Apple Silicon and macOS 13 or newer.
[ "$(uname -s)" = Darwin ] || die "Bolo runs on macOS only."
[ "$(uname -m)" = arm64 ] || die "Bolo needs an Apple Silicon Mac (M1 or newer)."
major="$(sw_vers -productVersion | cut -d. -f1)"
[ "$major" -ge 13 ] || die "Bolo needs macOS 13 or newer (this Mac has $(sw_vers -productVersion))."

# 2. Xcode Command Line Tools (git and a C compiler for Bolo.app's launcher).
if ! xcode-select -p >/dev/null 2>&1; then
  say "Installing Xcode Command Line Tools. Click Install in the window that opens."
  xcode-select --install || true
  until xcode-select -p >/dev/null 2>&1; do sleep 5; done
fi

# 3. uv, which also brings Python 3.12.
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python package manager)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# 4. Bolo itself. On an update the questions below are skipped; run with --reconfigure to ask again.
FIRST=1
[ -f "$SUPPORT/config.toml" ] && FIRST=0
[ "${1:-}" = --reconfigure ] && FIRST=1
mkdir -p "$SUPPORT"
if [ -d "$DIR/.git" ]; then
  say "Updating Bolo"
  # Copies installed before releases were shallow; releases need the full history.
  [ -f "$DIR/.git/shallow" ] && git -C "$DIR" fetch --quiet --unshallow
  git -C "$DIR" fetch --quiet --tags --force origin
else
  say "Downloading Bolo"
  git clone --quiet "$REPO" "$DIR"
fi
# The newest release, or the latest code when there is no release yet.
# BOLO_REF=main installs the latest code anyway; BOLO_REF=v0.1.0 installs that release.
ref="${BOLO_REF:-$(git -C "$DIR" tag -l 'v*' --sort=-v:refname | head -n1)}"
case "$ref" in ""|main) ref=origin/main ;; esac
git -C "$DIR" -c advice.detachedHead=false checkout --quiet --force "$ref"
say "Installing Bolo $(git -C "$DIR" describe --tags --always)"
cd "$DIR"
uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev
setup() { uv run --quiet python -m bolo.setup "$@"; }

# 5. Ollama, for the cleanup pass (optional).
ollama_bin() {
  command -v ollama 2>/dev/null && return
  for path in /Applications/Ollama.app/Contents/Resources/ollama \
              "$HOME/Applications/Ollama.app/Contents/Resources/ollama"; do
    [ -x "$path" ] && { echo "$path"; return; }
  done
}
OLLAMA="$(ollama_bin || true)"
if [ -z "$OLLAMA" ] && [ "$FIRST" = 1 ]; then
  reply="$(ask "Install Ollama for text cleanup (punctuation, lists, styles)? [Y/n]")"
  if [[ ! "$reply" =~ ^[Nn] ]]; then
    say "Installing Ollama into ~/Applications"
    tmp="$(mktemp -d)"
    curl -fL --progress-bar -o "$tmp/Ollama.zip" https://ollama.com/download/Ollama-darwin.zip
    mkdir -p "$HOME/Applications"
    ditto -x -k "$tmp/Ollama.zip" "$HOME/Applications"
    rm -rf "$tmp"
    OLLAMA="$HOME/Applications/Ollama.app/Contents/Resources/ollama"
  else
    setup set cleanup.enabled false
  fi
fi
if [ -n "$OLLAMA" ] && [ "$FIRST" = 1 ]; then
  if ! curl -fs "$OLLAMA_URL/api/version" >/dev/null 2>&1; then
    nohup "$OLLAMA" serve >>"$HOME/Library/Logs/Ollama.log" 2>&1 &
    for _ in $(seq 20); do curl -fs "$OLLAMA_URL/api/version" >/dev/null 2>&1 && break; sleep 0.5; done
  fi
  echo
  echo "Cleanup model:"
  echo "  1) qwen2.5:3b   best results (Qwen Research License: non-commercial use)"
  echo "  2) llama3.2:3b  (Llama 3.2 Community License)"
  choice="$(ask "Choose 1 or 2 [1]:")"
  model="qwen2.5:3b"; [ "$choice" = 2 ] && model="llama3.2:3b"
  say "Downloading $model (about 2 GB)"
  "$OLLAMA" pull "$model"
  setup set cleanup.model "$model"
  setup set prompt_mode.model "$model"
fi

# 6. The local speech model.
say "Getting the local speech model"
setup models

# 7. The only required question.
if [ "$FIRST" = 1 ]; then
echo
key="$(ask "Paste your Groq API key (free at console.groq.com/keys), or press Enter to stay 100% offline:")"
if [ -n "$key" ]; then
  setup groq-key "$key"
  setup set stt.order '["groq", "local"]'
else
  setup set stt.order '["local"]'
fi
fi

# 8. Bolo.app.
say "Building ~/Applications/Bolo.app"
reply=n
[ "$FIRST" = 1 ] && reply="$(ask "Start Bolo when you log in? [Y/n]")"
if [[ "$reply" =~ ^[Nn] ]]; then
  uv run --quiet python -m bolo.login
else
  uv run --quiet python -m bolo.login --at-login
fi
pkill -f "bolo.app" 2>/dev/null || true
open "$HOME/Applications/Bolo.app"

# 9. Permissions.
[ "$FIRST" = 1 ] || { say "Bolo is up to date."; exit 0; }
say "Almost done: give Bolo three permissions"
cat <<'EOF'
When macOS asks, allow Bolo in System Settings › Privacy & Security:
  • Microphone        to hear you
  • Accessibility     to paste the text
  • Input Monitoring  to see the talk key
Then quit Bolo from the menu bar (the b icon) and open it again from ~/Applications.

Hold fn and speak; let go to paste. Double-tap fn for hands-free.
EOF
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" || true
