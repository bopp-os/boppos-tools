"""Button prompts for the bottom bar: keyboard keys, or the glyphs of the controller in use.

Pure functions, so the labels are testable without a display.
"""

from __future__ import annotations

# SDL_GamepadType
_TYPE_FAMILY = {2: "xbox", 3: "xbox", 4: "playstation", 5: "playstation", 6: "playstation",
                7: "nintendo", 8: "nintendo", 9: "nintendo", 10: "nintendo"}

# role -> (text, css class). Roles are positions (SDL is positional): south = bottom face button.
GLYPHS = {
    "xbox": {"south": ("A", "glyph-a"), "east": ("B", "glyph-b"), "lb": ("LB", ""), "rb": ("RB", ""),
             "start": ("Menu", "")},
    "playstation": {"south": ("✕", "glyph-cross"), "east": ("○", "glyph-circle"), "lb": ("L1", ""),
                    "rb": ("R1", ""), "start": ("Options", "")},
    # Nintendo labels its bottom button B and its right button A
    "nintendo": {"south": ("B", ""), "east": ("A", ""), "lb": ("L", ""), "rb": ("R", ""), "start": ("+", "")},
    "generic": {"south": ("A", ""), "east": ("B", ""), "lb": ("LB", ""), "rb": ("RB", ""), "start": ("Start", "")},
}


def family_for_type(sdl_type: int) -> str:
    return _TYPE_FAMILY.get(sdl_type, "generic")


def hint_items(mode: str, family: str = "generic", auth: bool = False) -> list[tuple[str, str, str]]:
    """(key text, action label, css class) triples for the bar."""
    if mode != "pad":
        if auth:
            return [("Enter", "OK", ""), ("Esc", "Cancel", ""), ("Backspace", "Delete", "")]
        return [("Enter", "Select", ""), ("Esc", "Back", ""), ("Ctrl+Tab", "Page", ""), ("PgUp / PgDn", "Jump", "")]
    g = GLYPHS.get(family, GLYPHS["generic"])
    south, east, lb, rb, start = g["south"], g["east"], g["lb"], g["rb"], g["start"]
    if auth:
        return [(south[0], "Type", south[1]), (east[0], "Cancel", east[1]), (lb[0], "Delete", lb[1]),
                (rb[0], "Shift", rb[1]), (start[0], "OK", start[1])]
    return [(south[0], "Select", south[1]), (east[0], "Back", east[1]), (f"{lb[0]} / {rb[0]}", "Page", ""),
            ("R Stick", "Jump", "")]
