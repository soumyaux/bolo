# Build plan: backup dictation app for prompting

Status on 2026-10-14 (end of first build session): steps 1 to 8 are built and the app runs. The main speech engine is now a local Whisper model, with Gemini as the fallback and as the cleanup pass. Not done: the bake-off on the user's real voice (step 9), cleanup tuning (11), learning (12, 13). A new session should read this whole file, then continue at step 9 in "Build order". Run the app with `uv run python -m flow.app`.

## Found during the build (2026-10-14)

- This Mac is behind a company Zscaler web filter. It blocks `api.groq.com`, `console.groq.com` and `api.elevenlabs.io` with "Not allowed the use of this AI/ML site". Do not work around it. `generativelanguage.googleapis.com`, `aistudio.google.com` and `huggingface.co` are reachable.
- So on this Mac the chain is Gemini, then the local Parakeet model if the user agrees to install it. `flow/stt/groq.py` is not written. The cleanup provider default is now Gemini.
- Open question for the user: this is a company-managed Mac whose policy restricts AI sites. Gemini's free tier uses submitted content to improve Google products. The user must decide whether dictating work prompts to it is acceptable, or whether to go local-only.
- The user's Wispr key is Fn and they want takeover: `takeover = true`, `wispr_key = "fn"` in `config.toml`. Right Command always works; Fn works only while Wispr Flow is not running.
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
- `uv` needs the macOS keychain certificates here; `native-tls = true` is set in `pyproject.toml`.
- Recording works (microphone permission is granted). The first stream start in a process takes about 3 s, later ones about 80 ms, so `app.py` must open and close the stream once at launch.
- Clipboard save and restore is tested. The live paste and the hotkey are untested: Visual Studio Code has neither Accessibility nor Input Monitoring permission yet.
- Gemini docs check: smart mode is the string `"mode": "smart"`; the default is verbatim. Inline audio is not documented for this model, but the Interactions API takes inline base64 (`data` + `mime_type`) for other file types. `flow/stt/gemini.py` tries inline first and switches to the Files API on HTTP 400. The raw JSON location of the transcript is not shown in the docs; the parser accepts `output_text`, `steps[].content[]` and `outputs[]`. Confirm both with the first real request.

## Goal

A small macOS menu bar app: hold a hotkey, speak, release, and the cleaned-up text is pasted into the focused app. It is the backup for Wispr Flow, used when Wispr shows its weekly/daily limit.

- Main use: dictating prompts for AI tools. Mostly English, some Hinglish (Hindi in Roman script), sometimes mixed in one sentence.
- Priorities, in order: accuracy, speed (target about 1 s after key release), near-zero load on the Mac, no cost.
- The user speaks Indian-accented English and prefers short, plain-language explanations.

## Hard boundaries

- Do not bypass, patch, intercept or reverse-engineer Wispr Flow, and do not call Wispr's backend. Do not read Wispr's internal files to detect its limit. Checking whether the `Wispr Flow` process is running is fine.
- Use only the user's own API keys on providers' free or paid tiers, within their terms. One account per provider.
- Never ask the user to paste API keys into chat. Keys go in `.env`, which is git-ignored.
- Do not store audio on disk. Recordings stay in memory and are discarded after pasting.

## Machine (checked 2026-10-13)

- Apple M3 Pro, 18 GB RAM, macOS 26.5, about 136 GB free.
- `uv` is installed at `~/.local/bin/uv`. System Python is 3.14; pin the project to Python 3.12 with `uv`.
- No Homebrew, Ollama or ffmpeg. The design needs none of them.
- Wispr Flow is installed at `/Applications/Wispr Flow.app` and normally running.
- The folder path contains a space (`wisper hack`); quote it in shell commands.

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
