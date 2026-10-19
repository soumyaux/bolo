"""Setup steps for install.sh. Each can be run again safely.

Run: uv run python -m bolo.setup models              (downloads the local speech model)
     uv run python -m bolo.setup set KEY VALUE        (KEY like cleanup.model; VALUE is TOML,
                                                       or plain text for a string)
     uv run python -m bolo.setup groq-key KEY         (saves the Groq key, readable by you only)
"""

import sys
import tomllib

from . import config
from .stt.local_whisper import cached_model, ensure_model


def models() -> None:
    model = config.load()["stt"]["local"]["model"]
    if cached_model(model):
        print(f"Speech model {model} is already on this Mac.")
        return
    print(f"Downloading speech model {model} (about 1.5 GB)…")
    ensure_model(model)
    print("Speech model ready.")


def set_value(key: str, value: str) -> None:
    try:
        parsed = tomllib.loads(f"v = {value}")["v"]
    except tomllib.TOMLDecodeError:
        parsed = value
    config.save({tuple(key.split(".")): parsed})
    print(f"{key} = {parsed!r} saved in {config.PATH}")


def groq_key(key: str) -> None:
    config.USER_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        line
        for line in (config.ENV.read_text().splitlines() if config.ENV.exists() else [])
        if not line.startswith("GROQ_API_KEY=")
    ]
    config.ENV.write_text("\n".join(lines + [f"GROQ_API_KEY={key.strip()}"]) + "\n")
    config.ENV.chmod(0o600)
    print(f"Groq key saved in {config.ENV}")


def main(args: list[str]) -> None:
    match args:
        case ["models"]:
            models()
        case ["set", key, value]:
            set_value(key, value)
        case ["groq-key", key]:
            groq_key(key)
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
