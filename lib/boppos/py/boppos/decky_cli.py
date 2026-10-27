"""bopp-decky: install, update, remove and inspect Decky Loader (per user, in ~/homebrew)."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import decky
from .decky import DeckyError
from .runner import make_emitter, open_log, run_stream
from .system import explain_pkexec, helper_cmd


def _services(home: Path) -> Path:
    return home / "homebrew/services"


def read_status(home: Path | None = None, run=subprocess.run) -> dict:
    home = home or Path.home()
    binary = _services(home) / "PluginLoader"
    version_file = _services(home) / ".loader.version"

    def is_active(prop: str) -> str:
        try:
            out = run(["systemctl", prop, decky.SERVICE_NAME], capture_output=True, text=True, check=False)
        except OSError:
            return "unknown"
        return out.stdout.strip() or "unknown"

    return {
        "installed": binary.is_file(),
        "version": version_file.read_text().strip() if version_file.is_file() else None,
        "service_active": is_active("is-active") == "active",
        "service_enabled": is_active("is-enabled") == "enabled",
        "cef_debugging": all(p.exists() for p in decky.cef_flag_paths(str(home))),
    }


def fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "bopp-decky", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https URLs only, checked below
        return resp.read()


def download(url: str, dest: Path, expected_sha256: str, emit) -> None:
    if not url.startswith("https://"):
        raise DeckyError("refusing non-https download")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "bopp-decky"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:  # noqa: S310
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
        fh.flush()
        os.fsync(fh.fileno())
    if decky.sha256_file(tmp) != expected_sha256:
        tmp.unlink(missing_ok=True)
        raise DeckyError("sha256 mismatch for the downloaded PluginLoader; nothing was installed")
    os.chmod(tmp, 0o755)
    os.replace(tmp, dest)
    emit({"event": "log", "line": f"downloaded and verified {dest.name}"})


def gaming_mode() -> bool:
    return any(os.environ.get(v) for v in ("GAMESCOPE_WAYLAND_DISPLAY", "SteamGamepadUI", "SteamDeck"))


def do_install(emit, home: Path | None = None) -> dict:
    home = home or Path.home()
    emit({"event": "log", "line": "looking up the latest Decky Loader release"})
    rel = decky.pick_release(fetch(decky.RELEASES_URL).decode(), platform.machine())
    emit({"event": "log", "line": f"latest release: {rel['version']}"})
    (home / "homebrew/plugins").mkdir(parents=True, exist_ok=True)
    for flag in decky.cef_flag_paths(str(home)):
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
    download(rel["url"], _services(home) / "PluginLoader", rel["sha256"], emit)
    (_services(home) / ".loader.version").write_text(rel["version"] + "\n")
    rc = run_stream(helper_cmd("decky-helper", ["install"]), emit)
    if rc:
        raise DeckyError(explain_pkexec(rc, "") or "decky-helper install failed")
    # Decky only attaches to Steam's CEF when Steam starts with the flag file present.
    steam_restarted = False
    if gaming_mode():
        steam_restarted = subprocess.run(["steam", "-shutdown"], check=False).returncode == 0
    return {"version": rel["version"], "restart_steam": not steam_restarted, "steam_restarted": steam_restarted}


def do_uninstall(emit, home: Path | None = None) -> dict:
    home = home or Path.home()
    rc = run_stream(helper_cmd("decky-helper", ["uninstall"]), emit)
    if rc:
        raise DeckyError(explain_pkexec(rc, "") or "decky-helper uninstall failed")
    leftovers = (_services(home) / "PluginLoader", _services(home) / ".loader.version")
    for path in (*leftovers, *decky.cef_flag_paths(str(home))):
        path.unlink(missing_ok=True)
    emit({"event": "log", "line": "service removed; plugins and settings in ~/homebrew were kept"})
    return {"restart_steam": True}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bopp-decky", description="Install and manage Decky Loader")
    p.add_argument("--json", action="store_true", help="machine-readable output (JSON lines for actions)")
    p.add_argument("cmd", choices=["status", "install", "uninstall"])
    args = p.parse_args(argv)
    if args.cmd == "status":
        st = read_status()
        print(json.dumps(st, indent=2) if args.json else "\n".join(f"{k}: {v}" for k, v in st.items()))
        return 0
    emit = make_emitter(args.json, open_log(f"decky-{args.cmd}"))
    emit({"event": "step", "id": args.cmd, "status": "start"})
    try:
        result = do_install(emit) if args.cmd == "install" else do_uninstall(emit)
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
