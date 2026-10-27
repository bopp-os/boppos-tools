"""bopp-system: updates, deployments, firmware, about and support report (UI/CLI backend)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import system
from .privileged import UsageError, validate_image_ref
from .runner import log_dir, make_emitter, open_log, run_stream
from .support import build_report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bopp-system", description="BoppOS system management")
    p.add_argument("--json", action="store_true", help="machine-readable output (JSON lines for actions)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="deployments and update state")
    sub.add_parser("check", help="check OS, Flatpak and firmware for updates")
    up = sub.add_parser("upgrade", help="apply updates (default: os, flatpak, brew)")
    up.add_argument("--only", help="comma list of: " + ",".join(system.TARGETS))
    sub.add_parser("rollback", help="queue a rollback to the previous deployment")
    for name in ("pin", "unpin"):
        s = sub.add_parser(name, help=f"{name} a deployment")
        s.add_argument("index", help="deployment index from `status`")
    sw = sub.add_parser("switch", help="switch to a different image; give the whole reference, registry/name:tag")
    sw.add_argument("image", metavar="IMAGE", help="for example ghcr.io/bopp-os/cachyos-plasma:v4")
    sub.add_parser("reboot", help="reboot now")
    sub.add_parser("about", help="image, flavor, kernel, device")
    sub.add_parser("support-report", help="write ~/boppos-support-<time>.txt")
    return p


def _print(args, data, text: str) -> None:
    print(json.dumps(data, indent=2) if args.json else text)


def _fail(args, msg: str, code: int = 1) -> int:
    if args.json:
        print(json.dumps({"error": msg}))
    else:
        print(f"bopp-system: {msg}", file=sys.stderr)
    return code


def _helper_action(args, action: str, helper_args: list[str]) -> int:
    emit = make_emitter(args.json, open_log(action))
    emit({"event": "step", "id": action, "status": "start"})
    cmd = system.helper_cmd("bootc-helper", helper_args)
    lines: list[str] = []

    def tee(ev: dict) -> None:
        if ev["event"] == "log":
            lines.append(ev["line"])
        emit(ev)

    rc = run_stream(cmd, tee)
    if rc:
        hint = system.explain_pkexec(rc, "\n".join(lines))
        if hint:
            emit({"event": "log", "line": hint})
    emit({"event": "step", "id": action, "status": "ok" if rc == 0 else "fail"})
    st = system.get_status() if rc == 0 else {}
    emit({"event": "done", "ok": rc == 0, "reboot_required": bool(st.get("reboot_required"))})
    return 0 if rc == 0 else 1


def _upgrade(args) -> int:
    only = args.only.split(",") if args.only else None
    if only and (bad := [t for t in only if t not in system.TARGETS]):
        return _fail(args, f"unknown target(s): {','.join(bad)}", 2)
    emit = make_emitter(args.json, open_log("upgrade"))
    failed: list[str] = []
    for target, cmds in system.upgrade_steps(only):
        emit({"event": "step", "id": target, "status": "start"})
        ok = all(system.accepted_rc(target, c, run_stream(c, emit)) for c in cmds)
        emit({"event": "step", "id": target, "status": "ok" if ok else "fail"})
        if not ok:
            failed.append(target)
    st = system.get_status()
    emit({"event": "done", "ok": not failed, "failed": failed, "reboot_required": bool(st.get("reboot_required"))})
    return 1 if failed else 0


def _check(args) -> int:
    subprocess.run(["systemctl", "start", "boppos-update-monitor.service"], check=False, capture_output=True)
    st = system.get_status()
    flatpak = {"count": None}
    if shutil.which("flatpak"):
        proc = system.run_capture(["flatpak", "remote-ls", "--updates", "--columns=application"], timeout=90)
        if proc.returncode == 0:
            flatpak = {"count": len([ln for ln in proc.stdout.splitlines() if ln.strip()])}
    fw = {"count": None, "devices": []}
    if shutil.which("fwupdmgr"):
        proc = system.run_capture(["fwupdmgr", "get-updates", "--json", "--no-authenticate"], timeout=60)
        fw = system.parse_fw_updates(proc.stdout, proc.returncode)
    data = {"os": st, "flatpak": flatpak, "fw": fw, "brew": {"count": None}}
    lines = [f"OS: {'update available' if st.get('update_available') else 'up to date'}"
             if "error" not in st else f"OS: {st['error']}",
             f"Flatpak: {flatpak['count'] if flatpak['count'] is not None else '?'} update(s)",
             f"Firmware: {fw['count'] if fw['count'] is not None else '?'} update(s)"]
    _print(args, data, "\n".join(lines))
    return 0


def _support_report(args) -> int:
    from .kargs_cli import HELPER as KARGS_HELPER

    sections: dict[str, str] = {}
    about_data = system.about(system.get_status())
    sections["about"] = json.dumps(about_data, indent=2)
    sections["bootc status"] = json.dumps(system.get_status(), indent=2)
    sections["update monitor"] = json.dumps(system.read_update_status(), indent=2)
    if os.path.exists(KARGS_HELPER):
        for verb in ("dropins", "status"):
            proc = system.run_capture(["bopp-kargs", "--json", verb] if verb == "dropins"
                                      else system.helper_cmd("kargs-helper", [verb]))
            sections[f"kargs {verb}"] = proc.stdout or proc.stderr
    sections["/proc/cmdline"] = Path("/proc/cmdline").read_text()
    for log in sorted(log_dir().glob("*.log"))[-5:]:
        sections[f"log {log.name}"] = "\n".join(log.read_text(errors="replace").splitlines()[-200:])
    out = Path.home() / f"boppos-support-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    out.write_text(build_report(sections))
    out.chmod(0o600)
    _print(args, {"path": str(out)}, str(out))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd = args.cmd
    if cmd == "status":
        st = system.get_status()
        _print(args, st, st.get("error") or json.dumps(st, indent=2))
        return 1 if "error" in st else 0
    if cmd == "about":
        info = system.about(system.get_status())
        _print(args, info, "\n".join(f"{k}: {v}" for k, v in info.items() if k != "dmi"))
        return 0
    if cmd == "check":
        return _check(args)
    if cmd == "upgrade":
        return _upgrade(args)
    if cmd in ("rollback",):
        return _helper_action(args, cmd, [cmd])
    if cmd in ("pin", "unpin"):
        return _helper_action(args, cmd, [cmd, args.index])
    if cmd == "switch":
        try:
            image = validate_image_ref(args.image)
        except UsageError as exc:
            return _fail(args, str(exc), 2)
        return _helper_action(args, cmd, [cmd, image])
    if cmd == "reboot":
        return subprocess.run(["systemctl", "reboot"], check=False).returncode
    return _support_report(args)
