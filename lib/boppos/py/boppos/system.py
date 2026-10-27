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
TARGETS = ("os", "fw", "flatpak", "brew")
DEFAULT_TARGETS = ("os", "flatpak", "brew")  # same default as boppos-update


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
            brew = which("brew") or next((p for p in BREW_PATHS if os.access(p, os.X_OK)), None)
            if brew:
                steps.append((t, [[brew, "update"], [brew, "upgrade"]]))
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


CHANNELS = (
    {"tag": "latest", "label": "v3 (default)", "family": "v3",
     "description": "Works on most modern x86-64 CPUs. The default and the most compatible."},
    {"tag": "v4", "label": "v4", "family": "v4",
     "description": "Built for x86-64-v4 CPUs, which means AVX-512 support."},
    {"tag": "znver4", "label": "znver4", "family": "znver4",
     "description": "Built for AMD Zen 4 and newer (Ryzen 7000 and later)."},
)
_TAG_PARTS = re.compile(r"^(latest|v3|v4|znver4)(?:-(\d{8}|test))?$")


def tag_family(tag: str | None) -> str | None:
    """v3, v4 or znver4 for a channel tag, a dated build (v4-20260409) or a test build (v4-test)."""
    m = _TAG_PARTS.match(tag or "")
    if not m:
        return None
    return "v3" if m.group(1) in ("latest", "v3") else m.group(1)


def classify_tags(all_tags: list[str], current: str | None, limit: int = 14) -> dict:
    """What the update-channel screen offers: the fixed channels, recent dated builds of the channel in
    use (newest first), and published test builds. Signature tags and anything else are left out."""
    family = tag_family(current)
    dated: list[tuple[str, str]] = []
    tests: list[str] = []
    for tag in set(all_tags):
        m = _TAG_PARTS.match(tag)
        if not m or not m.group(2):
            continue
        if m.group(2) == "test":
            tests.append(tag)
        elif family and tag_family(tag) == family:
            dated.append((m.group(2), tag))
    return {
        "current": current,
        "current_channel": next((c["tag"] for c in CHANNELS if c["family"] == family), None),
        "channels": [dict(c) for c in CHANNELS],
        "builds": [t for _, t in sorted(dated, reverse=True)[:limit]],
        "test": sorted(tests, key=lambda t: (tag_family(t) != family, t)),  # your channel's test build first
    }


def tag_choices(repo: str, current: str | None) -> dict:
    """classify_tags over the registry's tag list (needs skopeo and network; the channels work without)."""
    tags: list[str] = []
    if shutil.which("skopeo"):
        proc = run_capture(["skopeo", "list-tags", f"docker://{repo}"], timeout=45)
        if proc.returncode == 0:
            try:
                tags = json.loads(proc.stdout).get("Tags", [])
            except json.JSONDecodeError:
                tags = []
    return classify_tags(tags, current)
