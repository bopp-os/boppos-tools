"""Download the Steam client into ~/.local/share/Steam before Steam's first start. GTK-free, so it can be tested.

Game Mode on the deck image starts Steam only after `boppos-deck` exits (docs/deck-first-boot.md), and a fresh
account's first Steam start downloads the client (~450 MB) behind a black screen. The first-boot tour does that
download itself, with progress, so Steam finds its client in place.

The method is the one of the installer's preload (bopp-os/fisherman, internal/post/steam.go, PreloadSteam), ported
step for step so the two never differ:

1. extract the bootstrap the `steam` package ships (/usr/lib/steam/bootstraplinux_ubuntu12_32.tar.xz) into the
   Steam folder (created 0700, as Steam's launcher does), unless the folder already has it;
2. download Valve's client manifest (https://client-update.steamstatic.com/steam_client_ubuntu12) and store it as
   package/steam_client_ubuntu12.manifest;
3. download every package it names into package/ under the name Steam uses (the ".zip.vz.<sha>_<size>" form when
   the manifest has one, else the plain zip), each checked against the manifest's size and SHA-256 before it takes
   its final name. A package already there with the right size and SHA-256 (the installer's preload, or an earlier
   try) is not downloaded again.

One step more than the installer: 4. unpack the packages into the Steam folder, so ubuntu12_32/steamui.so exists,
which is what the image's first-boot check looks for. steamui.so is written last, and only when every package
unpacked, so a failed or cancelled run never leaves a steamui.so that would make the check think Steam is ready.
Like the installer, no ".installed" marker is written: Steam still installs the packages on its first start (it
then only extracts, no download) and sets its own file modes.

Everything runs as the logged-in user, so every file belongs to them.
"""

from __future__ import annotations

import hashlib
import lzma
import os
import re
import shutil
import struct
import tarfile
import threading
import urllib.error
import urllib.request
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

CDN = "https://client-update.steamstatic.com"
MANIFEST_NAME = "steam_client_ubuntu12"
BOOTSTRAP = "/usr/lib/steam/bootstraplinux_ubuntu12_32.tar.xz"
STEAMUI = ("ubuntu12_64/steamui.so", "ubuntu12_32/steamui.so")  # what the image's first-boot check looks for
MANIFEST_LIMIT = 1 << 20
TIMEOUT = 30  # seconds without data before a request gives up
CHUNK = 1 << 16

_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

Emit = Callable[[dict], None]


class SteamError(Exception):
    pass


class Cancelled(SteamError):
    pass


class ChecksumError(SteamError):
    pass


def steam_dir(home: str | Path | None = None) -> Path:
    return Path(home if home is not None else Path.home()) / ".local/share/Steam"


def installed(home: str | Path | None = None) -> bool:
    """The image's own test: Steam's UI library is there, so Steam can start without downloading its client."""
    d = steam_dir(home)
    return any((d / rel).is_file() for rel in STEAMUI)


def bootstrap_available(root: str | Path = "/") -> bool:
    return (Path(root) / BOOTSTRAP.lstrip("/")).is_file()


# --- the manifest (Valve KeyValues text) -----------------------------------------------------------------------


@dataclass(frozen=True)
class Package:
    name: str  # the file name Steam keeps in package/ and the name on the CDN
    size: int
    sha256: str
    vz: bool  # Valve's LZMA wrapper around the zip


@dataclass(frozen=True)
class Manifest:
    version: str
    packages: list[Package]

    @property
    def total(self) -> int:
        return sum(p.size for p in self.packages)


def _tokens(text: str) -> list[tuple[str, str]]:
    toks: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif c in "{}":
            toks.append((c, ""))
            i += 1
        elif c == '"':
            out: list[str] = []
            i += 1
            while True:
                if i >= n:
                    raise SteamError("unterminated string in the manifest")
                if text[i] == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                out.append(text[i])
                i += 1
            toks.append(("s", "".join(out)))
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n{}"':
                j += 1
            toks.append(("s", text[i:j]))
            i = j
    return toks


def parse_keyvalues(text: str) -> list[tuple[str, object]]:
    """Valve's KeyValues text: a list of (key, value) where value is a str or another such list."""
    toks = _tokens(text)
    pos = 0

    def block(depth: int) -> list[tuple[str, object]]:
        nonlocal pos
        nodes: list[tuple[str, object]] = []
        while pos < len(toks):
            kind, key = toks[pos]
            if kind == "}":
                if depth == 0:
                    raise SteamError("unexpected } in the manifest")
                pos += 1
                return nodes
            if kind != "s":
                raise SteamError(f"unexpected {kind!r} in the manifest")
            pos += 1
            if pos >= len(toks):
                raise SteamError(f"key {key!r} has no value")
            vkind, value = toks[pos]
            pos += 1
            if vkind == "s":
                nodes.append((key, value))
            elif vkind == "{":
                nodes.append((key, block(depth + 1)))
            else:
                raise SteamError(f"key {key!r} has no value")
        if depth:
            raise SteamError("the manifest ends inside a block")
        return nodes

    return block(0)


def parse_manifest(data: bytes) -> Manifest:
    """The version and the package files of the client manifest. The compressed form (zipvz, sha2vz, the size from
    the name's "_<size>" suffix) is preferred, as Steam does; a package with only a plain zip uses file/size/sha2."""
    root = parse_keyvalues(data.decode("utf-8", "replace"))
    body = next((v for k, v in root if k == "ubuntu12" and isinstance(v, list)), None)
    if body is None:
        raise SteamError('the manifest has no "ubuntu12" section')
    version, packages = "", []
    for key, value in body:
        if isinstance(value, str):
            if key == "version":
                version = value
            continue
        fields = {k: v for k, v in value if isinstance(v, str)}
        vz, sha_vz = fields.get("zipvz", ""), fields.get("sha2vz", "")
        try:
            if vz and sha_vz:
                head, sep, size = vz.rpartition("_")
                if not sep:
                    raise SteamError(f"package {key}: zipvz name {vz!r} has no size suffix")
                p = Package(vz, int(size), sha_vz.lower(), True)
            elif fields.get("file"):
                p = Package(fields["file"], int(fields.get("size", "")), fields.get("sha2", "").lower(), False)
            else:
                continue
        except ValueError as exc:
            raise SteamError(f"package {key}: bad size") from exc
        if not _PACKAGE_NAME.fullmatch(p.name) or ".." in p.name:
            raise SteamError(f"package {key}: unsafe file name {p.name!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", p.sha256):
            raise SteamError(f"package {key}: no SHA-256")
        if p.size <= 0:
            raise SteamError(f"package {key}: size {p.size}")
        packages.append(p)
    if not version:
        raise SteamError("the manifest has no version")
    if not packages:
        raise SteamError("the manifest lists no packages")
    return Manifest(version, packages)


# --- downloading ------------------------------------------------------------------------------------------------


def _open(url: str, timeout: float):
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)  # noqa: S310 - a fixed https host, or a test server
    except urllib.error.HTTPError as exc:
        raise SteamError(f"HTTP {exc.code} for {url.rsplit('/', 1)[-1]}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise SteamError(f"cannot reach the Steam servers ({getattr(exc, 'reason', exc)})") from exc
    return resp


def fetch_manifest(cdn: str = CDN, timeout: float = TIMEOUT) -> bytes:
    with _open(f"{cdn.rstrip('/')}/{MANIFEST_NAME}", timeout) as resp:
        data = resp.read(MANIFEST_LIMIT + 1)
    if len(data) > MANIFEST_LIMIT:
        raise SteamError("the manifest is larger than expected")
    return data


def _sha256_of(path: Path, cancel: threading.Event | None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            if cancel is not None and cancel.is_set():
                raise Cancelled("cancelled")
            h.update(chunk)
    return h.hexdigest()


def stored_ok(pkg_dir: Path, p: Package, cancel: threading.Event | None = None) -> bool:
    """The package is already in package/ with the manifest's size and SHA-256."""
    path = pkg_dir / p.name
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size != p.size:
            return False
    except OSError:
        return False
    return _sha256_of(path, cancel) == p.sha256


def download_package(pkg_dir: Path, p: Package, cdn: str, on_bytes: Callable[[int], None],
                     cancel: threading.Event | None = None, timeout: float = TIMEOUT) -> None:
    """One package into pkg_dir, through a ".part" file that only takes the final name once its size and SHA-256
    match. A failed or cancelled download leaves nothing behind."""
    final = pkg_dir / p.name
    tmp = pkg_dir / (p.name + ".part")
    h = hashlib.sha256()
    got = 0
    try:
        with _open(f"{cdn.rstrip('/')}/{p.name}", timeout) as resp, tmp.open("wb") as fh:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled("cancelled")
                chunk = resp.read(min(CHUNK, p.size + 1 - got))
                if not chunk:
                    break
                fh.write(chunk)
                h.update(chunk)
                got += len(chunk)
                on_bytes(len(chunk))
                if got > p.size:
                    break
        if got != p.size:
            raise ChecksumError(f"{p.name}: got {got} bytes, want {p.size}")
        if h.hexdigest() != p.sha256:
            raise ChecksumError(f"{p.name}: SHA-256 differs from the manifest")
        os.replace(tmp, final)
    except (OSError, TimeoutError) as exc:
        tmp.unlink(missing_ok=True)
        raise SteamError(f"downloading {p.name} failed: {exc}") from exc
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# --- unpacking --------------------------------------------------------------------------------------------------


def unvz(src: Path, dst: Path, cancel: threading.Event | None = None) -> None:
    """Valve's VZ file to the zip inside it: "VZa", 4 bytes of time stamp, 5 bytes of LZMA properties, a raw LZMA
    stream, then the CRC-32 and size of the result and "zv"."""
    with src.open("rb") as fh:
        head = fh.read(12)
        fh.seek(-10, os.SEEK_END)
        tail = fh.read(10)
        end = fh.tell() - 10
        if len(head) != 12 or head[:3] != b"VZa" or tail[-2:] != b"zv":
            raise SteamError(f"{src.name} is not a VZ file")
        crc_want, size = struct.unpack("<II", tail[:8])
        try:
            filt = lzma._decode_filter_properties(lzma.FILTER_LZMA1, head[7:12])  # noqa: SLF001 - no public API
            dec = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[filt])
        except (lzma.LZMAError, ValueError) as exc:
            raise SteamError(f"{src.name}: bad LZMA header") from exc
        fh.seek(12)
        crc, written, left = 0, 0, end - 12
        with dst.open("wb") as out:
            while left > 0:
                if cancel is not None and cancel.is_set():
                    raise Cancelled("cancelled")
                chunk = fh.read(min(1 << 20, left))
                if not chunk:
                    break
                left -= len(chunk)
                try:
                    data = dec.decompress(chunk)
                except lzma.LZMAError as exc:
                    raise SteamError(f"{src.name}: {exc}") from exc
                data = data[: max(0, size - written)]
                out.write(data)
                crc = zlib.crc32(data, crc)
                written += len(data)
        if written != size or crc != crc_want:
            raise ChecksumError(f"{src.name}: the unpacked data does not match its checksum")


def _mode_for(name: str) -> int:
    """Valve's zips store 0666 for everything. Steam sets its own modes when it installs; until then, executables
    (no extension, or a .sh script) get 0755 and the rest 0644."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return 0o755 if base.endswith(".sh") or "." not in base else 0o644


def _member_path(dest: Path, name: str) -> Path | None:
    """Where a zip member goes, or None when its name would leave the Steam folder. Some of Valve's zips were made on
    Windows and separate with backslashes (tenfoot\\resource\\images/), so both count as separators."""
    name = name.replace("\\", "/")
    if not name or name.startswith("/") or "\x00" in name or re.match(r"^[A-Za-z]:", name):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return dest.joinpath(*parts)


def _write_member(z: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.boppos-tmp")
    try:
        with z.open(info) as src, tmp.open("wb") as out:
            shutil.copyfileobj(src, out, CHUNK)
        tmp.chmod(_mode_for(info.filename))
        if target.is_symlink() or target.is_dir():
            raise SteamError(f"{info.filename}: something else is in the way")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def is_steamui(name: str) -> bool:
    return name.replace("\\", "/").rsplit("/", 1)[-1] == "steamui.so"


def unpack_package(pkg_file: Path, p: Package, dest: Path, scratch: Path,
                   cancel: threading.Event | None = None) -> list[tuple[Path, str]]:
    """Unpack one package into dest, except steamui.so: returns (zip, member) pairs for those, to be written last.
    The zip of a VZ package goes to scratch, which the caller removes."""
    zpath = pkg_file
    if p.vz:
        zpath = scratch / (p.name + ".zip")
        unvz(pkg_file, zpath, cancel)
    later: list[tuple[Path, str]] = []
    try:
        with zipfile.ZipFile(zpath) as z:
            for info in z.infolist():
                if cancel is not None and cancel.is_set():
                    raise Cancelled("cancelled")
                target = _member_path(dest, info.filename)
                if target is None:
                    raise SteamError(f"{p.name}: unsafe path {info.filename!r}")
                if info.is_dir() or info.filename.endswith("\\"):
                    target.mkdir(parents=True, exist_ok=True)
                elif is_steamui(info.filename):
                    later.append((zpath, info.filename))
                else:
                    _write_member(z, info, target)
    except zipfile.BadZipFile as exc:
        raise SteamError(f"{p.name}: not a zip file") from exc
    return later


def extract_bootstrap(bootstrap: Path, dest: Path) -> None:
    with tarfile.open(bootstrap, "r:*") as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="data")
        else:  # pragma: no cover - Python without extraction filters
            for m in tar.getmembers():
                if m.name.startswith("/") or ".." in Path(m.name).parts or m.issym() or m.islnk():
                    raise SteamError(f"unsafe path in the bootstrap: {m.name}")
            tar.extractall(dest)  # noqa: S202 - checked above


# --- the whole run ----------------------------------------------------------------------------------------------


def install(home: str | Path | None = None, emit: Emit = lambda _e: None, cancel: threading.Event | None = None,
            cdn: str = CDN, bootstrap: str | Path = BOOTSTRAP, timeout: float = TIMEOUT) -> str:
    """Download and unpack the Steam client; returns the client version. Raises SteamError (Cancelled when
    `cancel` was set). emit() gets {"phase": "manifest" | "check" | "download" | "unpack" | "done", ...} with
    "done"/"total" bytes for the download and "index"/"count" packages for checking and unpacking."""
    dest = steam_dir(home)
    if installed(home):
        emit({"phase": "done", "already": True})
        return ""
    bootstrap = Path(bootstrap)
    if not bootstrap.is_file():
        raise SteamError(f"this system has no Steam bootstrap ({bootstrap}); is the steam package installed?")

    emit({"phase": "manifest"})
    data = fetch_manifest(cdn, timeout)  # offline: nothing is written to the home at all
    manifest = parse_manifest(data)

    new_dir = not dest.exists()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.mkdir(mode=0o700, exist_ok=True)
    pkg_dir = dest / "package"
    stored = 0
    try:
        if not (dest / "steam.sh").exists():
            extract_bootstrap(bootstrap, dest)
        pkg_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = pkg_dir / f".{MANIFEST_NAME}.manifest.part"
        tmp.write_bytes(data)
        os.replace(tmp, pkg_dir / f"{MANIFEST_NAME}.manifest")

        count, total = len(manifest.packages), manifest.total
        todo: list[Package] = []
        done_bytes = 0
        for i, p in enumerate(manifest.packages):
            emit({"phase": "check", "index": i + 1, "count": count, "package": p.name})
            if stored_ok(pkg_dir, p, cancel):
                done_bytes += p.size
                stored += 1
            else:
                todo.append(p)

        progress = {"done": done_bytes}

        def on_bytes(n: int) -> None:
            progress["done"] += n
            emit({"phase": "download", "done": progress["done"], "total": total})

        emit({"phase": "download", "done": done_bytes, "total": total, "version": manifest.version})
        for p in todo:
            if cancel is not None and cancel.is_set():
                raise Cancelled("cancelled")
            download_package(pkg_dir, p, cdn, on_bytes, cancel, timeout)
            stored += 1

        scratch = pkg_dir / ".boppos-unpack"
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(mode=0o700)
        try:
            later: list[tuple[Path, str]] = []
            for i, p in enumerate(manifest.packages):
                emit({"phase": "unpack", "index": i + 1, "count": count, "package": p.name})
                later += unpack_package(pkg_dir / p.name, p, dest, scratch, cancel)
            # Last of all, once everything else is in place: the file the image's check looks for.
            for zpath, member in later:
                target = _member_path(dest, member)
                with zipfile.ZipFile(zpath) as z:
                    _write_member(z, z.getinfo(member), target)  # type: ignore[arg-type]
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        if not installed(home):
            raise SteamError("the client packages did not contain steamui.so")
    except BaseException:
        if stored == 0 and new_dir:
            shutil.rmtree(dest, ignore_errors=True)  # as if nothing happened, like the installer's preload
        raise
    emit({"phase": "done", "version": manifest.version})
    return manifest.version


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} GB"
