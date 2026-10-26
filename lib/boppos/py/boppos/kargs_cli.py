"""Command line front end for boppos.kargs (used by bin/bopp-kargs and the root helper)."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from . import kargs
from .bootc import parse_status
from .dmi import read_dmi
from .kargs import KargError

MUTATING = {"add", "remove", "apply", "enable", "disable", "edit"}
HELPER = "/usr/libexec/boppos/kargs-helper"
VIA_HELPER = {"status", "apply", "enable", "disable"}  # what the polkit-gated helper exposes


def _bootc_status(path: str | None) -> dict:
    if path:
        return parse_status(Path(path).read_text())
    try:
        proc = subprocess.run(
            ["bootc", "status", "--format=json"], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        return {"error": str(exc)}
    if proc.returncode:
        return {"error": proc.stderr.strip() or f"bootc exited {proc.returncode}"}
    return parse_status(proc.stdout)


def _cmdline(root: str) -> str:
    try:
        return (Path(root) / "proc/cmdline").read_text().strip()
    except OSError as exc:
        raise KargError(f"cannot read /proc/cmdline: {exc}", "no-cmdline") from exc


def _dropin_json(d: kargs.Dropin, dmi: dict[str, str]) -> dict:
    return {
        "name": d.name,
        "source": d.source,
        "description": d.description,
        "kargs": d.kargs,
        "enabled": d.enabled,
        "matches_device": kargs.matches_device(d, dmi),
        "error": d.error,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bopp-kargs", description="BoppOS kernel-argument manager")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--root", default="/", help=argparse.SUPPRESS)
    p.add_argument("--bootc-status", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="boot entries and pending-change state")
    lst = sub.add_parser("list", help="arguments of the next-boot entry, one per line")
    lst.add_argument("index", nargs="?", type=int, help="entry index (default: next boot)")
    for name in ("add", "remove"):
        s = sub.add_parser(name, help=f"{name} an argument (token-exact)")
        s.add_argument("arg")
        s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("apply", help="apply enabled drop-ins that match this device")
    s.add_argument("--dry-run", action="store_true")
    sub.add_parser("dropins", help="drop-ins and whether they match this device")
    for name in ("enable", "disable"):
        s = sub.add_parser(name, help=f"{name} a drop-in and apply")
        s.add_argument("name")
        s.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("diff", help="compare the arguments of two entries")
    d.add_argument("a", nargs="?", type=int, default=0)
    d.add_argument("b", nargs="?", type=int, default=1)
    sub.add_parser("edit", help="edit the next-boot arguments in $EDITOR")
    return p


def _emit(args, data, text: str) -> None:
    print(json.dumps(data, indent=2) if args.json else text)


def _entry_by_index(root: str, index: int | None, args) -> kargs.Entry:
    entries = kargs.load_entries(root)
    if index is None:
        return kargs.target_entry(entries, _bootc_status(args.bootc_status), _cmdline(root))
    if not 0 <= index < len(entries):
        raise KargError(f"no entry with index {index}", "no-entry")
    return entries[index]


def _via_helper(args: argparse.Namespace) -> int:
    """Run as a normal user: ask the polkit-gated root helper (no password for active wheel)."""
    if getattr(args, "dry_run", False):
        raise KargError("--dry-run needs root; run it with sudo", "not-root")
    argv = [args.cmd, *([args.name] if hasattr(args, "name") else [])]
    proc = subprocess.run(["pkexec", HELPER, *argv], capture_output=True, text=True, check=False)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise KargError(proc.stderr.strip() or "privileged helper failed", "helper") from None
    if args.json:
        print(json.dumps(data, indent=2))
    elif "error" in data:
        raise KargError(data["error"], data.get("code", "error"))
    else:
        print(_summary(data) if args.cmd != "status" else json.dumps(data, indent=2))
    return 1 if "error" in data else 0


def run(args: argparse.Namespace) -> int:
    root = args.root
    if root == "/" and os.geteuid() != 0 and args.cmd in VIA_HELPER and os.path.exists(HELPER):
        return _via_helper(args)
    if args.cmd in MUTATING and root == "/" and os.geteuid() != 0:
        raise KargError(f"'{args.cmd}' needs root; run it with sudo", "not-root")
    dry = getattr(args, "dry_run", False)

    if args.cmd == "status":
        status = _bootc_status(args.bootc_status)
        cmdline = _cmdline(root)
        key = kargs.booted_deploy_key(cmdline)
        entries = [
            {
                "index": i,
                "file": e.path.name,
                "title": e.title,
                "commit": e.commit,
                "booted": e.deploy_key == key,
                "options": e.options,
            }
            for i, e in enumerate(kargs.load_entries(root))
        ]
        blocked = status.get("error") or (
            "staged" if status.get("staged") or status.get("rollback_queued") else None
        )
        data = {"entries": entries, "editable": not blocked, "blocked_reason": blocked,
                "managed": kargs.read_managed(root)}
        text = "\n".join(
            f"{'*' if e['booted'] else ' '} [{e['index']}] {e['title']} ({e['file']})" for e in entries
        )
        if blocked:
            text += f"\nchanges blocked: {blocked}"
        _emit(args, data, text)
    elif args.cmd == "list":
        entry = _entry_by_index(root, args.index, args)
        _emit(args, {"options": entry.options}, "\n".join(entry.options))
    elif args.cmd == "diff":
        a, b = (_entry_by_index(root, i, args).options for i in (args.a, args.b))
        data = {"only_a": [t for t in a if t not in b], "only_b": [t for t in b if t not in a]}
        _emit(args, data, "\n".join([*(f"- {t}" for t in data["only_a"]), *(f"+ {t}" for t in data["only_b"])]))
    elif args.cmd == "dropins":
        dmi = read_dmi(root)
        items = [_dropin_json(d, dmi) for d in kargs.load_dropins(root).values()]
        text = "\n".join(
            f"{'on ' if i['enabled'] else 'off'} {'match' if i['matches_device'] else 'skip '} "
            f"{i['name']}: {' '.join(i['kargs'])}{'  ERROR: ' + i['error'] if i['error'] else ''}"
            for i in items
        )
        _emit(args, {"dropins": items, "device": dmi}, text)
    else:
        status = _bootc_status(args.bootc_status)
        cmdline = _cmdline(root)
        if args.cmd == "add":
            res = kargs.apply_changes(root, status, cmdline, add=[args.arg], dry_run=dry)
        elif args.cmd == "remove":
            res = kargs.apply_changes(root, status, cmdline, remove=[args.arg], dry_run=dry)
        elif args.cmd in ("enable", "disable"):
            kargs.set_enabled(root, args.name, args.cmd == "enable")
            res = kargs.apply_dropins(root, status, cmdline, dry_run=dry)
        elif args.cmd == "apply":
            res = kargs.apply_dropins(root, status, cmdline, dry_run=dry)
        else:  # edit
            res = _edit(root, status, cmdline)
        _emit(args, res, _summary(res))
    return 0


def _summary(res: dict) -> str:
    if not res["changed"]:
        return "No changes."
    lines = [*(f"+ {t}" for t in res["added"]), *(f"- {t}" for t in res["removed"])]
    if res["reboot_required"]:
        lines.append("Reboot to apply.")
    return "\n".join(lines)


def _edit(root: str, status: dict, cmdline: str) -> dict:
    entry = kargs.target_entry(kargs.load_entries(root), status, cmdline)
    editor = shlex.split(os.environ.get("SUDO_EDITOR") or os.environ.get("VISUAL")
                         or os.environ.get("EDITOR") or "vi")
    with tempfile.NamedTemporaryFile("w+", suffix=".karg", delete=False) as fh:
        fh.write("\n".join(entry.options) + "\n")
        tmp = fh.name
    try:
        if subprocess.run([*editor, tmp], check=False).returncode:
            raise KargError("editor failed", "editor")
        new = kargs.tokenize(Path(tmp).read_text())
    finally:
        Path(tmp).unlink(missing_ok=True)
    add = [t for t in new if t not in entry.options]
    remove = [t for t in entry.options if t not in new]
    return kargs.apply_changes(root, status, cmdline, add=add, remove=remove)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except KargError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "code": exc.code}))
        else:
            print(f"bopp-kargs: {exc}", file=sys.stderr)
        return 1
