"""bopp-decky: turn the system Decky Loader on or off for the current user, and move an old ~/homebrew install."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

from . import decky
from .decky import DeckyError
from .runner import make_emitter, open_log, run_stream
from .system import explain_pkexec, helper_cmd

NOT_AVAILABLE = "Decky Loader is not part of this system image yet. Update your system first."


def current_user() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def package_version(run=subprocess.run) -> str | None:
    try:
        out = run(["pacman", "-Q", "decky-loader"], capture_output=True, text=True, check=False)
    except OSError:
        return None
    parts = out.stdout.split()
    return parts[1] if out.returncode == 0 and len(parts) == 2 else None


def read_status(home: Path | None = None, user: str | None = None, run=subprocess.run,
                loader_bin: str = decky.LOADER_BIN, legacy_unit: str = decky.LEGACY_UNIT_PATH) -> dict:
    home = home or Path.home()
    user = user or current_user()
    unit = decky.unit_name(user)

    def systemctl(prop: str) -> str:
        try:
            out = run(["systemctl", prop, unit], capture_output=True, text=True, check=False)
        except OSError:
            return "unknown"
        return out.stdout.strip() or "unknown"

    available = os.path.isfile(loader_bin)
    enabled = available and systemctl("is-enabled") == "enabled"
    return {
        "available": available,
        "unit": unit,
        "installed": enabled,
        "version": package_version(run) if available else None,
        "service_active": available and systemctl("is-active") == "active",
        "service_enabled": enabled,
        # An old install still runs a file from the home folder as root; the page offers to move it.
        "legacy": os.path.lexists(legacy_unit),
        "legacy_files": (home / decky.LEGACY_BINARY).is_file(),
        "cef_debugging": all(p.exists() for p in decky.cef_flag_paths(str(home))),
    }


def gaming_mode() -> bool:
    return any(os.environ.get(v) for v in ("GAMESCOPE_WAYLAND_DISPLAY", "SteamGamepadUI", "SteamDeck"))


def run_helper(action: str, emit) -> None:
    rc = run_stream(helper_cmd("decky-helper", [action]), emit)
    if rc:
        raise DeckyError(explain_pkexec(rc, "") or f"decky-helper {action} failed")


def set_cef_flags(home: Path) -> None:
    """Steam turns on the CEF remote debugger Decky attaches to when it starts with this file present. The unit
    sets it for native Steam too, but only once ~/.steam exists; Flatpak Steam needs it here."""
    for flag in decky.cef_flag_paths(str(home)):
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()


def do_install(emit, home: Path | None = None, loader_bin: str = decky.LOADER_BIN) -> dict:
    home = home or Path.home()
    if not os.path.isfile(loader_bin):
        raise DeckyError(NOT_AVAILABLE)  # /usr is read-only on BoppOS: it comes with a system update
    set_cef_flags(home)
    run_helper("install", emit)
    # Decky only attaches to Steam's CEF when Steam starts with the flag file present.
    steam_restarted = False
    if gaming_mode():
        steam_restarted = subprocess.run(["steam", "-shutdown"], check=False).returncode == 0
    return {"restart_steam": not steam_restarted, "steam_restarted": steam_restarted}


def do_migrate(emit) -> dict:
    """Stop and remove an old ~/homebrew install and move its plugins, without turning Decky on."""
    run_helper("migrate", emit)
    return {"restart_steam": True}


def do_uninstall(emit, home: Path | None = None) -> dict:
    home = home or Path.home()
    run_helper("uninstall", emit)
    for path in decky.cef_flag_paths(str(home)):
        path.unlink(missing_ok=True)
    emit({"event": "log", "line": "Decky Loader is off; its plugins and settings were kept for a reinstall"})
    return {"restart_steam": True}


ACTIONS = {"install": do_install, "uninstall": do_uninstall, "migrate": do_migrate}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bopp-decky", description="Turn Decky Loader on or off")
    p.add_argument("--json", action="store_true", help="machine-readable output (JSON lines for actions)")
    p.add_argument("cmd", choices=["status", *ACTIONS])
    args = p.parse_args(argv)
    if args.cmd == "status":
        st = read_status()
        print(json.dumps(st, indent=2) if args.json else "\n".join(f"{k}: {v}" for k, v in st.items()))
        return 0
    emit = make_emitter(args.json, open_log(f"decky-{args.cmd}"))
    emit({"event": "step", "id": args.cmd, "status": "start"})
    try:
        result = ACTIONS[args.cmd](emit)
    except (DeckyError, OSError) as exc:
        emit({"event": "log", "line": f"error: {exc}"})
        emit({"event": "step", "id": args.cmd, "status": "fail"})
        emit({"event": "done", "ok": False, "error": str(exc)})
        return 1
    emit({"event": "step", "id": args.cmd, "status": "ok"})
    emit({"event": "done", "ok": True, **result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
