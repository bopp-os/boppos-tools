"""Running the bopp-* CLIs for the UI: tool lookup, background jobs with JSON-line events, settings."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from ..session import gaming_mode  # noqa: F401 - re-exported for the UI

_DEV_BIN = Path(__file__).resolve().parents[5] / "bin"


def tool(name: str) -> str:
    """Path of a bopp-* tool. BOPPOS_DECK_BIN_DIR overrides the lookup (development and tests)."""
    override = os.environ.get("BOPPOS_DECK_BIN_DIR")
    if override and (Path(override) / name).exists():
        return str(Path(override) / name)
    if (_DEV_BIN / name).exists():
        return str(_DEV_BIN / name)
    return name


def parse_event(line: str) -> dict:
    """One output line from an action: a JSON event, or plain text treated as a log line."""
    try:
        ev = json.loads(line)
        if isinstance(ev, dict) and "event" in ev:
            return ev
    except json.JSONDecodeError:
        pass
    return {"event": "log", "line": line}


# A polkit agent shows one dialog at a time: a second pkexec cancels the open dialog, and every cancelled
# dialog counts as a failed login towards pam_faillock. So while developing with pkexec from a checkout
# (no waiver rule installed) the calls run one after another.
_PKEXEC_LOCK = threading.Lock()


def _serialize() -> bool:
    return bool(os.environ.get("BOPPOS_HELPER_DIR")) and os.environ.get("BOPPOS_HELPER_ESCALATE") != "sudo"


def run_json(cmd: list[str], timeout: int = 120) -> dict | list:
    """Run a CLI that prints one JSON document. Failures come back as {"error": ...}."""
    try:
        if _serialize():
            with _PKEXEC_LOCK:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        else:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc)}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": (proc.stderr or proc.stdout).strip() or f"{cmd[0]} exited {proc.returncode}"}


class Job:
    """Runs commands one after another on a thread, delivering parsed events through `deliver`.

    `deliver` must be thread-safe (the GTK side passes GLib.idle_add).
    """

    def __init__(self, cmds: list[list[str]], deliver: Callable[[Callable[[], None]], None],
                 on_event: Callable[[dict], None], on_finish: Callable[[bool, dict], None]):
        self.cmds, self.deliver, self.on_event, self.on_finish = cmds, deliver, on_event, on_finish
        self.running = False

    def start(self) -> None:
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        ok, final = True, {}
        for cmd in self.cmds:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        errors="replace")
            except OSError as exc:
                self._send({"event": "log", "line": f"cannot run {cmd[0]}: {exc}"})
                ok = False
                break
            assert proc.stdout is not None
            for raw in proc.stdout:
                ev = parse_event(raw.rstrip("\n"))
                if ev["event"] == "done":
                    final.update({k: v for k, v in ev.items() if k != "event"})
                    ok = ok and bool(ev.get("ok", True))
                self._send(ev)
            if proc.wait() != 0:
                ok = False
                break
        self.running = False
        self.deliver(lambda: self.on_finish(ok, final))

    def _send(self, ev: dict) -> None:
        self.deliver(lambda ev=ev: self.on_event(ev))


def settings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "boppos-deck/settings.json"


def load_settings() -> dict:
    try:
        data = json.loads(settings_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(data: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def fingerprint(data: object, view: object = None) -> str | None:
    """A comparable summary of what a page would show: its data plus the settings that change its layout.
    None means "cannot be compared", which always counts as changed."""
    try:
        return json.dumps([data, view], sort_keys=True, default=str)
    except (TypeError, ValueError):
        return None
