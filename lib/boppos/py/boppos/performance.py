"""Performance and battery settings for boppos-deck: screen power saving and CPU boost on battery, and the
sched-ext scheduler that scx_loader runs.

Reading needs no root. Changing goes through libexec/boppos/performance-helper; the battery settings are kept in
/etc/boppos/performance.json and re-applied by boppos-performance.service (libexec/boppos/performance-watch).

power-profiles-daemon writes both sysfs values itself (CPU boost from its amd-pstate driver, panel power savings from
its amdgpu_panel_power action) whenever the profile or the power source changes. So a value written once does not
last: the watcher writes ours again shortly after every such change, and the helper switches ppd's panel action off
while BoppOS manages the screen setting. "system" leaves a setting to ppd and the kernel, as without BoppOS.

The root is injectable everywhere so tests run against a fake sysfs and /etc.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import hwprofiles

CONFIG = Path("etc/boppos/performance.json")
SERVICE = "boppos-performance.service"
PPD_PANEL_ACTION = "amdgpu_panel_power"

SYSTEM = "system"
ABM_LEVELS = (0, 1, 2, 3)
BOOST_CHOICES = (SYSTEM, "off-on-battery")
ABM_LABELS = {SYSTEM: "System default", 0: "Off", 1: "Low", 2: "Medium", 3: "Strong"}
BOOST_LABELS = {SYSTEM: "System default", "off-on-battery": "Off on battery"}
ABM_LABELS = {SYSTEM: "System default", 0: "Off", 1: "Low", 2: "Medium", 3: "Strong"}
BOOST_LABELS = {SYSTEM: "System default", "off-on-battery": "Off on battery"}

# scx_loader's modes: lowercase for scxctl, capitalised in its config file
SCX_MODES = {"auto": "Auto", "gaming": "Gaming", "powersave": "PowerSave", "lowlatency": "LowLatency",
             "server": "Server"}
SCX_MODE_LABELS = {"auto": "Auto", "gaming": "Gaming", "powersave": "Power save", "lowlatency": "Low latency",
                   "server": "Server"}
SCX_NONE = "none"  # no sched-ext scheduler: the kernel's own
# scx_loader's lookup order: the first file that exists is the whole configuration
SCX_CONFIGS = (Path("etc/scx_loader/config.toml"), Path("etc/scx_loader.toml"),
               Path("usr/share/scx_loader/config.toml"), Path("usr/share/scx_loader.toml"))
SCX_ETC_CONFIG = Path("etc/scx_loader.toml")
_SCHED = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


# --- settings -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    abm: str | int = SYSTEM  # screen power saving level used on battery (0 on AC), or "system"
    boost: str = SYSTEM  # "off-on-battery" or "system"

    @property
    def managed(self) -> bool:
        return self.abm != SYSTEM or self.boost != SYSTEM

    def to_json(self) -> dict:
        return {"abm": self.abm, "boost": self.boost}


def parse_abm(value: object) -> str | int:
    if value == SYSTEM:
        return SYSTEM
    try:
        level = int(str(value))
    except ValueError:
        raise ValueError(f"screen power saving must be {SYSTEM} or 0-3, not {value!r}") from None
    if level not in ABM_LEVELS:
        raise ValueError(f"screen power saving must be {SYSTEM} or 0-3, not {value!r}")
    return level


def parse_boost(value: object) -> str:
    if value not in BOOST_CHOICES:
        raise ValueError(f"CPU boost must be one of {', '.join(BOOST_CHOICES)}, not {value!r}")
    return str(value)


def load(root: Path = Path("/")) -> Settings:
    """The saved settings; anything missing or unreadable falls back to "system"."""
    try:
        data = json.loads((root / CONFIG).read_text())
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    try:
        abm = parse_abm(data.get("abm", SYSTEM))
    except ValueError:
        abm = SYSTEM
    try:
        boost = parse_boost(data.get("boost", SYSTEM))
    except ValueError:
        boost = SYSTEM
    return Settings(abm, boost)


def save(settings: Settings, root: Path = Path("/")) -> None:
    path = root / CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings.to_json(), indent=2) + "\n")
    tmp.replace(path)


# --- sysfs --------------------------------------------------------------------------------------


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def panel_files(root: Path = Path("/")) -> list[Path]:
    """amdgpu's panel power savings (ABM) for each built-in panel."""
    return sorted((root / "sys/class/drm").glob("card*-eDP-*/amdgpu/panel_power_savings"))


def abm_locked(root: Path = Path("/")) -> bool:
    """amdgpu.abmlevel= on the kernel command line fixes the level, and the sysfs file then cannot change it."""
    value = _read(root / "sys/module/amdgpu/parameters/abmlevel")
    return value is not None and value not in ("-1", "")


def boost_files(root: Path = Path("/")) -> list[Path]:
    """The global CPU boost switch and, with amd-pstate, the per-policy ones that ppd writes."""
    files = [root / "sys/devices/system/cpu/cpufreq/boost"]
    files += sorted((root / "sys/devices/system/cpu/cpufreq").glob("policy*/boost"))
    return [f for f in files if f.exists()]


def batteries(root: Path = Path("/")) -> list[Path]:
    base = root / "sys/class/power_supply"
    return [p for p in sorted(base.glob("*")) if _read(p / "type") == "Battery"]


def on_battery(root: Path = Path("/")) -> bool:
    """True when running from the battery. An online charger (mains or USB-C) means AC; with no charger listed at
    all, a discharging battery means battery. No battery is never "on battery"."""
    base = root / "sys/class/power_supply"
    if not batteries(root):
        return False
    chargers = [p for p in sorted(base.glob("*")) if _read(p / "type") in ("Mains", "USB")]
    if chargers:
        return not any(_read(p / "online") == "1" for p in chargers)
    return any(_read(p / "status") == "Discharging" for p in batteries(root))


def targets(settings: Settings, battery: bool) -> dict[str, str]:
    """What the managed settings want written right now: {"abm": "0".."3", "boost": "0"/"1"}."""
    want: dict[str, str] = {}
    if settings.abm != SYSTEM:
        want["abm"] = str(settings.abm if battery else 0)
    if settings.boost != SYSTEM:
        want["boost"] = "0" if battery else "1"
    return want


def _write(path: Path, value: str) -> bool:
    """Write only when it differs, so the watcher's periodic pass does not touch sysfs needlessly."""
    if _read(path) == value:
        return False
    try:
        path.write_text(value)
    except OSError:
        return False
    return True


def apply(settings: Settings, root: Path = Path("/"), battery: bool | None = None) -> list[str]:
    """Write the managed settings for the current power source. Returns what was changed, for the log."""
    battery = on_battery(root) if battery is None else battery
    changed = []
    want = targets(settings, battery)
    if "abm" in want and not abm_locked(root):
        changed += [f"{f}={want['abm']}" for f in panel_files(root) if _write(f, want["abm"])]
    if "boost" in want:
        changed += [f"{f}={want['boost']}" for f in boost_files(root) if _write(f, want["boost"])]
    return changed


def release(old: Settings, new: Settings, root: Path = Path("/")) -> None:
    """Put back what a setting that went back to "system" had changed: boost on, screen saving off. ppd then sets
    its own values at its next profile or power change."""
    if old.abm != SYSTEM and new.abm == SYSTEM and not abm_locked(root):
        for f in panel_files(root):
            _write(f, "0")
    if old.boost != SYSTEM and new.boost == SYSTEM:
        for f in boost_files(root):
            _write(f, "1")


# --- scx_loader ---------------------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or proc.stderr).strip()


def parse_scx_get(text: str) -> tuple[str, str]:
    """`scxctl get` ("running Lavd in Auto mode" / "no scx scheduler running") as (sched, mode)."""
    m = re.search(r"running\s+(?:scx_)?(\w+)\s+in\s+(\w+)\s+mode", text, re.IGNORECASE)
    if not m:
        return SCX_NONE, ""
    return m.group(1).lower(), m.group(2).lower()


def parse_scx_list(text: str) -> list[str]:
    """Scheduler names from `scxctl list` (a quoted list, or one name per word), without the scx_ prefix."""
    names = re.findall(r'"([^"]+)"', text) or re.split(r"[\s,\[\]]+", text.split(":", 1)[-1])
    out: list[str] = []
    for n in names:
        n = n.strip().lower().removeprefix("scx_")
        if _SCHED.match(n) and n not in out:
            out.append(n)
    return out


def scx_config(root: Path = Path("/")) -> Path | None:
    for rel in SCX_CONFIGS:
        if (root / rel).is_file():
            return root / rel
    return None


def parse_scx_config(text: str) -> tuple[str, str]:
    """(default_sched without scx_, default_mode lowercase) from scx_loader's TOML, top-level keys only."""
    sched = mode = ""
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            break
        if m := re.match(r'\s*default_sched\s*=\s*"([^"]*)"', line):
            sched = m.group(1).lower().removeprefix("scx_")
        elif m := re.match(r'\s*default_mode\s*=\s*"([^"]*)"', line):
            mode = m.group(1).lower()
    return sched, mode


def edit_scx_config(text: str, sched: str, mode: str) -> str:
    """Set (or, for "none", drop) default_sched and default_mode, keeping everything else as it is."""
    lines = text.splitlines()
    end = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    head = [line for line in lines[:end] if not re.match(r"\s*default_(sched|mode)\s*=", line)]
    if sched != SCX_NONE:
        head = [f'default_sched = "scx_{sched}"', f'default_mode = "{SCX_MODES[mode]}"', *head]
    out = head + lines[end:]
    return "\n".join(out).rstrip("\n") + "\n"


def validate_scx(sched: str, mode: str, known: list[str]) -> None:
    if sched != SCX_NONE and (not _SCHED.match(sched) or sched not in known):
        raise ValueError(f"unknown scheduler {sched!r}")
    if mode not in SCX_MODES:
        raise ValueError(f"unknown mode {mode!r}; choose one of: {', '.join(SCX_MODES)}")


def scx_status(root: Path = Path("/")) -> dict:
    if not shutil.which("scxctl"):
        return {"available": False}
    _, got = _run(["scxctl", "get"])
    sched, mode = parse_scx_get(got)
    _, listed = _run(["scxctl", "list"])
    cfg = scx_config(root)
    default_sched, default_mode = parse_scx_config(cfg.read_text()) if cfg else ("", "")
    return {"available": True, "sched": sched, "mode": mode, "schedulers": parse_scx_list(listed),
            "default_sched": default_sched or SCX_NONE, "default_mode": default_mode or "auto",
            "modes": list(SCX_MODES)}


def scx_commands(sched: str, mode: str, running: str) -> list[list[str]]:
    """scxctl calls that make the running scheduler match (as root, so scx_loader's polkit check passes)."""
    if sched == SCX_NONE:
        return [] if running == SCX_NONE else [["/usr/bin/scxctl", "stop"]]
    verb = "start" if running == SCX_NONE else "switch"
    return [["/usr/bin/scxctl", verb, "--sched", sched, "--mode", mode]]


def write_scx_config(sched: str, mode: str, root: Path = Path("/")) -> Path:
    """Save the choice for the next boot. /etc/scx_loader/config.toml wins if it exists; otherwise
    /etc/scx_loader.toml, started from the vendor file so its per-scheduler settings are kept."""
    etc_dir = root / SCX_CONFIGS[0]
    path = etc_dir if etc_dir.is_file() else root / SCX_ETC_CONFIG
    base = path if path.is_file() else scx_config(root)
    text = base.read_text() if base else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(edit_scx_config(text, sched, mode))
    return path


# --- recommended settings -----------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    """Recommended settings for a kind of hardware. A profile with products matches those devices (DMI product_name
    patterns, any case); one without matches every machine of its kinds. Devices come before kinds."""

    key: str
    title: str
    note: str  # why these values; shown on the page
    abm: str | int  # screen power saving on battery
    boost: str
    sched: str
    mode: str = "auto"
    kinds: tuple[str, ...] = ()
    products: tuple[str, ...] = ()


# CPU boost stays with the system in every profile: switching it off costs frame rate in CPU-heavy games, and
# power-profiles-daemon already turns it off in its power-saver profile. The research behind each value, with
# sources and dates, is in docs/performance-profiles.md.
PERF_PROFILES: tuple[Profile, ...] = (
    Profile("legion-go", "Lenovo Legion Go",
            "Screen power saving 3 measured 0.6 W lower than off on this device at full brightness (18.5 W to 17.9 W). "
            "Bazzite's battery profile uses the same level. scx_lavd is CachyOS Handheld's default scheduler.",
            abm=3, boost=SYSTEM, sched="lavd", kinds=("handheld",), products=("83e1",)),
    Profile("steam-deck", "Steam Deck",
            "Valve tunes the Deck's panel and power itself, so only the scheduler is set. scx_lavd is CachyOS "
            "Handheld's default scheduler.",
            abm=SYSTEM, boost=SYSTEM, sched="lavd", kinds=("handheld",), products=("jupiter", "galileo")),
    Profile("handheld", "Handheld",
            "Medium screen power saving on battery, and scx_lavd, which CachyOS Handheld ships as its default "
            "scheduler. Raise the level to 3 if the colours look fine to you.",
            abm=2, boost=SYSTEM, sched="lavd", kinds=("handheld",)),
    Profile("laptop", "Laptop",
            "Low screen power saving on battery. The kernel scheduler stays: CachyOS and Bazzite ship scx_lavd for "
            "their handheld editions, and no measurement here shows a gain on a laptop.",
            abm=1, boost=SYSTEM, sched=SCX_NONE, kinds=("laptop",)),
    Profile("desktop", "Desktop",
            "Nothing to save on battery, and the kernel's own scheduler (BORE on CachyOS) is the better fit for a "
            "desktop.",
            abm=SYSTEM, boost=SYSTEM, sched=SCX_NONE, kinds=("desktop",)),
    Profile("vm", "Virtual machine",
            "The host decides power and scheduling.",
            abm=SYSTEM, boost=SYSTEM, sched=SCX_NONE, kinds=("vm",)),
)


def profile_for(hw: hwprofiles.Hardware) -> Profile:
    product = hw.dmi.get("product_name", "").lower()
    for prof in PERF_PROFILES:
        if prof.products and hw.kind in prof.kinds and any(fnmatch.fnmatchcase(product, pat) for pat in prof.products):
            return prof
    for prof in PERF_PROFILES:
        if not prof.products and hw.kind in prof.kinds:
            return prof
    return next(p for p in PERF_PROFILES if p.key == "desktop")


@dataclass(frozen=True)
class Recommended:
    label: str  # the device ("Lenovo Legion Go")
    profile: str  # the profile's key
    title: str
    note: str
    abm: str | int
    boost: str
    sched: str
    mode: str

    def to_json(self) -> dict:
        return {"label": self.label, "profile": self.profile, "title": self.title, "note": self.note,
                "abm": self.abm, "boost": self.boost, "sched": self.sched, "mode": self.mode}


def hardware_label(hw: hwprofiles.Hardware) -> str:
    """The friendly name of a known device ("Lenovo Legion Go"), else the DMI vendor and product."""
    for known in hwprofiles.PROFILES.values():
        keys = ("sys_vendor", "product_name")
        if known.dmi.get("product_name") and all(known.dmi.get(k) == hw.dmi.get(k) for k in keys):
            return known.label
    return hw.label


def recommended(hw: hwprofiles.Hardware) -> Recommended:
    prof = profile_for(hw)
    return Recommended(hardware_label(hw), prof.key, prof.title, prof.note, prof.abm, prof.boost, prof.sched,
                       prof.mode)


def effective(rec: Recommended, root: Path = Path("/")) -> Settings:
    """The battery settings of a profile that this machine can use: nothing on a machine without a battery, no screen
    level without an amdgpu panel, no boost switch without cpufreq boost. Unused settings would only keep the
    watcher service running."""
    if not batteries(root):
        return Settings()
    abm = rec.abm if panel_files(root) else SYSTEM
    boost = rec.boost if boost_files(root) else SYSTEM
    return Settings(abm, boost)


def scx_text(sched: str, mode: str) -> str:
    if sched in ("", SCX_NONE):
        return "None (kernel scheduler)"
    return f"scx_{sched} ({SCX_MODE_LABELS.get(mode, mode)})"


def plan(rec: Recommended, current: Settings, scx: dict, root: Path = Path("/")) -> list[dict]:
    """What applying `rec` here would change, as [{"what", "from", "to"}]: empty when everything already matches.
    The same inputs as `helper_main("recommended")` uses, so the list is what will happen."""
    want = effective(rec, root)
    changes: list[dict] = []
    if want.abm != current.abm:
        changes.append({"what": "Screen power saving on battery", "from": ABM_LABELS[current.abm],
                        "to": ABM_LABELS[want.abm]})
    if want.boost != current.boost:
        changes.append({"what": "CPU boost", "from": BOOST_LABELS[current.boost], "to": BOOST_LABELS[want.boost]})
    if scx.get("available") and (rec.sched == SCX_NONE or rec.sched in scx["schedulers"]):
        goal = (rec.sched, "" if rec.sched == SCX_NONE else rec.mode)

        def norm(sched: str, mode: str) -> tuple[str, str]:
            return (sched, "" if sched in ("", SCX_NONE) else mode)

        running = norm(scx["sched"], scx["mode"])
        saved = norm(scx["default_sched"], scx["default_mode"])
        if running != goal:
            changes.append({"what": "CPU scheduler", "from": scx_text(*running), "to": scx_text(*goal)})
        if saved != goal:
            changes.append({"what": "CPU scheduler at boot", "from": scx_text(*saved), "to": scx_text(*goal)})
    return changes


# --- status for the page ------------------------------------------------------------------------


def status(root: Path = Path("/")) -> dict:
    settings = load(root)
    panels = panel_files(root)
    boosts = boost_files(root)
    hw = hwprofiles.detect(root)
    rec = recommended(hw)
    scx = scx_status(root)
    if scx.get("available"):
        scx["recommended"] = rec.sched
    rec_json = rec.to_json()
    rec_json["changes"] = plan(rec, settings, scx, root)
    return {
        "settings": settings.to_json(),
        "battery": bool(batteries(root)),
        "on_battery": on_battery(root),
        "abm": {"supported": bool(panels), "locked": abm_locked(root),
                "current": _read(panels[0]) if panels else None},
        "boost": {"supported": bool(boosts), "current": _read(boosts[0]) if boosts else None},
        "ppd": bool(shutil.which("powerprofilesctl")),
        "service": _run(["systemctl", "is-active", SERVICE])[1] == "active",
        "scx": scx,
        "recommended": rec_json,
    }


# --- root side (performance-helper) -------------------------------------------------------------


def _systemctl(*args: str) -> None:
    _run(["/usr/bin/systemctl", *args], timeout=30)


def _ppd_panel_action(enabled: bool) -> None:
    """Hand the screen setting to ppd or take it away (ppd keeps this in its own state, across reboots).
    Older ppd versions have no configure-action; the watcher still wins there, just a moment later."""
    if shutil.which("powerprofilesctl"):
        _run(["/usr/bin/powerprofilesctl", "configure-action", PPD_PANEL_ACTION,
              "--enable" if enabled else "--disable"])


def change_settings(new: Settings, root: Path = Path("/"), *, side_effects: bool = True) -> list[str]:
    """Save, undo what a released setting changed, apply the rest now, and start or stop the watcher."""
    old = load(root)
    save(new, root)
    release(old, new, root)
    if side_effects and (old.abm == SYSTEM) != (new.abm == SYSTEM):
        _ppd_panel_action(new.abm == SYSTEM)
    changed = apply(new, root)
    if side_effects:
        if new.managed:
            _systemctl("enable", SERVICE)
            _systemctl("restart", SERVICE)
        else:
            _systemctl("disable", "--now", SERVICE)
    return changed


def change_scx(sched: str, mode: str, root: Path = Path("/")) -> list[str]:
    """Switch the running scheduler and save it as scx_loader's default. Returns error text, empty when fine."""
    status = scx_status(root)
    if not status.get("available"):
        raise ValueError("scx_loader is not installed")
    validate_scx(sched, mode, status["schedulers"])
    write_scx_config(sched, mode, root)
    errors = []
    for cmd in scx_commands(sched, mode, status["sched"]):
        rc, out = _run(cmd, timeout=30)
        if rc:
            errors.append(f"{' '.join(cmd[1:])}: {out}")
    return errors


def helper_main(argv: list[str], root: Path = Path("/")) -> int:
    """performance-helper's verbs, after privileged.performance_argv has checked them. Prints one JSON object."""
    verb, rest = argv[0], argv[1:]
    out: dict = {}
    try:
        settings = load(root)
        if verb == "set-abm":
            out["changed"] = change_settings(Settings(parse_abm(rest[0]), settings.boost), root)
        elif verb == "set-boost":
            out["changed"] = change_settings(Settings(settings.abm, parse_boost(rest[0])), root)
        elif verb == "scx":
            out["errors"] = change_scx(rest[0], rest[1], root)
        elif verb == "recommended":
            rec = recommended(hwprofiles.detect(root))
            scx = scx_status(root)
            out["plan"] = plan(rec, settings, scx, root)
            out["changed"] = change_settings(effective(rec, root), root)
            if scx.get("available") and (rec.sched == SCX_NONE or rec.sched in scx["schedulers"]):
                out["errors"] = change_scx(rec.sched, rec.mode, root)
            out["recommended"] = rec.to_json()
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    ok = not out.get("errors")
    print(json.dumps({"ok": ok, **out}))
    return 0 if ok else 1
