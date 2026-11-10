"""bopp-performance: battery settings and the sched-ext scheduler (UI/CLI backend for the Performance page)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from . import performance, system


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bopp-performance", description="BoppOS performance and battery settings")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="current settings, hardware support and scheduler")
    s = sub.add_parser("set-abm", help="screen power saving on battery: system or 0-3")
    s.add_argument("value")
    s = sub.add_parser("set-boost", help="CPU boost: system or off-on-battery")
    s.add_argument("value", choices=performance.BOOST_CHOICES)
    s = sub.add_parser("scx", help="run and keep a sched-ext scheduler")
    s.add_argument("sched", help="scheduler name from `scxctl list`, or none")
    s.add_argument("mode", nargs="?", default="auto", choices=list(performance.SCX_MODES))
    s = sub.add_parser("recommended", help="apply the recommended settings for this hardware")
    s.add_argument("--dry-run", action="store_true", help="only list what would change")
    return p


def _helper(args: list[str]) -> dict:
    try:
        proc = subprocess.run(system.helper_cmd("performance-helper", args), capture_output=True, text=True,
                              check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc)}
    try:
        res = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        res = {}
    if proc.returncode and "error" not in res:
        reason = system.explain_pkexec(proc.returncode, proc.stderr) or proc.stderr.strip() or "failed"
        res = {**res, "error": "; ".join(res.get("errors") or []) or reason}
    return res


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "status":
        res = performance.status()
    elif args.cmd == "scx":
        res = _helper(["scx", args.sched, args.mode])
    elif args.cmd == "recommended" and args.dry_run:
        res = {"changes": performance.status()["recommended"]["changes"]}
    elif args.cmd == "recommended":
        res = _helper(["recommended"])
    else:
        res = _helper([args.cmd, args.value])
    if args.json:
        print(json.dumps(res, indent=2))
    elif "error" in res:
        print(f"bopp-performance: {res['error']}", file=sys.stderr)
    else:
        print(json.dumps(res, indent=2))
    return 1 if "error" in res else 0
