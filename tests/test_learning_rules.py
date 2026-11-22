from bolo.learn import usable
from bolo.learn.diff import english


def test_ordinary_words_are_never_replaced_everywhere():
    for heard, intended in [("then", "when"), ("mode", "more"), ("test", "text"), ("pooled", "pulled"), ("if", "IAF")]:
        assert not usable(heard, intended)


def test_numbers_and_typos_are_not_learned():
    assert not usable("190", "149")
    assert not usable("buy", "buy.\\")
    assert not usable("pl", "PilL")
    assert not usable("id", "IDv")


def test_mis_heard_names_are_learned():
    assert usable("dipti", "Deepti")
    assert usable("sq", "sku")
    assert usable("github", "GitHub")


def test_word_endings_count_as_english():
    assert english("updated") and english("multiplied") and english("pooled")
