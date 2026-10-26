"""Run a command and report its output as events (JSON lines for the UI, plain text otherwise)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

Emit = Callable[[dict], None]
LOG_KEEP = 20


def log_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "boppos-deck/logs"


def open_log(action: str, directory: Path | None = None) -> Path:
    """New log file for an action; older ones beyond LOG_KEEP are removed."""
    d = directory or log_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{action}.log"
    for old in sorted(d.glob("*.log"))[:-LOG_KEEP]:
        old.unlink(missing_ok=True)
    return path


def make_emitter(as_json: bool, log_path: Path | None = None) -> Emit:
    fh = open(log_path, "a") if log_path else None  # noqa: SIM115 - lives as long as the emitter

    def emit(ev: dict) -> None:
        if ev.get("event") == "log":
            line = ev["line"]
            if fh:
                fh.write(line + "\n")
                fh.flush()
            print(json.dumps(ev) if as_json else line, flush=True)
        elif as_json:
            print(json.dumps(ev), flush=True)
            if fh:
                fh.write(json.dumps(ev) + "\n")
                fh.flush()
        elif ev.get("event") == "step":
            print(f"==> {ev['id']}: {ev['status']}", flush=True)
        elif ev.get("event") == "done":
            print("Done." if ev["ok"] else "Failed.", flush=True)

    return emit


def run_stream(cmd: list[str], emit: Emit) -> int:
    """Run cmd (no shell), forwarding merged stdout/stderr line by line. Returns the exit code."""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
        )
    except OSError as exc:
        emit({"event": "log", "line": f"cannot run {cmd[0]}: {exc}"})
        return 127
    assert proc.stdout is not None
    for line in proc.stdout:
        emit({"event": "log", "line": line.rstrip("\n")})
    return proc.wait()
