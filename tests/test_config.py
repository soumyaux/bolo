import tomllib

import pytest

from bolo import config

DEFAULTS = """[hotkey]
# The talk key.
push_to_talk = "fn"

[cleanup]
model = "qwen2.5:3b"
enabled = true

[styles.apps]
"com.apple.mail" = "formal"
"""


@pytest.fixture
def files(tmp_path, monkeypatch):
    defaults = tmp_path / "config.default.toml"
    defaults.write_text(DEFAULTS)
    monkeypatch.setattr(config, "DEFAULTS", defaults)
    return defaults, tmp_path / "user" / "config.toml"


def test_defaults_alone(files):
    defaults, user = files
    assert config.load(user, defaults)["cleanup"]["model"] == "qwen2.5:3b"


def test_user_values_win_and_the_rest_stays(files):
    defaults, user = files
    user.parent.mkdir()
    user.write_text('[cleanup]\nmodel = "llama3.2:3b"\n[styles.apps]\n"Slack" = "casual"\n')
    cfg = config.load(user, defaults)
    assert cfg["cleanup"] == {"model": "llama3.2:3b", "enabled": True}
    # A list of the user's own entries replaces the default one whole.
    assert cfg["styles"]["apps"] == {"Slack": "casual"}


def test_first_save_copies_the_defaults_with_comments(files):
    defaults, user = files
    config.save({("hotkey", "push_to_talk"): "cmd_r"}, user)
    text = user.read_text()
    assert "# The talk key." in text
    assert tomllib.loads(text)["hotkey"]["push_to_talk"] == "cmd_r"
    assert user.stat().st_mode & 0o777 == 0o600
    assert "fn" in defaults.read_text()


def test_save_keeps_hand_edits(files):
    defaults, user = files
    config.save({("cleanup", "enabled"): False}, user)
    user.write_text(user.read_text() + '\n[extra]\nmine = 1\n')
    config.save({("cleanup", "model"): "x"}, user)
    saved = tomllib.loads(user.read_text())
    assert saved["extra"]["mine"] == 1 and saved["cleanup"] == {"model": "x", "enabled": False}
