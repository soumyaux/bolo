# Changelog

## Unreleased

**After updating:** macOS asks you one last time to allow Bolo again (Microphone, Accessibility, Input Monitoring), because Bolo.app is now signed with its own certificate. After this, updates keep the permissions.

- The installer creates a private "Bolo Local Signing" certificate in your login keychain (no admin password needed), so updates no longer reset macOS permissions.

## 1.0.0 (2026-11-22)

Faster, types as you watch, and learns without making new mistakes.

**After updating:** macOS asks you once to allow Bolo again (Microphone, Accessibility, Input Monitoring) in System Settings › Privacy & Security, because Bolo.app now carries its own icon. Then quit Bolo from the b menu and open it again.

- Settings › Speech: choose the microphone, the language (English by default, or English + Hindi), and Groq or this Mac only (for VPNs and offline).
- English only is about twice as fast on short dictations.
- Text is typed into the text box word by word. Lists and code editors still get a paste.
- Text now goes into web and Electron text boxes (Gmail, Slack) instead of only being copied.
- Learning no longer turns ordinary words into others ("then" into "when", "mode" into "more"), and ignores numbers and typos made while editing. Fixing several words at once teaches all of them.
- New Settings look with Liquid Glass, and a Restart Bolo button.
- The blue b icon shows everywhere, also in Finder and when Bolo quits.
- The log keeps earlier runs (`~/Library/Logs/Bolo-history.log`) and never contains what you said.
- Voice clips for training are off by default.
- Security: patched packages, the installer only installs the tested versions, and every change is checked for known vulnerabilities.

## 0.2.0 (2026-10-21)

A new look in white and blue.

- New app icon: a bold blue b on white.
- The menu bar shows a b instead of 🎙; 🔵 while listening.
- The Dynamic Island uses blue accents, with a green check when text is pasted.
- README pictures redrawn in white and blue.

## 0.1.0 (2026-10-19)

First public release.

- Push-to-talk dictation on `fn`, hands-free with a double-tap, Esc to cancel.
- Whisper large-v3 on Groq's free tier, with Whisper large-v3-turbo on the Mac as the offline fallback; text from long dictations while you speak.
- Local cleanup with Ollama: punctuation, lists, per-app writing styles and prompt mode.
- Learning from corrections, a personal dictionary, and optional training on your voice.
- Saved dictations that are retried when transcription fails.
- A Dynamic Island under the notch showing Bolo's state.
- Update checks: the menu offers new releases and installs them with one click.
- One-command installer and uninstaller; per-user settings in `~/Library/Application Support/Bolo`.
