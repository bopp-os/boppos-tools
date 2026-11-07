"""What the deck image's first-boot check found missing, and so which steps `boppos-deck` shows. GTK-free.

The image's `boppos-deck-first-boot.service` runs before Steam in the gamescope session and starts
`boppos-deck --first-run` (the installer's marker is there) or `boppos-deck --welcome` (it is gone) with
BOPPOS_FIRST_BOOT_MISSING set to the words it found: `first-run`, `steam`, `network` (docs/deck-first-boot.md).

- The variable set with known words: those are missing. Unknown words are ignored; when none is left (or the
  variable is empty) the live system decides, as below.
- The variable not set at all (a manual `--welcome`, the XDG autostart): nothing is assumed, but with the marker
  present (`--first-run`) the live system is still asked whether Steam and the network are there.
- `network` is also added whenever the Steam step will be shown and the system is offline: it cannot download
  anything otherwise.

With `first-run` missing, the Network and Steam steps join the normal tour (after Welcome). Without it, a
first-boot launch shows only the missing steps and Done, never the whole tour again. A manual `--welcome` (no
variable, no marker) is the whole tour, as before.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

from . import firstrun, steam_client

ENV = "BOPPOS_FIRST_BOOT_MISSING"
WORDS = ("first-run", "steam", "network")


def parse(value: str | None) -> frozenset[str] | None:
    """The known words of the variable; None when it is empty or names nothing known (decide from the live
    system)."""
    known = frozenset(w for w in (value or "").replace(",", " ").split() if w in WORDS)
    return known or None


def launched_by_image(env: Mapping[str, str] | None = None) -> bool:
    """Started by the image's first-boot check (before Steam, in gamescope): the variable is set, even empty."""
    return ENV in (os.environ if env is None else env)


@dataclass(frozen=True)
class Live:
    marker: bool  # /var/lib/boppos/first-run exists
    steam: bool  # Steam's client is unpacked (steamui.so), or there is nothing to download it with
    online: bool  # NetworkManager has an active connection


def nm_online(timeout: float = 5) -> bool | None:
    """NetworkManager's overall state: True when a connection is active ("connected", also "site only"), None when
    nmcli cannot say."""
    try:
        p = subprocess.run(["nmcli", "-t", "-f", "STATE", "general"], capture_output=True, text=True,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode:
        return None
    return p.stdout.strip().startswith("connected")


def live(root: str = "/") -> Live:
    from . import oobe  # the setup's link check, the fallback when nmcli cannot answer

    up = nm_online()
    return Live(
        marker=firstrun.read_hint(root) is not None,
        steam=steam_client.installed() or not steam_client.bootstrap_available(root),
        online=oobe.online(root) if up is None else up,
    )


def missing(env_value: str | None, env_set: bool, state: Live) -> frozenset[str]:
    """What is missing: see the module's doc."""
    words = parse(env_value) if env_set else None
    if words is None:
        if not (env_set or state.marker):
            return frozenset()  # a manual run: the tour as it always was
        words = frozenset(w for w, gone in (("steam", not state.steam), ("network", not state.online)) if gone)
    words = set(words)
    if state.marker:
        words.add("first-run")  # the marker is the truth for that one
    if "steam" in words and not state.online:
        words.add("network")
    return frozenset(words)


@dataclass(frozen=True)
class Plan:
    missing: frozenset[str]
    only_missing: bool  # show just the missing steps and Done, not the tour

    @property
    def network(self) -> bool:
        return "network" in self.missing

    @property
    def steam(self) -> bool:
        return "steam" in self.missing


def plan(env: Mapping[str, str] | None = None, state: Live | None = None) -> Plan:
    env = os.environ if env is None else env
    env_set = ENV in env
    state = live() if state is None else state
    words = missing(env.get(ENV), env_set, state)
    only = env_set and "first-run" not in words and bool(words & {"steam", "network"})
    return Plan(words, only)
