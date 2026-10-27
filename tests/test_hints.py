from boppos.ui.hints import GLYPHS, family_for_type, hint_items


def labels(items):
    return [(g, label) for g, label, _ in items]


def test_family_from_sdl_gamepad_type():
    assert [family_for_type(t) for t in (5, 6, 4)] == ["playstation"] * 3
    assert [family_for_type(t) for t in (2, 3)] == ["xbox"] * 2
    assert [family_for_type(t) for t in (7, 8, 9, 10)] == ["nintendo"] * 4
    assert family_for_type(0) == family_for_type(1) == family_for_type(99) == "generic"


def test_playstation_uses_shapes_and_shoulder_names():
    items = hint_items("pad", "playstation")
    assert labels(items) == [("✕", "Select"), ("○", "Back"), ("L1 / R1", "Page"), ("R Stick", "Jump")]
    assert items[0][2] == "glyph-cross" and items[1][2] == "glyph-circle"
    assert labels(hint_items("pad", "playstation", auth=True))[-1] == ("Options", "OK")


def test_xbox_and_generic_and_nintendo_labels():
    assert labels(hint_items("pad", "xbox"))[0] == ("A", "Select")
    assert labels(hint_items("pad", "xbox", auth=True))[-1] == ("Menu", "OK")
    assert labels(hint_items("pad", "generic", auth=True))[-1] == ("Start", "OK")
    # Nintendo's bottom button is labelled B and confirms; its right button is A and goes back
    assert labels(hint_items("pad", "nintendo"))[:2] == [("B", "Select"), ("A", "Back")]


def test_keyboard_prompts_ignore_the_controller_family():
    assert labels(hint_items("kb", "playstation")) == [("Enter", "Select"), ("Esc", "Back"), ("Ctrl+Tab", "Page"),
                                                       ("PgUp / PgDn", "Jump")]
    assert labels(hint_items("kb", "xbox", auth=True)) == [("Enter", "OK"), ("Esc", "Cancel"),
                                                           ("Backspace", "Delete")]


def test_unknown_family_falls_back_to_generic():
    assert hint_items("pad", "does-not-exist") == hint_items("pad", "generic")
    assert set(GLYPHS) == {"xbox", "playstation", "nintendo", "generic"}
