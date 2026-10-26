"""The curated BoppOS Flatpak list: parsing, installed state and system-scope commands."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

DEFAULT_LIST = "/usr/share/boppos/boppos-flatpaks.txt"
REMOTE_URL = "https://flathub.org/repo/flathub.flatpakrepo"
_REF = re.compile(r"^(runtime/)?[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?$")


class ListError(Exception):
    pass


@dataclass(frozen=True)
class App:
    ref: str  # exactly as written in the list
    id: str  # application / runtime id
    kind: str  # "app" or "runtime"


def parse_list(text: str) -> list[App]:
    apps: list[App] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line in seen:
            continue
        if not _REF.match(line):
            raise ListError(f"invalid Flatpak reference: {line!r}")
        seen.add(line)
        if line.startswith("runtime/"):
            apps.append(App(line, line.split("/")[1], "runtime"))
        else:
            apps.append(App(line, line, "app"))
    return apps


def parse_installed(output: str) -> set[str]:
    return {ln.strip() for ln in output.splitlines() if ln.strip()}


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
