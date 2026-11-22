from bolo.inject import _MAX_UNITS, _pieces, can_type


def test_pieces_join_back_to_the_text():
    text = "hello world, this is Bolo typing नमस्ते 🎉 done"
    assert "".join(_pieces(text)) == text


def test_long_word_is_cut_to_fit_one_event():
    pieces = _pieces("a" * 50)
    assert "".join(pieces) == "a" * 50
    assert all(len(p.encode("utf-16-le")) // 2 <= _MAX_UNITS for p in pieces)


def test_lines_are_pasted_not_typed():
    assert can_type("one line")
    assert not can_type("1. first\n2. second")
    assert not can_type("")
