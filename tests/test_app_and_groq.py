from bolo.app import parse_shortcut
from bolo.stt.groq import Budget, _prompt


def test_parse_shortcut():
    code, flags = parse_shortcut("<ctrl>+<alt>+f")
    assert flags and code == parse_shortcut("<alt>+<ctrl>+F")[0]
    assert parse_shortcut("f") is None
    assert parse_shortcut("<ctrl>+<nope>") is None
    assert parse_shortcut("") is None


def test_prompt_keeps_the_first_words_and_the_limit():
    assert _prompt(["a", "b", "a"]) == "a, b."
    assert _prompt([]) == ""
    long = _prompt([f"word{i}" for i in range(500)])
    assert len(long) <= 601 and long.startswith("word0, word1")


def test_budget_counts_short_clips_as_ten_seconds(tmp_path):
    budget = Budget(tmp_path / "usage.json")
    budget.spend(2)
    assert budget.day_share() == max(1 / 2000, 10 / 28_800)
    # Kept across a restart.
    assert Budget(tmp_path / "usage.json").day_share() == budget.day_share()


def test_budget_stops_before_the_per_minute_limit():
    budget = Budget()
    for _ in range(17):
        budget.spend(1)
    assert budget.full(1) == ""
    budget.spend(1)
    assert "minute" in budget.full(1)


def test_budget_uses_groqs_own_count():
    budget = Budget()
    budget.spend(1, {"x-ratelimit-limit-requests": "2000", "x-ratelimit-remaining-requests": "200"})
    assert budget.day_share() >= 1800 / 2000
