"""First-run wizard state: the done flag, the steps in order and the choices collected along the way.

No GTK in here, so it is testable on its own. The UI lives in ui/welcome.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import flatpaks, kargs
from .dmi import device_label, read_dmi

FLAG = "welcome-done"

# The wizard only describes what it would do. run_plan() has no code path that changes the system, and
# the Finish button does not write the done flag, until the apply step exists and this is turned off.
DRY_RUN = True


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
    """What the user picked. Nothing is applied until the Review step (and not at all while DRY_RUN)."""

    flatpaks: set[str] = field(default_factory=set)
    decky: bool = False
    steam_shortcuts: bool = False
    kargs: set[str] = field(default_factory=set)  # names of kargs presets to switch on
    firmware_check: bool = False
    howdy: bool = False
    tpm_unlock: bool = False
    auto_updates: bool = False
    dev_container: bool = False

    def is_empty(self) -> bool:
        return not any((self.flatpaks, self.decky, self.steam_shortcuts, self.kargs, self.firmware_check,
                        self.howdy, self.tpm_unlock, self.auto_updates, self.dev_container))


@dataclass(frozen=True)
class KargOption:
    name: str
    description: str
    category: str
    risk_level: str
    risk: str
    suggested: bool  # the preset names this device (match.*) and it fits


def default_flatpak_ids() -> set[str]:
    """The apps every user gets today from boppos-default-flatpaks.service."""
    try:
        return {a.ref for a in flatpaks.parse_list(Path(flatpaks.DEFAULTS_LIST).read_text())}
    except (OSError, flatpaks.ListError):
        return set()


def app_options() -> dict[str, list[flatpaks.App]]:
    """The curated apps by category (runtimes and extensions are not offered)."""
    try:
        apps = flatpaks.load_curated()
    except (OSError, flatpaks.ListError):
        return {}
    out: dict[str, list[flatpaks.App]] = {}
    for app in apps:
        if app.kind == "app":
            out.setdefault(app.category, []).append(app)
    return out


def karg_options(dmi: dict[str, str] | None = None) -> list[KargOption]:
    """Every kargs preset, the ones that match this device first."""
    dmi = read_dmi() if dmi is None else dmi
    try:
        dropins = kargs.load_dropins()
    except OSError:
        return []
    opts = [
        KargOption(d.name, d.description, d.category, d.risk_level, d.risk,
                   bool(d.match) and kargs.matches_device(d, dmi))
        for d in dropins.values() if not d.error
    ]
    return sorted(opts, key=lambda o: (not o.suggested, o.name))


def device(dmi: dict[str, str] | None = None) -> str:
    return device_label(read_dmi() if dmi is None else dmi)


def has_tpm(root: str | Path = "/") -> bool:
    return (Path(root) / "dev/tpmrm0").exists() or (Path(root) / "dev/tpm0").exists()


def has_camera(root: str | Path = "/") -> bool:
    return any((Path(root) / "dev").glob("video*"))


def initial_choices() -> Choices:
    """What is switched on before the user touches anything: today's defaults plus what fits this device."""
    return Choices(
        flatpaks=default_flatpak_ids(),
        kargs={o.name for o in karg_options() if o.suggested},
        firmware_check=True,
    )


@dataclass(frozen=True)
class Action:
    area: str
    text: str


def build_plan(choices: Choices, gaming: bool = False) -> list[Action]:
    """The actions the choices add up to, in the order they would run. Pure: it changes nothing."""
    plan: list[Action] = []
    if choices.flatpaks:
        names = ", ".join(sorted(flatpaks.display_name(r) for r in choices.flatpaks))
        plan.append(Action("Apps", f"Install {len(choices.flatpaks)} Flatpak(s) for this user: {names}"))
    steam_when = " after your first Steam login" if gaming else ""
    if choices.steam_shortcuts:
        plan.append(Action("Apps", f"Add the BoppOS shortcut to your Steam library{steam_when}"))
    if choices.decky:
        plan.append(Action("Apps", f"Install Decky Loader{steam_when}"))
    if choices.kargs:
        plan.append(Action("Hardware", "Switch on kernel argument presets: " + ", ".join(sorted(choices.kargs))
                           + " (takes effect after a reboot)"))
    if choices.firmware_check:
        plan.append(Action("Hardware", "Check for firmware updates (fwupd)"))
    if choices.howdy:
        plan.append(Action("Security", "Set up face unlock (Howdy)"))
    if choices.tpm_unlock:
        plan.append(Action("Security", "Enrol the TPM to unlock the disk automatically"))
    if choices.auto_updates:
        plan.append(Action("Updates", "Turn on automatic updates"))
    if choices.dev_container:
        plan.append(Action("Updates", "Create the dev container (distrobox)"))
    return plan


def run_plan(plan: list[Action]) -> list[str]:
    """Dry run: report what would happen. Deliberately has no way to change the system."""
    lines = [f"[dry run] would: {a.area}: {a.text}" for a in plan]
    return lines or ["[dry run] nothing chosen, nothing to do"]
