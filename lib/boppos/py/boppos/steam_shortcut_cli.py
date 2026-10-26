"""CLI for boppos.steam_shortcut.

Exit codes: 0 done (or nothing to do), 1 error, 3 Steam is running (try again later),
4 no Steam user data yet (Steam has never been started by this user).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import steam_shortcut as ss
from .vdf import VdfError

EXIT_RUNNING, EXIT_NO_USERDATA = 3, 4


def marker_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "boppos-deck/steam-shortcut-offered"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bopp-steam-shortcut", description=__doc__.split("\n")[0])
    p.add_argument("cmd", choices=["add", "status"])
    p.add_argument("--force", action="store_true", help="add even if the shortcut was offered before")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    def out(data: dict, code: int) -> int:
        print(json.dumps(data) if args.json else " ".join(f"{k}={v}" for k, v in data.items()))
        return code

    files = ss.find_shortcut_files(Path.home())
    if args.cmd == "status":
        return out({"steam_running": ss.steam_running(), "files": len(files), "offered": marker_path().exists()}, 0)
    if marker_path().exists() and not args.force:
        return out({"result": "already-offered"}, 0)
    if ss.steam_running():
        return out({"result": "steam-running"}, EXIT_RUNNING)
    if not files:
        return out({"result": "no-steam-userdata"}, EXIT_NO_USERDATA)
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
    return out({"result": "error" if failed else "ok", "files": results}, 1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
