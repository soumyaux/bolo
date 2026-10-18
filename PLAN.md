# Build plan: backup dictation app for prompting

Status on 2026-10-18 (speed and learning session): Gemini is removed. Speech goes to Groq `whisper-large-v3` when it can be reached and to local Whisper large-v3-turbo otherwise; cleanup runs on this Mac through Ollama (`qwen2.5:3b`, `qwen2.5:7b` for prompt mode). See "Session of 2026-10-18" below. Run the app with `uv run python -m flow.app`, or open `~/Applications/Bolo.app`.

## Session of 2026-10-18: faster, more accurate, learns the voice

- Problem measured: Gemini cleanup often hit its 4 s timeout (36 ReadTimeout, 4 × 503 in the log), and in prompt-style apps every 2-word dictation went to it; a 0.6 s clip took about 5.6 s.
- Gemini is removed everywhere (code, config, usage counters, menu line, `.env` key, `scripts/try_transcribe.py`).
- Ollama 0.40.6 is installed without Homebrew (this user is not an admin) as the official standalone build in `~/.local/ollama`, linked at `~/.local/bin/ollama`. A LaunchAgent for it was blocked in the session, so Bolo starts `ollama serve` itself at launch and again on key down if the server stopped (`cleanup.start_ollama`, log in `~/Library/Logs/Ollama.log`). Models: `qwen2.5:3b` (1.8 GB) and `qwen2.5:7b` (4.7 GB) in `~/.ollama`. Pulling from the Ollama registry worked through Zscaler but stalled often (about 20 min for the 3b).
- Cleanup (`flow/cleanup.py`): Ollama `/api/chat`, temperature 0, `keep_alive` 30 min, proxy bypassed. Warmed on key down; the 7b only when the front app's style is "prompt" or the prompt key is held. Clean text of 12 words or fewer skips the LLM even in styled apps. After 2 failures in a row the LLM is skipped for 5 minutes. Measured with the 3b: 0.5 to 1.0 s per pass with 5 examples. Seen once: the 3b kept "no wait" in "padding to the card, no wait, spacing to the card"; check self-corrections on real dictation and compare the 7b.
- Groq (`flow/stt/groq.py`): persistent connection, glossary prompt cut to about 600 characters (224 tokens), FLAC upload. A thread checks `GET /models` every 30 s, every 5 s while down, and at once when the Mac's network address changes; the chain skips Groq instantly while it is down. Groq free tier (2026-10-18 docs): 20 requests a minute, 2000 a day, 7200 audio seconds an hour, each request billed as at least 10 s. Early pieces are now 6 s and go through the chain, so Groq hears them; a 429 rests Groq for its retry-after and pieces go to local. `GROQ_API_KEY` is not yet in `.env`; until it is, `groq` is skipped and local is used.
- Learning: a swap that sounds almost the same (letters within 2 edits, or sound keys within 1) counts as seen twice and applies from the next dictation, unless a real word was changed to a non-word (a typo such as "button" to "buttom"). Deleting a phrase Whisper wrote twice no longer counts as a rewrite. `tidy()` drops repeated 2-4 word phrases and writes a dictionary term where a phrase that is not all English words sounds like it ("impact UI", "Hinglis", "Wisper Flow"), using `/usr/share/dict/words`. Learned words go first in the speech prompt by count; up to 200 examples are kept and the 5 closest are sent.
- Dropped presses are logged and shown: too short, too quiet, no speech, microphone sent no audio. `BOLO_DEBUG=1` logs key timing. macOS "Press 🌐 key to" was already Do Nothing (`AppleFnUsageType = 0`).
- Voice training (user decision): `[learning] save_voice` keeps each dictation up to 30 s as FLAC in `voice/` (mode 600), linked to its row; deleted after 14 days or when a training run used it. Prompt-mode dictations are not kept. `scripts/accuracy.py` scores Groq, stock turbo and the trained model by WER on held-out clips (every fifth by id). `scripts/train_voice.py` is plain MLX LoRA (mlx-examples has no Whisper trainer): the bottom 24 encoder layers are frozen and computed once per clip, LoRA rank 8 on query/value of the top 8 encoder layers and all decoder attention; merged and saved to `models/whisper-me`, switched in only when its WER beats stock and any earlier trained model. Smoke test on 12 synthetic clips: 2 passes in 16 s, merged model transcribed the held-out clips correctly. A real run on 45 min will likely take minutes, not the 30 to 60 estimated. Only clips up to 30 s count toward the 45 min.
- Clean-up: repo `__pycache__` deleted; `uv cache prune` freed 2.5 GB (4.1 GB to 1.5 GB). The Hugging Face cache holds only the turbo model (1.5 GB), kept. `pending/` was empty.

## Found during the build (2026-10-14)

- This Mac is behind a company Zscaler web filter. It blocks `api.groq.com`, `console.groq.com` and `api.elevenlabs.io` with "Not allowed the use of this AI/ML site". Do not work around it. `generativelanguage.googleapis.com`, `aistudio.google.com` and `huggingface.co` are reachable.
- So on this Mac the chain is Gemini, then the local Parakeet model if the user agrees to install it. `flow/stt/groq.py` is not written. The cleanup provider default is now Gemini.
- Open question for the user: this is a company-managed Mac whose policy restricts AI sites. Gemini's free tier uses submitted content to improve Google products. The user must decide whether dictating work prompts to it is acceptable, or whether to go local-only.
- The user's Wispr key is Fn. On 2026-10-15 they asked for Fn as the talk key: `push_to_talk = "fn"`, so Fn answers even while Wispr runs (both would type; Wispr is quit). Right Command no longer answers.
- `flow/app.py` is written early (step 8, without cleanup): one Quartz listen-only event tap handles both keys, so `pynput` is not used for push-to-talk. The key logic passes with simulated events and the app starts cleanly; real key presses and paste are untested until the permissions are granted.
- Proposed to the user, not yet confirmed: replace Parakeet with Whisper large-v3-turbo (MLX) as the local fallback, because Parakeet is English only and Groq was the Hinglish-capable fallback.
- Step 5 result (measured 2026-10-14 about 00:40 IST, which is midday US Pacific, with synthetic `say` clips): the Gemini key works. Inline FLAC is accepted and the transcript is at `steps[].content[].text`. Smart mode met the cleanup rules on the test clip (numbered list, kept only "Tuesday", spelled "uv"). But the free tier is not usable as the main provider: successful `gemini-3.5-transcribe` requests took 7.6 to 20 s, several returned 503 "high demand", and HTTP 429 started after about five requests within a minute. `gemini-3.5-flash-lite` and `gemini-3.1-flash-lite` with audio took 5 to 17 s. The network itself is fast (about 0.2 s warm round trip), so the delay is on Google's side. Text-only `gemini-3.5-flash-lite` took about 1 s, so it may still serve as a cleanup pass. Not yet measured at Indian daytime or on the paid tier.
- Recommended next step, waiting for the user's yes: make a local model the main provider (Whisper large-v3-turbo through MLX, about 1.5 GB) and keep Gemini as a second choice. `timeout_seconds` is raised to 30 so the app can be tried with Gemini meanwhile.
- The user pasted the Gemini key into chat on 2026-10-14; they were advised to replace it.
- Local model (2026-10-14): Zscaler also blocks Hugging Face's file servers (`*.hf.co`). The user opened access for a short time and `mlx-community/whisper-large-v3-turbo` (1.6 GB, in `~/.cache/huggingface`) was downloaded then. The app sets `HF_HUB_OFFLINE=1`, so it needs no Hugging Face access again unless the model is changed or the cache is deleted. Do not plan on Groq: it was reachable only during that window.
- Local speed, measured with synthetic `say` clips on the M3 Pro: about 0.7 s per clip of 3 to 9 s once loaded; about 4 s to import and load in a fresh process, done at app launch. The model unloads after 10 idle minutes and reloads on key down. `mlx-whisper` replaces `parakeet-mlx` in `pyproject.toml` (the venv is now about 1.1 GB because it pulls in torch).
- Provider order is `["local", "gemini"]`. Local `language` is `"en"`; behaviour on the user's Hinglish is untested and must be checked in the bake-off (try `language = ""` for auto-detect).
- `flow/cleanup.py` is built: a local pass always runs (fillers, repeated words, dictionary spelling, "new line"); a `gemini-3.5-flash-lite` text pass runs only for self-corrections, spoken steps, Devanagari, or more than 40 words, with a 4 s timeout that falls back to the local pass. It took 0.7 to 2.9 s in tests and passed the list, correction, do-not-obey and romanise-without-translating cases on typed samples.
- Added at the user's request: speakers are muted while the key is held (`mute_while_recording`). Verified by the user so far: Fn hotkey with Wispr quit, recording, and paste, on the Gemini path. The local path and the mute have not been confirmed by the user yet.
- macOS is set to open the emoji picker on Fn (`AppleFnUsageType = 2`); the user was told how to change it.
- Synthetic bake-off (2026-10-14, 10 English prompts in the macOS en-IN voices Rishi, Tara, Aman; 147 words; not the user's voice). Accuracy was equal: local 1 word error ("Postgres" heard as "postgaz", fixed once the glossary prompt was passed), Gemini verbatim 1 ("PostgreSQL" for "Postgres"). Speed: local 0.69 to 0.79 s per clip; Gemini 3.4 to 20.6 s, median 6.6 s, with two 503 retries, and one later request stayed rate limited for over three minutes.
- Hinglish (2 clips in the hi-IN voice Lekha): local with `language="en"` produced nonsense or a wrong English translation, so it must not be forced to English. Local with Hindi detected gave the right words in Devanagari, and so did Gemini smart mode. The cleanup pass rewrites Devanagari in Roman letters. `[stt.local] languages = ["en", "hi"]` now picks per clip between those two only.
- Cost of that choice: language detection is a second encoder pass, so local speech time rose from about 0.75 s to about 1.3 s per clip. Possible later optimisation: encode once and reuse the features for detection and decoding (clips up to 30 s). `languages = ["en"]` restores 0.75 s.
- Full pipeline on the same clips: 1.3 s for plain English (no LLM), 2.4 to 3.0 s when the cleanup LLM pass runs (lists, corrections, Hindi).
- Cleanup prompt fix: the LLM changed a misheard "force" to "robust"; a rule now forbids swapping words.
- `uv` needs the macOS keychain certificates here; `native-tls = true` is set in `pyproject.toml`.
- Recording works (microphone permission is granted). The first stream start in a process takes about 3 s, later ones about 80 ms, so `app.py` must open and close the stream once at launch.
- Clipboard save and restore is tested. The live paste and the hotkey are untested: Visual Studio Code has neither Accessibility nor Input Monitoring permission yet.
- Second session (2026-10-14): the user replaced the Gemini key. The new key works for the cleanup model. `gemini-3.5-transcribe` answered "limit: 25 requests per day on Free Tier" for this account, so Gemini cannot be more than an occasional speech fallback; a bake-off of 12 prompts uses about half of a day's quota.
- Learning is built in `flow/learn/`: `store.py` (SQLite, keeps the last 200 dictations and 20 examples, file mode 600), `diff.py` (word swaps; a rewrite teaches nothing), `fix_window.py`, `ax.py` and `ax_watch.py`. Learned words are added to the glossary sent to the speech provider and the cleanup prompt, and are replaced in the transcript. A name or identifier is used after one correction, an ordinary word after two. A capital on the first word of a sentence is never learned as a name.
- The fix window is a non-activating panel. macOS 26 did not let the menu bar app come to the front from the shortcut (`activateIgnoringOtherApps` left the window without the keyboard), and the panel also means the other app keeps its focus.
- After a fix, the old text is replaced in place only where the Accessibility API can find and select it (native fields). In VS Code the focused element cannot be read (error -25204), so the corrected text is put on the clipboard and the menu says so.
- The fix shortcut passes through to the focused app as well, because the event tap is listen-only.
- `[learning] auto_capture` is off by default. Tested with a native text view: an edit made in the field was picked up and learned.
- Start at login: `flow/login.py` writes `~/Applications/Bolo.app` (a shell launcher that runs the project's venv) and, from the "Start at login" menu item, a LaunchAgent. The bundle is a separate identity for macOS permissions: a probe run through it had neither Accessibility nor Input Monitoring, while the microphone worked. The user must grant those to Flow Backup once; dictation through the bundle is untested until then. A lock file stops a second copy from starting.
- Gemini docs check: smart mode is the string `"mode": "smart"`; the default is verbatim. Inline audio is not documented for this model, but the Interactions API takes inline base64 (`data` + `mime_type`) for other file types. `flow/stt/gemini.py` tries inline first and switches to the Files API on HTTP 400. The raw JSON location of the transcript is not shown in the docs; the parser accepts `output_text`, `steps[].content[]` and `outputs[]`. Confirm both with the first real request.
- Third session (2026-10-14): renamed to Bolo (`~/Applications/Bolo.app`, `local.bolo`, data in `~/Library/Application Support/Bolo/`). Bolo.app is a new identity and needs Accessibility and Input Monitoring granted again.
- Accuracy work, from the user's real dictations: silence is trimmed and quiet speech raised before transcribing (`audio.prepare`); noise-only clips are dropped; a looping or glossary-echo transcript is retried without the prompt, then passed to Gemini (`BadResult`, not rested). The prompt no longer starts with "Glossary:", which was read back as speech. Hindi is chosen only when it scores twice English. `[dictionary] hints` lists the user's frequent words; the cleanup pass may fix sound-alikes to those only ("Site banner" became "side panel" in a test).
- Whisper large-v3 (full) is not downloaded: Hugging Face file servers are still blocked. Estimated 2 to 3 times slower than turbo per clip (not measured).
- On-screen pill (`flow/overlay.py`): Listening with a meter, Writing with a spinner, short messages. Esc or × cancels; Esc is held back from the focused app (the event tap is now active, listen-only as fallback).
- Auto-capture is on by default. `ax.open_up()` sets AXManualAccessibility (Electron) and AXEnhancedUserInterface (Chromium browsers only) at key down. In Cursor the system-wide focused element fails (-25204) but the app element answers; `ax.focused()` falls back to it, and Cursor's chat box was read as an AXTextArea. An edit-then-learn round trip in Cursor is not yet confirmed by the user.
- Hands-free (`[audio] hands_free`): a double tap of the talk key (two presses under 0.3 s, 0.4 s apart) keeps recording until the next tap, Esc, ×, or max_seconds. Tested with simulated key events.
- Recovery (`flow/pending.py`): when every provider fails, the trimmed FLAC is saved in `~/Library/Application Support/Bolo/pending/` (folder 700, files 600) and retried after 30 s, 1, 2, 4, 8 min, then every 15 min; a failed paste saves the text. Ready ones paste with `paste_saved` (⌃⌥V) or from the "Saved dictations" menu. Deleted when pasted or after `[recovery] keep_hours`. Tested with a fake failing provider.
- Sounds (`flow/sounds.py`, `[sounds]`): Tink at key down, Pop after release, Basso when a dictation fails. The sound files run on in a quiet fade (Tink is 0.56 s long, audible about 40 ms), so the audible length is measured from the file at launch. Recording starts first, the sound plays, the mute waits for the audible length plus 100 ms (a played sound reached the built-in speakers about 80 ms later, measured with Glass), and Pop plays after the unmute. The start sound and its echo (about 200 ms with Tink) are cut from the start of the clip before the quiet check and `prepare()`: in a synthetic test a loud beep left in made `prepare()` give the speech 5 dB less gain and trim less silence. At the user's volume Tink was hardly above room noise in the built-in mic. A talk-key press while Ctrl, Shift, Option or Cmd is already held no longer starts dictation, and a shortcut pressed while the talk key is held still runs. Tested with simulated key events; how it sounds is untested by the user.
- Styles per app (`flow/styles.py`, `[styles]`): `prompt` for Cursor, VS Code and Antigravity; `casual` for Slack, WhatsApp and Messages; `formal` for Mail, Outlook and Gmail; the Claude and ChatGPT apps and sites stay `default` at the user's request. Matched by bundle ID, then app name; in a browser by the page address, read through Accessibility (`AXURL` of the web area holding the focused field, about 2 ms in Chrome), and only when that cannot be read by window title words (`AXTitle`; window-server titles would need Screen Recording). Any style but `default` always runs the LLM pass with that style's rules added; it changes form, never words. Casual drops only the final full stop. Examples from the same app come first. Live on typed samples: 0.6 to 1.4 s per pass, no translation, no answering, corrections kept. flash-lite's free tier returned 429 after about 16 requests in one minute; the local text is pasted then. The prompt style once expanded "I'll" to "I will". Real dictation in each app is untested by the user.
- Prompt mode (`[prompt_mode]`, `Cleaner.to_prompt`): starts when a dictation begins with "as a prompt" or "prompt mode" (the user chose the spoken prefix only; a second key and "always in prompt apps" are built but off). Local Whisper wrote the prefix correctly in all 12 test clips (voices Rishi, Tara, Aman). The pill says "Writing prompt…". Output uses plain labels: Goal, Context, Requirements, Constraints, and Open questions only when the speaker asked some. It has its own 8 s timeout and a looser length check (1.6× + 120 characters). After a timeout the local text is pasted at once; after a quick failure the normal cleanup (with the app's style) runs. Either way Basso plays and the pill says so. A prompt-mode dictation is stored with `mode = 'prompt'` (new column, added on first start) and is never used as a style example. Live on 7 typed samples: 0.6 to 1.4 s, every listed detail kept, no invented questions, nothing answered, Hinglish kept. The user's own prompts are untested.
- Fast long dictations (`flow/early.py`, `[audio] chunk_seconds = 20`): while recording, every 20 s of new speech is cut at a pause (0.3 s near the room's noise floor, searched in the last 6 s; a forced cut at the quietest 100 ms after 28 s) and transcribed by the local model; on release only the rest is heard and the texts are joined before learned words, the prompt prefix and cleanup. Only when the local model is the first ready provider, so Gemini's 25 a day are not spent on pieces. If a piece fails, the whole clip goes the normal way; if the rest fails, the whole clip is saved for retry. Measured with synthetic voices fed faster than real time: 52 s clip 2.0 s → 0.8 s after release, 147 s clip 5.9 s → 0.8 s, every cut inside a pause, text within 1 to 3 words of the whole-clip result. Found on the way: Whisper's temperature fallback occasionally turns a stretch of a long clip into nonsense (one run in four, whole clip and pieces alike), and a 147 s clip of repeated sentences was rejected by the loop check while 20 s pieces were not. Untested on the user's real long dictations.
- Wrong-window guard (`[audio] guard_window`): on release Bolo notes the front app and its focused window (`ax.place()`, Accessibility, about 0.1 ms); just before pasting it checks again. If the app or window changed, the text goes to "Saved dictations" and the pill says to press ⌃⌥V where it belongs. Windows are compared with CFEqual: one window read twice matched, two windows of one app did not. Browser tabs share a window, so a tab switch is not caught. Tested with fake windows through `_write`; untested by the user.
- Usage in the menu (`flow/usage.py`): two menu lines, "Today: 12 dictations · 840 words · ~14 min saved" (typing at 40 wpm minus speaking and waiting time) and "Gemini today: speech 3/25 · cleanup 41", warning from 20 of 25 and naming the local reset time when used up. Gemini requests are counted where they are sent (each 503 retry too, since it is unclear whether Google counts those) and by the Pacific day, because the limits reset at midnight Pacific (12:30 IST in summer, 13:30 in winter). Only numbers are kept, in `~/Library/Application Support/Bolo/usage.json` (mode 600, last 30 days). A kept, not pasted, dictation is not counted. Tested with fake requests; the real menu is untested by the user.
- Settings window (`flow/settings_window.py`, menu "Settings…"): a non-activating panel like the fix window, with three tabs: General (talk key, hands-free, mute, sounds and volume, cleanup, auto-learning, early text, window guard), Dictionary (terms and hints, one per line) and Apps and prompts (style per app from running apps or a typed bundle ID; prompt-mode triggers). `config.save()` uses tomlkit (added with `uv add`; PyPI was reachable): it reads the file again at save time so hand edits are kept, changes only values that differ (a save with no change leaves the file byte for byte the same), keeps line-end comments, writes lists one per line and swaps the file in atomically. Everything applies without a restart: keys, styles and prompt mode are read from the config each time, and the cleaner, sounds and edit watcher are rebuilt. A talk or prompt key equal to the Wispr key gives a warning, not a block (the user runs fn for both); a prompt key equal to the talk key is refused. Sites and title words stay config.toml-only. Tested by driving the controls in code against a scratch copy of config.toml, and drawn to PNG in light and dark; typing in it and the pop-up menus are untested by the user.

## Goal

A small macOS menu bar app: hold a hotkey, speak, release, and the cleaned-up text is pasted into the focused app. It is the backup for Wispr Flow, used when Wispr shows its weekly/daily limit.

- Main use: dictating prompts for AI tools. Mostly English, some Hinglish (Hindi in Roman script), sometimes mixed in one sentence.
- Priorities, in order: accuracy, speed (target about 1 s after key release), near-zero load on the Mac, no cost.
- The user speaks Indian-accented English and prefers short, plain-language explanations.

## Hard boundaries

- Do not bypass, patch, intercept or reverse-engineer Wispr Flow, and do not call Wispr's backend. Do not read Wispr's internal files to detect its limit. Checking whether the `Wispr Flow` process is running is fine.
- Use only the user's own API keys on providers' free or paid tiers, within their terms. One account per provider.
- Never ask the user to paste API keys into chat. Keys go in `.env`, which is git-ignored.
- Audio is stored on disk only for voice training (user decision, 2026-10-18): `~/Library/Application Support/Bolo/voice/`, mode 600, deleted as soon as a training run has used it and after 14 days at the latest. Otherwise recordings stay in memory and are discarded after pasting.

## Machine (checked 2026-10-13)

- Apple M3 Pro, 18 GB RAM, macOS 26.5, about 136 GB free.
- `uv` is installed at `~/.local/bin/uv`. System Python is 3.14; pin the project to Python 3.12 with `uv`.
- No Homebrew, Ollama or ffmpeg. The design needs none of them.
- Wispr Flow is installed at `/Applications/Wispr Flow.app` and normally running.
- The project folder is `~/Desktop/bolo`. The user renamed it from `wisper hack` on 2026-10-14.

## Architecture

Hold hotkey -> record mic (16 kHz mono, in memory) -> release -> encode FLAC -> speech provider -> cleanup (if needed) -> paste via clipboard -> restore clipboard.

Speech providers are tried in an ordered chain from `config.toml`. HTTP 429 or a timeout moves to the next provider and remembers that until the cap resets. No internet goes straight to the local model if it is installed.

| Order | Provider | Model | Why |
|---|---|---|---|
| 1 | Google Gemini API | `gemini-3.5-transcribe` | Most accurate model with a free tier (2.6% on Artificial Analysis WER); code-switching; custom vocabulary; smart mode does cleanup |
| 2 | Groq | `whisper-large-v3` (or `-turbo`) | Free, very fast, about 8 h audio per day |
| 3 | Local, optional | Parakeet TDT 0.6B v2 via `parakeet-mlx` | Offline, no limits; English only |

Optional paid upgrades, only if the bake-off shows a clear gain and the user chooses to pay: ElevenLabs Scribe v2 (2.2%, about $0.39/h), Microsoft MAI-Transcribe-2 (2.0%, about $0.10/h). Not usable: Claude API (no audio input); OpenAI GPT Transcribe (3.3%, no free tier).

## API facts gathered (verify each before relying on it)

### Gemini 3.5 Transcribe (public preview, released 2026-08-26)

- Endpoint: `POST https://generativelanguage.googleapis.com/v1beta/interactions`, header `x-goog-api-key: <key>`, JSON body.
- Body: `model: "gemini-3.5-transcribe"`, `input: [{"type": "audio", "uri": <Files API uri>, "mime_type": "audio/flac"}]`, optional `generation_config.transcription_config`.
- `transcription_config` fields: `language_codes` (BCP-47; omit or empty for auto-detect and code-switching; `hi-IN` is supported), `custom_vocabulary` (up to 1,000 terms, best under about 100), `mode`.
- Python SDK: `from google import genai`; `client.files.upload(file=...)`; `client.interactions.create(...)`; text is in `interaction.output_text`.
- Supported audio includes `audio/flac` and `audio/wav`.
- Official docs: https://ai.google.dev/gemini-api/docs/transcribe
- Unresolved, check the official page first:
  - Shape of smart mode: sources disagree between `"mode": "smart"` and `"mode": {"type": "smart"}`.
  - Default mode: the official page says verbatim; one third-party source says smart.
  - Docs only show audio via Files API upload (two round trips). Test whether inline audio is accepted; if not, measure the upload cost on a 5 s clip.
  - One app reports "a few seconds" latency on this file model. If it cannot get near 1 s, either make Groq provider 1 or use `gemini-3.5-transcribe-live` (Live API, WebSocket, 4.0% WER, 10 min sessions).
- Free tier: confirmed "Free of charge" on Google's pricing page. Free-tier content is used to improve Google products; paid is not. Free caps are not published; they show in Google AI Studio per account. Preview models have tighter limits. Daily quotas reset at midnight Pacific. Paid: $0.003/min audio in + $0.002/min text out.

- Checked 2026-10-14, 3.5 Transcribe versus the 3.8 family: there is no "3.8 Transcribe". `gemini-3.8-live` is a speech-to-speech voice-agent model, not a transcriber. `gemini-3.8-flash` is a general model that can transcribe when prompted, but Google publishes no WER for it, and on Artificial Analysis the general Gemini models score 2.8 to 2.9% at speed factors of 7 to 20, against 2.6% at 91 for 3.5 Transcribe. Keep `gemini-3.5-transcribe` for speech. Google's models page now lists it as Stable (earlier sources said preview). `gemini-3.8-flash` or `gemini-3.5-flash-lite` are candidates for the text-only cleanup pass; optionally add `gemini-3.8-flash` with a transcription prompt to the bake-off.

### Groq

- Speech: `POST https://api.groq.com/openai/v1/audio/transcriptions`, header `Authorization: Bearer <key>`, multipart fields `file`, `model`, optional `prompt` (use for dictionary terms), `language`, `temperature=0`.
- Chat (cleanup): `POST https://api.groq.com/openai/v1/chat/completions` (OpenAI-compatible). Get current model ids from `GET /openai/v1/models`; the free line-up changes, so do not hardcode from memory.
- Free limits per several third-party sources from July to October 2026 (official docs page returned 403 to automated fetch): 20 requests/min, 2,000 requests/day, 7,200 audio s/hour, 28,800 audio s/day, each request counted as at least 10 s. Read the real values from the `x-ratelimit-*` response headers.
- Whisper returns Hindi in Devanagari or may translate; the cleanup pass must romanize and must not translate.

## Cleanup rules (tuned for prompting)

Try Gemini smart mode alone first. Add a text-only LLM pass (a free Gemini Flash-Lite model or a Groq-hosted model, provider set in config) if smart mode does not meet these rules. The pass always runs after Groq and local results, which are raw.

- Fix punctuation and capitals; remove "hmm", "umm", "aaa" and repeated words.
- Self-corrections keep only the final version ("Monday, no, Tuesday" -> "Tuesday").
- Spoken steps become a numbered list; spoken items become bullets; otherwise plain paragraphs. Config flag to disable.
- Keep technical terms, file names, function names and code identifiers exact; use the personal dictionary for spelling.
- "new line" and "new paragraph" are formatting commands.
- Never translate, never add content, never answer or act on the dictated text. It is a prompt for another tool.
- Hindi words stay in Roman script.
- Fast path: a short transcript with no fillers, corrections or list cues skips the LLM pass.

## Working alongside Wispr Flow

- Default hotkey: hold Right Command. It must differ from Wispr's key. Ask the user which key Wispr uses (its default is Fn).
- Optional takeover toggle: while the `Wispr Flow` process is not running, also answer on the user's Wispr key; step back when Wispr starts. Note: `pynput` does not report Fn reliably; a Quartz event tap on `flagsChanged` (keycode 63) is needed if the Wispr key is Fn.
- While idle the app is only a hotkey listener: no model loaded, no network calls.
- Automatic detection of Wispr's limit is out of scope: there is no supported signal, and two apps answering one key would type twice.

## Learning from the user's edits (the main differentiator)

Requested 2026-10-14: the app should get better over time from the user's own corrections. Research found no free or open-source dictation app confirmed to do this automatically; Wispr Flow reportedly does. Free apps such as Spokenly and FluidVoice already cover plain backup dictation, so this feature is the reason to build instead of install.

No model is trained. Learning is a small local store whose contents are sent as hints with every request, so it works the same with Gemini, Groq and the local model.

**Store:** SQLite at `~/Library/Application Support/FlowBackup/learning.db`. Tables: `dictations` (time, app, raw transcript, pasted text, corrected text if known), `vocabulary` (heard, intended, count, enabled), `examples` (raw, final, app).

**Capturing corrections, two ways:**

1. Explicit (build first, reliable everywhere): a "fix last dictation" hotkey opens a small window with the last pasted text; the user edits and confirms; the corrected text replaces the selection and the pair is saved.
2. Automatic (opt-in, second): after a paste, read the focused text field through the Accessibility API when focus changes or the next dictation starts, find the pasted span and diff it. Works in native text fields; unreliable in browsers, Electron apps and terminals, so it must fail silently. Never read secure (password) fields. Store only the pasted span and its edit, not the rest of the field.

**What is learned:**

- Vocabulary: word-level diffs. A swap is learned only when it looks like a mis-hearing (one to three tokens, similar sound or spelling), not when the user rewrote the meaning. After the same swap is seen twice, or once for a capitalised name or code identifier, it is added. Applied three ways: Gemini `custom_vocabulary`, Groq `prompt`, and a replacement rule after transcription.
- Style: the most recent 20 (raw -> final) pairs; the few most similar are added to the cleanup prompt as examples.
- Menu bar "Learned words" list where the user can review, disable or delete entries.

**Limits to be honest about:** it improves spelling of names and terms and the user's formatting preferences. It does not improve the underlying speech model's hearing.

## Going public (phase 2, only after the personal version works)

- Each user brings their own free API key (BYOK). Never ship the developer's key in the app; it would be extracted and one free tier cannot serve many users.
- Learning stays on each user's device by default.
- "Improves from all users" in a realistic form: opt-in, anonymous sharing of vocabulary pairs only (not sentences or audio), published as community word packs (for example developer terms, Hinglish). Needs a small server, a privacy policy and explicit consent; check India's DPDP Act and GDPR.
- Training a shared model on users' audio or text is out of scope: it needs explicit consent, large volumes of data and paid GPU time.
- The Python prototype is not distributable to normal users. A public app must be a signed and notarized Mac app (Apple Developer Program, $99 per year), rebuilt in Swift or built on an existing open-source app. License check: FluidVoice and VoiceInk are GPL-3.0 (a fork must stay GPL); Handy and OpenWhispr are MIT.
- Re-check each provider's terms for apps where end users supply their own free-tier keys.

## Files to create

```
pyproject.toml          # uv project, requires-python 3.12; extra "offline" = parakeet-mlx
config.toml             # hotkey, provider order, models, cleanup flags, personal dictionary
.env.example            # GEMINI_API_KEY=, GROQ_API_KEY=, optional ELEVENLABS_API_KEY=
.gitignore              # .env, .venv, __pycache__
flow/
  audio.py              # record to numpy; FLAC encode in memory (soundfile)
  stt/
    base.py             # shared interface: transcribe(audio) -> text; RateLimited / Unavailable errors
    gemini.py
    groq.py
    local_parakeet.py   # lazy-loaded; unload after 10 idle minutes
    elevenlabs.py       # optional, bake-off only
  cleanup.py            # fast-path check + LLM pass
  inject.py             # clipboard save, Cmd+V, restore
  learn/
    store.py            # SQLite: dictations, vocabulary, examples
    diff.py             # word-level diff; mis-hearing vs rewrite check
    fix_window.py       # "fix last dictation" popup
    ax_watch.py         # opt-in automatic capture via Accessibility API
  app.py                # hotkey, menu bar (idle / recording / processing / offline), provider chain
scripts/
  check_limits.py       # one tiny request per provider; print quotas and rate-limit headers
  try_transcribe.py     # record 5 s; print raw vs cleaned text with per-stage timings
  bakeoff.py            # run saved recordings through every provider, side by side
```

Dependencies: `sounddevice`, `soundfile`, `numpy`, `httpx`, `pynput`, `rumps`, `python-dotenv`, `google-genai` (if the SDK route is used; plain `httpx` otherwise).

## Build order

Steps 1 to 3 need no keys. Step 4 onward needs the user's keys in `.env`.

1. Scaffold: `pyproject.toml`, `.gitignore`, `.env.example`, `config.toml`; `uv sync`.
2. `flow/audio.py` and `flow/inject.py`; test recording and pasting locally.
3. Re-read the official Gemini transcribe page and resolve the unresolved items above.
4. Ask the user to create keys (Google AI Studio, Groq console), put them in `.env`, and report the free caps AI Studio shows for `gemini-3.5-transcribe`.
5. `scripts/check_limits.py`. Stop and report to the user if Gemini's free cap is too small for a default; then Groq becomes provider 1.
6. `flow/stt/gemini.py` and `flow/stt/groq.py`; `scripts/try_transcribe.py` with timings.
7. `flow/cleanup.py`.
8. `flow/app.py`: push-to-talk, provider chain, menu bar, Wispr-running check.
9. Bake-off on 12 of the user's real prompts (8 English with technical terms and one with steps, 3 mixed, 1 Hinglish). Pick provider order on accuracy first, speed second. Show the user the side-by-side output and let them choose.
10. Optional, ask first: offline Parakeet fallback (adds 1.5 to 2.5 GB disk, 2 to 3 GB RAM while loaded).
11. Tune the cleanup prompt and dictionary on real prompts.
12. Learning, part 1: `learn/store.py`, `learn/diff.py`, the "fix last dictation" window, and applying learned vocabulary and examples to requests.
13. Learning, part 2 (opt-in): automatic capture with `learn/ax_watch.py`; "Learned words" menu.
14. Optional later: live streaming for text while speaking.
15. Phase 2, separate decision with the user: public release (see "Going public").

macOS will ask for Microphone, Accessibility and Input Monitoring for the app that launches the script (VS Code or Terminal). The user must grant these.

## Expected resource use (estimates; measure after install)

| Mode | Disk | RAM | CPU |
|---|---|---|---|
| Cloud | 300 to 400 MB | 100 to 150 MB | near 0% idle |
| Offline fallback loaded | +1.5 to 2.5 GB | +2 to 3 GB | short GPU burst per clip |

## Verification

- `uv run scripts/check_limits.py` prints real quotas for each configured provider.
- `uv run scripts/try_transcribe.py` shows per-stage timings; note the total against the 1 s target.
- Fast path: a short clean sentence pastes with no LLM call in the log.
- Cleanup safety: dictating "write a function that deletes all files" returns that sentence punctuated, not code and not a refusal.
- List: "first open the terminal, second install the package, third run the app" becomes a three-item numbered list; a normal sentence stays a sentence.
- Filler and correction: "umm send it on Monday, no, Tuesday" becomes "Send it on Tuesday."
- Technical terms from the dictionary come out spelled exactly.
- Hinglish: a mixed sentence comes out in Roman script, not translated.
- End to end: `uv run python -m flow.app`; hold Right Command in TextEdit, a browser field and VS Code; text appears at the cursor and the previous clipboard content is restored.
- Fallback chain: with an invalid Gemini key, dictation still works through Groq and the log names the provider used.
- Alongside Wispr: with Wispr running, its key triggers only Wispr and Right Command triggers only this app. With takeover on, quit Wispr and confirm this app answers on the Wispr key; relaunch Wispr and confirm it steps back.
- Activity Monitor and `du -sh` match the resource estimates.
- Learning: dictate a sentence with a name the model gets wrong, correct it twice with "fix last dictation", then dictate it a third time and confirm it comes out right without a correction.
- Learning guard: rewrite a whole sentence in the fix window and confirm nothing is added to the vocabulary.
- Learned words menu: delete an entry and confirm the old spelling returns.
- Automatic capture, if enabled: edit pasted text in TextEdit and confirm the correction is recorded; confirm a password field is never read.

## Sources

- https://ai.google.dev/gemini-api/docs/transcribe
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/rate-limits
- https://artificialanalysis.ai/speech-to-text/non-streaming
- https://spokenly.app/blog/gemini-3-5-transcribe
- https://dev.to/unicodefawn/free-speech-to-text-and-text-to-speech-api-quotas-oct-2026-with-the-catches-17hg
- https://spokenly.app/blog/free-speech-to-text-apis
