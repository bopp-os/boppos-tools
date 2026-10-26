"""Decky Loader install logic (modelled on SteamDeckHomebrew/decky-installer cli/install_release.sh).

Differences from upstream: the download is verified against the sha256 digest GitHub publishes for the
asset, and the root helper writes a fixed unit template instead of fetching one from the main branch.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

RELEASES_URL = "https://api.github.com/repos/SteamDeckHomebrew/decky-loader/releases"
SERVICE_NAME = "plugin_loader.service"
UNIT_PATH = f"/etc/systemd/system/{SERVICE_NAME}"
_HOME_RE = re.compile(r"^/[A-Za-z0-9_./-]{1,200}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TAG_RE = re.compile(r"^v?[0-9A-Za-z._-]{1,40}$")


class DeckyError(Exception):
    pass


def asset_name(machine: str) -> str:
    return "PluginLoader-arm64" if machine in ("aarch64", "arm64") else "PluginLoader"


def pick_release(releases_json: str, machine: str = "x86_64") -> dict:
    """First non-prerelease release that has the loader asset with a sha256 digest."""
    try:
        releases = json.loads(releases_json)
    except json.JSONDecodeError as exc:
        raise DeckyError(f"unreadable release list: {exc}") from exc
    want = asset_name(machine)
    for rel in releases if isinstance(releases, list) else []:
        if rel.get("prerelease") or rel.get("draft"):
            continue
        for asset in rel.get("assets", []):
            if asset.get("name") != want:
                continue
            tag, digest, url = rel.get("tag_name", ""), asset.get("digest") or "", asset.get("browser_download_url", "")
            if not _TAG_RE.match(tag):
                raise DeckyError(f"unexpected release tag {tag!r}")
            if not _DIGEST_RE.match(digest):
                raise DeckyError(f"release {tag} has no sha256 digest for {want}; refusing unverified download")
            if not url.startswith("https://github.com/SteamDeckHomebrew/decky-loader/releases/download/"):
                raise DeckyError("unexpected download URL")
            return {"version": tag, "url": url, "sha256": digest.split(":", 1)[1], "size": asset.get("size")}
    raise DeckyError(f"no stable release with a {want} asset found")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_home(home: str) -> str:
    if not _HOME_RE.match(home) or ".." in home.split("/"):
        raise DeckyError(f"home directory not usable in a unit file: {home!r}")
    return home


def unit_text(home: str) -> str:
    home = validate_home(home)
    hb = f"{home}/homebrew"
    return f"""[Unit]
Description=SteamDeck Plugin Loader (installed by bopp-decky)
After=network.target

[Service]
Type=simple
User=root
Restart=always
KillMode=process
TimeoutStopSec=15
ExecStart={hb}/services/PluginLoader
WorkingDirectory={hb}/services
Environment=UNPRIVILEGED_PATH={hb}
Environment=PRIVILEGED_PATH={hb}
Environment=LOG_LEVEL=INFO

[Install]
WantedBy=multi-user.target
"""


def check_binary(path: Path, uid: int) -> None:
    """The root service will exec this file: it must be a plain file owned by the user and not be
    writable by anyone else, and so must the directories above it up to the home directory."""
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != uid or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise DeckyError(f"{path} must be a regular file owned by the user and not group/world writable")
    if not st.st_mode & stat.S_IXUSR:
        raise DeckyError(f"{path} is not executable")
    for parent in (path.parent, path.parent.parent):
        pst = os.lstat(parent)
        if not stat.S_ISDIR(pst.st_mode) or pst.st_uid != uid or pst.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise DeckyError(f"{parent} must be a directory owned by the user and not group/world writable")


def cef_flag_paths(home: str) -> list[Path]:
    paths = [Path(home) / ".steam/steam/.cef-enable-remote-debugging"]
    flatpak = Path(home) / ".var/app/com.valvesoftware.Steam/data/Steam"
    if flatpak.is_dir():
        paths.append(flatpak / ".cef-enable-remote-debugging")
    return paths
