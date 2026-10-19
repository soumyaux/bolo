# Plan: free the prompt model's memory, and catch Ollama losing the GPU

Written 2026-10-19, for a later session. Two independent parts; do A first, it is the bigger win.

## Why

Measured on this Mac (M3 Pro, 18 GB) on 2026-10-19:

| | Memory | Cold load on the GPU |
|---|---|---|
| qwen2.5:3b (cleanup) | 2.2 GB | 1.7 s |
| qwen2.5:7b (prompt mode) | 4.8 GB | 4.3 s |
| Whisper large-v3-turbo (local MLX) | about 2 GB | 1.3 to 1.7 s |

- **The 7b model is nearly always loaded.** `_warm_providers(self._prompt_likely())` ([flow/app.py](../flow/app.py), in `_worker` on `"down"`) warms it at every key-down in a prompt-style app. VS Code, Cursor and Antigravity are prompt-style, and the user dictates in VS Code most of the time. `_KEEP_ALIVE = "30m"` ([flow/cleanup.py](../flow/cleanup.py)) then keeps it loaded.
- **Prompt mode is rare.** The user starts it only by saying "as a prompt" or "prompt mode". `[prompt_mode] key = ""` and `in_prompt_apps = false` in [config.toml](../config.toml).
- **Everything together takes about 9 GB of 18 GB.**
- **Ollama lost the GPU on 2026-10-18 at 22:47.** It was restarted at that time. Its GPU check hit a Metal assert, `GGML_ASSERT([rsets->data count] == 0)` in `ggml-metal-device.m`, and timed out after 15 s. Ollama then reported `"inference compute" ... library=cpu ... available="4.6 GiB"` in `~/Library/Logs/Ollama.log`.
- **What the CPU fallback cost.** Every model ran on the CPU (`size_vram: 0` in `/api/ps`). A one-line cleanup took 2.6 to 5 s, so every cleanup hit the 3 s timeout, and after two failures the pass rested for 5 minutes. Nothing told the user.
- **Fixing it by hand worked.** Killing `ollama serve` and starting it again with `start_ollama()` brought Metal back: cleanup took 0.36 s.
- **Low free memory may have caused the crash.** This is a guess, not proven, but it is a second reason for Part A.

### Constraint that shapes part A

Prompt mode has a 5 s budget (`[prompt_mode] timeout_seconds = 5`), and the 7b model needs 4.3 s just to load. So "load it once the transcript starts with the prefix" (after the key is released) would time out almost every time. The model has to start loading while the user is still speaking. Alternatively, the request needs a fallback that never waits for a cold model.

## Part A: load the 7b model only for prompt dictations

### A1. Stop warming the 7b model just because a prompt-style app is in front
- In `_prompt_likely()` ([flow/app.py](../flow/app.py)), the front-app style counts only when `prompt_mode.in_prompt_apps` is on. That is the only case where every dictation in that app is a prompt. The prompt key still counts.
- Nothing else changes: the 3b model is still warmed at every key-down.

### A2. Catch the spoken prefix early, while the user is still talking
- **Where:** a new small helper, in [flow/early.py](../flow/early.py) or next to it, started from the `"down"` branch of `_worker`. Only start it when `prompt_mode.use_prefix` is on and the prompt model is not loaded already (see A4).
- **What it does:**
  - Wait until 2.5 s of audio after the start sound: `recorder.frames - sum(self._beep)`.
  - Take that audio with `recorder.snapshot(take, start)`.
  - Turn it into text with the **local** model, never Groq. Groq bills each request as 10 s and would waste the free quota.
  - If `prompt_prefix(text, prefixes)` ([flow/cleanup.py](../flow/cleanup.py)) is not None, call `self.cleaner.warm(prompt=True)`.
  - At most one try per dictation. Stop if the key is released first.
- **Why it fits:** prompts are long dictations, so the 4.3 s load finishes while the user is still speaking. A 2.5 s clip takes the local turbo model about 0.2 to 0.4 s.
- **Thread safety:**
  - `LocalWhisper` ([flow/stt/local_whisper.py](../flow/stt/local_whisper.py)) has no lock. Add a `threading.Lock` around the MLX call in `transcribe`, because `EarlyText` may run at the same time on long clips.
  - If the local model is not loaded, skip the check rather than load it. Find out how `_loaded` / the idle unload work in that file.
- **Log it:** `log.info("prompt prefix heard early: loading %s", model)`.

### A3. Never wait on a cold 7b model
- **Where:** `Cleaner.to_prompt()` ([flow/cleanup.py](../flow/cleanup.py)).
- **Check first:** ask `GET /api/ps` (about 1 ms on localhost) whether `self.prompt_model` is loaded.
- **If it is not loaded:**
  - Lay out the prompt with `self.model` (the 3b model, already warm) and the same `_PROMPT` system text.
  - Start loading the 7b model in the background for next time.
  - Return the route `"prompt-small"`. Add it where routes are checked in [flow/app.py](../flow/app.py) `_write`. It counts as a success, like `"prompt"`: the island shows done, and `learning.record` gets mode `"prompt"`.
- **Check quality first:** before making `"prompt-small"` the fallback, run the 3b model over the 7 typed prompt samples used before. PLAN.md describes them under "Prompt mode"; ask the user for the samples if they are not in the repo. Compare its output with the 7b output.
  - If 3b drops details or invents questions, do not use it. Fall back to the current behaviour instead: normal cleanup, route `"prompt-failed"`.

### A4. Free the 7b model sooner
- **Where:** `Cleaner.warm()` and `_ask()` ([flow/cleanup.py](../flow/cleanup.py)).
- **What:** give the prompt model its own `keep_alive`: a new key `[prompt_mode] keep_alive = "5m"`, default `"5m"`. The 3b model keeps `"30m"`.
- **Why:** a burst of prompt dictations still finds it loaded, and an idle Mac gets 4.8 GB back within 5 minutes.

### Done when
- With VS Code in front and normal dictation, `curl -s localhost:11434/api/ps` lists only `qwen2.5:3b`.
- Saying "as a prompt …" for about 8 s:
  - the log shows "prompt prefix heard early";
  - the island shows "Writing prompt" with `qwen2.5:7b`;
  - the route is `prompt`, not `prompt-timeout`.
- A short "as a prompt" dictation (about 3 s) with the 7b model unloaded gives route `prompt-small` (or `prompt-failed` if A3 was rejected), never a 5 s wait.
- Five minutes after the last prompt, `/api/ps` no longer lists the 7b model.

## Part B: notice when Ollama runs without the GPU, and restart it

### B1. Detect it
- **Where:** a new function `on_gpu(client, model) -> bool | None` in [flow/cleanup.py](../flow/cleanup.py).
- **How:** read `/api/ps` and find `model`. Return `size_vram > 0`, or None when the model is not loaded or the server does not answer.
- **Skip on Intel:** only check on Apple Silicon (`platform.machine() == "arm64"`). Intel Macs have no Metal path to expect.
- **When to call it:**
  - in `Cleaner.warm()`'s background thread, right after the load request returns;
  - once after startup, after `start_ollama()` and the first warm in `_worker`.

### B2. Restart Ollama once
- **Where:** a new function `restart_ollama()` next to `start_ollama()`.
- **Which process:**
  - Find the PID that listens on port 11434: `lsof -ti tcp:11434 -sTCP:LISTEN`.
  - Restart only an `ollama serve` whose binary is in `_OLLAMA_BINARIES` or on the PATH. If it belongs to `Ollama.app`, quit and reopen the app with `osascript -e 'quit app "Ollama"'` and `open -a Ollama`. If it is anything else, do nothing and only warn.
  - Send SIGTERM and wait up to 5 s for the port to close. Then call `start_ollama()`.
- **Wait for it:** poll `/api/version` for up to 30 s. A failed GPU check took 15 s.
- **Then:** warm the 3b model and run `on_gpu` again.
- **Limits:**
  - At most one restart per 10 minutes (keep the time in `Cleaner`), so a Mac that really cannot use Metal never loops.
  - Never restart while a cleanup request is running: hold `_ask`'s path with a lock, or skip if one is in flight.

### B3. Tell the user if it stays on the CPU
- **The log line:** `log.warning("Ollama is running on the CPU (no GPU); restarting it")`, then either "back on the GPU" or "still on the CPU".
- **If it is still on the CPU after the restart:**
  - set the menu status line through the app with `_show(WARN, "Cleanup is slow: Ollama has no GPU")`;
  - skip the LLM pass (route `llm-skipped`) until the next check, so dictations do not each wait 3 s first.
- **Wiring:** pass a callback into `Cleaner`, or return a status that `app.py` reads, so `cleanup.py` does not import the app.

### Done when
- **Test 1 (refused GPU):** temporarily point `on_gpu` at a fake `/api/ps` that answers `size_vram: 0`. The log shows one restart, then "still on the CPU", and the menu shows the warning. A second warm within 10 minutes does not restart again.
- **Test 2 (normal):** with a healthy Ollama, nothing is restarted and nothing extra is logged.

## Files touched
- [flow/app.py](../flow/app.py):
  - `_prompt_likely`;
  - the `"down"` branch of `_worker` (start the prefix check);
  - the route handling in `_write` (`prompt-small`);
  - the WARN status from B3.
- [flow/cleanup.py](../flow/cleanup.py): `warm`, `to_prompt`, `_ask`'s keep_alive, and the new `on_gpu` and `restart_ollama`.
- [flow/early.py](../flow/early.py), or a new small module: the prefix check.
- [flow/stt/local_whisper.py](../flow/stt/local_whisper.py): the lock.
- [config.toml](../config.toml): `[prompt_mode] keep_alive`.
- [PLAN.md](../PLAN.md): a line under the prompt mode and cleanup notes.

## Verify end to end
1. `uv run python -m flow.app` from the terminal, or restart Bolo by PID and `open ~/Applications/Bolo.app`.
2. Run the "Done when" checks above while watching `~/Library/Logs/Bolo.log`, `~/Library/Logs/Ollama.log` and `curl -s localhost:11434/api/ps`.
3. Before and after part A, compare the memory Ollama uses during an hour of normal (non-prompt) dictation. Expect about 4.8 GB less.
4. Commit on `main` (never a new branch).
