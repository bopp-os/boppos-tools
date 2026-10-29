"""The curated BoppOS Flatpak list: parsing, installed state (user and system scope) and commands.

New installs go to the user scope; the system scope is an explicit choice. Removal and state look at both.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .paths import share_file

DEFAULT_LIST = str(share_file("boppos-flatpaks.txt"))
DEFAULTS_LIST = str(share_file("boppos-flatpaks-default.txt"))
EXTRA_LIST = str(share_file("boppos-flatpaks-extra.txt"))
NATIVE_MAP = str(share_file("boppos-flatpaks-native.txt"))
REMOTE_URL = "https://flathub.org/repo/flathub.flatpakrepo"
_REF = re.compile(r"^(runtime/)?[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?$")


NAMES = {
    "it.mijorus.gearlever": "Gear Lever",
    "io.github.kolunmi.Bazaar": "Bazaar",
    "com.vysp3r.ProtonPlus": "ProtonPlus",
    "com.github.tchx84.Flatseal": "Flatseal",
    "org.gimp.GIMP": "GIMP",
    "org.kde.kdenlive": "Kdenlive",
    "org.qbittorrent.qBittorrent": "qBittorrent",
    "io.github.dvlv.boxbuddyrs": "BoxBuddy",
    "org.localsend.localsend_app": "LocalSend",
}


def display_name(app_id: str) -> str:
    """Readable name for the UI: curated override, else the last id segment with underscores spaced."""
    return NAMES.get(app_id) or app_id.rsplit(".", 1)[-1].replace("_", " ").replace("-", " ")


class ListError(Exception):
    pass


@dataclass(frozen=True)
class App:
    ref: str  # exactly as written in the list
    id: str  # application / runtime id
    kind: str  # "app" or "runtime"
    category: str = "Other"
    name: str = ""  # from a trailing "# name: Proper Name" comment in the list
    default: bool = False  # "; default" after the name: pre-ticked in the first-run wizard


def parse_list(text: str) -> list[App]:
    apps: list[App] = []
    seen: set[str] = set()
    category = "Other"
    for raw in text.splitlines():
        if raw.lstrip().startswith("##"):  # "## Name" sets the category of the entries below it
            category = raw.lstrip()[2:].strip() or "Other"
            continue
        line, _, comment = raw.partition("#")
        line, comment = line.strip(), comment.strip()
        if not line or line in seen:
            continue
        # the comment is "name: Proper Name" optionally followed by "; default"
        fields = [f.strip() for f in comment.split(";")]
        name = fields[0][5:].strip() if fields[0].lower().startswith("name:") else ""
        default = "default" in (f.lower() for f in fields[1:])
        if not _REF.match(line):
            raise ListError(f"invalid Flatpak reference: {line!r}")
        seen.add(line)
        if line.startswith("runtime/"):
            apps.append(App(line, line.split("/")[1], "runtime", category, name, default))
        else:
            # extensions (for example the QEMU add-on of virt-manager) are installed but are not apps to show
            kind = "runtime" if ".Extension." in line else "app"
            apps.append(App(line, line, kind, category, name, default))
    return apps


def load_curated(core: str = DEFAULT_LIST, extra: str | None = EXTRA_LIST) -> list[App]:
    """What boppos-deck offers: the core list plus the opt-in extras (a missing extra file is fine).

    The interactive install-optional-flatpaks only ever reads the core list, so "install everything"
    stays the size it was.
    """
    text = Path(core).read_text()
    if extra:
        try:
            text += "\n" + Path(extra).read_text()
        except OSError:
            pass
    return parse_list(text)


def parse_native_map(text: str) -> dict[str, tuple[str, ...]]:
    """flatpak id -> the native packages / .desktop files that make it redundant (see boppos-flatpaks-native.txt)."""
    out: dict[str, tuple[str, ...]] = {}
    for raw in text.splitlines():
        fields = raw.partition("#")[0].split()
        if len(fields) < 2:
            if fields:
                raise ListError(f"no native package given for {fields[0]!r}")
            continue
        out[fields[0]] = tuple(fields[1:])
    return out


def load_native_map(path: str = NATIVE_MAP) -> dict[str, tuple[str, ...]]:
    """The shipped mapping; a missing or broken file hides nothing."""
    try:
        return parse_native_map(Path(path).read_text())
    except (OSError, ListError):
        return {}


@lru_cache(maxsize=1)
def native_installed(root: str = "/") -> frozenset[str]:
    """Installed pacman packages plus the .desktop files in /usr/share/applications (same namespace, the
    mapping tells them apart by the suffix). Cached: the image does not change while the app runs."""
    found: set[str] = set()
    try:
        cmd = ["pacman", "-Qq"] + ([] if root == "/" else ["--root", root])
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            found |= parse_installed(proc.stdout)
    except OSError:
        pass
    apps = Path(root) / "usr/share/applications"
    try:
        found |= {f.name for f in apps.iterdir() if f.name.endswith(".desktop")}
    except OSError:
        pass
    return frozenset(found)


def redundant(apps: list[App], mapping: dict[str, tuple[str, ...]], installed: set[str] | frozenset[str]) -> set[str]:
    """Ids of the apps a native package or .desktop file already provides. Unmapped apps are never redundant."""
    return {a.id for a in apps if any(n in installed for n in mapping.get(a.id, ()))}


def parse_installed(output: str) -> set[str]:
    return {ln.strip() for ln in output.splitlines() if ln.strip()}


SCOPES = ("user", "system")


def installed_names() -> dict[str, str]:
    """id -> the name the installed app calls itself (from its own metadata). Empty when unavailable."""
    out: dict[str, str] = {}
    for scope in SCOPES:
        try:
            proc = subprocess.run(["flatpak", "list", f"--{scope}", "--columns=application,name"],
                                  capture_output=True, text=True, check=False)
        except OSError:
            return {}
        for line in proc.stdout.splitlines() if proc.returncode == 0 else []:
            app_id, _, name = line.partition("\t")
            if app_id.strip() and name.strip():
                out.setdefault(app_id.strip(), name.strip())
    return out


def best_name(app: App, installed: dict[str, str]) -> str:
    """The name chosen in the curated list, else the name the installed app reports, else a tidied id.

    The list wins on purpose: Flatpak reports "GNU Image Manipulation Program" and plain "Bitwarden",
    and the list is where those are shortened or clarified.
    """
    return app.name or installed.get(app.id) or display_name(app.id)


def installed_scopes() -> dict[str, set[str]]:
    """id -> the scopes it is installed in ("user", "system"). Fails only when neither scope can be listed."""
    out: dict[str, set[str]] = {}
    errors: list[str] = []
    for scope in SCOPES:
        try:
            proc = subprocess.run(["flatpak", "list", f"--{scope}", "--columns=application"],
                                  capture_output=True, text=True, check=False)
        except OSError as exc:
            raise ListError(f"cannot run flatpak: {exc}") from exc
        if proc.returncode:
            errors.append(proc.stderr.strip() or "flatpak list failed")
            continue
        for app_id in parse_installed(proc.stdout):
            out.setdefault(app_id, set()).add(scope)
    if len(errors) == len(SCOPES):
        raise ListError(errors[0])
    return out


def select(apps: list[App], only: list[str] | None) -> list[App]:
    """Restrict to `only` (ids or refs). Anything not in the curated list is an error."""
    if not only:
        return apps
    by_key = {k: a for a in apps for k in (a.ref, a.id)}
    unknown = [o for o in only if o not in by_key]
    if unknown:
        raise ListError(f"not in the curated list: {', '.join(unknown)}")
    chosen = {by_key[o] for o in only}
    return [a for a in apps if a in chosen]


def remote_add_cmd(scope: str = "user") -> list[str]:
    return ["flatpak", "remote-add", f"--{scope}", "--if-not-exists", "flathub", REMOTE_URL]


def install_cmd(app: App, scope: str = "user") -> list[str]:
    return ["flatpak", "install", f"--{scope}", "-y", "--noninteractive", "flathub", app.ref]


def remove_cmd(app: App, scope: str = "user") -> list[str]:
    return ["flatpak", "uninstall", f"--{scope}", "-y", "--noninteractive", app.id]
