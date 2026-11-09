"""Pure logic of the built-in gamepad keyboard used for password prompts.

The layout follows a real US keyboard (and the on-screen keyboards of Steam and Plasma): a number row with
Backspace at its end, Enter at the end of the home row, a Shift at both ends of the bottom letter row and Caps
Lock where it sits on a real keyboard. Shift gives the symbols printed on the same key of a real keyboard
(1 -> !, [ -> {). The Sym key where Tab would be swaps the letters for a page of every ASCII symbol, so they need
no Shift, plus a few non-ASCII ones (€ £ ¥ § °).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# One string of base characters and one of shifted characters per row, key for key (US layout)
CHAR_ROWS = [
    ("`1234567890-=", "~!@#$%^&*()_+"),
    ("qwertyuiop[]\\", "QWERTYUIOP{}|"),
    ("asdfghjkl;'", 'ASDFGHJKL:"'),
    ("zxcvbnm,./", "ZXCVBNM<>?"),
]
# The Sym page, key for key with CHAR_ROWS. The number row keeps its digits; Shift there works as usual.
SYM_ROWS = [
    CHAR_ROWS[0],
    ("!@#$%^&*()_+|", "!@#$%^&*()_+|"),
    ("[]{}\\;:'\"~?", "[]{}\\;:'\"~?"),
    ("<>,./€£¥§°", "<>,./€£¥§°"),
]

# Width of the whole keyboard in key units (a letter key is 1 unit wide); every row adds up to this
UNITS = 15


@dataclass(frozen=True)
class Key:
    """One key: kind is "char" or an action ("backspace", "shift", "caps", "symbols", "enter", "cancel",
    "show", "space", "gap"); width is in key units. A char key types base/shifted, or sym/sym_shifted on the
    Sym page."""

    kind: str
    width: float = 1.0
    base: str = ""
    shifted: str = ""
    sym: str = ""
    sym_shifted: str = ""


def _chars(row: int) -> list[Key]:
    (base, shifted), (sym, sym_shifted) = CHAR_ROWS[row], SYM_ROWS[row]
    return [Key("char", 1.0, *chars) for chars in zip(base, shifted, sym, sym_shifted, strict=True)]


LAYOUT: list[list[Key]] = [
    [*_chars(0), Key("backspace", 2.0)],
    # Sym sits where Tab is on a real keyboard (there is nothing to tab to here). Backslash is 1.5 wide, as there.
    [Key("symbols", 1.5), *_chars(1)[:-1], replace(_chars(1)[-1], width=1.5)],
    [Key("caps", 2.0), *_chars(2), Key("enter", 2.0)],
    [Key("shift", 2.5), *_chars(3), Key("shift", 2.5)],
    [Key("cancel", 2.5), Key("gap", 1.0), Key("space", 7.0), Key("gap", 1.0), Key("show", 3.5)],
]


class PasswordBuffer:
    """What the user has typed so far. Shift is one-shot like on a phone keyboard; Caps Lock stays on."""

    def __init__(self) -> None:
        self._chars: list[str] = []
        self.shift = False
        self.caps = False
        self.symbols = False
        self.visible = False

    def type(self, ch: str) -> None:
        if len(ch) != 1 or not ch.isprintable():
            return
        self._chars.append(ch)
        self.shift = False

    def press(self, key: Key) -> None:
        """Type the character of a char key in the current shift state."""
        self.type(self.label(key))

    def label(self, key: Key) -> str:
        """What a char key types right now. Caps Lock only changes letters, and Shift undoes it, like a real
        keyboard."""
        if self.symbols:
            return key.sym_shifted if self.shift else key.sym
        if key.base.isalpha():
            return key.shifted if self.shift != self.caps else key.base
        return key.shifted if self.shift else key.base

    def backspace(self) -> None:
        if self._chars:
            self._chars.pop()

    def toggle_shift(self) -> None:
        self.shift = not self.shift

    def toggle_caps(self) -> None:
        self.caps = not self.caps
        self.shift = False

    def toggle_symbols(self) -> None:
        self.symbols = not self.symbols
        self.shift = False

    def toggle_visible(self) -> None:
        self.visible = not self.visible

    def take(self) -> str:
        """The finished password; the buffer is emptied."""
        text = "".join(self._chars)
        self.clear()
        return text

    def clear(self) -> None:
        self._chars = [""] * len(self._chars)  # overwrite before dropping the references
        self._chars = []
        self.shift = self.caps = self.symbols = self.visible = False

    def __len__(self) -> int:
        return len(self._chars)

    def display(self, echo: bool = False) -> str:
        if echo or self.visible:
            return "".join(self._chars)
        return "•" * len(self._chars)


def key_unit(width: int, height: int) -> tuple[int, int]:
    """Key size in pixels (width, height of a 1-unit key) for a window this big. The keyboard spans the
    window's width, like Steam's, but takes no more than about half its height, so the prompt above it
    stays on screen in a wide, short window."""
    usable = min(width - 56, width * 0.94)  # the page has 28px margins
    unit_w = usable / UNITS
    # The prompt, password field, hint bar and title bar above and below take about 380px: the keyboard gets
    # the rest, up to half the window. (It sets the window's minimum height, so it must leave room.)
    unit_h = min(unit_w * 0.85, min(height * 0.5, height - 380) / len(LAYOUT))
    unit_w = min(unit_w, unit_h * 1.25)  # no long flat keys either
    return max(20, int(unit_w)), max(24, int(unit_h))
