"""Things that can be put into the Steam library: BoppOS itself and every installed Flatpak app."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path

from . import steam_shortcut as ss

BOPPOS_KEY = "boppos-deck"
ICON_ROOTS = (Path("/var/lib/flatpak/exports/share/icons"), Path.home() / ".local/share/flatpak/exports/share/icons")
_APP_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class Target:
    key: str  # what the CLI and UI call it: the Flatpak app id, or "boppos-deck"
    name: str
    exe: str
    launch: str
    icon: str

    def entry(self) -> dict:
        return ss.new_entry(self.exe, self.name, self.icon, self.launch)

    def identity(self) -> tuple[str, str]:
        return (self.exe, self.launch)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> Target:
        return cls(d["key"], d["name"], d["exe"], d["launch"], d.get("icon", ""))


def icon_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "boppos-deck/icons"


def rasterize_svg(svg: Path, app_id: str, cache: Path | None = None, run=subprocess.run) -> str:
    """A 512px PNG of an SVG icon, cached under a name that changes with the SVG's content (files deployed by
    Flatpak are all dated 1970, so a date cannot tell an update). '' when rsvg-convert is missing or fails."""
    cache = cache or icon_cache()
    try:
        digest = f"{zlib.crc32(svg.read_bytes()):08x}"
    except OSError:
        return ""
    dest = cache / f"{app_id}-{digest}.png"
    if dest.is_file():
        return str(dest)
    tool = shutil.which("rsvg-convert")
    if not tool:
        return ""
    cache.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    try:
        proc = run([tool, "-w", "512", "-h", "512", "--keep-aspect-ratio", "-o", str(tmp), str(svg)],
                   capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        tmp.unlink(missing_ok=True)
        return ""
    if proc.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        return ""
    for old in cache.glob(f"{app_id}-*.png"):  # an earlier version of this app's icon
        old.unlink(missing_ok=True)
    tmp.replace(dest)
    return str(dest)


def find_icon(app_id: str, roots: tuple[Path, ...] = ICON_ROOTS, cache: Path | None = None) -> str:
    """Largest PNG icon the Flatpak app exports, else its scalable SVG made into a PNG (Steam does not take
    SVG), or ''."""
    best: tuple[int, Path] | None = None
    for root in roots:
        for path in root.glob(f"hicolor/*x*/apps/{app_id}.png"):
            m = re.match(r"(\d+)x\d+$", path.parent.parent.name)
            if m and (best is None or int(m.group(1)) > best[0]):
                best = (int(m.group(1)), path)
    if best and best[0] >= 128:
        return str(best[1])
    for root in roots:  # a small PNG only, or none: a scalable icon draws sharper on the artwork
        svg = root / f"hicolor/scalable/apps/{app_id}.svg"
        if svg.is_file():
            png = rasterize_svg(svg, app_id, cache)
            if png:
                return png
    return str(best[1]) if best else ""


def parse_flatpak_list(text: str) -> list[tuple[str, str]]:
    """`flatpak list --app --columns=application,name` -> [(app id, name)], de-duplicated and sorted by name."""
    seen: dict[str, str] = {}
    for line in text.splitlines():
        app_id, _, name = line.partition("\t")
        app_id, name = app_id.strip(), name.strip()
        if _APP_ID.match(app_id) and app_id not in seen:
            seen[app_id] = name or app_id
    return sorted(seen.items(), key=lambda kv: kv[1].lower())


def installed_flatpaks(run=subprocess.run) -> list[tuple[str, str]]:
    try:
        proc = run(["flatpak", "list", "--app", "--columns=application,name"], capture_output=True, text=True,
                   check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_flatpak_list(proc.stdout) if proc.returncode == 0 else []


def boppos_target() -> Target:
    return Target(BOPPOS_KEY, ss.APP_NAME, ss.EXE, "", ss.ICON)


def is_app_id(key: str) -> bool:
    return bool(_APP_ID.match(key)) and "." in key


def flatpak_target(app_id: str, name: str, icon: str = "") -> Target:
    exe = shutil.which("flatpak") or "/usr/bin/flatpak"
    return Target(app_id, name, exe, f"run {app_id}", icon)


def curated() -> dict:
    """app id -> App of the curated list (names and categories); empty when the list is unreadable."""
    from . import flatpaks

    try:
        return {a.id: a for a in flatpaks.load_curated()}
    except (OSError, flatpaks.ListError):
        return {}


def categories() -> dict[str, str]:
    """app id -> category of the curated list; anything else is "Other apps" to the caller."""
    return {k: a.category for k, a in curated().items()}


def all_targets(flatpaks: list[tuple[str, str]] | None = None) -> dict[str, Target]:
    """key -> Target, BoppOS first."""
    apps = installed_flatpaks() if flatpaks is None else flatpaks
    names = curated()
    out = {BOPPOS_KEY: boppos_target()}
    for app_id, name in apps:
        # the curated list's name wins, so an app is called the same on the Apps and Steam library pages
        out[app_id] = flatpak_target(app_id, (names[app_id].name if app_id in names and names[app_id].name else name),
                                     find_icon(app_id))
    return out
