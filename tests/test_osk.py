from boppos.ui.osk import ROWS_ABC, ROWS_SYM, PasswordBuffer


def test_typing_backspace_and_take_clears():
    b = PasswordBuffer()
    for ch in "hunter2":
        b.type(ch)
    b.backspace()
    assert len(b) == 6 and b.display() == "••••••"
    assert b.take() == "hunter" and len(b) == 0


def test_shift_is_one_shot_and_only_for_letters():
    b = PasswordBuffer()
    b.toggle_shift()
    b.type("1")
    assert b.shift  # digits do not consume shift
    b.type("a")
    b.type("b")
    assert b.take() == "1Ab"


def test_visibility_toggle_and_echo():
    b = PasswordBuffer()
    b.type("x")
    assert b.display() == "•" and b.display(echo=True) == "x"
    b.toggle_visible()
    assert b.display() == "x"


def test_rejects_non_printable_and_multichar():
    b = PasswordBuffer()
    b.type("\n")
    b.type("ab")
    b.type("")
    assert len(b) == 0


def test_layers():
    b = PasswordBuffer()
    assert b.rows() == ROWS_ABC
    b.toggle_shift()
    assert b.rows()[1] == "QWERTYUIOP" and b.rows()[0] == "1234567890"
    b.toggle_symbols()
    assert b.rows() == ROWS_SYM and not b.shift
    assert all(len(r) >= 8 for r in ROWS_SYM)


def test_every_ascii_special_is_reachable():
    keys = set("".join(ROWS_ABC) + "".join(ROWS_SYM) + " ")
    for ch in "!@#$%^&*()-_=+[]{}\\|;:'\"/?<>`~,.":
        assert ch in keys, ch
