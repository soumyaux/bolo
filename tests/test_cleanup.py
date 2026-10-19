from bolo.cleanup import _lost, _sound_alike_terms, needs_llm, prompt_prefix, tidy


def test_short_plain_text_skips_the_llm():
    assert not needs_llm("open the settings", 20)


def test_long_text_corrections_and_lists_need_the_llm():
    assert needs_llm("word " * 30, 20)
    assert needs_llm("meet on Monday, no, Tuesday", 20)
    assert needs_llm("first buy milk and then buy eggs", 20)


def test_prompt_prefix():
    assert prompt_prefix("As a prompt, write a poem", ["as a prompt"]) == "Write a poem"
    assert prompt_prefix("Ajax prompt, write a poem", ["as a prompt"]) == "Write a poem"
    assert prompt_prefix("Write a prompt for me", ["as a prompt"]) is None
    assert prompt_prefix("Prompt engineering is fun", ["as a prompt"]) is None


def test_dictionary_term_is_spelled_as_written():
    assert _sound_alike_terms("I live in Hinglis mostly", ["Hinglish"]) == "I live in Hinglish mostly"


def test_ordinary_words_are_never_turned_into_a_term():
    assert _sound_alike_terms("the cable is long", ["table"]) == "the cable is long"


def test_tidy_removes_fillers_and_repeats():
    assert tidy("um so the the button is red", []) == "So the button is red."


def test_lost_words_are_found():
    assert _lost("restart Bolo now please", "restart now please") == ["Bolo"]
    assert _lost("first buy milk and then eggs", "1. Buy milk\n2. Eggs") == []
