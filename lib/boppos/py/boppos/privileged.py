"""Allowlists for the root helpers in /usr/libexec/boppos.

Helpers never pass caller input to a shell or accept paths. Every verb maps to a fixed
command line and every argument must match a strict pattern. Pure functions, so they are
unit-tested without root.
"""

from __future__ import annotations

import re

from .kargs import PROTECTED_KEYS, KargError, key_of, validate_arg
from .kargs_info import forbidden_reason, is_critical

BOOTC = "/usr/bin/bootc"
OSTREE = "/usr/bin/ostree"
PROGRESS_FD = 3  # bootc's JSON progress; the helper makes it a copy of its stdout pipe

_INDEX = re.compile(r"^[0-9]$")
_DROPIN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class UsageError(Exception):
    pass


def checked_karg(arg: str, verb: str = "add") -> str:
    """Validate an argument for the helper. Adding is checked against the whole forbidden list; removing only
    against what is needed to boot, so a bad argument that is already there can always be taken out."""
    try:
        validate_arg(arg)
    except KargError as exc:
        raise UsageError(str(exc)) from exc
    key = key_of(arg)
    if key in PROTECTED_KEYS:
        raise UsageError(f"not allowed through the helper: {key}")
    if verb == "add" and (reason := forbidden_reason(arg)):
        raise UsageError(f"not allowed through the helper: {arg} ({reason})")
    if is_critical(arg):
        raise UsageError(f"{key} is needed to boot or unlock the disk; the helper never changes it")
    return arg


def _expect(argv: list[str], n: int, usage: str) -> None:
    if len(argv) != n:
        raise UsageError(f"usage: {usage}")


_TRANSPORT = re.compile(
    r"^(docker|oci|dir|containers-storage|docker-archive|oci-archive|docker-daemon|ostree)(:|$)", re.I)
_IMAGE_REF = re.compile(
    r"^(?P<host>(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::[0-9]{1,5})?)/"
    r"(?P<path>[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*)"
    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9._-]{0,127}))?"
    r"(?:@(?P<digest>sha256:[0-9a-f]{64}))?$")


def validate_image_ref(ref: str) -> str:
    """A complete registry reference: host/path:tag (or @sha256:digest). Nothing is filled in for the user.

    Refused: bare tags, a missing registry, a missing tag (no silent ':latest'), and anything with a
    transport prefix (oci:, dir:, docker://, containers-storage:...), which could point at local files.
    """
    ref = ref.strip()
    if "://" in ref or _TRANSPORT.match(ref):
        raise UsageError("give the registry reference only, without a transport prefix such as docker:// or oci:")
    if "/" not in ref:
        raise UsageError("give the full image reference, for example ghcr.io/bopp-os/cachyos-plasma:v4, "
                         "not just a tag")
    m = _IMAGE_REF.match(ref)
    if not m:
        raise UsageError(f"not a valid image reference: {ref!r}")
    host = m.group("host")
    if "." not in host and ":" not in host and host != "localhost":
        raise UsageError(f"{host!r} does not look like a registry host; start with the registry, "
                         "for example ghcr.io/…")
    if not m.group("tag") and not m.group("digest"):
        raise UsageError("add a tag (:v4) or a digest (@sha256:…); there is no default")
    if len(ref) > 300:
        raise UsageError("reference is too long")
    return ref


def system_command(argv: list[str]) -> list[str]:
    """Map `bootc-helper <verb> [arg]` to the command to exec."""
    if not argv:
        raise UsageError("usage: bootc-helper status|check|upgrade|rollback|pin N|unpin N|switch IMAGE")
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
        _expect(rest, 1, "switch IMAGE")
        # always over the registry transport, and always honouring the system's signature policy
        return [BOOTC, "switch", "--transport", "registry", "--enforce-container-sigpolicy",
                validate_image_ref(rest[0])]
    raise UsageError(f"unknown verb: {verb!r}")


def with_progress_fd(cmd: list[str]) -> list[str]:
    """bootc upgrade/switch with JSON progress (layers, bytes) on PROGRESS_FD; other commands unchanged."""
    if cmd[:1] == [BOOTC] and cmd[1:2] in (["upgrade"], ["switch"]) and "--check" not in cmd:
        return [*cmd[:2], "--progress-fd", str(PROGRESS_FD), *cmd[2:]]
    return cmd


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
        return ["--json", verb, checked_karg(rest[0], verb)]
    raise UsageError(usage)
