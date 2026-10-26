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


def helper_cmd(name: str, args: list[str], euid: int | None = None) -> list[str]:
    """Command that runs a root helper: directly as root, otherwise through pkexec."""
    # BOPPOS_HELPER_DIR: run the helpers from a checkout (use with sudo; polkit only knows the /usr path)
    path = f"{os.environ.get('BOPPOS_HELPER_DIR') or HELPER_DIR}/{name}"
    return [path, *args] if (os.geteuid() if euid is None else euid) == 0 else ["pkexec", path, *args]


def explain_pkexec(returncode: int, stderr: str) -> str | None:
    if "No such file" in stderr:
        return None
    if returncode == 126:
        return "not authorized (polkit): the user must be an active local member of wheel"
    if returncode == 127 and "pkexec" in stderr.lower():
        return "no polkit agent is available and the action needs authentication"
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


def list_tags(repo: str, current: str | None, limit: int = 25) -> list[str]:
    """Known tags of the booted image's repository (via skopeo), always including the current."""
    tags: list[str] = []
    if shutil.which("skopeo"):
        proc = run_capture(["skopeo", "list-tags", f"docker://{repo}"], timeout=30)
        if proc.returncode == 0:
            try:
                tags = json.loads(proc.stdout).get("Tags", [])
            except json.JSONDecodeError:
                tags = []
    clean = sorted({t for t in tags if re.match(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$", t)
                    and not t.startswith("sha256-") and not t.endswith((".sig", ".att", ".sbom"))})
    stable = [t for t in clean if t in ("stable", "latest", "testing", "unstable")]
    rest = [t for t in reversed(clean) if t not in stable][:limit]
    out = stable + rest
    if current and current not in out:
        out.insert(0, current)
    return out
