"""Which kind of session we are in."""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping


def gaming_mode(env: dict[str, str] | None = None) -> bool:
    """True inside Steam Gaming Mode (gamescope session)."""
    env = os.environ if env is None else env
    return any(env.get(v) for v in ("GAMESCOPE_WAYLAND_DISPLAY", "SteamGamepadUI", "SteamDeck"))


def launched_by_steam(env: dict[str, str] | None = None) -> bool:
    """True when Steam started us (a library entry or a non-Steam shortcut): Steam sets SteamGameId (and SteamAppId,
    which is "0" for a non-Steam shortcut) for what it launches. Steam Input then hides the real controller and
    gives us its virtual gamepad instead."""
    env = os.environ if env is None else env
    return any(env.get(v) for v in ("SteamGameId", "SteamAppId"))


# Steam preloads its overlay into everything it launches, the 32-bit and the 64-bit build together, so every child
# process then prints "ERROR: ld.so: object '.../gameoverlayrenderer.so' from LD_PRELOAD cannot be preloaded (wrong
# ELF class: ...): ignored." for the one that does not fit. boppos-deck has no use for the overlay in its children.
OVERLAY = "gameoverlayrenderer"
_OVERLAY_NOISE = re.compile(r"ld\.so: object '[^']*" + OVERLAY + r"[^']*' from LD_PRELOAD cannot be preloaded")


def strip_overlay_preload(env: MutableMapping[str, str] | None = None) -> list[str]:
    """Remove Steam's overlay from LD_PRELOAD (in place; os.environ by default, so no child process inherits it),
    keep every other entry, and unset the variable when nothing is left. Returns what was removed."""
    env = os.environ if env is None else env
    value = env.get("LD_PRELOAD")
    if not value:
        return []
    entries = [e for e in re.split(r"[:\s]+", value) if e]  # ld.so takes colons and spaces as separators
    removed = [e for e in entries if OVERLAY in e]
    if not removed:
        return []
    kept = [e for e in entries if OVERLAY not in e]
    if kept:
        env["LD_PRELOAD"] = ":".join(kept)
    else:
        del env["LD_PRELOAD"]
    return removed


def is_overlay_noise(line: str) -> bool:
    """ld.so's complaint about Steam's overlay preload (see strip_overlay_preload), which no one needs to see."""
    return bool(_OVERLAY_NOISE.search(line))


def drop_overlay_noise(text: str) -> str:
    """text without the lines is_overlay_noise matches."""
    if OVERLAY not in text:
        return text
    return "".join(line for line in text.splitlines(keepends=True) if not is_overlay_noise(line))
