# Contributing to Bolo

Thanks for helping. Bug reports, fixes and small features are all welcome; for anything big, open an issue first so we can agree on the approach.

## Development setup

You need an Apple Silicon Mac with macOS 13+, the Xcode Command Line Tools and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/soumyaux/bolo.git
cd bolo
uv sync
cp .env.example .env          # optional: add GROQ_API_KEY
uv run python -m bolo.app     # run from the terminal
```

Run from a terminal, macOS asks for the permissions for the terminal app, not Bolo.app. To test the real app, build it with `uv run python -m bolo.login` and open `~/Applications/Bolo.app`.

- `BOLO_DEBUG=1 uv run python -m bolo.app` also logs every talk-key press with its timing.
- Your settings live in `~/Library/Application Support/Bolo/config.toml`; defaults are in `config.default.toml`. Change the defaults only for something every user should get.
- [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) explains the pipeline and where each part lives.

## Before you open a pull request

```bash
uv run pytest
uvx ruff check bolo tests
```

Add a test when you change logic that does not need a microphone or macOS APIs (see `tests/`).

## Commit style

Small commits with a [Conventional Commits](https://www.conventionalcommits.org/) prefix and a plain sentence: `fix: say Copied, not Pasted, when no text box has the focus`. Write what changes for the user, not how.

## Privacy rules

Bolo must never log or send what the user said, except the audio to the speech provider they chose. Keep it that way in every change.
