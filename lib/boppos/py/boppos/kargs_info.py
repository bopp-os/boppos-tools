"""Plain-language descriptions of common kernel arguments, and which ones are needed to boot.

The descriptions come from share/boppos/kargs-info.json and are only help text. The boot-critical floor
below is code on purpose: a missing or edited data file must never make a critical argument removable.
"""

from __future__ import annotations

import json
from functools import lru_cache

from .paths import share_file

# Never removable or settable through the helper, whatever the data file says. Everything the initramfs
# reads (rd.*) counts: it decides how disks are found, unlocked and assembled.
CRITICAL_KEYS = frozenset({"ostree", "root", "rootflags", "rootfstype"})
CRITICAL_PREFIXES = ("rd.", "luks.")
# Harmless or recoverable: the TPM option only costs a passphrase prompt, the rest is diagnostics.
NOT_CRITICAL = frozenset({"rd.luks.options", "rd.plymouth", "rd.debug", "rd.udev.log_level"})

# Not settable through the app or the helper (add); `sudo bopp-kargs add --force` still can, on purpose.
DENY_KEYS = frozenset({
    "init", "rdinit", "initrd", "break", "single", "emergency", "rescue", "s", "S", "1", "2", "3", "4", "5",
})
DENY_PREFIXES = ("systemd.",)
SECURITY_KEYS = frozenset({
    "selinux", "enforcing", "security", "apparmor", "lsm", "lockdown", "ima", "ima_appraise", "ima_policy",
    "evm", "audit", "fips",
})
DRIVER_KEYS = frozenset({"module_blacklist", "modprobe.blacklist", "initcall_blacklist", "blacklist"})
HARDWARE_KEYS = frozenset({"acpi", "noacpi", "nosmp", "maxcpus", "nr_cpus", "mem", "memmap"})


def _key(token: str) -> str:
    return token.split("=", 1)[0]


@lru_cache(maxsize=1)
def load_info() -> tuple[dict, ...]:
    try:
        data = json.loads(share_file("kargs-info.json").read_text())
    except (OSError, json.JSONDecodeError):
        return ()
    return tuple(e for e in data if isinstance(e, dict) and e.get("summary"))


def is_critical(token: str) -> bool:
    key = _key(token)
    if key in NOT_CRITICAL:
        return False
    if key in CRITICAL_KEYS or key.startswith(CRITICAL_PREFIXES):
        return True
    if token == "rw":
        return True
    entry = describe(token)
    return bool(entry and entry.get("critical"))


def forbidden_reason(token: str) -> str | None:
    """Why this argument may not be ADDED through the app or helper, or None. Removing is judged by
    is_critical() alone, so a bad argument that is already there can always be taken out again."""
    key = _key(token)
    if is_critical(token):
        return "it is needed to boot or to unlock the disk"
    if key in DENY_KEYS or key.startswith(DENY_PREFIXES):
        return "it can bypass the login or change how the system starts"
    if key in SECURITY_KEYS:
        return "it changes or turns off a system security feature"
    if key in DRIVER_KEYS:
        return "it can disable a driver the system needs to start"
    if key in HARDWARE_KEYS:
        return "it can stop the system from starting"
    return None


def describe(token: str) -> dict | None:
    """Best match: exact token, then key, then key prefix."""
    info = load_info()
    for e in info:
        if e.get("match") == token:
            return e
    key = _key(token)
    for e in info:
        if e.get("key") == key:
            return e
    for e in info:
        if e.get("prefix") and key.startswith(e["prefix"]):
            return e
    return None


def details(token: str, managed: set[str] | frozenset[str] = frozenset()) -> dict:
    """What the UI shows next to one argument."""
    e = describe(token) or {}
    return {
        "token": token,
        "summary": e.get("summary", ""),
        "risk": e.get("risk", ""),
        "risk_level": e.get("risk_level", ""),
        "image_default": bool(e.get("image_default")),
        "critical": is_critical(token),
        "forbidden": forbidden_reason(token),
        "managed": token in managed,
        "known": bool(e),
    }
