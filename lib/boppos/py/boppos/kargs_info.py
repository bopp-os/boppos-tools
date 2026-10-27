"""Plain-language descriptions of common kernel arguments, and which ones are needed to boot.

The descriptions come from share/boppos/kargs-info.json and are only help text. The boot-critical floor
below is code on purpose: a missing or edited data file must never make a critical argument removable.
"""

from __future__ import annotations

import json
from functools import lru_cache

from .paths import share_file

# Never removable or settable through the helper, whatever the data file says.
CRITICAL_KEYS = frozenset({"ostree", "root", "rootflags", "rootfstype"})
CRITICAL_PREFIXES = ("rd.luks.", "rd.lvm.", "rd.md.", "rd.dm.", "luks.")
NOT_CRITICAL = frozenset({"rd.luks.options"})  # only controls TPM auto-unlock; the passphrase still works


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
        "managed": token in managed,
        "known": bool(e),
    }
