"""Compares the cleanup pass through Ollama with the same Qwen model run in-process by mlx-lm.

Same system prompt, same inputs, temperature 0. Prints each answer, its time, and whether the
safety checks in Cleaner.clean would have accepted it.

Run: uv run --with mlx-lm scripts/cleanup_compare.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bolo import config
from bolo.cleanup import _CORRECTION, Cleaner, _lost, tidy

MLX_MODEL = "mlx-community/Qwen2.5-3B-Instruct-4bit"

CASES = [
    "send it to Rahul on Monday, no wait, Tuesday, and copy Priya",
    "first open the settings then pick the microphone next press save",
    "so the plan is we ship the installer this week and then we write the readme next week and after that we make the repo public and announce it",
    "i mean the meeting is at three not four",
    "step one clone the repo step two run install dot sh step three hold fn and speak",
    "kal subah meeting hai, उसके बाद lunch karenge",
    "can you check why the build fails on macos latest scratch that on macos fourteen",
    "please review the pull request and leave comments on the error handling and the tests and the naming",
]


def accepted(text: str, cleaned: str) -> str:
    if not cleaned or len(cleaned) > len(text) * 1.5 + 40:
        return "REJECTED (too long)"
    lost = [] if _CORRECTION.search(text) else _lost(text, cleaned)
    return f"REJECTED (lost {lost})" if lost else "ok"


def main() -> None:
    cfg = config.load()
    cleaner = Cleaner(cfg)
    terms = cfg["dictionary"]["terms"]
    captured: dict[str, str] = {}
    # Capture the exact system prompt Cleaner builds, then send it to both backends.
    cleaner._ask = lambda system, text, **_: captured.setdefault("system", system) and ""

    from mlx_lm import generate, load

    started = time.perf_counter()
    model, tokenizer = load(MLX_MODEL)
    print(f"mlx-lm load: {time.perf_counter() - started:.1f} s\n")
    cleaner_ollama = Cleaner(cfg)
    cleaner_ollama._ask("", "warm", timeout=60)  # loads the Ollama model so its first time is fair

    totals = {"ollama": 0.0, "mlx": 0.0}
    for raw in CASES:
        text = tidy(raw, terms)
        captured.clear()
        cleaner._llm(text, terms, [], "default")
        system = captured["system"]
        user = f"<transcript>\n{text}\n</transcript>"

        t = time.perf_counter()
        ollama = cleaner_ollama._ask(system, text)
        totals["ollama"] += (o_s := time.perf_counter() - t)

        prompt = tokenizer.apply_chat_template(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            add_generation_prompt=True,
            tokenize=False,
        )
        t = time.perf_counter()
        mlx = generate(model, tokenizer, prompt=prompt, max_tokens=len(text) // 2 + 64).strip()
        totals["mlx"] += (m_s := time.perf_counter() - t)

        print(f"IN     {text}")
        print(f"OLLAMA {o_s:4.2f}s {accepted(text, ollama):8} {ollama}")
        print(f"MLX    {m_s:4.2f}s {accepted(text, mlx):8} {mlx}\n")
    print(f"total  ollama {totals['ollama']:.1f} s   mlx {totals['mlx']:.1f} s")


if __name__ == "__main__":
    main()
