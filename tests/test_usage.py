from bolo.usage import Usage


def test_fresh_usage_is_empty(tmp_path):
    usage = Usage(tmp_path / "usage.json")

    assert usage.today() == {}
    assert usage.line() == "Today: 0 dictations · 0 words"


def test_add_dictation_and_words(tmp_path):
    usage = Usage(tmp_path / "usage.json")

    usage.add("dictations")
    usage.add("words", 25)

    assert usage.today()["dictations"] == 1
    assert usage.today()["words"] == 25


def test_menu_line_shows_today_usage(tmp_path):
    usage = Usage(tmp_path / "usage.json")

    usage.add("dictations")
    usage.add("dictations")
    usage.add("words", 50)

    assert usage.line() == "Today: 2 dictations · 50 words · ~1 min saved"
