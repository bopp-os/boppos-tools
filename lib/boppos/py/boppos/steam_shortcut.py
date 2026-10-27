"""Put a non-Steam shortcut into the user's Steam library (shortcuts.vdf), safely."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import zlib
from pathlib import Path

from . import vdf
from .paths import share_file
from .vdf import Int32, VdfError

APP_NAME = "BoppOS"
EXE = "/usr/bin/boppos-deck"
ICON = "/usr/share/icons/hicolor/256x256/apps/boppos-icon.png"
BACKUPS_KEEP = 3
# Steam's library artwork for a shortcut lives in <config>/grid, named after the shortcut's id
ART_NAMES = {"capsule.png": "{id}.png", "portrait.png": "{id}p.png", "hero.png": "{id}_hero.png",
             "logo.png": "{id}_logo.png"}


class SteamRunning(Exception):
    pass


def shortcut_appid(exe: str, name: str) -> int:
    """The id Steam derives for a shortcut (crc32 of exe+name with the top bit set), as int32."""
    value = (zlib.crc32((exe + name).encode()) | 0x80000000) & 0xFFFFFFFF
    return value - (1 << 32) if value >= 1 << 31 else value


def new_entry(exe: str = EXE, name: str = APP_NAME, icon: str = ICON, launch_options: str = "") -> dict:
    quoted = f'"{exe}"'
    return {
        "appid": Int32(shortcut_appid(quoted, name)),
        "AppName": name,
        "Exe": quoted,
        "StartDir": f'"{os.path.dirname(exe)}/"',
        "icon": icon,
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": Int32(0),
        "AllowDesktopConfig": Int32(1),
        "AllowOverlay": Int32(1),
        "OpenVR": Int32(0),
        "Devkit": Int32(0),
        "DevkitGameID": "",
        "DevkitOverrideAppID": Int32(0),
        "LastPlayTime": Int32(0),
        "FlatpakAppID": "",
        "tags": {},
    }


def is_boppos(exe: str, launch: str = "") -> bool:
    return exe.strip('"') == EXE and not launch


def _art_files(grid: Path, source: Path | None) -> list[tuple[Path, Path]]:
    src = source or share_file("steam-art")
    app_id = shortcut_appid(f'"{EXE}"', APP_NAME) & 0xFFFFFFFF
    return [(src / name, grid / pattern.format(id=app_id)) for name, pattern in ART_NAMES.items()
            if (src / name).is_file()]


def install_art(grid: Path, source: Path | None = None) -> int:
    """Copy BoppOS's Steam artwork into the grid folder. A file that is already there is left alone, so artwork
    you chose yourself is never replaced. Returns how many files were copied."""
    copied = 0
    for src, dest in _art_files(grid, source):
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
    return copied


def remove_art(grid: Path, source: Path | None = None) -> int:
    """Remove the artwork install_art put there. Only files identical to ours go: your own are kept."""
    removed = 0
    for src, dest in _art_files(grid, source):
        if dest.is_file() and dest.read_bytes() == src.read_bytes():
            dest.unlink()
            removed += 1
    return removed


def _shortcuts(data: dict) -> dict:
    sc = data.get("shortcuts")
    if sc is None:
        return {}
    if not isinstance(sc, dict):
        raise VdfError("'shortcuts' is not a map")
    return sc


def _matches(entry: object, exe: str, launch: str) -> bool:
    """A shortcut is identified by what it runs: the executable plus its launch options."""
    if not isinstance(entry, dict):
        return False
    have = str(entry.get("Exe", entry.get("exe", ""))).strip('"')
    return have == exe and str(entry.get("LaunchOptions", entry.get("launchoptions", ""))).strip() == launch.strip()


def has_shortcut(data: dict, exe: str = EXE, launch: str = "") -> bool:
    return any(_matches(e, exe, launch) for e in _shortcuts(data).values())


def remove_shortcuts(data: dict, targets: list[tuple[str, str]]) -> dict:
    """Return data without the shortcuts matching any (exe, launch options) pair; keys are renumbered."""
    kept = [e for e in _shortcuts(data).values() if not any(_matches(e, x, la) for x, la in targets)]
    out = dict(data)
    out["shortcuts"] = {str(i): e for i, e in enumerate(kept)}
    return out


def add_shortcut(data: dict, entry: dict) -> dict:
    """Return data with `entry` appended under the next numeric key (input is not modified)."""
    sc = dict(_shortcuts(data))
    nums = [int(k) for k in sc if k.isdigit()]
    sc[str(max(nums) + 1 if nums else 0)] = entry
    out = dict(data)
    out["shortcuts"] = sc
    return out


def steam_running(proc: str | Path = "/proc", uid: int | None = None) -> bool:
    uid = os.getuid() if uid is None else uid
    for d in Path(proc).glob("[0-9]*"):
        try:
            if (d / "comm").read_text().strip() != "steam":
                continue
            for line in (d / "status").read_text().splitlines():
                if line.startswith("Uid:") and int(line.split()[1]) == uid:
                    return True
        except (OSError, ValueError, IndexError):
            continue
    return False


def find_shortcut_files(home: Path) -> list[Path]:
    """shortcuts.vdf locations (existing or creatable) for native and Flatpak Steam, de-duplicated."""
    roots = [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    found: dict[Path, Path] = {}
    for root in roots:
        for cfg in sorted(root.glob("userdata/[1-9]*/config")):
            if cfg.is_dir():
                found.setdefault((cfg / "shortcuts.vdf").resolve(), cfg / "shortcuts.vdf")
    return list(found.values())


def _backup(path: Path, stamp: str) -> Path | None:
    if not path.exists():
        return None
    dest = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, dest)
    for old in sorted(path.parent.glob(f"{path.name}.bak-*"))[:-BACKUPS_KEEP]:
        old.unlink(missing_ok=True)
    return dest


def apply_changes(path: Path, adds: list[dict], removes: list[tuple[str, str]], stamp: str | None = None) -> dict:
    """Add and remove shortcuts in one shortcuts.vdf with a single backup and one atomic write.

    Returns {"added": n, "removed": n}; nothing is written when there is nothing to do. Raises (file
    untouched) when the file cannot be parsed or the result fails verification.
    """
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    raw = path.read_bytes() if path.exists() else vdf.dumps({"shortcuts": {}})
    data = vdf.loads(raw)
    before = len(_shortcuts(data))
    new_data = remove_shortcuts(data, removes) if removes else data
    removed = before - len(_shortcuts(new_data)) if removes else 0
    added = 0
    for entry in adds:
        if not has_shortcut(new_data, entry["Exe"].strip('"'), entry.get("LaunchOptions", "")):
            new_data = add_shortcut(new_data, entry)
            added += 1
    if any(is_boppos(e["Exe"], e.get("LaunchOptions", "")) for e in adds):
        install_art(path.parent / "grid")  # also when the shortcut already exists: older installs get artwork
    if not added and not removed:
        return {"added": 0, "removed": 0}
    new = vdf.dumps(new_data)
    check = vdf.loads(new)  # must parse back with exactly the expected number of shortcuts
    if len(_shortcuts(check)) != before + added - removed:
        raise VdfError("verification failed: unexpected number of shortcuts")
    _backup(path, stamp)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".shortcuts.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(new)
            fh.flush()
            os.fsync(fh.fileno())
        if vdf.loads(Path(tmp).read_bytes()) != check:
            raise VdfError("verification failed: written file differs")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    if removed and any(is_boppos(exe, launch) for exe, launch in removes):
        remove_art(path.parent / "grid")
    return {"added": added, "removed": removed}


def install_into(path: Path, entry: dict, stamp: str | None = None) -> str:
    """Add `entry` to one shortcuts.vdf. Returns "added" or "present"; raises (file untouched) on trouble."""
    return "added" if apply_changes(path, [entry], [], stamp)["added"] else "present"
