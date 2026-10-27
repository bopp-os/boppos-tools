"""The curated BoppOS Flatpak list: parsing, installed state and system-scope commands."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from .paths import share_file

DEFAULT_LIST = str(share_file("boppos-flatpaks.txt"))
DEFAULTS_LIST = str(share_file("boppos-flatpaks-default.txt"))
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
        name = comment[5:].strip() if comment.lower().startswith("name:") else ""
        if not _REF.match(line):
            raise ListError(f"invalid Flatpak reference: {line!r}")
        seen.add(line)
        if line.startswith("runtime/"):
            apps.append(App(line, line.split("/")[1], "runtime", category, name))
        else:
            # extensions (for example the QEMU add-on of virt-manager) are installed but are not apps to show
            kind = "runtime" if ".Extension." in line else "app"
            apps.append(App(line, line, kind, category, name))
    return apps


def parse_installed(output: str) -> set[str]:
    return {ln.strip() for ln in output.splitlines() if ln.strip()}


def installed_names() -> dict[str, str]:
    """id -> the name the installed app calls itself (from its own metadata). Empty when unavailable."""
    try:
        proc = subprocess.run(["flatpak", "list", "--system", "--columns=application,name"],
                              capture_output=True, text=True, check=False)
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines() if proc.returncode == 0 else []:
        app_id, _, name = line.partition("\t")
        if app_id.strip() and name.strip():
            out[app_id.strip()] = name.strip()
    return out


def best_name(app: App, installed: dict[str, str]) -> str:
    """The installed app's own name, else the name in the list, else a tidied id."""
    return installed.get(app.id) or app.name or display_name(app.id)


def installed_ids() -> set[str]:
    try:
        proc = subprocess.run(
            ["flatpak", "list", "--system", "--columns=application"],
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise ListError(f"cannot run flatpak: {exc}") from exc
    if proc.returncode:
        raise ListError(proc.stderr.strip() or "flatpak list failed")
    return parse_installed(proc.stdout)


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


def remote_add_cmd() -> list[str]:
    return ["flatpak", "remote-add", "--system", "--if-not-exists", "flathub", REMOTE_URL]


def install_cmd(app: App) -> list[str]:
    return ["flatpak", "install", "--system", "-y", "--noninteractive", "flathub", app.ref]


def remove_cmd(app: App) -> list[str]:
    return ["flatpak", "uninstall", "--system", "-y", "--noninteractive", app.id]
