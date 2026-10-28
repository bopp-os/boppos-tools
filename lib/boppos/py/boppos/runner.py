"""Run a command and report its output as events (JSON lines for the UI, plain text otherwise)."""

from __future__ import annotations

import codecs
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .bootc import ProgressReader

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


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_PERCENT = re.compile(r"^[#=\-O. ]*?\s*(\d{1,3}(?:\.\d+)?)%\s*$")
_BAR_NOISE = re.compile(r"^[#O=\- ]+$")  # curl's initial "##O=-  #" scribble
BAR_WIDTH = 24


def clean(text: str) -> str:
    return _CONTROL.sub("", _ANSI.sub("", text)).rstrip()


def format_progress(text: str) -> tuple[str, float | None]:
    """Tidy one progress redraw. A curl-style bar of any width becomes a fixed-width bar plus the percent.

    Returns (text, percent or None); empty text means "nothing worth showing".
    """
    text = clean(text)
    if not text or _BAR_NOISE.match(text):
        return "", None
    m = _PERCENT.match(text)
    if m:
        pct = max(0.0, min(100.0, float(m.group(1))))
        filled = round(pct * BAR_WIDTH / 100)
        return f"[{'#' * filled}{'.' * (BAR_WIDTH - filled)}] {pct:5.1f}%", pct
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s?%", text)
    return text, (min(100.0, float(m.group(1))) if m else None)


class LineSplitter:
    """Splits process output into log lines (\\n) and in-place progress redraws (\\r).

    "\\r\\n" counts as an ordinary newline, so Windows-style output is not mistaken for progress.
    """

    def __init__(self) -> None:
        self._cur: list[str] = []
        self._cr = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for ch in text:
            if self._cr:
                self._cr = False
                if ch == "\n":
                    out.append(("log", "".join(self._cur)))
                    self._cur = []
                    continue
                out.append(("progress", "".join(self._cur)))
                self._cur = []
            if ch == "\r":
                self._cr = True
            elif ch == "\n":
                out.append(("log", "".join(self._cur)))
                self._cur = []
            else:
                self._cur.append(ch)
        return out

    def flush(self) -> list[tuple[str, str]]:
        text, cr = "".join(self._cur), self._cr
        self._cur, self._cr = [], False
        if not text:
            return []
        return [("log" if not cr else "progress", text)]


def make_emitter(as_json: bool, log_path: Path | None = None) -> Emit:
    """Prints events. Progress redraws replace each other on a terminal and stay out of the log file,
    which only gets the final state of each bar."""
    fh = open(log_path, "a") if log_path else None  # noqa: SIM115 - lives as long as the emitter
    tty = not as_json and os.isatty(1)
    pending: list[str] = []  # the latest progress text not yet committed to a line

    def to_log(line: str) -> None:
        if fh:
            fh.write(line + "\n")
            fh.flush()

    def commit() -> None:
        if pending:
            text = pending.pop()
            if tty:
                print(flush=True)
            elif not as_json:
                print(text, flush=True)
            to_log(text)

    def emit(ev: dict) -> None:
        kind = ev.get("event")
        if kind == "progress":
            if as_json:
                print(json.dumps(ev), flush=True)
            else:
                pending[:] = [ev["line"]]
                if tty:
                    print("\r" + ev["line"].ljust(shutil_width()), end="", flush=True)
            return
        if kind == "log":
            commit()
            to_log(ev["line"])
            print(json.dumps(ev) if as_json else ev["line"], flush=True)
        else:
            commit()
            if as_json:
                print(json.dumps(ev), flush=True)
                to_log(json.dumps(ev))
            elif kind == "step":
                name = ev.get("label") or ev["id"]
                if ev["status"] == "start":
                    print(f"\n==> {name}", flush=True)
                else:
                    status = {"ok": "done", "fail": "FAILED"}.get(ev["status"], ev["status"])
                    print(f"==> {name}: {status}", flush=True)
            elif kind == "done":
                print("Done." if ev["ok"] else "Failed.", flush=True)

    return emit


def shutil_width() -> int:
    import shutil

    return shutil.get_terminal_size((80, 20)).columns - 1


def run_stream(cmd: list[str], emit: Emit) -> int:
    """Run cmd (no shell), forwarding output. Lines become log events; \\r redraws (progress bars)
    become progress events. Returns the exit code."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as exc:
        emit({"event": "log", "line": f"cannot run {cmd[0]}: {exc}"})
        return 127
    assert proc.stdout is not None
    splitter = LineSplitter()
    bootc_progress = ProgressReader()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def deliver(parts: list[tuple[str, str]]) -> None:
        for kind, text in parts:
            if kind == "progress":
                shown, pct = format_progress(text)
                if shown:
                    emit({"event": "progress", "line": shown, "percent": pct})
            else:
                line = clean(text)
                if (events := bootc_progress.feed(line, time.monotonic())) is not None:
                    for ev in events:  # bootc's --progress-fd JSON, sent through the same pipe
                        emit(ev)
                    continue
                if _PERCENT.match(line):  # the final redraw of a bar arrives with its newline
                    line = format_progress(line)[0] or line
                if line or text == "":
                    emit({"event": "log", "line": line})

    fd = proc.stdout.fileno()
    while chunk := os.read(fd, 4096):
        deliver(splitter.feed(decoder.decode(chunk)))
    deliver(splitter.feed(decoder.decode(b"", final=True)))
    deliver(splitter.flush())
    return proc.wait()
