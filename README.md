<p align="center">
  <img src="assets/icon.png" alt="Bolo" width="110">
</p>

<h1 align="center">Bolo</h1>

<p align="center">
  <b>Hold a key, speak, let go. Your words are typed for you.</b><br>
  Free dictation for Apple Silicon Macs. Works in any app, even offline.
</p>

<p align="center">
  <img src="docs/how-it-works.svg" alt="1. Hold fn. 2. Speak. 3. Let go, and clean text is typed." width="760">
</p>

## Install

<p align="center">
  <img src="docs/install.svg" alt="1. Open Terminal. 2. Paste the command. 3. Allow Microphone, Accessibility and Input Monitoring." width="760">
</p>

Paste this into **Terminal** and press Return:

```bash
curl -fsSL https://raw.githubusercontent.com/soumyaux/bolo/main/install.sh | bash
```

Press Return to accept each suggested answer. A Groq key is optional: skip it to stay offline.

Then allow Bolo in **System Settings › Privacy & Security**, quit it from the 🎙 menu, and open it again.

**You need:** a Mac with Apple Silicon (M1 or newer), macOS 13 or newer, and 4 GB of free space.

## Use

<p align="center">
  <img src="docs/shortcuts.svg" alt="Hold fn to dictate. Double-tap fn for hands-free. Esc to cancel. Control-Option-F fixes the last dictation. Control-Option-V pastes a saved one. Say 'as a prompt' for AI prompts." width="760">
</p>

## Good to know

- 🔒 **Private.** Without a Groq key, your voice never leaves your Mac.
- 🔄 **Updates.** The 🎙 menu tells you when a new version is out. One click installs it.
- 🗑 **Uninstall:** `curl -fsSL https://raw.githubusercontent.com/soumyaux/bolo/main/uninstall.sh | bash`

**Something not working?** See the [full guide](docs/GUIDE.md#troubleshooting) or [open an issue](https://github.com/soumyaux/bolo/issues/new/choose).

[Full guide](docs/GUIDE.md) · [How it works](docs/HOW-IT-WORKS.md) · [Contributing](CONTRIBUTING.md) · [MIT License](LICENSE)
