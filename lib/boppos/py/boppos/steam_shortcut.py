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
EPOCH_DATED = 86400 * 365  # an mtime before 1971: copied from an image's /usr with its date kept
# Steam's library artwork for a shortcut lives in <config>/grid, named after the shortcut's id
ART_NAMES = {"capsule.png": "{id}.png", "portrait.png": "{id}p.png", "hero.png": "{id}_hero.png",
             "logo.png": "{id}_logo.png", "icon.jpg": "{id}_icon.jpg"}


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


def grid_icon(grid: Path, app_id: int) -> Path:
    """Where the library sidebar's icon is kept. Steam draws a JPEG there; PNG icons stayed blank."""
    return grid / f"{app_id & 0xFFFFFFFF}_icon.jpg"


def make_jpeg_icon(src: Path, dest: Path, background: tuple[int, int, int] = (32, 34, 40)) -> bool:
    """Convert an icon to a JPEG, flattening transparency onto `background`. False when Pillow is missing or
    the image cannot be read (the shortcut then keeps its original icon)."""
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        with Image.open(src) as im:
            rgba = im.convert("RGBA")
            flat = Image.new("RGBA", rgba.size, (*background, 255))
            flat.alpha_composite(rgba)
            dest.parent.mkdir(parents=True, exist_ok=True)
            flat.convert("RGB").save(dest, "JPEG", quality=92)
    except (OSError, ValueError):
        return False
    return True


def with_grid_icon(entry: dict, grid: Path) -> dict:
    """`entry` with its icon pointing at a JPEG in the grid folder (made from the entry's own icon, or BoppOS's
    shipped one). The entry is returned unchanged when no JPEG can be made."""
    app_id = shortcut_appid(entry["Exe"], entry["AppName"])
    dest = grid_icon(grid, app_id)
    if not dest.exists() and not is_boppos(entry["Exe"], entry.get("LaunchOptions", "")):
        icon = entry.get("icon", "")
        if icon and Path(icon).is_file() and not str(icon).lower().endswith(".jpg"):
            make_jpeg_icon(Path(icon), dest)
    if not dest.exists():
        return entry
    return {**entry, "icon": str(dest)}


# Where Steam draws a shortcut's logo over its hero. Steam keeps this in <grid>/<id>.json; a shortcut without one
# can show a blank logo (decky-steamgriddb writes one for the same reason).
LOGO_POSITION = b'{"nVersion":1,"logoPosition":{"pinnedPosition":"BottomLeft","nWidthPct":50,"nHeightPct":50}}'


def install_logo_position(grid: Path, app_id: int) -> bool:
    """Write our logo position next to a logo, unless one is there already (yours, or Steam's)."""
    dest = grid / f"{app_id & 0xFFFFFFFF}.json"
    if dest.exists() or not (grid / f"{app_id & 0xFFFFFFFF}_logo.png").exists():
        return False
    dest.write_bytes(LOGO_POSITION)
    return True


def remove_logo_position(grid: Path, app_id: int) -> bool:
    """Remove the logo position install_logo_position wrote; one you moved in Steam differs and stays."""
    dest = grid / f"{app_id & 0xFFFFFFFF}.json"
    if dest.is_file() and dest.read_bytes() == LOGO_POSITION:
        dest.unlink()
        return True
    return False


APP_ART_TAG = "boppos-deck"  # PNG text chunk on the artwork we render, so removal never takes art you put there
APP_ART = ("{id}.png", "{id}p.png", "{id}_hero.png", "{id}_logo.png")


def _art_font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)  # scalable when Pillow has FreeType (10.1+)
    except (TypeError, OSError):
        return None


def _backdrop(size: tuple[int, int], tint: tuple[int, int, int]):
    """A vertical gradient from a dark shade of `tint` (top) to a darker one (bottom)."""
    from PIL import Image
    top = tuple(int(c * 0.55) for c in tint)
    bottom = tuple(int(c * 0.22) for c in tint)
    column = Image.new("RGB", (1, 256))
    for y in range(256):
        t = y / 255
        column.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))
    return column.resize(size, Image.Resampling.BILINEAR).convert("RGBA")


def _tint(icon) -> tuple[int, int, int]:
    """The icon's average colour over its visible pixels, brightened so dark icons still get a coloured backdrop."""
    from PIL import Image
    small = icon.resize((32, 32), Image.Resampling.BILINEAR)
    px = [p for p in (small.getpixel((x, y)) for x in range(32) for y in range(32)) if p[3] > 128]
    if not px:
        return (90, 100, 120)
    avg = [sum(p[i] for p in px) / len(px) for i in range(3)]
    peak = max(avg) or 1
    scale = max(1.0, 160 / peak)
    return tuple(min(255, int(c * scale)) for c in avg)


def _draw_name(img, name: str, center_x: int, top: int, size: int, max_width: int) -> None:
    from PIL import ImageDraw
    font = _art_font(size)
    if font is None:
        return
    draw = ImageDraw.Draw(img)
    lines, line = [], ""
    for word in name.split():
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=font) > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    lines.append(line)
    for i, text in enumerate(lines[:2]):
        draw.text((center_x, top + i * int(size * 1.25)), text, font=font, fill=(255, 255, 255, 235), anchor="ma")


def render_app_art(icon: str, name: str) -> dict[str, object]:
    """Library artwork for a shortcut made from its icon: capsule, portrait, hero and logo (Pillow images, keyed
    like APP_ART). The logo is left out when there is no icon (Steam then shows the name). {} without Pillow."""
    try:
        from PIL import Image
    except ImportError:
        return {}
    src = None
    if icon and Path(icon).is_file():
        try:
            with Image.open(icon) as im:
                src = im.convert("RGBA")
        except (OSError, ValueError):
            src = None
    tint = _tint(src) if src is not None else (90, 100, 120)

    def place(img, side: int, cx: int, cy: int, alpha: float = 1.0) -> None:
        if src is None:
            return
        mark = src.resize((side, side), Image.Resampling.LANCZOS)
        if alpha < 1:
            mark.putalpha(mark.getchannel("A").point(lambda a: int(a * alpha)))
        img.alpha_composite(mark, (cx - side // 2, cy - side // 2))

    capsule = _backdrop((920, 430), tint)
    place(capsule, 220, 460, 165 if src is not None else 215)
    _draw_name(capsule, name, 460, 300 if src is not None else 185, 46, 840)
    portrait = _backdrop((600, 900), tint)
    place(portrait, 300, 300, 370)
    _draw_name(portrait, name, 300, 580 if src is not None else 400, 54, 540)
    hero = _backdrop((1920, 620), tint)
    place(hero, 900, 1500, 310, alpha=0.18)  # faint: the logo is drawn over the hero's left side
    art = {"{id}.png": capsule, "{id}p.png": portrait, "{id}_hero.png": hero}
    if src is not None:
        logo = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        place(logo, 400, 200, 200)
        art["{id}_logo.png"] = logo
    return art


def _is_ours(path: Path) -> bool:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.format == "PNG" and im.text.get("Software") == APP_ART_TAG
    except (ImportError, OSError, ValueError):
        return False


def install_app_art(grid: Path, entry: dict, app_id: int | None = None) -> int:
    """Render library artwork for a (non-BoppOS) shortcut from its icon into the grid folder. A file that is
    already there is left alone, so artwork you chose (or Steam fetched) is kept. Returns how many were written."""
    app_id = (shortcut_appid(entry["Exe"], entry["AppName"]) if app_id is None else app_id) & 0xFFFFFFFF
    todo = [p for p in APP_ART if not (grid / p.format(id=app_id)).exists()]
    if not todo:
        install_logo_position(grid, app_id)  # older installs had the logo without it
        return 0
    try:
        from PIL.PngImagePlugin import PngInfo
    except ImportError:
        return 0
    art = render_app_art(str(entry.get("icon", "")), entry["AppName"])
    info = PngInfo()
    info.add_text("Software", APP_ART_TAG)
    written = 0
    for pattern in todo:
        if pattern in art:
            grid.mkdir(parents=True, exist_ok=True)
            art[pattern].save(grid / pattern.format(id=app_id), "PNG", pnginfo=info, optimize=True)
            written += 1
    install_logo_position(grid, app_id)
    return written


def remove_app_art(grid: Path, app_id: int) -> int:
    """Remove the artwork install_app_art rendered for `app_id`; files without our tag are yours and stay."""
    removed = 0
    for pattern in APP_ART:
        path = grid / pattern.format(id=app_id & 0xFFFFFFFF)
        if path.is_file() and _is_ours(path):
            path.unlink()
            removed += 1
    remove_logo_position(grid, app_id)
    return removed


def _boppos_id(app_id: int | None) -> int:
    return (shortcut_appid(f'"{EXE}"', APP_NAME) if app_id is None else app_id) & 0xFFFFFFFF


def _art_files(grid: Path, source: Path | None, app_id: int | None = None) -> list[tuple[Path, Path]]:
    src = source or share_file("steam-art")
    app_id = _boppos_id(app_id)
    return [(src / name, grid / pattern.format(id=app_id)) for name, pattern in ART_NAMES.items()
            if (src / name).is_file()]


def install_art(grid: Path, source: Path | None = None, refresh: bool = False, app_id: int | None = None) -> int:
    """Copy BoppOS's Steam artwork into the grid folder. A file that is already there is left alone, so artwork
    you chose yourself is never replaced; with `refresh`, our own (tagged) files are brought up to date too.
    Returns how many files were copied."""
    copied = 0
    for src, dest in _art_files(grid, source, app_id):
        stale = refresh and dest.is_file() and _is_ours(dest) and dest.read_bytes() != src.read_bytes()
        if not dest.exists() or stale:
            dest.parent.mkdir(parents=True, exist_ok=True)
            # copyfile, not copy2: files in an image's /usr are dated 1970 (mtime 0), and Steam did not show art
            # copied with that date. A fresh copy gets the current time.
            shutil.copyfile(src, dest)
            copied += 1
        elif dest.is_file() and dest.stat().st_mtime < EPOCH_DATED:
            os.utime(dest)  # an older release copied it with the 1970 date
            copied += 1
    install_logo_position(grid, _boppos_id(app_id))
    return copied


def remove_art(grid: Path, source: Path | None = None, app_id: int | None = None) -> int:
    """Remove the artwork install_art put there. Only files identical to ours go: your own are kept."""
    removed = 0
    for src, dest in _art_files(grid, source, app_id):
        if dest.is_file() and dest.read_bytes() == src.read_bytes():
            dest.unlink()
            removed += 1
    remove_logo_position(grid, _boppos_id(app_id))
    return removed


def _entry_appid(e: dict) -> int:
    """The id Steam files a shortcut's artwork under: the stored appid (it survives renaming in Steam), else the
    one derived from exe and name."""
    stored = e.get("appid")
    if isinstance(stored, int) and stored:
        return int(stored)
    return shortcut_appid(str(e.get("Exe", e.get("exe", ""))), str(e.get("AppName", e.get("appname", ""))))


def refresh_art(path: Path, sources: dict[tuple[str, str], str]) -> int:
    """Bring the artwork of shortcuts already in one library up to date: missing files are made, our own are
    re-rendered, files you replaced are kept. `sources` maps (exe, launch options) to the icon to render from;
    other shortcuts are not touched. Only the grid folder changes, so Steam may be running (it shows the new art
    the next time it starts). Returns how many files were written."""
    if not path.exists():
        return 0
    grid = path.parent / "grid"
    written = 0
    for e in _shortcuts(vdf.loads(path.read_bytes())).values():
        identity = next((k for k in sources if _matches(e, *k)), None)
        if identity is None:
            continue
        app_id = _entry_appid(e)  # Steam may have given the shortcut an id of its own: the art goes by that one
        if is_boppos(*identity):
            written += install_art(grid, refresh=True, app_id=app_id)
            continue
        remove_app_art(grid, app_id)
        name = str(e.get("AppName", e.get("appname", ""))) or identity[1]
        written += install_app_art(grid, {"AppName": name, "icon": sources[identity]}, app_id)
        icon = grid_icon(grid, app_id)  # the sidebar icon the shortcut points at, if it went missing
        if str(e.get("icon", "")) == str(icon) and not icon.exists() and sources[identity]:
            written += make_jpeg_icon(Path(sources[identity]), icon)
    return written


def _repair_icon(data: dict, entry: dict, exe: str, launch: str) -> int:
    """Point an existing shortcut with no icon, or a PNG one, at the JPEG icon. An icon you chose is left alone."""
    new = str(entry.get("icon", ""))
    if not new.lower().endswith(".jpg"):
        return 0
    n = 0
    for e in _shortcuts(data).values():
        old = str(e.get("icon", "")) if isinstance(e, dict) else ""
        if _matches(e, exe, launch) and old != new and (not old or old.lower().endswith(".png")):
            e["icon"] = new
            n += 1
    return n


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
    if have != exe and os.path.realpath(have) != os.path.realpath(exe):  # /bin/flatpak is /usr/bin/flatpak
        return False
    return str(entry.get("LaunchOptions", entry.get("launchoptions", ""))).strip() == launch.strip()


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
    grid = path.parent / "grid"
    gone = [e for e in _shortcuts(data).values() if any(_matches(e, x, la) for x, la in removes)]
    added = updated = 0
    for entry in adds:
        exe, launch = entry["Exe"].strip('"'), entry.get("LaunchOptions", "")
        # Artwork goes by the id the library has for the shortcut: ours for a new one, and whatever Steam gave an
        # existing one (Steam can replace the id we wrote with one of its own). Also for shortcuts that exist, so
        # older installs get artwork. Before with_grid_icon, which looks for BoppOS's shipped JPEG icon.
        stored = next((e for e in _shortcuts(new_data).values() if _matches(e, exe, launch)), entry)
        if is_boppos(exe, launch):
            install_art(grid, app_id=_entry_appid(stored))
        else:
            install_app_art(grid, entry, _entry_appid(stored))  # from the original icon, not the JPEG
        entry = with_grid_icon(entry, grid)
        if not has_shortcut(new_data, exe, launch):
            new_data = add_shortcut(new_data, entry)
            added += 1
        else:
            updated += _repair_icon(new_data, entry, exe, launch)
    if not added and not removed and not updated:
        return {"added": 0, "removed": 0, "updated": 0}
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
    for e in gone:  # icons and artwork we made for the removed shortcuts
        exe = str(e.get("Exe", e.get("exe", "")))
        if is_boppos(exe, str(e.get("LaunchOptions", ""))):
            remove_art(grid, app_id=_entry_appid(e))
            remove_art(grid)  # where older releases put it, if that id differs
        else:
            remove_app_art(grid, _entry_appid(e))
        icon = Path(str(e.get("icon", "")))
        if icon.parent == grid and icon.name.endswith("_icon.jpg"):
            icon.unlink(missing_ok=True)
    return {"added": added, "removed": removed, "updated": updated}


def install_into(path: Path, entry: dict, stamp: str | None = None) -> str:
    """Add `entry` to one shortcuts.vdf. Returns "added" or "present"; raises (file untouched) on trouble."""
    res = apply_changes(path, [entry], [], stamp)
    return "added" if res["added"] else ("updated" if res["updated"] else "present")
