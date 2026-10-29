"""bin/bopp-tray with pystray and Pillow stubbed out: menu wiring and the background update path."""

import importlib.machinery
import importlib.util
import json
import stat
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPT = Path(__file__).parent.parent / "bin/bopp-tray"


class Menu:
    SEPARATOR = "---"

    def __init__(self, *items):
        self.items = items


class MenuItem:
    def __init__(self, text, action=None, enabled=True, visible=True, default=False, **_kw):
        self.text, self.action, self.enabled, self.visible, self.default = text, action, enabled, visible, default


class Icon:
    def __init__(self, name, icon, title, menu):
        self.icon, self.title, self.menu, self.stopped = icon, title, menu, False

    def update_menu(self):
        pass

    def stop(self):
        self.stopped = True


@pytest.fixture
def tray(monkeypatch, tmp_path):
    fake = types.ModuleType("pystray")
    fake.Menu, fake.MenuItem, fake.Icon = Menu, MenuItem, Icon
    monkeypatch.setitem(sys.modules, "pystray", fake)
    for name in ("PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont"):
        monkeypatch.setitem(sys.modules, name, MagicMock())
    monkeypatch.setenv("PATH", str(tmp_path) + ":/usr/bin")
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # never leave a bin/__pycache__ behind
    loader = importlib.machinery.SourceFileLoader("bopp_tray", str(SCRIPT))
    spec = importlib.util.spec_from_loader("bopp_tray", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    mod.STATE_FILE = tmp_path / "state.json"
    for name in ("boppos-deck", "bopp-system"):
        (tmp_path / name).write_text("#!/bin/sh\n")
        (tmp_path / name).chmod(0o755)
    return mod


def flat(menu):
    return [i if isinstance(i, str) else i.text for i in menu.items]


def test_menu_has_the_new_order_and_names(tray):
    t = tray.BoppTray()
    t._status = {"update_available": False, "checked_at": "2026-10-26T12:30:00Z"}
    labels = flat(t._build_menu())
    assert labels[0] == "BoppOS is up to date"
    assert labels[2:6] == ["Open BoppOS Settings", "---", "Check for Updates", "Install Updates"]
    assert labels[-1] == "Quit" and labels[-2].startswith("Last checked")
    items = {i.text: i for i in t._build_menu().items if not isinstance(i, str)}
    assert items["Open BoppOS Settings"].default is True
    sub = [i.text for i in items["Install Updates"].action.items if not isinstance(i, str)]
    assert sub[0].startswith("Everything") and "Containers (Distrobox & Litterbox)…" in sub


def test_restart_state_adds_the_restart_item(tray):
    t = tray.BoppTray()
    t._status = {"update_available": False}
    t._deploy = {"reboot_required": True}
    assert flat(t._build_menu())[:2] == ["Restart to finish the update", "Restart to Finish Update…"]


def test_open_settings_and_quit_handlers(tray, monkeypatch):
    t = tray.BoppTray()
    launched = []
    monkeypatch.setattr(tray, "launch_detached", lambda cmd: launched.append(cmd) or True)
    t._handlers["open-settings"]()
    assert launched == [["boppos-deck"]]
    t._handlers["quit"]()
    assert t._icon.stopped


def test_background_update_reports_and_offers_restart(tray, monkeypatch, tmp_path):
    exe = tmp_path / "bopp-system"
    exe.write_text("#!/bin/sh\n"
                   'echo \'{"event": "log", "line": "downloading"}\'\n'
                   'echo \'{"event": "done", "ok": true, "reboot_required": true}\'\n')
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    notes, restarts = [], []
    monkeypatch.setattr(tray, "_notify", lambda *a, **k: notes.append(a))
    monkeypatch.setattr(tray, "ask_restart", lambda: restarts.append(1))
    monkeypatch.setattr(tray, "_trigger_update_check", lambda: None)
    t = tray.BoppTray()
    t._busy = True
    t._update_worker("os", "the operating system")
    assert restarts == [1] and t._busy is False


def test_failed_update_notifies_with_the_last_log_lines(tray, monkeypatch, tmp_path):
    exe = tmp_path / "bopp-system"
    exe.write_text("#!/bin/sh\n"
                   'echo \'{"event": "log", "line": "not authorized: wrong password"}\'\n'
                   'echo \'{"event": "done", "ok": false}\'\nexit 1\n')
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    notes = []
    monkeypatch.setattr(tray, "_notify", lambda *a, **k: notes.append((a, k)))
    monkeypatch.setattr(tray, "_trigger_update_check", lambda: None)
    t = tray.BoppTray()
    t._update_worker(None, "everything")
    (summary, body), kw = notes[-1]
    assert "failed" in summary and "wrong password" in body and kw.get("urgent")


def test_update_entries_open_the_deck_update_screen(tray, monkeypatch):
    launched = []
    monkeypatch.setattr(tray, "launch_detached", lambda cmd: launched.append(cmd) or True)
    t = tray.BoppTray()
    t._on_update("os", "the operating system")
    t._on_update(None, "everything")
    assert launched == [["boppos-deck", "--update", "os"], ["boppos-deck", "--update", "all"]]
    assert t._busy is False  # the window runs the update; the tray has no background job


def test_second_update_while_busy_is_refused(tray, monkeypatch, tmp_path):
    (tmp_path / "boppos-deck").unlink()  # the background path is the one that tracks "busy"
    notes = []
    monkeypatch.setattr(tray, "_notify", lambda *a, **k: notes.append(a))
    t = tray.BoppTray()
    t._busy = True
    t._on_update(None, "everything")
    assert notes and "already running" in notes[0][0]


def test_without_bopp_system_updates_fall_back_to_a_terminal(tray, monkeypatch, tmp_path):
    (tmp_path / "boppos-deck").unlink()
    (tmp_path / "bopp-system").unlink()
    ran = []
    monkeypatch.setattr(tray, "open_in_terminal", lambda cmd, title="": ran.append(cmd))
    tray.BoppTray()._on_update(None, "everything")
    assert "boppos-update" in ran[0][-1]


def test_deployment_state_none_when_unavailable(tray, tmp_path):
    (tmp_path / "bopp-system").write_text('#!/bin/sh\necho \'{"error": "x"}\'\n')
    assert tray.read_deployment_state() is None
    (tmp_path / "bopp-system").write_text('#!/bin/sh\necho \'{"reboot_required": true}\'\n')
    assert tray.read_deployment_state() == {"reboot_required": True}
    json.dumps({})


def test_a_second_copy_exits_quietly_with_status_zero(tray, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    tray.single_instance()  # the first copy holds the lock
    assert tray.lock_path() == tmp_path / "run/bopp-tray.lock" and tray.lock_path().exists()
    held = tray._instance_lock
    with pytest.raises(SystemExit) as exc:
        tray.single_instance()  # a second copy: flock conflicts between separate opens, even in one process
    assert exc.value.code == 0 and tray._instance_lock is held


def test_without_xdg_runtime_dir_the_lock_goes_to_a_cache_dir_that_is_created(tray, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    tray.STATE_FILE = tmp_path / "missing/cache/state.json"
    tray.single_instance()
    assert (tmp_path / "missing/cache/bopp-tray.lock").exists() and tray._instance_lock is not None


def test_only_the_user_unit_starts_the_tray():
    root = SCRIPT.parent.parent
    assert not (root / "autostart/bopp-tray.desktop").exists()
    assert "ExecStart=/usr/bin/bopp-tray" in (root / "systemd/user/bopp-tray.service").read_text()
