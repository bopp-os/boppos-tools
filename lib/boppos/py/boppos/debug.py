"""Debugging aids for the Support page: temporary SSH state and hardware details for the support report.

Reading all of this needs no root. Starting and stopping SSH goes through libexec/boppos/debug-helper.
"""

from __future__ import annotations

import getpass
import json
import stat
import subprocess
from pathlib import Path

from .privileged import SSH_STOP_TIMER, SSH_UNIT

BACKLIGHT_DIR = Path("/sys/class/backlight")
REPORT_SERVICES = ("steamos-manager.service", "inputplumber.service", "sshd.service")


def _capture(cmd: list[str], timeout: int = 20) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    return proc.stdout or proc.stderr


def parse_show(text: str) -> dict[str, str]:
    """`systemctl show -p A -p B` output as a dict."""
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def ssh_state(props: dict[str, str]) -> str:
    """missing, always (the owner enabled it, so it is not ours to stop), on or off."""
    if props.get("LoadState") != "loaded":
        return "missing"
    if props.get("UnitFileState") == "enabled":
        return "always"
    return "on" if props.get("ActiveState") == "active" else "off"


def parse_addresses(text: str) -> list[str]:
    """IPv4 addresses from `ip -j -4 addr`, without loopback."""
    try:
        links = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [a["local"] for link in links if "LOOPBACK" not in link.get("flags", [])
            for a in link.get("addr_info", []) if a.get("local")]


def ssh_status() -> dict:
    props = parse_show(_capture(["systemctl", "show", SSH_UNIT, "-p", "LoadState", "-p", "UnitFileState",
                                 "-p", "ActiveState"]))
    timer = _capture(["systemctl", "is-active", SSH_STOP_TIMER]).strip() == "active"
    return {"state": ssh_state(props), "auto_stop": timer, "user": getpass.getuser(),
            "addresses": parse_addresses(_capture(["ip", "-j", "-4", "addr"]))}


def backlight_report(root: Path = BACKLIGHT_DIR) -> str:
    """Each backlight device with its values and who may write it."""
    out = []
    for dev in sorted(root.glob("*")):
        values = []
        for name in ("type", "brightness", "actual_brightness", "max_brightness", "bl_power"):
            try:
                values.append(f"{name}={(dev / name).read_text().strip()}")
            except OSError as exc:
                values.append(f"{name}=({exc.strerror})")
        try:
            st = (dev / "brightness").stat()
            values.append(f"mode={stat.filemode(st.st_mode)} uid={st.st_uid} gid={st.st_gid}")
        except OSError:
            pass
        out.append(f"{dev.name}: " + " ".join(values))
    return "\n".join(out) or "no backlight device"


def report_sections() -> dict[str, str]:
    """Extra support report sections: what is needed to debug a device without having it in hand."""
    states = [f"{unit}: {_capture(['systemctl', 'is-active', unit]).strip()}" for unit in REPORT_SERVICES]
    return {
        "backlight": backlight_report(),
        "kernel": _capture(["uname", "-a"]),
        "services": "\n".join(states),
        "failed units": _capture(["systemctl", "--failed", "--no-legend", "--no-pager"]),
        "errors this boot": _capture(["journalctl", "-b", "-p", "err", "-n", "300", "--no-pager"]),
    }
