"""Allowlists for the root helpers in /usr/libexec/boppos.

Helpers never pass caller input to a shell or accept paths. Every verb maps to a fixed
command line and every argument must match a strict pattern. Pure functions, so they are
unit-tested without root.
"""

from __future__ import annotations

import re

from .bootc import parse_status
from .kargs import PROTECTED_KEYS, KargError, key_of, validate_arg

BOOTC = "/usr/bin/bootc"
OSTREE = "/usr/bin/ostree"

_INDEX = re.compile(r"^[0-9]$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_DROPIN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


# Arguments that give a root shell or a different init at boot. Never settable through the helper
# (the CLI run with sudo is not restricted).
DENY_KEYS = frozenset({
    "init", "rdinit", "initrd", "break", "rd.break", "rd.shell", "rd.emergency", "single", "emergency",
    "rescue", "systemd.unit", "systemd.run", "systemd.debug_shell", "s", "S", "1", "2", "3", "4", "5",
})


class UsageError(Exception):
    pass


def checked_karg(arg: str) -> str:
    try:
        validate_arg(arg)
    except KargError as exc:
        raise UsageError(str(exc)) from exc
    key = key_of(arg)
    if key in PROTECTED_KEYS or key in DENY_KEYS:
        raise UsageError(f"not allowed through the helper: {key}")
    return arg


def _expect(argv: list[str], n: int, usage: str) -> None:
    if len(argv) != n:
        raise UsageError(f"usage: {usage}")


def switch_target(status_json: str, tag: str) -> str:
    """`repo:tag` for the booted image; only the tag may change, never registry or repository."""
    if not _TAG.match(tag):
        raise UsageError(f"invalid tag: {tag!r}")
    status = parse_status(status_json)
    booted = status.get("booted")
    if not booted or booted.get("transport", "registry") != "registry":
        raise UsageError("booted image is not a registry image; cannot switch tag")
    ref = booted["ref"]
    ref = ref.split("@", 1)[0]  # drop a digest
    repo, sep, last = ref.rpartition(":")
    if not sep or "/" in last:  # no tag (a ':' inside the registry host:port does not count)
        repo = ref
    return f"{repo}:{tag}"


def system_command(argv: list[str], bootc_status: str | None = None) -> list[str]:
    """Map `bootc-helper <verb> [arg]` to the command to exec."""
    if not argv:
        raise UsageError("usage: bootc-helper status|check|upgrade|rollback|pin N|unpin N|switch TAG")
    verb, rest = argv[0], argv[1:]
    if verb == "status":
        _expect(rest, 0, "status")
        return [BOOTC, "status", "--format=json"]
    if verb == "check":
        _expect(rest, 0, "check")
        return [BOOTC, "upgrade", "--check"]
    if verb == "upgrade":
        _expect(rest, 0, "upgrade")
        return [BOOTC, "upgrade"]
    if verb == "rollback":
        _expect(rest, 0, "rollback")
        return [BOOTC, "rollback"]
    if verb in ("pin", "unpin"):
        _expect(rest, 1, f"{verb} INDEX")
        if not _INDEX.match(rest[0]):
            raise UsageError(f"invalid deployment index: {rest[0]!r}")
        flag = [] if verb == "pin" else ["--unpin"]
        return [OSTREE, "admin", "pin", *flag, rest[0]]
    if verb == "switch":
        _expect(rest, 1, "switch TAG")
        if bootc_status is None:
            raise UsageError("internal: switch needs the current bootc status")
        return [BOOTC, "switch", switch_target(bootc_status, rest[0])]
    raise UsageError(f"unknown verb: {verb!r}")


def kargs_argv(argv: list[str]) -> list[str]:
    """Map `kargs-helper <verb> [arg]` to bopp-kargs arguments."""
    usage = "usage: kargs-helper status|list|dropins|apply|enable NAME|disable NAME|add ARG|remove ARG"
    if not argv:
        raise UsageError(usage)
    verb, rest = argv[0], argv[1:]
    if verb in ("status", "list", "dropins", "apply"):
        _expect(rest, 0, verb)
        return ["--json", verb]
    if verb in ("enable", "disable"):
        _expect(rest, 1, f"{verb} NAME")
        if not _DROPIN.match(rest[0]):
            raise UsageError(f"invalid drop-in name: {rest[0]!r}")
        return ["--json", verb, rest[0]]
    if verb in ("add", "remove"):
        _expect(rest, 1, f"{verb} ARG")
        return ["--json", verb, checked_karg(rest[0])]
    raise UsageError(usage)


def tags_from_status(raw: str) -> dict:
    """Small helper for tests and the UI: current tag of the booted image, if any."""
    booted = parse_status(raw).get("booted") or {}
    ref = (booted.get("ref") or "").split("@", 1)[0]
    _, sep, tag = ref.rpartition(":")
    return {"ref": ref, "tag": tag if sep and "/" not in tag else None}


__all__ = ["DENY_KEYS", "UsageError", "checked_karg", "kargs_argv", "switch_target", "system_command",
           "tags_from_status"]
