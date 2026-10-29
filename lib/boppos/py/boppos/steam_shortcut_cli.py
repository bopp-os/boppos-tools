"""CLI for the Steam library: the first-login BoppOS shortcut, and per-app shortcuts for Gaming Mode.

Exit codes: 0 done (or nothing to do), 1 error, 2 unknown app, 3 Steam is running (changes are queued and
apply when it closes), 4 no Steam user data yet (Steam has never been started by this user).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import steam_apps, steam_sync
from . import steam_shortcut as ss
from .runner import make_emitter
from .session import gaming_mode
from .vdf import VdfError

EXIT_UNKNOWN, EXIT_RUNNING, EXIT_NO_USERDATA = 2, 3, 4


def marker_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "boppos-deck/steam-shortcut-offered"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bopp-steam-shortcut", description="Steam library shortcuts for BoppOS and apps")
    p.add_argument("--json", action="store_true", help="machine-readable output (JSON lines for sync)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="first login: add the BoppOS shortcut while Steam is closed")
    a.add_argument("--force", action="store_true", help="add even if the shortcut was offered before")
    sub.add_parser("status", help="is Steam running, which libraries exist")
    sub.add_parser("apps", help="BoppOS and installed Flatpak apps, and whether each is in the Steam library")
    s = sub.add_parser("sync", help="add and remove shortcuts (queued while Steam is running)")
    s.add_argument("--add", default="", metavar="KEY[,KEY...]", help="app ids (or boppos-deck) to add")
    s.add_argument("--remove", default="", metavar="KEY[,KEY...]", help="app ids to remove")
    s.add_argument("--restart-steam", action="store_true",
                   help="ask a running Steam to quit so the queued changes apply, and reopen it on the desktop")
    s.add_argument("--queue-ok", action="store_true",
                   help="queued changes count as success (Steam running, or no library yet): they apply later")
    w = sub.add_parser("apply-pending", help="apply queued changes once Steam is closed")
    w.add_argument("--wait", type=float, default=0, metavar="SECONDS", help="wait this long for Steam to quit")
    w.add_argument("--reopen-steam", action="store_true", help="start Steam again afterwards (desktop only)")
    return p


def _print(args, data: dict) -> None:
    print(json.dumps(data) if args.json else " ".join(f"{k}={v}" for k, v in data.items()))


def _add_first_login(args, files) -> int:
    if marker_path().exists() and not args.force:
        _print(args, {"result": "already-offered"})
        return 0
    if ss.steam_running():
        _print(args, {"result": "steam-running"})
        return EXIT_RUNNING
    if not files:
        _print(args, {"result": "no-steam-userdata"})
        return EXIT_NO_USERDATA
    results, failed = {}, False
    entry = ss.new_entry()
    for path in files:
        try:
            results[str(path)] = ss.install_into(path, entry)
        except (VdfError, OSError) as exc:
            results[str(path)] = f"skipped: {exc}"
            failed = True
    if not failed:
        marker_path().parent.mkdir(parents=True, exist_ok=True)
        marker_path().write_text("1\n")
    _print(args, {"result": "error" if failed else "ok", "files": results})
    return 1 if failed else 0


def _apps(args, home: Path, files) -> int:
    targets = steam_apps.all_targets()
    present = steam_sync.present_keys(home, targets)
    pend = steam_sync.load_pending()
    categories = steam_apps.categories()
    rows = []
    for key, t in targets.items():
        queued = "add" if key in pend["add"] else "remove" if key in pend["remove"] else ""
        now = (key in present or queued == "add") and queued != "remove"
        rows.append({"key": key, "name": t.name, "present": now, "queued": queued, "has_icon": bool(t.icon),
                     "category": "BoppOS" if key == steam_apps.BOPPOS_KEY else categories.get(key, "Other apps")})
    data = {"steam_found": bool(files), "steam_running": ss.steam_running(), "gaming": gaming_mode(), "apps": rows}
    print(json.dumps(data, indent=2) if args.json else "\n".join(
        f"[{'x' if r['present'] else ' '}] {r['name']} ({r['key']}){'  queued ' + r['queued'] if r['queued'] else ''}"
        for r in rows))
    return 0


def _sync(args, home: Path) -> int:
    emit = make_emitter(args.json)
    targets = steam_apps.all_targets()
    add = [k for k in args.add.split(",") if k]
    remove = [k for k in args.remove.split(",") if k]
    for k in remove:  # an app that was just uninstalled still has a shortcut to take out; its identity is enough
        if k not in targets and steam_apps.is_app_id(k):
            targets[k] = steam_apps.flatpak_target(k, k)
    unknown = [k for k in add + remove if k not in targets]
    if unknown:
        emit({"event": "log", "line": f"unknown app(s): {', '.join(unknown)}"})
        emit({"event": "done", "ok": False, "error": "unknown-app"})
        return EXIT_UNKNOWN
    emit({"event": "step", "id": "steam library", "status": "start"})
    steam_sync.queue([targets[k] for k in add], [targets[k] for k in remove])
    emit({"event": "log", "line": f"{len(add)} to add, {len(remove)} to remove"})
    code, final = 0, {"ok": True}
    if not ss.steam_running():
        try:
            res = steam_sync.apply_pending(home)
            emit({"event": "log", "line": f"Steam library updated: {res['added']} added, {res['removed']} removed"})
        except steam_sync.NoUserData as exc:
            emit({"event": "log", "line": f"{exc}. Start Steam once, then try again; the changes stay queued."})
            code, final = EXIT_NO_USERDATA, {"ok": False, "error": "no-steam-userdata"}
        except (VdfError, OSError) as exc:
            emit({"event": "log", "line": f"could not update the library: {exc}"})
            code, final = 1, {"ok": False, "error": str(exc)}
    elif args.restart_steam:
        emit({"event": "log", "line": "Closing Steam to apply the changes. It comes back by itself"
              + ("" if gaming_mode() else " (reopened on the desktop)") + "."})
        if not steam_sync.spawn_apply_waiter():
            emit({"event": "log", "line": "could not start the background helper; the changes apply the next "
                  "time Steam is closed"})
        steam_sync.request_steam_shutdown()
        final = {"ok": True, "queued": True, "restart_steam": True}
    else:
        emit({"event": "log", "line": "Steam is running. The changes are queued and apply when it is closed."})
        code, final = EXIT_RUNNING, {"ok": False, "error": "steam-running", "queued": True}
    if args.queue_ok and final.get("error") in ("steam-running", "no-steam-userdata"):
        code, final = 0, {"ok": True, "queued": True}
    emit({"event": "step", "id": "steam library", "status": "ok" if final["ok"] else "fail"})
    emit({"event": "done", **final})
    return code


def _apply_pending(args, home: Path) -> int:
    if args.wait and ss.steam_running():
        steam_sync.wait_for_steam_exit(args.wait)
    try:
        res = steam_sync.apply_pending(home)
    except steam_sync.SteamRunning:
        _print(args, {"result": "steam-running"})
        return EXIT_RUNNING
    except steam_sync.NoUserData:
        _print(args, {"result": "no-steam-userdata"})
        return EXIT_NO_USERDATA
    except (VdfError, OSError) as exc:
        _print(args, {"result": "error", "error": str(exc)})
        return 1
    if args.reopen_steam and (res["added"] or res["removed"]):
        steam_sync.start_steam_if_desktop()
    _print(args, {"result": "ok", **res})
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    home = Path.home()
    files = ss.find_shortcut_files(home)
    if args.cmd == "status":
        _print(args, {"steam_running": ss.steam_running(), "files": len(files), "offered": marker_path().exists(),
                      "pending": bool(any(steam_sync.load_pending().values()))})
        return 0
    if args.cmd == "add":
        return _add_first_login(args, files)
    if args.cmd == "apps":
        return _apps(args, home, files)
    if args.cmd == "sync":
        return _sync(args, home)
    return _apply_pending(args, home)


if __name__ == "__main__":
    sys.exit(main())
