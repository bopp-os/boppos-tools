"""The hourly update check (boppos-update-monitor.service, as root).

Looks for a newer OS image, then writes /run/boppos/update-status.json for the tray to read:
  update_available, checked_at, current_image, staged_image, transport, diff, error
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .bootc import parse_status

STATUS_FILE = Path("/run/boppos/update-status.json")
RETRIES = 3
RETRY_DELAY = 5
_AVAILABLE = re.compile(r"available update|upgrade available", re.I)
_DEFINITIVE = re.compile(r"available update|upgrade available|no update", re.I)

Run = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _status(available: bool, current: str, staged: str, transport: str, diff: str, error: str) -> dict:
    return {
        "update_available": available,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_image": current,
        "staged_image": staged or None,
        "transport": transport,
        "diff": diff or None,
        "error": error or None,
    }


def _failure(message: str) -> dict:
    return _status(False, "unknown", "", "unknown", "", message)


def _transport(raw: str) -> str:
    try:
        return (((json.loads(raw).get("spec") or {}).get("image") or {}).get("transport")) or "registry"
    except (json.JSONDecodeError, AttributeError):
        return "registry"


def check_upgrade_output(text: str) -> tuple[bool, str]:
    """(update found, its digest) from `bootc upgrade --check` output."""
    for line in text.splitlines():
        if _AVAILABLE.search(line):
            return True, line.split()[-1] if line.split() else ""
    return False, ""


def check(run: Run = _run, which: Callable[[str], str | None] = shutil.which,
          sleep: Callable[[float], None] = time.sleep) -> dict:
    """The status document for right now. Never raises: problems become the `error` field."""
    if not which("bootc"):
        return _failure("bootc not found in PATH")
    proc = run(["bootc", "status", "--format=json"])
    if proc.returncode:
        return _failure(f"bootc status failed: {(proc.stdout + proc.stderr).strip()}")
    parsed = parse_status(proc.stdout)
    if "error" in parsed:
        return _failure(parsed["error"].removeprefix("parse: "))
    booted, staged = parsed["booted"], parsed["staged"]
    current = (booted["digest"] if booted else "") or "unknown"
    image_ref = booted["ref"] if booted else ""
    staged_digest = staged["digest"] if staged else ""
    transport = _transport(proc.stdout)
    available = bool(staged_digest and staged_digest != current)

    if not available:
        # bootc only shows a staged image after an earlier check. The registry digest can only rule an
        # update out: an install from containers-storage (offline installer media) records a local
        # manifest digest that never matches the registry's, so a different digest proves nothing.
        remote = ""
        if which("skopeo") and image_ref:
            for _ in range(RETRIES):  # a few tries: the network may just be waking from sleep
                out = run(["skopeo", "inspect", "--format", "{{.Digest}}", f"docker://{image_ref}"])
                remote = out.stdout.strip() if out.returncode == 0 else ""
                if remote:
                    break
                sleep(RETRY_DELAY)
        if remote != current:  # unknown or different: bootc decides
            text = ""
            for _ in range(RETRIES):
                out = run(["bootc", "upgrade", "--check"])
                text = out.stdout + out.stderr
                if _DEFINITIVE.search(text):
                    break
                sleep(RETRY_DELAY)
            available, found = check_upgrade_output(text)
            if available:
                staged_digest = found
        if available:  # the check may have staged metadata: prefer the digest bootc itself reports
            again = run(["bootc", "status", "--format=json"])
            if again.returncode == 0:
                restaged = parse_status(again.stdout).get("staged")
                if restaged and restaged["digest"]:
                    staged_digest = restaged["digest"]

    diff = ""
    if available and which("bopp-diff"):
        out = run(["bopp-diff"])
        diff = (out.stdout + out.stderr).strip()
    return _status(available, current, staged_digest, transport, diff, "")


def write_status(doc: dict, path: Path = STATUS_FILE) -> None:
    """Atomic: readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.chmod(tmp, 0o644)
    tmp.replace(path)


def main() -> int:
    write_status(check())
    # Wake any running tray: it sleeps until this signal rather than polling.
    subprocess.run(["pkill", "-SIGUSR1", "-f", "bopp-tray"], capture_output=True, check=False)
    return 0
