# How Bolo works

## The pipeline

1. **Key.** A Quartz event tap watches the talk key (`fn` by default). Holding it starts recording; a double-tap keeps recording hands-free until the next tap. Esc cancels.
2. **Record.** The microphone is captured in memory and encoded as FLAC. Nothing is written to disk unless the dictation fails (see *Saved dictations*) or voice training is on.
3. **Speech to text.** Providers are tried in `stt.order`:
   - **Groq** (`whisper-large-v3`), when a Groq key is set. Short clips can go to the local model to save the free quota.
   - **Local Whisper** (`whisper-large-v3-turbo` through MLX), which runs on the Mac and works offline. The model (about 1.5 GB) is downloaded once, on first use or by the installer.
   For long dictations, the local model turns finished pieces into text while you are still speaking.
4. **Learned words.** Corrections you made before ("cable" → "table") are applied, and your dictionary terms and learned words are sent as spelling hints.
5. **Cleanup.** A local pass removes fillers and repeats and fixes dictionary spellings. When the text needs more (lists, punctuation, a per-app style, prompt mode), a small model in **Ollama** on the Mac rewrites it. A result that drops spoken words or answers the text instead of cleaning it is thrown away.
6. **Paste.** The text goes on the clipboard, ⌘V is sent to the focused app, and the old clipboard is put back. With no text box focused, the text is left on the clipboard.

After a paste, Bolo can watch the text field for up to 90 s and learn from how you edit what it typed (or use ⌃⌥F, *Fix last dictation*).

## File map

| Path | What it does |
| --- | --- |
| `bolo/app.py` | Menu bar app, key handling, the worker that runs each dictation |
| `bolo/audio.py` | Microphone capture and FLAC encoding |
| `bolo/early.py` | Text from long recordings while you speak |
| `bolo/stt/` | Speech providers: `groq.py`, `local_whisper.py`, shared `base.py` |
| `bolo/cleanup.py` | Local cleanup pass and the Ollama pass, prompt mode |
| `bolo/styles.py` | Picks a writing style from the app, website or window title |
| `bolo/learn/` | Learning from corrections, the fix window, voice training clips |
| `bolo/inject.py` | Pasting through the clipboard |
| `bolo/overlay.py` | The island under the notch |
| `bolo/pending.py` | Saved dictations that failed, retried in the background |
| `bolo/settings_window.py` | The Settings window |
| `bolo/config.py` | Default + user settings, API keys |
| `bolo/login.py`, `bolo/launcher.c` | Builds `~/Applications/Bolo.app` and the start-at-login item |
| `bolo/setup.py` | Steps used by `install.sh` |
| `bolo/updates.py` | Finds new releases and runs the installer to update |
| `scripts/` | Developer tools: `release.sh` to publish a version, accuracy test, provider bake-off, voice training, limits check |

## What is stored, and where

Everything is in `~/Library/Application Support/Bolo/`, readable by your user only:

| File | Contents | Kept for |
| --- | --- | --- |
| `config.toml` | Your settings (over `config.default.toml`) | Until you change it |
| `.env` | Your Groq key | Until you remove it |
| `learning.db` | Last 200 dictations' text, learned words, up to 200 style examples | Rolling |
| `voice/` | Audio clips for voice training (only with `learning.save_voice`) | Until used, 14 days at most |
| `pending/` | Audio of dictations that could not be transcribed | Until pasted, 24 h at most |
| `usage.json`, `groq_usage.json` | Counts only, never text | Rolling |

The log is `~/Library/Logs/Bolo.log`. It never contains what you said.

## Privacy

- With a Groq key, audio is sent to Groq to be transcribed. Without one, nothing leaves the Mac.
- Cleanup runs in Ollama on the Mac.
- No telemetry, no accounts, no analytics.
- Every 6 hours Bolo runs `git ls-remote` against GitHub to see if a newer release tag exists (`bolo/updates.py`). Nothing else is sent.
