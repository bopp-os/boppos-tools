"""Decky Loader on BoppOS: the system `decky-loader` package, one `decky-loader@<user>.service` per user.

The package ships /usr/bin/decky-loader and a template unit that keeps plugins and settings in a root-owned
/var/lib/decky-loader/<user> (PRIVILEGED_PATH) and plugin data and logs in ~/.local/var/opt/decky-loader
(UNPRIVILEGED_PATH). The unit stays off until bopp-decky enables it.

Older BoppOS (and the upstream installer) ran ~/homebrew/services/PluginLoader as root from plugin_loader.service,
a file the user can replace. The functions below move such an install over: they copy regular files and
directories only, never follow a symlink, and only add entries the destination does not have yet, so running the
migration again changes nothing.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import tarfile
from pathlib import Path

LOADER_BIN = "/usr/bin/decky-loader"
STATE_ROOT = "/var/lib/decky-loader"
LEGACY_SERVICE = "plugin_loader.service"
LEGACY_UNIT_PATH = f"/etc/systemd/system/{LEGACY_SERVICE}"
# Relative to the home folder
LEGACY_DIR = "homebrew"
LEGACY_BINARY = "homebrew/services/PluginLoader"
LEGACY_LEFTOVERS = ("homebrew/services/PluginLoader", "homebrew/services/.loader.version")
UNPRIVILEGED_DIR = ".local/var/opt/decky-loader"
# Which ~/homebrew folders go where
PRIVILEGED_TOPS = ("plugins", "settings")
UNPRIVILEGED_TOPS = ("data", "logs")

_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_COPY_CHUNK = 1 << 20


class DeckyError(Exception):
    pass


def validate_user(name: str) -> str:
    if not _USER_RE.match(name):
        raise DeckyError(f"user name not usable as a unit instance: {name!r}")
    return name


def unit_name(user: str) -> str:
    return f"decky-loader@{validate_user(user)}.service"


def state_dir(user: str) -> Path:
    return Path(STATE_ROOT) / validate_user(user)


def _warn(msg: str) -> None:
    print(f"decky: {msg}", file=sys.stderr, flush=True)


# --- walking and writing trees ------------------------------------------------------------------


def iter_tree(base: Path, tops: tuple[str, ...]):
    """Yield (relpath, is_dir, fileobj | None, mode) for the regular files and directories under base/<top>.

    Symlinks, devices, sockets and FIFOs are skipped, and a top folder that is itself a symlink is skipped too.
    Files are opened with O_NOFOLLOW, so a path swapped for a symlink after the check is refused, not followed.
    Unreadable entries are skipped with a warning."""
    for top in tops:
        root = base / top
        try:
            st = os.lstat(root)
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(st.st_mode):
            _warn(f"skipped {root}: not a directory")
            continue
        yield from _walk(root, top, st.st_mode)


def _walk(path: Path, rel: str, mode: int):
    yield rel, True, None, mode
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name)
    except OSError as exc:
        _warn(f"skipped {path}: {exc.strerror}")
        return
    for entry in entries:
        child, child_rel = Path(entry.path), f"{rel}/{entry.name}"
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            _warn(f"skipped {child}: {exc.strerror}")
            continue
        if stat.S_ISDIR(st.st_mode):
            yield from _walk(child, child_rel, st.st_mode)
        elif stat.S_ISREG(st.st_mode):
            try:
                fd = os.open(child, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            except OSError as exc:
                _warn(f"skipped {child}: {exc.strerror}")
                continue
            with os.fdopen(fd, "rb") as fh:
                if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                    _warn(f"skipped {child}: changed while copying")
                    continue
                yield child_rel, False, fh, st.st_mode
        else:
            _warn(f"skipped {child}: not a regular file or directory")


def _check_rel(rel: str, tops: tuple[str, ...]) -> list[str]:
    parts = rel.split("/")
    if not parts or parts[0] not in tops or any(p in ("", ".", "..") or "\0" in p for p in parts):
        raise DeckyError(f"refusing path {rel!r}")
    return parts


def _write_entry(dest: Path, rel: str, is_dir: bool, src, mode: int) -> None:
    """Create dest/rel (which must not exist yet). Group and world write bits and setuid/setgid are dropped."""
    target = dest / rel
    perm = stat.S_IMODE(mode) & 0o755
    if is_dir:
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(target, perm | 0o700)
        return
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as out:
        shutil.copyfileobj(src, out, _COPY_CHUNK)
        os.fchmod(out.fileno(), perm | 0o600)


def copy_tree(base: Path, tops: tuple[str, ...], staging: Path) -> int:
    """Copy base/<top> for each top into staging (a fresh, empty folder). Returns the number of files."""
    files = 0
    for rel, is_dir, fh, mode in iter_tree(base, tops):
        _write_entry(staging, rel, is_dir, fh, mode)
        files += not is_dir
    return files


def write_tar(base: Path, tops: tuple[str, ...], fileobj) -> None:
    """Stream base/<top> as an uncompressed tar of regular files and directories (no owners, no links)."""
    with tarfile.open(fileobj=fileobj, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for rel, is_dir, fh, mode in iter_tree(base, tops):
            info = tarfile.TarInfo(rel)
            info.mode = stat.S_IMODE(mode)
            if is_dir:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = os.fstat(fh.fileno()).st_size
                tar.addfile(info, fh)


def extract_tar(fileobj, tops: tuple[str, ...], staging: Path) -> int:
    """Unpack a stream from write_tar into staging. The stream comes from the user, so every member is checked:
    only regular files and directories, only below one of tops, no absolute paths and no '..'."""
    files = 0
    with tarfile.open(fileobj=fileobj, mode="r|") as tar:
        for member in tar:
            _check_rel(member.name, tops)
            if member.isdir():
                _write_entry(staging, member.name, True, None, member.mode)
            elif member.isreg():
                src = tar.extractfile(member)
                _write_entry(staging, member.name, False, src, member.mode)
                files += 1
            else:
                raise DeckyError(f"refusing {member.name!r}: not a regular file or directory")
    return files


def merge_into(staging: Path, dest: Path, tops: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Move staging/<top>/<entry> to dest/<top>/<entry> where dest has no such entry yet (a plugin, a settings
    file, a plugin's data folder). Existing entries are left alone, which makes a second run a no-op.
    Returns (moved, kept)."""
    moved, kept = [], []
    for top in tops:
        src_top = staging / top
        if not src_top.is_dir():
            continue
        dst_top = dest / top
        if dst_top.is_symlink():
            raise DeckyError(f"{dst_top} is a symlink")
        dst_top.mkdir(mode=0o755, parents=True, exist_ok=True)
        for entry in sorted(src_top.iterdir()):
            target = dst_top / entry.name
            if os.path.lexists(target):
                kept.append(f"{top}/{entry.name}")
            else:
                os.rename(entry, target)
                moved.append(f"{top}/{entry.name}")
    return moved, kept


def remove_legacy_files(home: Path) -> list[str]:
    """Delete the old root-run binary (and its version file). Plugins, data and settings in ~/homebrew are kept."""
    removed = []
    for rel in LEGACY_LEFTOVERS:
        path = home / rel
        if path.is_symlink() or path.is_file():
            path.unlink()
            removed.append(rel)
    return removed


def cef_flag_paths(home: str) -> list[Path]:
    paths = [Path(home) / ".steam/steam/.cef-enable-remote-debugging"]
    flatpak = Path(home) / ".var/app/com.valvesoftware.Steam/data/Steam"
    if flatpak.is_dir():
        paths.append(flatpak / ".cef-enable-remote-debugging")
    return paths
