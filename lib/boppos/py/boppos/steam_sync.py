"""Changing the Steam library while Steam may be running.

Steam keeps shortcuts.vdf in memory and rewrites it on exit, so edits only stick while Steam is closed.
Changes are therefore queued in a small file and applied as soon as Steam is not running: right away
when it is closed, by a detached waiter after Steam is asked to quit, or by a user service at the next
login as a safety net (the Gaming Mode session may restart when Steam exits and take its children along).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from . import steam_shortcut as ss
from .kargs import atomic_write
from .session import gaming_mode
from .steam_apps import Target
from .vdf import VdfError


class SteamRunning(Exception):
    pass


class NoUserData(Exception):
    pass


def pending_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "boppos-deck/steam-pending.json"


def load_pending(path: Path | None = None) -> dict[str, dict[str, Target]]:
    """{"add": {key: Target}, "remove": {key: Target}}; unreadable files count as empty."""
    try:
        raw = json.loads((path or pending_path()).read_text())
        return {k: {key: Target.from_json(t) for key, t in raw.get(k, {}).items()} for k in ("add", "remove")}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return {"add": {}, "remove": {}}


def _save(pending: dict[str, dict[str, Target]], path: Path | None = None) -> None:
    path = path or pending_path()
    if not pending["add"] and not pending["remove"]:
        path.unlink(missing_ok=True)
        return
    atomic_write(path, json.dumps({k: {key: t.to_json() for key, t in v.items()} for k, v in pending.items()}))


def queue(adds: list[Target], removes: list[Target], path: Path | None = None) -> dict[str, dict[str, Target]]:
    """Merge changes into the queue; asking for the opposite of a queued change cancels it."""
    pending = load_pending(path)
    for t in adds:
        pending["remove"].pop(t.key, None)
        pending["add"][t.key] = t
    for t in removes:
        pending["add"].pop(t.key, None)
        pending["remove"][t.key] = t
    _save(pending, path)
    return pending


def clear_pending(path: Path | None = None) -> None:
    (path or pending_path()).unlink(missing_ok=True)


def present_keys(home: Path, targets: dict[str, Target]) -> set[str]:
    """Targets that already have a shortcut in at least one of the user's libraries."""
    from . import vdf

    found: set[str] = set()
    for path in ss.find_shortcut_files(home):
        try:
            data = vdf.loads(path.read_bytes())
        except (OSError, VdfError):
            continue
        found |= {k for k, t in targets.items() if ss.has_shortcut(data, *t.identity())}
    return found


def apply_pending(home: Path, running: Callable[[], bool] = ss.steam_running,
                  path: Path | None = None) -> dict:
    """Apply the queue to every library. Raises SteamRunning / NoUserData and keeps the queue then."""
    pending = load_pending(path)
    if not pending["add"] and not pending["remove"]:
        return {"added": 0, "removed": 0, "files": 0}
    if running():
        raise SteamRunning("Steam is running")
    files = ss.find_shortcut_files(home)
    if not files:
        raise NoUserData("Steam has not created a library for this user yet")
    adds = [t.entry() for t in pending["add"].values()]
    removes = [t.identity() for t in pending["remove"].values()]
    total = {"added": 0, "removed": 0, "files": len(files)}
    errors = []
    for f in files:
        try:
            res = ss.apply_changes(f, adds, removes)
            total["added"] += res["added"]
            total["removed"] += res["removed"]
        except (VdfError, OSError) as exc:
            errors.append(f"{f}: {exc}")
    if errors:
        raise VdfError("; ".join(errors))  # the queue stays, so a later run can retry
    clear_pending(path)
    return total


def request_steam_shutdown(run=subprocess.run) -> bool:
    try:
        return run(["steam", "-shutdown"], check=False, timeout=15, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def wait_for_steam_exit(seconds: float, running: Callable[[], bool] = ss.steam_running,
                        sleep: Callable[[float], None] = time.sleep,
                        clock: Callable[[], float] = time.monotonic) -> bool:
    deadline = clock() + seconds
    while running():
        if clock() >= deadline:
            return False
        sleep(0.3)
    return True


def start_steam_if_desktop(gaming: bool | None = None, popen=subprocess.Popen) -> bool:
    """Reopen Steam on the desktop. In Gaming Mode the session brings Steam back itself."""
    if gaming_mode() if gaming is None else gaming:
        return False
    try:
        popen(["steam", "-silent"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def tool_path() -> str:
    """bin/bopp-steam-shortcut next to lib/ (the repo or /usr), else whatever is on PATH."""
    here = Path(__file__).resolve().parents[4] / "bin/bopp-steam-shortcut"
    return str(here) if here.exists() else (shutil.which("bopp-steam-shortcut") or "bopp-steam-shortcut")


def spawn_apply_waiter(tool: str | None = None) -> bool:
    """Start `apply-pending` detached from the caller, so it outlives the app and the session's children."""
    cmd = [tool or tool_path(), "apply-pending", "--wait", "120", "--reopen-steam"]
    runner = shutil.which("systemd-run")
    attempts = []
    if runner:
        attempts.append([runner, "--user", "--collect", "--quiet", f"--unit=bopp-steam-apply-{int(time.time())}",
                         *cmd])
    attempts.append(cmd)
    for argv in attempts:
        try:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return True
        except OSError:
            continue
    return False
