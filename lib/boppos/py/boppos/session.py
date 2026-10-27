"""Which kind of session we are in."""

from __future__ import annotations

import os


def gaming_mode(env: dict[str, str] | None = None) -> bool:
    """True inside Steam Gaming Mode (gamescope session)."""
    env = os.environ if env is None else env
    return any(env.get(v) for v in ("GAMESCOPE_WAYLAND_DISPLAY", "SteamGamepadUI", "SteamDeck"))
