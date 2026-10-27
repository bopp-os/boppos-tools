"""Pure logic of the built-in gamepad keyboard used for password prompts."""

from __future__ import annotations

ROWS_ABC = ["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm,."]
ROWS_SYM = ["!@#$%^&*()", "-_=+[]{}\\|", ";:'\"/?<>`~", "@€£¥§°±µ"]


class PasswordBuffer:
    """What the user has typed so far. Shift is one-shot like on a phone keyboard."""

    def __init__(self) -> None:
        self._chars: list[str] = []
        self.shift = False
        self.symbols = False
        self.visible = False

    def type(self, ch: str) -> None:
        if len(ch) != 1 or not ch.isprintable():
            return
        if self.shift and ch.isalpha():
            ch = ch.upper()
            self.shift = False
        self._chars.append(ch)

    def backspace(self) -> None:
        if self._chars:
            self._chars.pop()

    def toggle_shift(self) -> None:
        self.shift = not self.shift

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
        self.shift = self.symbols = self.visible = False

    def __len__(self) -> int:
        return len(self._chars)

    def display(self, echo: bool = False) -> str:
        if echo or self.visible:
            return "".join(self._chars)
        return "•" * len(self._chars)

    def rows(self) -> list[str]:
        rows = ROWS_SYM if self.symbols else ROWS_ABC
        if self.shift and not self.symbols:
            rows = [r.upper() if r.isalpha() or r[0].isalpha() else r for r in rows]
        return rows
