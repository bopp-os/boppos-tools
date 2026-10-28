"""System-level queries and command construction for bopp-system (no I/O at import time)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from . import __version__
from .bootc import parse_status
from .dmi import device_label, read_dmi

HELPER_DIR = "/usr/libexec/boppos"
UPDATE_STATUS = "/run/boppos/update-status.json"
BREW_PATHS = ("/home/linuxbrew/.linuxbrew/bin/brew",)
# Gear Lever and ProtonPlus: the native binary, else the Flatpak's exported launcher (user install first)
GEARLEVER = ("gearlever", "it.mijorus.gearlever")
PROTONPLUS = ("protonplus", "com.vysp3r.ProtonPlus")
FLATPAK_EXPORTS = ("~/.local/share/flatpak/exports/bin", "/var/lib/flatpak/exports/bin")
TARGETS = ("os", "fw", "flatpak", "brew", "appimage", "proton", "distrobox", "litterbox")
# What "Update everything" (settings app, tray) does: all but firmware, which can need a reboot of its own
DEFAULT_TARGETS = ("os", "flatpak", "brew", "appimage", "proton", "distrobox", "litterbox")
TARGET_LABELS = {"os": "Operating system", "fw": "Firmware", "flatpak": "Flatpaks", "brew": "Homebrew",
                 "appimage": "AppImages (Gear Lever)", "proton": "Proton Latest (ProtonPlus)",
                 "distrobox": "Distroboxes", "litterbox": "Litterboxes"}
# boppos-update in a checkout (lib/boppos/py/boppos/system.py -> bin/), when it is not on PATH
_DEV_UPDATE = Path(__file__).resolve().parents[4] / "bin/boppos-update"


def parse_targets(value: str) -> list[str] | None:
    """Update targets from a comma list ("os,fw"). "all" or empty means the default set (None). Unknown names
    raise ValueError."""
    names = [t.strip() for t in value.split(",") if t.strip()]
    if not names or names == ["all"]:
        return None
    bad = [t for t in names if t not in TARGETS]
    if bad:
        raise ValueError(f"unknown update target(s): {', '.join(bad)} (use all, or {', '.join(TARGETS)})")
    return [t for t in TARGETS if t in names]


def escalation(euid: int | None = None) -> list[str]:
    """Prefix that gets a helper running as root: nothing as root, pkexec normally.

    BOPPOS_HELPER_ESCALATE=sudo (development only) uses `sudo -n`, so a single `sudo -v` in the
    terminal covers every action instead of one polkit prompt each.
    """
    if (os.geteuid() if euid is None else euid) == 0:
        return []
    return ["sudo", "-n"] if os.environ.get("BOPPOS_HELPER_ESCALATE") == "sudo" else ["pkexec"]


def helper_cmd(name: str, args: list[str], euid: int | None = None) -> list[str]:
    """Command that runs a root helper: directly as root, otherwise through pkexec."""
    # BOPPOS_HELPER_DIR: run the helpers from a checkout (polkit only knows the /usr path)
    path = f"{os.environ.get('BOPPOS_HELPER_DIR') or HELPER_DIR}/{name}"
    return [*escalation(euid), path, *args]


def explain_pkexec(returncode: int, stderr: str) -> str | None:
    if "No such file" in stderr:
        return None
    if returncode == 126:
        return "not authorized: wrong password, or this account may not do that"
    if returncode == 127:
        return "authentication was cancelled, or no authentication agent is running"
    return None


def read_update_status(path: str = UPDATE_STATUS) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def booted_tag(status: dict) -> str | None:
    ref = ((status.get("booted") or {}).get("ref") or "").split("@", 1)[0]
    _, sep, tag = ref.rpartition(":")
    return tag if sep and "/" not in tag else None


def flavor_from_ref(ref: str, variant_id: str = "") -> str:
    m = re.search(r"/(?:[a-z0-9]+-)?cachyos-([a-z0-9]+)(?:[:@]|$)", ref) or re.search(
        r"cachyos-([a-z0-9]+)(?:[:@]|$)", ref
    )
    return m.group(1) if m else (variant_id or "unknown")


def parse_os_release(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and re.match(r"^[A-Z_]+$", key):
            out[key] = value.strip().strip('"')
    return out


def about(status: dict, root: str | Path = "/") -> dict:
    root = Path(root)
    try:
        osr = parse_os_release((root / "etc/os-release").read_text())
    except OSError:
        osr = {}
    dmi = read_dmi(root)
    booted = status.get("booted") or {}
    ref = booted.get("ref", "")
    repo = ref.split("@", 1)[0]
    if booted_tag(status):
        repo = repo.rsplit(":", 1)[0]
    return {
        "image": repo or osr.get("IMAGE_ID", "unknown"),
        "tag": booted_tag(status),
        "version": booted.get("version") or osr.get("VERSION_ID"),
        "flavor": flavor_from_ref(ref, osr.get("VARIANT_ID", "")),
        "kernel": os.uname().release,
        "device": device_label(dmi),
        "dmi": dmi,
        "boppos_version": __version__,
    }


def app_command(app: tuple[str, str], which: Callable[[str], str | None] = shutil.which) -> str | None:
    """Path to run a (binary, flatpak app id) pair with, or None when neither is installed."""
    binary, app_id = app
    candidates = [binary, app_id] + [os.path.join(os.path.expanduser(d), app_id) for d in FLATPAK_EXPORTS]
    return next((p for c in candidates if (p := which(c))), None)


def update_script(which: Callable[[str], str | None] = shutil.which) -> str | None:
    return which("boppos-update") or (str(_DEV_UPDATE) if _DEV_UPDATE.is_file() else None)


def upgrade_steps(
    only: list[str] | None, which: Callable[[str], str | None] = shutil.which
) -> list[tuple[str, list[list[str]]]]:
    """(target, [commands]) in a fixed order. Targets whose tool is missing are skipped."""
    want = [t for t in TARGETS if t in (only or DEFAULT_TARGETS)]
    steps: list[tuple[str, list[list[str]]]] = []
    for t in want:
        if t == "os":
            steps.append((t, [helper_cmd("bootc-helper", ["upgrade"])]))
        elif t == "fw" and which("fwupdmgr"):
            steps.append((t, [["fwupdmgr", "refresh", "--force"], ["fwupdmgr", "update", "-y", "--no-reboot-check"]]))
        elif t == "flatpak" and which("flatpak"):
            steps.append((t, [["flatpak", "update", "-y", "--noninteractive"]]))
        elif t == "brew":
            brew = which("brew") or next((p for p in BREW_PATHS if which(p)), None)
            if brew:
                steps.append((t, [[brew, "update"], [brew, "upgrade"]]))
        elif t == "appimage" and (gl := app_command(GEARLEVER, which)):
            # Only AppImages with an update source; running apps are skipped (no --force)
            steps.append((t, [[gl, "--update", "--all", "--yes"]]))
        elif t == "proton" and (pp := app_command(PROTONPLUS, which)):
            # Updates only the "<tool> Latest" installs; pinned versions stay as they are
            steps.append((t, [[pp, "update", "all"]]))
        elif t == "distrobox" and which("distrobox") and (script := update_script(which)):
            # boppos-update knows each box's package manager (shelly or distrobox upgrade); exits 1 if one fails
            steps.append((t, [[script, "--distrobox", "--yes", "--verbose"]]))
        elif t == "litterbox" and (which("litterbox-update") or which("litterbox")) \
                and (script := update_script(which)):
            steps.append((t, [[script, "--litterbox", "--yes", "--verbose"]]))
    return steps


def accepted_rc(target: str, cmd: list[str], rc: int) -> bool:
    """fwupdmgr exits 2 when there is nothing to do (or nothing to refresh)."""
    return rc == 0 or (target == "fw" and rc in (2,))


def parse_fw_updates(raw: str, rc: int) -> dict:
    if rc == 2 or not raw.strip():
        return {"count": 0, "devices": []}
    try:
        devices = json.loads(raw).get("Devices", [])
    except (json.JSONDecodeError, AttributeError):
        return {"count": None, "devices": [], "error": "unreadable fwupdmgr output"}
    items = []
    for d in devices:
        rel = (d.get("Releases") or [{}])[0]
        items.append({"name": d.get("Name", "?"), "version": rel.get("Version")})
    return {"count": len(items), "devices": items}


def run_capture(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def get_status() -> dict:
    proc = run_capture(helper_cmd("bootc-helper", ["status"]))
    if proc.returncode:
        err = explain_pkexec(proc.returncode, proc.stderr) or proc.stderr.strip() or "bootc status failed"
        return {"error": err}
    st = parse_status(proc.stdout)
    st["tag"] = booted_tag(st)
    st["monitor"] = read_update_status()
    return st


CHANNEL_NOTES = {
    "latest": "v3: works on most modern x86-64 CPUs. The default.",
    "v3": "v3: works on most modern x86-64 CPUs. The default.",
    "v4": "v4: built for x86-64-v4 CPUs (AVX-512).",
    "znver4": "znver4: built for AMD Zen 4 and newer (Ryzen 7000 and later).",
}
_DATED = re.compile(r"^(latest|v3|v4|znver4)-(\d{8}|test)$")


def channel_note(tag: str | None) -> str:
    """One line about the build the system follows (read-only: switching it is a deliberate, typed action)."""
    if not tag:
        return ""
    if tag in CHANNEL_NOTES:
        return CHANNEL_NOTES[tag]
    m = _DATED.match(tag)
    if m:
        kind = "test build" if m.group(2) == "test" else f"build from {m.group(2)}"
        return f"{CHANNEL_NOTES[m.group(1)].split(':')[0]}: {kind}."
    return "A custom tag."
