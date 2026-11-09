from boppos.ui.osk import CHAR_ROWS, LAYOUT, SYM_ROWS, UNITS, PasswordBuffer, key_unit


def char_key(base: str):
    return next(k for row in LAYOUT for k in row if k.kind == "char" and k.base == base)


def test_typing_backspace_and_take_clears():
    b = PasswordBuffer()
    for ch in "hunter2":
        b.type(ch)
    b.backspace()
    assert len(b) == 6 and b.display() == "••••••"
    assert b.take() == "hunter" and len(b) == 0


def test_shift_is_one_shot_and_gives_the_real_keyboard_symbol():
    b = PasswordBuffer()
    b.toggle_shift()
    b.press(char_key("1"))
    assert not b.shift
    b.press(char_key("a"))
    b.toggle_shift()
    b.press(char_key("a"))
    b.toggle_shift()
    b.press(char_key("["))
    assert b.take() == "!aA{"


def test_caps_lock_only_letters_and_shift_undoes_it():
    b = PasswordBuffer()
    b.toggle_caps()
    b.press(char_key("a"))
    b.press(char_key("1"))
    b.toggle_shift()
    b.press(char_key("b"))
    assert b.caps and b.take() == "A1b"
    assert not b.caps  # take() clears the state for the next prompt


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


def test_layout_rows_are_full_width_and_on_half_keys():
    for row in LAYOUT:
        assert sum(k.width for k in row) == UNITS, row
        assert all((k.width * 2).is_integer() for k in row)


def test_layout_matches_a_real_keyboard():
    kinds = [[k.kind for k in row] for row in LAYOUT]
    assert kinds[0][-1] == "backspace"  # end of the number row
    assert kinds[1][0] == "symbols"  # where Tab is
    assert kinds[2][0] == "caps" and kinds[2][-1] == "enter"  # home row
    assert kinds[3][0] == kinds[3][-1] == "shift"  # both ends of z..
    assert "space" in kinds[4]
    assert [k.base for k in LAYOUT[1] if k.kind == "char"][:3] == list("qwe")


def test_every_printable_ascii_is_reachable():
    keys = {k.base for row in LAYOUT for k in row} | {k.shifted for row in LAYOUT for k in row} | {" "}
    for cp in range(0x21, 0x7F):
        assert chr(cp) in keys, chr(cp)
    for base, shifted in CHAR_ROWS + SYM_ROWS:
        assert len(base) == len(shifted)


def test_sym_page_has_every_ascii_symbol_without_shift():
    b = PasswordBuffer()
    b.toggle_shift()
    b.toggle_symbols()
    assert b.symbols and not b.shift
    shown = {b.label(k) for row in LAYOUT for k in row if k.kind == "char"}
    for ch in "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~":
        assert ch in shown, ch
    b.press(char_key("q"))  # q's place on the Sym page is !
    b.press(char_key("1"))  # the number row keeps its digits
    b.toggle_symbols()
    b.press(char_key("q"))
    assert b.take() == "!1q"
    assert not b.symbols


def test_key_size_grows_with_the_window():
    deck = key_unit(1280, 800)
    legion = key_unit(2560, 1600)
    assert legion[0] >= 1.9 * deck[0] and legion[1] >= 1.9 * deck[1]
    assert UNITS * legion[0] <= 2560 and UNITS * deck[0] <= 1280


def test_key_size_leaves_room_for_the_prompt():
    w, h = key_unit(1920, 700)  # wide and short: the height limits it
    assert len(LAYOUT) * h <= 700 - 380
    assert w <= h * 1.25
