"""Curated Decky Loader plugins: a short pinned list that boppos-deck installs and updates with the controller.

The list is share/boppos/decky-plugins.json. Each entry pins one upstream release ZIP by URL, size and sha256.
decky-helper (root) only takes an entry's id from the caller; the URL, checksum and folder name come from the list
shipped in the image, so a user process cannot get anything else written into Decky's root-owned plugin folder.

A ZIP is checked before anything lands there: exactly the pinned size and sha256, then every member must be a
plain file or folder under one top-level folder (the pinned name) that has a plugin.json. Absolute paths, "..",
symlinks, hardlinks, device files and anything over the size cap are refused. A plugin that asks Decky for root
("root" in plugin.json "flags") is refused unless its entry says "allow_root": true.

Installing stages the plugin next to the plugins folder, then renames it in; the copy it replaces is kept in
plugin-backups/ (outside plugins/, which Decky scans) so `rollback` can put it back.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from .decky import DeckyError

PINS_FILE = "decky-plugins.json"
BACKUP_DIR = "plugin-backups"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_DIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_ENTRIES = 20000
MAX_ZIP_BYTES = 1 << 30
_CHUNK = 1 << 20
SOURCES = ("github", "store")
# Where Decky's own store serves a version's ZIP (decky-loader frontend/src/store.tsx pluginUrl)
STORE_API = "https://plugins.deckbrew.xyz/plugins"
STORE_CDN = "https://cdn.tzatzikiweeb.moe/file/steam-deck-homebrew/versions/{hash}.zip"


def default_pins_path() -> Path:
    """share/boppos next to lib/boppos/py: /usr/share/boppos when installed, the repo's share/ in a checkout."""
    return Path(__file__).resolve().parents[4] / "share/boppos" / PINS_FILE


def version_key(v: str) -> tuple:
    """Order "1.10.0" after "1.9.2", and a pre-release ("2.0.0-rc.1") before its release."""
    base, _, suffix = (v or "").partition("-")
    nums = tuple(int(x) if x.isdigit() else -1 for x in re.split(r"[.+_]", base))
    return nums, 0 if suffix else 1, suffix


def unpacked_cap(size: int) -> int:
    """Most bytes a ZIP of this size may unpack to (a ZIP bomb stops here)."""
    return max(4 * size, 64 << 20)


def _need(entry: dict, key: str, kind=str):
    val = entry.get(key)
    if not isinstance(val, kind) or isinstance(val, bool) and kind is not bool:
        raise DeckyError(f"plugin list: {entry.get('id', '?')}: '{key}' is missing or not a {kind.__name__}")
    return val


def parse_pin(entry: dict) -> dict:
    if not isinstance(entry, dict):
        raise DeckyError("plugin list: every entry must be an object")
    pid = _need(entry, "id")
    if not _ID_RE.match(pid):
        raise DeckyError(f"plugin list: bad id {pid!r}")
    pin = {
        "id": pid,
        "name": _need(entry, "name"),
        "repo": _need(entry, "repo"),
        "version": _need(entry, "version"),
        "url": _need(entry, "url"),
        "sha256": _need(entry, "sha256").lower(),
        "size": _need(entry, "size", int),
        "dir": _need(entry, "dir"),
        "note": entry.get("note", ""),
        "after_install": entry.get("after_install", ""),
        "source": entry.get("source", "github"),
        "store_name": entry.get("store_name", ""),
        "tag_prefix": entry.get("tag_prefix", "v"),
        "asset": entry.get("asset", ""),
        "allow_root": entry.get("allow_root", False) is True,
    }
    if not _REPO_RE.match(pin["repo"]):
        raise DeckyError(f"plugin list: {pid}: repo must be owner/name")
    if not _VERSION_RE.match(pin["version"]):
        raise DeckyError(f"plugin list: {pid}: bad version {pin['version']!r}")
    if not pin["url"].startswith("https://"):
        raise DeckyError(f"plugin list: {pid}: the download must be https")
    if not _SHA_RE.match(pin["sha256"]):
        raise DeckyError(f"plugin list: {pid}: sha256 must be 64 hex digits")
    if not 0 < pin["size"] <= MAX_ZIP_BYTES:
        raise DeckyError(f"plugin list: {pid}: size out of range")
    if not _DIR_RE.match(pin["dir"]) or pin["dir"] in (".", ".."):
        raise DeckyError(f"plugin list: {pid}: bad folder name {pin['dir']!r}")
    if pin["source"] not in SOURCES:
        raise DeckyError(f"plugin list: {pid}: source must be one of {', '.join(SOURCES)}")
    if pin["source"] == "store" and not pin["store_name"]:
        raise DeckyError(f"plugin list: {pid}: a store entry needs 'store_name'")
    for key in ("note", "after_install", "tag_prefix", "asset", "store_name"):
        if not isinstance(pin[key], str):
            raise DeckyError(f"plugin list: {pid}: '{key}' must be text")
    return pin


def load_pins(path: Path | None = None) -> list[dict]:
    path = path or default_pins_path()
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise DeckyError(f"cannot read the plugin list {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("plugins"), list):
        raise DeckyError(f"{path}: not a schema 1 plugin list")
    pins = [parse_pin(e) for e in data["plugins"]]
    for key in ("id", "dir"):
        seen = [p[key] for p in pins]
        if len(seen) != len(set(seen)):
            raise DeckyError(f"{path}: duplicate {key}")
    return pins


def find_pin(pid: str, pins: list[dict]) -> dict:
    if not isinstance(pid, str) or not _ID_RE.match(pid):
        raise DeckyError(f"not a plugin id: {pid!r}")
    for p in pins:
        if p["id"] == pid:
            return p
    raise DeckyError(f"{pid} is not in the BoppOS plugin list")


# --- Download ---------------------------------------------------------------------------------


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise DeckyError(f"refusing a redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(pin: dict, dest: Path, progress=None, opener=None) -> Path:
    """Fetch pin['url'] to dest (a new file), refusing anything that is not exactly the pinned size and sha256."""
    opener = opener or urllib.request.build_opener(_HttpsOnly)
    req = urllib.request.Request(pin["url"], headers={"User-Agent": "boppos-decky-plugins"})
    digest = hashlib.sha256()
    got = 0
    last = -1
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as out, opener.open(req, timeout=60) as resp:
        while chunk := resp.read(_CHUNK):
            got += len(chunk)
            if got > pin["size"]:
                raise DeckyError(f"the download is larger than the pinned {pin['size']} bytes; refused")
            digest.update(chunk)
            out.write(chunk)
            pct = got * 100 // pin["size"]
            if progress and pct != last:
                progress(pct)
                last = pct
    verify_file(dest, pin, digest.hexdigest(), got)
    return dest


def verify_file(path: Path, pin: dict, sha: str | None = None, size: int | None = None) -> None:
    if sha is None:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(_CHUNK):
                h.update(chunk)
        sha, size = h.hexdigest(), os.path.getsize(path)
    if size != pin["size"]:
        raise DeckyError(f"size mismatch: got {size} bytes, the pin says {pin['size']}; refused")
    if sha != pin["sha256"]:
        raise DeckyError(f"checksum mismatch: got sha256 {sha}, the pin says {pin['sha256']}; refused")


# --- Extract ----------------------------------------------------------------------------------


def _member_parts(info: zipfile.ZipInfo) -> tuple[str, ...]:
    name = info.filename
    if "\\" in name or "\x00" in name:
        raise DeckyError(f"refused: bad character in {name!r}")
    p = PurePosixPath(name)
    if p.is_absolute() or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise DeckyError(f"refused: absolute path {name!r}")
    parts = tuple(x for x in p.parts if x != ".")
    if not parts or any(x == ".." for x in parts):
        raise DeckyError(f"refused: path leaves the plugin folder: {name!r}")
    return parts


def _member_kind(info: zipfile.ZipInfo) -> str:
    mode = info.external_attr >> 16
    fmt = stat.S_IFMT(mode)
    if info.is_dir():
        if fmt not in (0, stat.S_IFDIR):
            raise DeckyError(f"refused: {info.filename!r} is not a plain folder")
        return "dir"
    if fmt not in (0, stat.S_IFREG):
        what = {stat.S_IFLNK: "a symlink", stat.S_IFCHR: "a device", stat.S_IFBLK: "a device",
                stat.S_IFIFO: "a FIFO", stat.S_IFSOCK: "a socket"}.get(fmt, "not a plain file")
        raise DeckyError(f"refused: {info.filename!r} is {what}")
    if info.flag_bits & 0x1:
        raise DeckyError(f"refused: {info.filename!r} is encrypted")
    return "file"


def extract_plugin(zip_path: Path, staging: Path, pin: dict) -> Path:
    """Unpack the checked ZIP into staging/<dir>. Returns that folder; refuses anything unexpected."""
    cap = unpacked_cap(pin["size"])
    total = 0
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise DeckyError(f"not a ZIP file: {exc}") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise DeckyError(f"refused: more than {MAX_ENTRIES} files")
        plan = []
        seen = set()
        tops = set()
        for info in infos:
            parts = _member_parts(info)
            kind = _member_kind(info)
            if parts in seen:
                raise DeckyError(f"refused: {info.filename!r} appears twice")
            seen.add(parts)
            tops.add(parts[0])
            if len(parts) == 1 and kind == "file":
                raise DeckyError(f"refused: file {parts[0]!r} outside the plugin folder")
            plan.append((info, parts, kind))
        if len(tops) != 1:
            raise DeckyError(f"refused: expected one top-level folder, found {sorted(tops)}")
        top = tops.pop()
        if top != pin["dir"]:
            raise DeckyError(f"refused: the plugin folder is {top!r}, the pin says {pin['dir']!r}")
        if (top, "plugin.json") not in seen:
            raise DeckyError("refused: no plugin.json in the plugin folder")
        # A file and a folder of the same path would make one of them land inside the other
        dirs = {parts[:i] for _, parts, _ in plan for i in range(1, len(parts))}
        for _, parts, kind in plan:
            if kind == "file" and parts in dirs:
                raise DeckyError(f"refused: {'/'.join(parts)!r} is both a file and a folder")

        root = staging / top
        root.mkdir(mode=0o755)
        for info, parts, kind in plan:
            target = staging.joinpath(*parts)
            for i in range(2, len(parts)):  # folders the ZIP did not list
                d = staging.joinpath(*parts[:i])
                if not d.is_dir():
                    d.mkdir(mode=0o755)
            if kind == "dir":
                if not target.is_dir():
                    target.mkdir(mode=0o755)
                continue
            mode = 0o755 if (info.external_attr >> 16) & 0o111 else 0o644
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            with os.fdopen(fd, "wb") as out, zf.open(info) as src:
                while chunk := src.read(_CHUNK):
                    total += len(chunk)  # counted as unpacked, so a lying header does not matter
                    if total > cap:
                        raise DeckyError(f"refused: unpacks to more than {cap} bytes")
                    out.write(chunk)
            os.chmod(target, mode)
        for d, _, _ in os.walk(root):
            os.chmod(d, 0o755)
    return root


def read_manifest(plugin_dir: Path) -> dict:
    try:
        data = json.loads((plugin_dir / "plugin.json").read_text())
    except (OSError, ValueError) as exc:
        raise DeckyError(f"cannot read plugin.json: {exc}") from exc
    if not isinstance(data, dict):
        raise DeckyError("plugin.json is not an object")
    return data


def plugin_version(plugin_dir: Path) -> str | None:
    """The version Decky shows: package.json "version" (plugin.json does not carry one)."""
    try:
        v = json.loads((plugin_dir / "package.json").read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return v if isinstance(v, str) else None


def check_plugin(plugin_dir: Path, pin: dict) -> dict:
    """Refuse a plugin that asks for root (unless the pin allows it) or that is not the pinned version."""
    manifest = read_manifest(plugin_dir)
    flags = manifest.get("flags", [])
    if not isinstance(flags, list):
        raise DeckyError("plugin.json 'flags' is not a list")
    if "root" in flags and not pin["allow_root"]:
        raise DeckyError(f"refused: {pin['name']} asks Decky to run it as root, and the BoppOS list does not "
                         "allow that")
    version = plugin_version(plugin_dir)
    if version is not None and version != pin["version"]:
        raise DeckyError(f"refused: the ZIP holds version {version}, the pin says {pin['version']}")
    return manifest


# --- Install / remove -------------------------------------------------------------------------


def _owned_dir(path: Path, create: bool = False, mode: int = 0o755) -> None:
    if create and not os.path.lexists(path):
        path.mkdir(mode=mode)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
        raise DeckyError(f"{path} must be a folder owned by {'root' if os.geteuid() == 0 else 'this user'}")


def _rmtree(path: Path) -> None:
    if os.path.lexists(path):
        if os.path.islink(path) or not os.path.isdir(path):
            os.unlink(path)
        else:
            shutil.rmtree(path)


def install_from_zip(zip_path: Path, state: Path, pin: dict, log=print) -> str | None:
    """Check and unpack zip_path, then swap it into state/plugins/<dir>. Returns the replaced version, if any."""
    _owned_dir(state)
    plugins = state / "plugins"
    _owned_dir(plugins, create=True)
    backups = state / BACKUP_DIR
    _owned_dir(backups, create=True)
    staging = Path(tempfile.mkdtemp(prefix=".plugin-staging-", dir=state))
    try:
        new = extract_plugin(zip_path, staging, pin)
        check_plugin(new, pin)
        target = plugins / pin["dir"]
        backup = backups / pin["dir"]
        old_version = None
        if os.path.lexists(target):
            old_version = plugin_version(target) if target.is_dir() and not target.is_symlink() else None
            _rmtree(backup)
            os.rename(target, backup)
        try:
            os.rename(new, target)
        except OSError:
            if os.path.lexists(backup) and not os.path.lexists(target):
                os.rename(backup, target)
            raise
        log(f"installed {pin['name']} {pin['version']} in {target}")
        if old_version is not None or os.path.lexists(backup):
            log(f"the previous copy ({old_version or 'unknown version'}) was kept in {backup}")
        return old_version
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def remove(state: Path, pin: dict, log=print) -> bool:
    """Move the plugin to plugin-backups/ (so it can be rolled back); False if it was not installed."""
    _owned_dir(state)
    target = state / "plugins" / pin["dir"]
    if not os.path.lexists(target):
        return False
    backups = state / BACKUP_DIR
    _owned_dir(backups, create=True)
    backup = backups / pin["dir"]
    _rmtree(backup)
    os.rename(target, backup)
    log(f"removed {pin['name']}; a copy was kept in {backup}")
    return True


def rollback(state: Path, pin: dict, log=print) -> str | None:
    """Put the kept copy back (swapping it with the current one). Returns the restored version."""
    _owned_dir(state)
    backup = state / BACKUP_DIR / pin["dir"]
    if not backup.is_dir() or backup.is_symlink():
        raise DeckyError(f"there is no earlier copy of {pin['name']} to go back to")
    target = state / "plugins" / pin["dir"]
    _owned_dir(state / "plugins", create=True)
    restored = plugin_version(backup)
    if os.path.lexists(target):
        swap = state / BACKUP_DIR / f".{pin['dir']}.swap"
        _rmtree(swap)
        os.rename(target, swap)
        os.rename(backup, target)
        os.rename(swap, backup)
    else:
        os.rename(backup, target)
    log(f"went back to {pin['name']} {restored or '(unknown version)'}")
    return restored


def status(pins: list[dict], state: Path) -> list[dict]:
    """Installed vs pinned for every entry (readable without root: the plugins folder is 0755)."""
    out = []
    for p in pins:
        d = state / "plugins" / p["dir"]
        present = d.is_dir() and not d.is_symlink()
        installed = plugin_version(d) if present else None
        if present and installed is None:
            installed = "unknown"
        if not present:
            state_name = "not installed"
        elif installed == p["version"]:
            state_name = "up to date"
        elif installed != "unknown" and version_key(installed) > version_key(p["version"]):
            state_name = "newer"  # updated by Decky's own store: never offer to go back
        else:
            state_name = "update available"
        out.append({
            "id": p["id"], "name": p["name"], "repo": p["repo"], "source": p["source"], "pinned": p["version"],
            "installed": installed,
            "state": state_name, "size": p["size"], "note": p["note"], "after_install": p["after_install"],
            "can_rollback": (state / BACKUP_DIR / p["dir"]).is_dir(),
        })
    return out
