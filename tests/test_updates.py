from bolo import updates

LS_REMOTE = """\
1111111111111111111111111111111111111111\trefs/tags/v0.1.0
2222222222222222222222222222222222222222\trefs/tags/v0.10.0
3333333333333333333333333333333333333333\trefs/tags/v0.9.2
4444444444444444444444444444444444444444\trefs/tags/v1.0.0-beta
5555555555555555555555555555555555555555\trefs/tags/notes
"""


def test_newest_compares_numbers_not_text():
    # 0.10.0 is newer than 0.9.2; tags that are not plain releases are ignored.
    assert updates.newest(LS_REMOTE) == "0.10.0"


def test_no_releases():
    assert updates.newest("") is None


def test_is_newer():
    assert updates.is_newer("0.2.0", "0.1.0")
    assert not updates.is_newer("0.1.0", "0.1.0")
    assert not updates.is_newer("0.1.0", "0.2.0")
    assert not updates.is_newer(None, "0.1.0")


def test_this_copy_has_a_version():
    assert updates.parse(updates.current())
