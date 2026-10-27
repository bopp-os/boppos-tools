"""Parse `bootc status --format=json` into plain dicts."""

from __future__ import annotations

import json
from typing import Any

PROGRESS_INTERVAL = 0.25  # seconds between redraws of the same task


def _image(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    img = entry.get("image") or {}
    ref = (img.get("image") or {}).get("image", "")
    ostree = entry.get("ostree") or {}
    return {
        "ref": ref,
        "transport": (img.get("image") or {}).get("transport", "registry"),
        "version": img.get("version"),
        "timestamp": img.get("timestamp"),
        "digest": img.get("imageDigest") or ref,
        "pinned": bool(entry.get("pinned")),
        "checksum": ostree.get("checksum"),
        "serial": ostree.get("deploySerial"),
    }


def parse_status(raw: str) -> dict[str, Any]:
    """Return {booted, staged, rollback, update_available, rollback_queued, reboot_required}
    or {error}."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"error": f"parse: {exc}"}
    status = data.get("status") or {}
    booted = _image(status.get("booted"))
    staged = _image(status.get("staged"))
    rollback = _image(status.get("rollback"))
    # ostree lists deployments in boot order: staged first; a queued rollback goes before booted.
    queued = bool(status.get("rollbackQueued"))
    order = [staged, rollback, booted] if queued else [staged, booted, rollback]
    for i, entry in enumerate(e for e in order if e):
        entry["index"] = i
    return {
        "booted": booted,
        "staged": staged,
        "rollback": rollback,
        "update_available": bool(staged and (not booted or staged["digest"] != booted["digest"])),
        "rollback_queued": bool(status.get("rollbackQueued")),
        "reboot_required": staged is not None or bool(status.get("rollbackQueued")),
    }


def human_size(n: float) -> str:
    if n < 1000:
        return f"{n:.0f} B"
    for unit in ("kB", "MB", "GB", "TB"):
        n /= 1000
        if n < 1000 or unit == "TB":
            break
    return f"{n:.1f} {unit}"


def _duration(seconds: float) -> str:
    if seconds < 60:
        return "under a minute"
    minutes = round(seconds / 60)
    return f"about {minutes} min" if minutes < 60 else f"about {minutes // 60} h {minutes % 60} min"


class ProgressReader:
    """Turns `bootc upgrade/switch --progress-fd` JSON lines into log and progress events.

    Each new task (pulling, importing, staging) starts with a log line; its updates are progress events with a
    detail line, a percent and a short text for the bar. Download speed is a running average over 2 s windows.
    """

    def __init__(self) -> None:
        self.task: str | None = None
        self.shown = float("-inf")  # when the last progress event went out
        self.mark: tuple[float, int] | None = None  # (time, bytes) the speed is measured from
        self.rate: float | None = None

    def feed(self, line: str, now: float) -> list[dict] | None:
        """Events for one output line, or None when the line is not bootc progress."""
        if not line.startswith('{"type":'):
            return None
        try:
            ev = json.loads(line)
        except ValueError:
            return None
        kind = ev.get("type") if isinstance(ev, dict) else None
        if kind not in ("ProgressBytes", "ProgressSteps"):
            return []  # Start, or a kind added later
        out: list[dict] = []
        task = str(ev.get("task") or kind)
        if task != self.task:
            self.task, self.mark, self.rate, self.shown = task, None, None, float("-inf")
            out.append({"event": "log", "line": self._heading(kind, ev)})
        steps, steps_total = int(ev.get("steps") or 0), int(ev.get("stepsTotal") or 0)
        done = kind == "ProgressSteps" and steps_total > 0 and steps >= steps_total
        if kind == "ProgressBytes":
            done = int(ev.get("bytes") or 0) >= int(ev.get("bytesTotal") or 0) > 0
        if now - self.shown < PROGRESS_INTERVAL and not done:
            return out
        self.shown = now
        out.append(self._bytes(ev, now) if kind == "ProgressBytes" else self._steps(ev))
        return out

    @staticmethod
    def _heading(kind: str, ev: dict) -> str:
        if kind == "ProgressBytes" and ev.get("task") == "pulling":
            total, cached = int(ev.get("bytesTotal") or 0), int(ev.get("bytesCached") or 0)
            text = f"Downloading {int(ev.get('stepsTotal') or 0)} layers ({human_size(total)})"
            return text + (f", {human_size(cached)} already on disk" if cached else "")
        return str(ev.get("description") or ev.get("task") or kind)

    def _bytes(self, ev: dict, now: float) -> dict:
        got, total = int(ev.get("bytes") or 0), int(ev.get("bytesTotal") or 0)
        steps, steps_total = int(ev.get("steps") or 0), int(ev.get("stepsTotal") or 0)
        if self.mark is None or got < self.mark[1]:
            self.mark = (now, got)
        elif now - self.mark[0] >= 2:
            speed = (got - self.mark[1]) / (now - self.mark[0])
            self.rate = speed if self.rate is None else 0.7 * self.rate + 0.3 * speed
            self.mark = (now, got)
        parts = [f"{human_size(got)} of {human_size(total)}" if total else human_size(got)]
        if steps_total:
            parts.insert(0, f"Layer {min(steps + 1, steps_total)} of {steps_total}")
        if self.rate:
            parts.append(f"{human_size(self.rate)}/s")
            if total > got:
                parts.append(f"{_duration((total - got) / self.rate)} left")
        pct = got * 100 / total if total else (steps * 100 / steps_total if steps_total else None)
        bar = None
        if pct is not None:
            bar = f"{pct:.0f}%" + (f" · {human_size(got)} of {human_size(total)}" if total else "")
        return {"event": "progress", "line": " · ".join(parts), "percent": pct, "bar": bar}

    @staticmethod
    def _steps(ev: dict) -> dict:
        steps, steps_total = int(ev.get("steps") or 0), int(ev.get("stepsTotal") or 0)
        active = next((s.get("description") for s in ev.get("subtasks") or []
                       if isinstance(s, dict) and not s.get("completed")), None)
        line = str(ev.get("description") or ev.get("task") or "")
        if active and active != line:
            line += f": {active}"
        if steps_total > 1:
            line += f" ({min(steps, steps_total)} of {steps_total})"
        pct = steps * 100 / steps_total if steps_total else None
        return {"event": "progress", "line": line, "percent": pct, "bar": None}
