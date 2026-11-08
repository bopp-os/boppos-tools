"""Which kind of session we are in."""

from __future__ import annotations

import os


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
