# Bolo

**Hold a key, speak, let go: your words are typed wherever the cursor is.** Free, open-source push-to-talk dictation for Apple Silicon Macs that can run 100% offline.

<img src="assets/icon.png" alt="Bolo" width="96">

## Features

- **Push to talk.** Hold `fn`, speak, let go. The text is pasted into any app.
- **Hands-free.** Double-tap `fn` to keep recording until the next tap. Esc cancels.
- **Fast and accurate.** Whisper large-v3 on Groq's free tier when you add a key, and Whisper large-v3-turbo on your Mac as an instant, offline fallback (or the only engine).
- **Clean text.** Fillers and repeats removed; punctuation and lists fixed by a small local model in [Ollama](https://ollama.com).
- **Writing styles per app.** Casual in Slack, formal in Mail, structured prompts in Cursor and VS Code.
- **Prompt mode.** Start with "as a prompt…" and your rambling becomes a structured prompt for an AI tool.
- **Learns your words.** Fix a mis-heard word once (⌃⌥F, or just edit the pasted text) and Bolo gets it right next time. Add your own names and terms in Settings.
- **Never loses a dictation.** If transcription fails, the audio is kept and retried; ⌃⌥V pastes it when ready.
- **Dynamic Island.** A small island under the notch shows what Bolo is doing.

## Requirements

- A Mac with Apple Silicon (M1 or newer)
- macOS 13 Ventura or newer
- About 4 GB of free disk space (speech model 1.5 GB, optional cleanup model 2 GB)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/soumyaux/bolo/main/install.sh | bash
```

The installer sets up everything (Python via [uv](https://docs.astral.sh/uv/), the speech model, optionally Ollama) and builds `~/Applications/Bolo.app`. The only question that matters is your **Groq API key**: get one free at [console.groq.com/keys](https://console.groq.com/keys), or press Enter to stay fully offline.

Run the same command again to update. To answer the setup questions again:

```bash
curl -fsSL https://raw.githubusercontent.com/soumyaux/bolo/main/install.sh | bash -s -- --reconfigure
```

### First run: permissions

macOS asks for three permissions in **System Settings › Privacy & Security**. Allow Bolo in each:

| Permission | Why |
| --- | --- |
| Microphone | To hear you |
| Accessibility | To paste the text |
| Input Monitoring | To see the talk key |

Then quit Bolo from the menu bar (🎙) and open it again.

## Usage

| Do this | To |
| --- | --- |
| Hold `fn`, speak, let go | Dictate |
| Double-tap `fn` | Dictate hands-free; tap again to stop |
| Esc | Cancel |
| Start with "as a prompt" | Get a structured prompt |
| ⌃⌥F | Fix the last dictation (Bolo learns from it) |
| ⌃⌥V | Paste a saved dictation |

The menu bar icon shows the state: 🎙 ready, 🔴 recording, ⏳ working, ⚠️ needs attention (the menu says what).

### Change the talk key

Open **Settings… › General › Talk key** and pick right ⌘, right ⌥ or right ⌃ instead of `fn`. If `fn` opens the emoji picker or dictation, set **System Settings › Keyboard › Press 🌐 key to** "Do Nothing".

## Privacy

- **With a Groq key**, your audio is sent to [Groq](https://groq.com/privacy-policy/) to be transcribed. **Without one**, audio never leaves your Mac.
- Cleanup runs in Ollama on your Mac.
- **No telemetry, no accounts, no analytics.**
- Stored on your Mac only, readable by your user only, in `~/Library/Application Support/Bolo/`: your settings and key, the text of your last 200 dictations and learned words (to fix and learn), failed dictations' audio (24 h at most), and voice-training clips if you turn that on (14 days at most). Details in [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

## Configuration

Most things are in **Settings…** in the menu. Everything is in `~/Library/Application Support/Bolo/config.toml`, which holds your changes over the commented defaults in [config.default.toml](config.default.toml). Restart Bolo after editing the file by hand.

## Models and licenses

| Model | Used for | License |
| --- | --- | --- |
| Whisper large-v3 / large-v3-turbo | Speech to text | MIT |
| qwen2.5:3b (default cleanup) | Text cleanup | Qwen Research License (non-commercial) |
| llama3.2:3b (alternative) | Text cleanup | Llama 3.2 Community License |

Pick the cleanup model in the installer, or set `[cleanup] model` to any Ollama model and run `ollama pull <model>`.

## Troubleshooting

- **Nothing happens when I hold fn.** Check Input Monitoring and Accessibility in System Settings, then quit and reopen Bolo. After an update that rebuilds Bolo.app, macOS may ask again: remove Bolo from each list and add it back.
- **"Cleanup model missing".** Run `ollama pull <model>` as the menu says.
- **Groq stopped working.** Groq's free-tier limits can change; Bolo falls back to the local model on its own.
- **Logs** are in `~/Library/Logs/Bolo.log` (never your dictated text). Attach an excerpt to bug reports.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/soumyaux/bolo/main/uninstall.sh | bash
```

It asks before deleting your saved data, and leaves Ollama alone.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

## License

[MIT](LICENSE)
