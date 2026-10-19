from bolo.learn.diff import Swap, similarity, swaps, trim


def test_a_misheard_word_is_a_swap():
    assert [(s.heard, s.intended) for s in swaps("set the cable width", "set the table width")] == [("cable", "table")]


def test_a_name_is_trusted_the_first_time():
    (swap,) = swaps("we use pie torch here", "we use PyTorch here")
    assert swap.name and swap.trusted


def test_a_sound_alike_word_is_trusted():
    assert Swap("test", "text", False).trusted


def test_a_typo_is_not_trusted():
    assert not Swap("button", "buttom", False).trusted


def test_a_rewrite_teaches_nothing():
    assert swaps("please send the report today", "could you mail it over tomorrow morning") == []


def test_trim_drops_typed_text_around_the_dictation():
    assert trim("hello there", "Note: hello there!!") .endswith("hello there!!")
    assert trim("hello there", "hello there") == "hello there"


def test_similarity():
    assert similarity("a b c", "a b c") == 1
    assert similarity("a b", "c d") == 0
