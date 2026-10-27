"""First-run wizard state: the done flag, the steps in order and the choices collected along the way.

No GTK in here, so it is testable on its own. The UI lives in ui/welcome.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

FLAG = "welcome-done"


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    gaming: bool = True  # False: needs a terminal or a desktop, so Gaming Mode skips it


STEPS = (
    Step("welcome", "Welcome"),
    Step("apps", "Apps"),
    Step("hardware", "Hardware"),
    Step("security", "Security", gaming=False),
    Step("updates", "Updates"),
    Step("review", "Review"),
    Step("done", "Done"),
)


def steps_for(gaming: bool) -> list[Step]:
    return [s for s in STEPS if s.gaming or not gaming]


def state_dir(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "boppos-deck"


def is_done(env: dict[str, str] | None = None) -> bool:
    return (state_dir(env) / FLAG).exists()


def mark_done(env: dict[str, str] | None = None) -> None:
    d = state_dir(env)
    d.mkdir(parents=True, exist_ok=True)
    (d / FLAG).touch()


def clear_done(env: dict[str, str] | None = None) -> None:
    (state_dir(env) / FLAG).unlink(missing_ok=True)


@dataclass
class Choices:
    """What the user picked. Nothing is applied until the Review step."""

    flatpaks: set[str] = field(default_factory=set)
    decky: bool = False
    steam_shortcuts: bool = False
    kargs: set[str] = field(default_factory=set)

    def is_empty(self) -> bool:
        return not (self.flatpaks or self.decky or self.steam_shortcuts or self.kargs)
