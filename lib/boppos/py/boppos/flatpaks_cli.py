"""Non-interactive mode of install-optional-flatpaks (user scope unless --system)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import flatpaks
from .runner import make_emitter, open_log, run_stream


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="install-optional-flatpaks",
        description="Install or remove the curated BoppOS Flatpaks without prompting "
        "(for the current user unless --system).",
    )
    p.add_argument("--list", default=None, metavar="FILE",
                   help="use only this list file (default: the curated list plus the extras)")
    p.add_argument("--defaults", action="store_true",
                   help="install the apps every BoppOS system should have (the default-apps list)")
    p.add_argument("--yes", action="store_true", help="do not prompt (required to change anything)")
    p.add_argument("--only", metavar="ID[,ID...]", help="restrict to these entries of the list")
    p.add_argument("--system", action="store_true",
                   help="install for all users (system scope). Removal always follows where the app is installed")
    p.add_argument("--remove", action="store_true", help="uninstall instead of install")
    p.add_argument("--status", action="store_true", help="show installed state and exit")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.defaults:
        args.list = flatpaks.defaults_list()
    try:
        apps = flatpaks.parse_list(Path(args.list).read_text()) if args.list else flatpaks.load_curated()
        apps = flatpaks.select(apps, args.only.split(",") if args.only else None)
    except (OSError, flatpaks.ListError) as exc:
        print(json.dumps({"error": str(exc)}) if args.json else f"error: {exc}", file=sys.stderr)
        return 1
    if not apps:
        print("error: the Flatpak list is empty", file=sys.stderr)
        return 1

    try:
        scopes = flatpaks.installed_scopes()
    except flatpaks.ListError as exc:
        print(json.dumps({"error": str(exc)}) if args.json else f"error: {exc}", file=sys.stderr)
        return 1
    if args.status:
        try:
            default_ids = {a.id for a in flatpaks.parse_list(Path(flatpaks.defaults_list()).read_text())}
        except (OSError, flatpaks.ListError):
            default_ids = set()
        names = flatpaks.installed_names()
        rows = [
            {"id": a.id, "name": flatpaks.best_name(a, names), "ref": a.ref, "kind": a.kind,
             "category": a.category, "default": a.id in default_ids, "installed": a.id in scopes,
             "scopes": sorted(scopes.get(a.id, ()))}
            for a in apps
        ]
        print(json.dumps(rows, indent=2) if args.json else
              "\n".join(f"{'[x]' if r['installed'] else '[ ]'} {r['id']}" for r in rows))
        return 0
    if not args.yes:
        print("error: refusing to change anything without --yes", file=sys.stderr)
        return 2

    action = "remove" if args.remove else "install"
    emit = make_emitter(args.json, open_log(f"flatpak-{action}"))
    scope = "system" if args.system else "user"
    todo = [a for a in apps if (a.id in scopes) == args.remove]
    failed: list[str] = []
    if todo and not args.remove and run_stream(flatpaks.remote_add_cmd(scope), emit):
        failed.append("flathub-remote")
        todo = []
    for app in todo:
        emit({"event": "step", "id": app.id, "status": "start"})
        if args.remove:
            ok = all(run_stream(flatpaks.remove_cmd(app, sc), emit) == 0 for sc in sorted(scopes[app.id]))
        else:
            ok = run_stream(flatpaks.install_cmd(app, scope), emit) == 0
        emit({"event": "step", "id": app.id, "status": "ok" if ok else "fail"})
        if not ok:
            failed.append(app.id)
    emit({"event": "done", "ok": not failed, "failed": failed, "changed": len(todo) - len(failed)})
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
