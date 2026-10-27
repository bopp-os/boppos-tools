"""Kernel-argument logic: token-exact edits, BLS entry lookup and drop-in based `apply`.

Everything takes a `root` so tests run against fixture trees, never the real /boot or /sys.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .dmi import read_dmi
from .kargs_info import forbidden_reason, is_critical
from .paths import kargs_catalog_dir

BLS_DIR = "boot/loader/entries"
def usr_dropin_dir(root: str | Path) -> Path:
    """Packaged presets. For the real system (root "/") that also finds a checkout; test roots use their own."""
    root = Path(root)
    return kargs_catalog_dir() if root == Path("/") else root / "usr/lib/bopp-kargs.d"
MANAGED_FILE = "var/lib/bopp-kargs/managed"
PROTECTED_KEYS = frozenset({"ostree", "root"})
RISK_LEVELS = ("low", "medium", "high")

_KEY = r"[A-Za-z0-9_.:/-]+"
_VALUE = r"[A-Za-z0-9_.:/,@%+=;~\[\]-]*"
_ARG_RE = re.compile(rf"^{_KEY}(={_VALUE})?$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class KargError(Exception):
    """User-facing failure; `code` is a stable identifier for --json consumers."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


# --- tokens -----------------------------------------------------------------------------------


def key_of(token: str) -> str:
    return token.split("=", 1)[0]


def validate_arg(arg: str) -> str:
    if len(arg) > 256 or not _ARG_RE.match(arg):
        raise KargError(f"invalid kernel argument: {arg!r}", "invalid-arg")
    return arg


def tokenize(options: str) -> list[str]:
    """Split a cmdline on whitespace, keeping double-quoted spans together."""
    tokens: list[str] = []
    cur: list[str] = []
    quoted = False
    for ch in options:
        if ch == '"':
            quoted = not quoted
            cur.append(ch)
        elif ch.isspace() and not quoted:
            if cur:
                tokens.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        tokens.append("".join(cur))
    return tokens


def add_tokens(tokens: list[str], args: list[str], force: bool = False) -> list[str]:
    """Append arguments that are not there yet. Anything on the forbidden list (shell-opening, security
    switches, driver blacklists...) is refused unless `force`, which only the sudo CLI can pass."""
    out = list(tokens)
    for arg in map(validate_arg, args):
        if key_of(arg) in PROTECTED_KEYS:
            raise KargError(f"refusing to add protected argument {key_of(arg)}=", "protected")
        reason = forbidden_reason(arg)
        if reason and not force:
            raise KargError(f"refusing to add {arg}: {reason} (sudo bopp-kargs add --force overrides this)",
                            "forbidden")
        if arg not in out:
            out.append(arg)
    return out


def remove_tokens(tokens: list[str], args: list[str], force: bool = False) -> list[str]:
    """Drop tokens equal to one of `args`. `quiet` never touches `rd.quiet` or `quiet=1`.

    Arguments the system needs to boot are refused; `force` (the CLI's --force) lifts that for
    everything except ostree= and root=, which are never removable.
    """
    drop = set(map(validate_arg, args))
    for arg in drop:
        if key_of(arg) in PROTECTED_KEYS:
            raise KargError(f"refusing to remove protected argument {key_of(arg)}=", "protected")
        if is_critical(arg) and not force:
            raise KargError(f"{arg} is needed to boot or unlock the disk; refusing to remove it "
                            "(sudo bopp-kargs remove --force overrides this)", "critical")
    return [t for t in tokens if t not in drop]


# --- boot entries -----------------------------------------------------------------------------


@dataclass
class Entry:
    path: Path
    title: str
    options: list[str]
    deploy_key: tuple[str, str, str] | None  # (os, bootcsum, serial) from ostree=
    commit: str | None

    @property
    def booted_hint(self) -> str:
        return "/".join(self.deploy_key) if self.deploy_key else ""


def _deploy_key(options: list[str]) -> tuple[str, str, str] | None:
    for tok in options:
        if tok.startswith("ostree="):
            parts = tok[len("ostree=") :].strip("/").split("/")
            if len(parts) >= 3:
                return (parts[-3], parts[-2], parts[-1])
    return None


def _resolve_commit(root: Path, options: list[str]) -> str | None:
    for tok in options:
        if tok.startswith("ostree="):
            link = root / tok[len("ostree=") :].lstrip("/")
            try:
                target = os.readlink(link)
            except OSError:
                return None
            name = os.path.basename(target.rstrip("/"))  # <commit>.<serial>
            return name.rsplit(".", 1)[0] if "." in name else name
    return None


def load_entries(root: str | Path = "/") -> list[Entry]:
    root = Path(root)
    entries: list[Entry] = []
    for path in sorted((root / BLS_DIR).glob("*.conf")):
        title, options = "", []
        for line in path.read_text(errors="replace").splitlines():
            key, _, value = line.partition(" ")
            if key == "title":
                title = value.strip()
            elif key == "options":
                options = tokenize(value)
        entries.append(Entry(path, title, options, _deploy_key(options), _resolve_commit(root, options)))
    return entries


def booted_deploy_key(cmdline: str) -> tuple[str, str, str] | None:
    return _deploy_key(tokenize(cmdline))


def target_entry(entries: list[Entry], status: dict, cmdline: str) -> Entry:
    """The entry that the next boot uses. Refuse when that is not the running deployment."""
    if status.get("error"):
        raise KargError(f"cannot read bootc status: {status['error']}", "bootc-status")
    if status.get("staged"):
        raise KargError("an update is staged; reboot before changing kernel arguments", "staged")
    if status.get("rollback_queued"):
        raise KargError("a rollback is queued; reboot before changing kernel arguments", "staged")
    key = booted_deploy_key(cmdline)
    if key is None:
        raise KargError("running kernel has no ostree= argument", "not-ostree")
    for entry in entries:
        if entry.deploy_key == key:
            return entry
    raise KargError("no boot entry matches the running deployment", "no-entry")


# --- drop-ins ---------------------------------------------------------------------------------


@dataclass
class Dropin:
    name: str
    source: str  # "usr" or "etc"
    path: Path
    kargs: list[str] = field(default_factory=list)
    enabled: bool = True
    description: str = ""
    category: str = "Other"
    risk_level: str = "low"  # low, medium or high
    risk: str = ""
    match: dict[str, list[str]] = field(default_factory=dict)
    error: str | None = None


def _strip_comment(line: str) -> str:
    return re.split(r"(?:^|\s)#", line, maxsplit=1)[0].strip()


def parse_dropin(name: str, source: str, path: Path) -> Dropin:
    d = Dropin(name, source, path)
    try:
        for raw in path.read_text(errors="replace").splitlines():
            line = _strip_comment(raw)
            if not line:
                continue
            key, sep, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not sep:
                raise KargError(f"line without '=': {raw!r}")
            if key == "kargs":
                d.kargs += [validate_arg(t) for t in value.split()]
            elif key == "enabled":
                if value.lower() not in ("true", "false", "yes", "no", "1", "0"):
                    raise KargError(f"enabled must be true or false, got {value!r}")
                d.enabled = value.lower() in ("true", "yes", "1")
            elif key == "description":
                d.description = value
            elif key == "category":
                d.category = value or "Other"
            elif key == "risk":
                d.risk = value
            elif key == "risk_level":
                if value.lower() not in RISK_LEVELS:
                    raise KargError(f"risk_level must be one of {', '.join(RISK_LEVELS)}, got {value!r}")
                d.risk_level = value.lower()
            elif key.startswith("match."):
                d.match.setdefault(key[len("match.") :], []).extend(
                    p.strip() for p in value.split("|") if p.strip()
                )
            else:
                raise KargError(f"unknown key {key!r}")
        for tok in d.kargs:
            if key_of(tok) in PROTECTED_KEYS or forbidden_reason(tok):
                raise KargError(f"drop-ins may not set {key_of(tok)}: {forbidden_reason(tok) or 'protected'}")
    except KargError as exc:
        d.error = str(exc)
    return d


def load_dropins(root: str | Path = "/") -> dict[str, Dropin]:
    """Load /usr then /etc drop-ins; a file in /etc replaces the same-named file in /usr."""
    root = Path(root)
    found: dict[str, Dropin] = {}
    for source, directory in (("usr", usr_dropin_dir(root)), ("etc", root / "etc/bopp-kargs.d")):
        for path in sorted(directory.glob("*.conf")):
            name = path.stem
            if _NAME_RE.match(name):
                found[name] = parse_dropin(name, source, path)
    return found


def matches_device(dropin: Dropin, dmi: dict[str, str]) -> bool:
    """All match.* keys must match; patterns of one key are alternatives (fnmatch, any case)."""
    for dmi_field, patterns in dropin.match.items():
        value = dmi.get(dmi_field)
        if value is None:
            return False
        if not any(fnmatch.fnmatchcase(value.lower(), p.lower()) for p in patterns):
            return False
    return True


GPU_NAMES = {"amd": "AMD", "intel": "Intel", "nvidia": "NVIDIA"}


def gpu_of(dropin: Dropin) -> str | None:
    """Which graphics vendor a preset is about, from its arguments (None: not about graphics)."""
    for t in dropin.kargs:
        if t.startswith("amdgpu."):
            return "amd"
        if t.startswith(("i915.", "xe.")):
            return "intel"
        if t.startswith("nvidia"):
            return "nvidia"
    return None


def fit(dropin: Dropin, dmi: dict[str, str], gpus: tuple[str, ...]) -> tuple[str, str]:
    """How a preset relates to this hardware, as (fit, reason). The one rule the wizard and the deck share.

    "fits": it is about this model or this graphics vendor. "optional": it could apply to anything.
    "risky": it could apply anywhere but is medium or high risk. "unsupported": it is for another model or
    graphics vendor, so it cannot work here.
    """
    gpu = gpu_of(dropin)
    if dropin.match and not matches_device(dropin, dmi):
        return "unsupported", "Only for other models than this one."
    if gpu and gpu not in gpus:
        return "unsupported", f"Needs {GPU_NAMES[gpu]} graphics, which this hardware does not have."
    if dropin.match or gpu:
        return "fits", ""
    if dropin.risk_level in ("medium", "high"):  # it could apply anywhere, which is exactly why it is not offered
        return "risky", (f"{dropin.risk_level.capitalize()} risk and not specific to this hardware. Only "
                               "change it from Kernel arguments in boppos-deck, and only if you need it.")
    return "optional", ""


def set_enabled(root: str | Path, name: str, enabled: bool) -> Dropin:
    """Persist enabled/disabled as an /etc override (or drop the override if it equals /usr)."""
    root = Path(root)
    if not _NAME_RE.match(name):
        raise KargError(f"invalid drop-in name: {name!r}", "invalid-name")
    dropins = load_dropins(root)
    cur = dropins.get(name)
    if cur is None:
        raise KargError(f"unknown drop-in: {name}", "unknown-dropin")
    if cur.error:
        raise KargError(f"drop-in {name} is invalid: {cur.error}", "invalid-dropin")
    etc = root / "etc/bopp-kargs.d" / f"{name}.conf"
    usr = usr_dropin_dir(root) / f"{name}.conf"
    lines = [
        ln
        for ln in cur.path.read_text().splitlines()
        if _strip_comment(ln).partition("=")[0].strip() != "enabled"
    ]
    text = "\n".join(lines).rstrip("\n") + f"\nenabled = {'true' if enabled else 'false'}\n"
    if usr.exists():
        usr_default = parse_dropin(name, "usr", usr).enabled
        if usr_default == enabled and etc.exists() and cur.source == "etc":
            etc.unlink()
            return load_dropins(root)[name]
        if usr_default == enabled and not etc.exists():
            return cur
    etc.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(etc, text)
    return load_dropins(root)[name]


# --- managed state and apply -----------------------------------------------------------------


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_managed(root: str | Path = "/") -> list[str]:
    try:
        text = (Path(root) / MANAGED_FILE).read_text()
    except OSError:
        return []
    return [t for t in (ln.strip() for ln in text.splitlines()) if t]


def write_managed(root: str | Path, tokens: list[str]) -> None:
    atomic_write(Path(root) / MANAGED_FILE, "".join(f"{t}\n" for t in sorted(set(tokens))))


def wanted_tokens(dropins: dict[str, Dropin], dmi: dict[str, str]) -> list[str]:
    out: list[str] = []
    for d in dropins.values():
        if d.error or not d.enabled or not matches_device(d, dmi):
            continue
        for tok in d.kargs:
            if tok not in out:
                out.append(tok)
    return out


def plan(wanted: list[str], managed: list[str], current: list[str]) -> tuple[list[str], list[str]]:
    """Return (to_add, to_remove). Only arguments we own (managed) are ever removed."""
    to_add = [t for t in wanted if t not in current]
    to_remove = [t for t in managed if t not in wanted and t in current]
    return to_add, to_remove


Runner = Callable[[list[str]], None]


def run_cmd(argv: list[str]) -> None:
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    if proc.returncode:
        raise KargError(f"{' '.join(argv[:4])} failed: {proc.stderr.strip() or proc.returncode}", "ostree")


def write_options(current: list[str], to_add: list[str], to_remove: list[str], run: Runner,
                  force: bool = False) -> None:
    """Additions go through edit-in-place. ostree has no delete option, so removals rewrite
    the full list with `instutil set-kargs`, which replaces the cmdline of deployment 0."""
    if to_remove:
        new = add_tokens(remove_tokens(current, to_remove, force), to_add, force)
        if not any(t.startswith("ostree=") for t in new):
            raise KargError("refusing to write a cmdline without ostree=", "protected")
        lost = [t for t in current if is_critical(t) and t not in new]
        if lost and not force:  # belt and braces: whatever the caller asked, keep what boot needs
            raise KargError(f"refusing to drop boot-critical arguments: {' '.join(lost)}", "critical")
        run(["ostree", "admin", "instutil", "set-kargs", *new])
    elif to_add:
        run(["ostree", "admin", "kargs", "edit-in-place", *[f"--append-if-missing={a}" for a in to_add]])


def apply_changes(
    root: str | Path,
    status: dict,
    cmdline: str,
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    run: Runner = run_cmd,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Apply explicit add/remove lists to the next-boot deployment (no drop-in logic)."""
    entry = target_entry(load_entries(root), status, cmdline)
    to_add = [a for a in map(validate_arg, add or []) if a not in entry.options]
    add_tokens([], to_add, force)  # refuses forbidden arguments before anything runs
    to_remove = [a for a in map(validate_arg, remove or []) if a in entry.options]
    if to_remove and not force:
        remove_tokens(entry.options, to_remove)  # raises for boot-critical arguments before anything runs
    return _commit(root, entry, to_add, to_remove, run, dry_run, managed=None, force=force)


def apply_dropins(
    root: str | Path,
    status: dict,
    cmdline: str,
    *,
    dmi: dict[str, str] | None = None,
    run: Runner = run_cmd,
    dry_run: bool = False,
) -> dict:
    root = Path(root)
    entry = target_entry(load_entries(root), status, cmdline)
    wanted = wanted_tokens(load_dropins(root), read_dmi(root) if dmi is None else dmi)
    managed = read_managed(root)
    to_add, to_remove = plan(wanted, managed, entry.options)
    owned = [t for t in managed if t in wanted] + to_add
    return _commit(root, entry, to_add, to_remove, run, dry_run, managed=owned)


def _commit(root, entry, to_add, to_remove, run, dry_run, managed, force=False):
    changed = bool(to_add or to_remove)
    if changed and not dry_run:
        write_options(entry.options, to_add, to_remove, run, force)
    if managed is not None and not dry_run and (changed or set(managed) != set(read_managed(root))):
        write_managed(root, managed)
    return {
        "added": to_add,
        "removed": to_remove,
        "changed": changed,
        "reboot_required": changed and not dry_run,
        "dry_run": dry_run,
    }


KARGS_WARNING = (
    "Only switch these on if you have the exact problem a preset fixes. Kernel arguments change how the "
    "system starts, a wrong one can cause crashes, a black screen or a machine that will not boot, and "
    "most systems are best left alone. Every preset here is off by default."
)

SECTIONS = ("on", "fits", "optional", "risky", "unsupported", "invalid")


def sections(items: list[dict]) -> dict[str, list[dict]]:
    """Sort the presets of `bopp-kargs --json dropins` into the groups both apps show, in display order.

    on: switched on or already running here (switchable, so it can be turned off); then by fit; invalid last.
    """
    out: dict[str, list[dict]] = {k: [] for k in SECTIONS}
    for d in items:
        if d.get("error"):
            out["invalid"].append(d)
        elif (d.get("enabled") or d.get("already_active")) and d.get("matches_device"):
            out["on"].append(d)  # already_active: running now, set by the image or by hand
        else:
            out[d.get("fit") or "unsupported"].append(d)
    for rows in out.values():
        rows.sort(key=lambda d: d["name"])
    return out


# Not changes anybody made: the bootloader adds BOOT_IMAGE and initrd, and ostree= names the deployment.
TRANSIENT_KEYS = frozenset({"BOOT_IMAGE", "initrd", "ostree"})


def diff_cmdline(running: str, next_boot: list[str]) -> dict[str, list[str]]:
    """What the next boot adds to and drops from the running command line, token-exact, in command-line order."""

    def keep(tokens: list[str]) -> list[str]:
        return [t for t in tokens if key_of(t) not in TRANSIENT_KEYS]

    now, nxt = keep(tokenize(running)), keep(list(next_boot))
    return {"added": [t for t in nxt if t not in now], "removed": [t for t in now if t not in nxt]}
