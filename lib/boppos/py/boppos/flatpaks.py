"""The curated BoppOS Flatpak list: parsing, installed state (user and system scope) and commands.

New installs go to the user scope; the system scope is an explicit choice. Removal and state look at both.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .paths import share_file

DEFAULT_LIST = str(share_file("boppos-flatpaks.txt"))
DEFAULTS_LIST = str(share_file("boppos-flatpaks-default.txt"))
EXTRA_LIST = str(share_file("boppos-flatpaks-extra.txt"))
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


def user_defaults_path(env: dict[str, str] | None = None) -> Path:
    """The list this user's boppos-default-flatpaks.service installs from, written by the setup wizard."""
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "boppos-deck" / "default-flatpaks.txt"


def defaults_list(env: dict[str, str] | None = None) -> str:
    """The wizard's list when there is one, else the one BoppOS ships."""
    mine = user_defaults_path(env)
    return str(mine) if mine.is_file() else DEFAULTS_LIST


def write_user_defaults(refs: set[str], env: dict[str, str] | None = None) -> Path:
    """Replace the list the service installs from. Apps are sorted; the curated runtimes and extensions
    stay in so a chosen app keeps working."""
    keep = {a.ref for a in parse_list(Path(DEFAULT_LIST).read_text()) if a.kind != "app"}
    path = user_defaults_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "# Written by the BoppOS setup wizard. boppos-default-flatpaks.service installs these.\n"
    text += "".join(f"{r}\n" for r in sorted(refs) + sorted(keep - refs))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return path


def load_curated(core: str = DEFAULT_LIST, extra: str | None = EXTRA_LIST) -> list[App]:
    """What boppos-deck offers: the core list plus the opt-in extras (a missing extra file is fine).

    The interactive install-optional-flatpaks only ever reads the core list, so "install everything"
    stays the size it was.
    """
    from pathlib import Path

    text = Path(core).read_text()
    if extra:
        try:
            text += "\n" + Path(extra).read_text()
        except OSError:
            pass
    return parse_list(text)


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
