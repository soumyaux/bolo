"""Loads config.toml and the API keys from .env."""

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load() -> dict:
    load_dotenv(ROOT / ".env")
    with open(ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def key(name: str) -> str | None:
    """Returns the key from the environment, or None when it is missing or blank."""
    value = os.environ.get(name, "").strip()
    return value or None
