from bolo.learn import ax


def test_changed_sees_a_late_update(monkeypatch):
    answers = iter(["old", "old", "old new"])
    monkeypatch.setattr(ax, "value", lambda _: next(answers))
    assert ax.changed(ax.Field(object(), "old"), 1)


def test_unchanged_box_is_reported(monkeypatch):
    monkeypatch.setattr(ax, "value", lambda _: "old")
    assert not ax.changed(ax.Field(object(), "old"), 0.1)
