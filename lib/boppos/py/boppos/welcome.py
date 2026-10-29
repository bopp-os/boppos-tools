"""First-run wizard state: the done flag, the steps in order and the choices collected along the way.

No GTK in here, so it is testable on its own. The UI lives in ui/welcome.py.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import firstrun, flatpaks, hwprofiles, kargs, oobe

FLAG = "welcome-done"

# True: the wizard only describes what it would do, and Finish does not write the done flag.
DRY_RUN = False

# The choices that have a real command behind them (apply_commands). Everything else is hidden from the
# wizard until it is wired: a switch that silently does nothing would be worse than no switch.
WIRED = frozenset({"flatpaks", "steam_shortcuts", "decky", "kargs"})

KARGS_WARNING = kargs.KARGS_WARNING

SESSIONS = oobe.SESSIONS  # the labels of the "start in" choice, shared with the setup


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    gaming: bool = True  # False: needs a terminal or a desktop, so Gaming Mode skips it
    wired: bool = True  # False: nothing on the step is applied yet, so the wizard leaves it out


STEPS = (
    Step("welcome", "Welcome"),
    Step("apps", "Apps"),
    Step("hardware", "Hardware"),
    Step("session", "Start in"),
    Step("security", "Security", gaming=False, wired=False),
    Step("updates", "Updates", wired=False),
    Step("review", "Review"),
    Step("done", "Done"),
)


def steps_for(gaming: bool, ask_start_in: bool = False) -> list[Step]:
    """ask_start_in: the "Start in" step is only there when the image can sign in automatically and the choice has
    not been made yet (setup asks it too, and the tour does not ask twice)."""
    return [s for s in STEPS if s.wired and (s.gaming or not gaming) and (s.key != "session" or ask_start_in)]


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
    start_in: str = ""  # "gaming" or "desktop" (boppos.firstrun.CHOICES); "" when the step was not shown

    def is_empty(self) -> bool:
        return not any((self.flatpaks, self.decky, self.steam_shortcuts, self.kargs, self.firmware_check,
                        self.howdy, self.tpm_unlock, self.auto_updates, self.dev_container, self.start_in))


@dataclass(frozen=True)
class KargOption:
    name: str
    description: str
    category: str
    risk_level: str
    risk: str
    fit: str  # kargs.fit: "fits", "optional", "risky" or "unsupported"
    reason: str = ""  # why it is unsupported

    @property
    def suggested(self) -> bool:
        return self.fit == "fits"

    @property
    def locked(self) -> bool:
        """The wizard does not offer it: it cannot work here, or it is too risky to switch on from a wizard."""
        return self.fit in ("risky", "unsupported")


def app_options(native: set[str] | frozenset[str] | None = None) -> dict[str, list[flatpaks.App]]:
    """The curated apps by category (runtimes and extensions are not offered), without the ones the image
    already ships natively (`native`: installed packages and .desktop files, detected when None)."""
    try:
        apps = flatpaks.load_curated()
    except (OSError, flatpaks.ListError):
        return {}
    native = flatpaks.native_installed() if native is None else native
    hidden = flatpaks.redundant(apps, flatpaks.load_native_map(), native)
    out: dict[str, list[flatpaks.App]] = {}
    for app in apps:
        if app.kind == "app" and app.id not in hidden:
            out.setdefault(app.category, []).append(app)
    return out


def default_flatpak_ids(options: dict[str, list[flatpaks.App]]) -> set[str]:
    """The offered apps the list marks "; default": the only ones pre-ticked (with the hardware kind's extras)."""
    return {a.ref for apps in options.values() for a in apps if a.default}


# Presets the wizard shows as a switch of their own instead of in the list of presets.
AMD_OVERCLOCK = "72-amdgpu-overclock"

def karg_fit(d: kargs.Dropin, hw: hwprofiles.Hardware) -> tuple[str, str]:
    """(fit, reason) of one preset on this hardware; the rule itself lives in kargs.fit."""
    return kargs.fit(d, hw.dmi, hw.gpus)


def karg_options(hw: hwprofiles.Hardware | None = None) -> list[KargOption]:
    """Every kargs preset, the ones that fit this hardware first and the ones that cannot work here last."""
    hw = hwprofiles.detect() if hw is None else hw
    try:
        dropins = kargs.load_dropins()
    except OSError:
        return []
    opts = []
    for d in dropins.values():
        if not d.error:
            fit, reason = karg_fit(d, hw)
            opts.append(KargOption(d.name, d.description, d.category, d.risk_level, d.risk, fit, reason))
    order = {"fits": 0, "optional": 1, "risky": 2, "unsupported": 3}
    return sorted(opts, key=lambda o: (order[o.fit], o.name))


def has_tpm(root: str | Path = "/") -> bool:
    return hwprofiles.detect(root).tpm


def has_camera(root: str | Path = "/") -> bool:
    return hwprofiles.detect(root).camera


# The yes/no options, as the wizard words them.
FLAGS = {
    "steam_shortcuts": ("BoppOS shortcut in Steam", "Opens boppos-deck from your Steam library."),
    "decky": ("Decky Loader", "Plugins for the Steam Gaming Mode overlay."),
    "firmware_check": ("Check for firmware updates", "Runs fwupd after setup. Nothing is installed without asking."),
    "auto_updates": ("Automatic updates", "Download new system images in the background; you choose when to reboot."),
}


@dataclass(frozen=True)
class Recommended:
    step: str  # the wizard step that shows it
    kind: str  # "flatpak" (key is the ref) or "flag" (key is a Choices attribute named in FLAGS)
    key: str


def recommended(hw: hwprofiles.Hardware | None = None,
                native: set[str] | frozenset[str] | None = None) -> list[Recommended]:
    """What the wizard recommends for this hardware: all of it starts switched on, the user can switch it off.
    Never a kargs preset, never an app the image already ships natively."""
    hw = hwprofiles.detect() if hw is None else hw
    extra = hwprofiles.KIND_DEFAULTS.get(hw.kind, {})
    options = app_options(native)
    known = {a.ref for apps in options.values() for a in apps}
    refs = (default_flatpak_ids(options) | set(extra.get("extra_flatpaks", ()))) & known
    recs = [Recommended("apps", "flatpak", r) for r in sorted(refs)]
    flags = {"firmware_check": True, "steam_shortcuts": False, "decky": False, "auto_updates": False}
    flags.update({k: bool(v) for k, v in extra.items() if k in flags})
    flags = {k: on for k, on in flags.items() if k in WIRED}
    steps = {"firmware_check": "hardware", "steam_shortcuts": "apps", "decky": "apps", "auto_updates": "updates"}
    recs += [Recommended(steps[k], "flag", k) for k, on in flags.items() if on]
    return recs


def initial_choices(hw: hwprofiles.Hardware | None = None,
                    native: set[str] | frozenset[str] | None = None) -> Choices:
    """Everything recommended is on before the user touches anything."""
    c = Choices()
    for r in recommended(hw, native):
        if r.kind == "flatpak":
            c.flatpaks.add(r.key)
        else:
            setattr(c, r.key, True)
    return c


@dataclass(frozen=True)
class Action:
    area: str
    text: str


def build_plan(choices: Choices, gaming: bool = False) -> list[Action]:
    """The actions the choices add up to, in the order they would run. Pure: it changes nothing."""
    plan: list[Action] = []
    if choices.start_in:
        plan.append(Action("Start", "Sign in automatically to " + SESSIONS[choices.start_in][0]))
    if choices.flatpaks:
        names = ", ".join(sorted(flatpaks.display_name(r) for r in choices.flatpaks))
        plan.append(Action("Apps", f"Install {len(choices.flatpaks)} Flatpak(s) for this user, with progress: {names}"))
    steam_when = " after your first Steam login" if gaming else ""
    if choices.steam_shortcuts:
        plan.append(Action("Apps", f"Add the BoppOS shortcut to your Steam library{steam_when}"))
    if choices.decky:
        plan.append(Action("Apps", f"Install Decky Loader{steam_when}"))
    if choices.kargs:
        plan.append(Action("Hardware", "Switch on kernel argument presets: " + ", ".join(sorted(choices.kargs))
                           + " (takes effect after a reboot; leave these off unless you need them)"))
    return plan


def first_run_commands(choices: Choices, user: str, helper_cmd: Callable[[list[str]], list[str]]) -> list[list[str]]:
    """The root-helper calls of the tour: where to start (only when it was asked), run before the apps."""
    if choices.start_in not in firstrun.CHOICES:
        return []
    return [helper_cmd(["set-start-in", user, choices.start_in])]


def finish_command(helper_cmd: Callable[[list[str]], list[str]]) -> list[str]:
    """The last command of the tour: ends the first run by removing the marker."""
    return helper_cmd(["finish-first-run"])


def apply_commands(choices: Choices, tool: Callable[[str], str]) -> list[list[str]]:
    """The commands the update window runs, in order: apps first (the update window shows their progress), then
    the Steam shortcut and Decky, and the kernel argument presets last because they only need a reboot."""
    cmds: list[list[str]] = []
    if choices.flatpaks:
        only = ",".join(sorted(choices.flatpaks))
        cmds.append([tool("install-optional-flatpaks"), "--json", "--yes", "--only", only])
    if choices.steam_shortcuts:
        cmds.append([tool("bopp-steam-shortcut"), "--json", "sync", "--add", "boppos-deck", "--queue-ok"])
    if choices.decky:
        cmds.append([tool("bopp-decky"), "--json", "install"])
    cmds += [[tool("bopp-kargs"), "--json", "enable", name] for name in sorted(choices.kargs)]
    return cmds


def run_plan(plan: list[Action]) -> list[str]:
    """Dry run: report what would happen. Deliberately has no way to change the system."""
    lines = [f"[dry run] would: {a.area}: {a.text}" for a in plan]
    return lines or ["[dry run] nothing chosen, nothing to do"]
