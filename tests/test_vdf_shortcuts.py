import json
import os
from pathlib import Path

import pytest
from boppos import steam_shortcut as ss
from boppos import steam_shortcut_cli as cli
from boppos import vdf
from boppos.vdf import VdfError

# A shortcuts.vdf as Steam writes it: one user shortcut with an int, strings and an empty tags map.
SAMPLE = (
    b"\x00shortcuts\x00"
    b"\x000\x00"
    b"\x02appid\x00\x01\x00\x00\x80"
    b"\x01AppName\x00Game\x00"
    b"\x01Exe\x00\"/usr/bin/game\"\x00"
    b"\x00tags\x00\x01" b"0\x00fav\x00\x08"
    b"\x08"
    b"\x08"
    b"\x08"
)


def test_round_trip_is_byte_exact():
    data = vdf.loads(SAMPLE)
    assert data["shortcuts"]["0"]["AppName"] == "Game"
    assert data["shortcuts"]["0"]["appid"] == -2147483647
    assert vdf.dumps(data) == SAMPLE


@pytest.mark.parametrize("raw", [SAMPLE[:-1], SAMPLE + b"\x00", b"\x05bad\x00", b"\x01k\x00v", b"\x02k\x00\x01", b""])
def test_malformed_input_rejected(raw):
    with pytest.raises(VdfError):
        vdf.loads(raw)


def test_duplicate_keys_rejected():
    with pytest.raises(VdfError):
        vdf.loads(b"\x01a\x00x\x00\x01a\x00y\x00\x08")


def test_new_entry_round_trips_and_appid_is_stable():
    e = ss.new_entry()
    assert vdf.loads(vdf.dumps({"shortcuts": {"0": e}}))["shortcuts"]["0"] == e
    assert e["appid"] < 0 and e["appid"] == ss.shortcut_appid('"/usr/bin/boppos-deck"', "BoppOS")
    assert e["Exe"] == '"/usr/bin/boppos-deck"' and e["StartDir"] == '"/usr/bin/"'


def test_add_is_idempotent_and_keeps_existing(tmp_path):
    path = tmp_path / "config/shortcuts.vdf"
    path.parent.mkdir()
    path.write_bytes(SAMPLE)
    assert ss.install_into(path, ss.new_entry(), stamp="t1") == "added"
    data = vdf.loads(path.read_bytes())
    assert list(data["shortcuts"]) == ["0", "1"] and data["shortcuts"]["0"]["AppName"] == "Game"
    assert (tmp_path / "config/shortcuts.vdf.bak-t1").read_bytes() == SAMPLE
    before = path.read_bytes()
    assert ss.install_into(path, ss.new_entry(), stamp="t2") == "present"
    assert path.read_bytes() == before and not (tmp_path / "config/shortcuts.vdf.bak-t2").exists()


def test_creates_file_when_missing(tmp_path):
    path = tmp_path / "shortcuts.vdf"
    assert ss.install_into(path, ss.new_entry()) == "added"
    assert list(vdf.loads(path.read_bytes())["shortcuts"]) == ["0"]


def test_corrupt_file_is_left_untouched(tmp_path):
    path = tmp_path / "shortcuts.vdf"
    path.write_bytes(SAMPLE[:-3])
    with pytest.raises(VdfError):
        ss.install_into(path, ss.new_entry())
    assert path.read_bytes() == SAMPLE[:-3]
    assert [p.name for p in tmp_path.iterdir()] == ["shortcuts.vdf"]  # no backup or temp left behind


def test_only_last_backups_kept(tmp_path):
    path = tmp_path / "shortcuts.vdf"
    path.write_bytes(vdf.dumps({"shortcuts": {}}))
    for i in range(5):
        (tmp_path / f"shortcuts.vdf.bak-{i:02d}").write_text("x")
    ss.install_into(path, ss.new_entry(), stamp="99")
    assert len(list(tmp_path.glob("shortcuts.vdf.bak-*"))) == ss.BACKUPS_KEEP


def fake_proc(root: Path, pid: int, comm: str, uid: int) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "status").write_text(f"Name:\t{comm}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")


def test_steam_running_detection(tmp_path):
    fake_proc(tmp_path, 100, "bash", 1000)
    fake_proc(tmp_path, 200, "steam", 1001)
    assert not ss.steam_running(tmp_path, uid=1000)
    fake_proc(tmp_path, 300, "steam", 1000)
    assert ss.steam_running(tmp_path, uid=1000)


def test_find_shortcut_files_dedupes_symlinked_roots(tmp_path):
    real = tmp_path / ".local/share/Steam/userdata/12345/config"
    real.mkdir(parents=True)
    (tmp_path / ".steam").mkdir()
    os.symlink(tmp_path / ".local/share/Steam", tmp_path / ".steam/steam")
    (tmp_path / ".local/share/Steam/userdata/0/config").mkdir(parents=True)  # anonymous user: skipped
    assert len(ss.find_shortcut_files(tmp_path)) == 1


def test_cli_waits_while_steam_runs_and_marks_when_done(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert cli.main(["--json", "add"]) == cli.EXIT_NO_USERDATA
    (tmp_path / ".local/share/Steam/userdata/42/config").mkdir(parents=True)
    monkeypatch.setattr(ss, "steam_running", lambda *a, **k: True)
    assert cli.main(["--json", "add"]) == cli.EXIT_RUNNING
    monkeypatch.setattr(ss, "steam_running", lambda *a, **k: False)
    assert cli.main(["--json", "add"]) == 0
    assert (tmp_path / ".local/share/Steam/userdata/42/config/shortcuts.vdf").exists()
    assert cli.marker_path().exists()
    capsys.readouterr()
    assert cli.main(["--json", "add"]) == 0  # offered once; user may delete the shortcut afterwards
    assert "already-offered" in capsys.readouterr().out


# --- per-app shortcuts, removal and the queue --------------------------------------------------------

from boppos import steam_apps, steam_sync  # noqa: E402


def flatpak(app_id="org.gimp.GIMP", name="GIMP"):
    return steam_apps.Target(app_id, name, "/usr/bin/flatpak", f"run {app_id}", "")


def test_flatpak_entry_runs_flatpak_with_launch_options():
    e = flatpak().entry()
    assert e["Exe"] == '"/usr/bin/flatpak"' and e["LaunchOptions"] == "run org.gimp.GIMP"
    assert e["AppName"] == "GIMP" and e["appid"] != flatpak("org.x.Y", "Y").entry()["appid"]


def test_shortcuts_are_identified_by_exe_and_launch_options(tmp_path):
    path = tmp_path / "shortcuts.vdf"
    a, b = flatpak("org.a.A", "A"), flatpak("org.b.B", "B")
    assert ss.apply_changes(path, [a.entry(), b.entry()], []) == {"added": 2, "removed": 0}
    assert ss.apply_changes(path, [a.entry()], []) == {"added": 0, "removed": 0}  # idempotent
    data = vdf.loads(path.read_bytes())
    assert ss.has_shortcut(data, "/usr/bin/flatpak", "run org.a.A") and not ss.has_shortcut(data, "/usr/bin/flatpak")
    assert ss.apply_changes(path, [], [a.identity()]) == {"added": 0, "removed": 1}
    left = vdf.loads(path.read_bytes())["shortcuts"]
    assert [e["AppName"] for e in left.values()] == ["B"] and list(left) == ["0"]  # renumbered, B untouched


def test_removing_leaves_other_shortcuts_byte_for_byte_and_backs_up_once(tmp_path):
    path = tmp_path / "shortcuts.vdf"
    path.write_bytes(SAMPLE)
    g = flatpak()
    ss.apply_changes(path, [g.entry()], [], stamp="1")
    ss.apply_changes(path, [], [g.identity()], stamp="2")
    after = vdf.loads(path.read_bytes())
    assert after == vdf.loads(SAMPLE)  # the user's own shortcut survived both edits
    assert len(list(tmp_path.glob("shortcuts.vdf.bak-*"))) == 2
    before = path.read_bytes()
    assert ss.apply_changes(path, [], [g.identity()], stamp="3") == {"added": 0, "removed": 0}
    assert path.read_bytes() == before and not (tmp_path / "shortcuts.vdf.bak-3").exists()


def test_add_and_remove_in_one_write(tmp_path):
    path = tmp_path / "shortcuts.vdf"
    a, b = flatpak("org.a.A", "A"), flatpak("org.b.B", "B")
    ss.apply_changes(path, [a.entry()], [])
    assert ss.apply_changes(path, [b.entry()], [a.identity()]) == {"added": 1, "removed": 1}
    assert [e["AppName"] for e in vdf.loads(path.read_bytes())["shortcuts"].values()] == ["B"]
    assert len(list(tmp_path.glob("shortcuts.vdf.bak-*"))) <= 2


def test_parse_flatpak_list_and_icons(tmp_path):
    text = "org.gimp.GIMP\tGIMP\ncom.vysp3r.ProtonPlus\tProtonPlus\norg.gimp.GIMP\tGIMP\nbad id\tx\n\n"
    assert steam_apps.parse_flatpak_list(text) == [("org.gimp.GIMP", "GIMP"), ("com.vysp3r.ProtonPlus", "ProtonPlus")]
    for size in ("64x64", "256x256", "128x128"):
        d = tmp_path / "hicolor" / size / "apps"
        d.mkdir(parents=True)
        (d / "org.gimp.GIMP.png").write_bytes(b"x")
    assert steam_apps.find_icon("org.gimp.GIMP", (tmp_path,)).endswith("256x256/apps/org.gimp.GIMP.png")
    assert steam_apps.find_icon("org.none.None", (tmp_path,)) == ""


def test_targets_always_start_with_boppos():
    t = steam_apps.all_targets([("org.a.A", "A")])
    assert list(t) == ["boppos-deck", "org.a.A"] and t["boppos-deck"].launch == ""


def test_queue_merges_and_cancels_opposites(tmp_path):
    q = tmp_path / "pending.json"
    a, b = flatpak("org.a.A", "A"), flatpak("org.b.B", "B")
    steam_sync.queue([a], [b], q)
    first = steam_sync.load_pending(q)
    assert set(first["add"]) == {"org.a.A"} and set(first["remove"]) == {"org.b.B"}
    steam_sync.queue([b], [a], q)  # the opposite requests replace the earlier ones
    p = steam_sync.load_pending(q)
    assert set(p["add"]) == {"org.b.B"} and set(p["remove"]) == {"org.a.A"}
    steam_sync.queue([a], [], q)
    steam_sync.queue([], [a], q)  # add then remove the same app: only the removal stays
    p = steam_sync.load_pending(q)
    assert "org.a.A" not in p["add"] and "org.a.A" in p["remove"]
    steam_sync.clear_pending(q)
    assert not q.exists() and not any(steam_sync.load_pending(q).values())


def test_apply_pending_waits_for_steam_and_keeps_the_queue(tmp_path):
    home = tmp_path / "home"
    cfg = home / ".local/share/Steam/userdata/42/config"
    cfg.mkdir(parents=True)
    q = tmp_path / "pending.json"
    steam_sync.queue([flatpak()], [], q)
    import pytest as _p

    with _p.raises(steam_sync.SteamRunning):
        steam_sync.apply_pending(home, running=lambda: True, path=q)
    assert q.exists() and not (cfg / "shortcuts.vdf").exists()
    res = steam_sync.apply_pending(home, running=lambda: False, path=q)
    assert res["added"] == 1 and not q.exists()
    assert ss.has_shortcut(vdf.loads((cfg / "shortcuts.vdf").read_bytes()), "/usr/bin/flatpak", "run org.gimp.GIMP")


def test_apply_pending_without_steam_userdata_keeps_the_queue(tmp_path):
    q = tmp_path / "pending.json"
    steam_sync.queue([flatpak()], [], q)
    import pytest as _p

    with _p.raises(steam_sync.NoUserData):
        steam_sync.apply_pending(tmp_path / "empty-home", running=lambda: False, path=q)
    assert q.exists()


def test_wait_for_steam_exit_times_out_and_succeeds():
    t = [0.0]
    ticks = iter([True, True, False])
    assert steam_sync.wait_for_steam_exit(5, running=lambda: next(ticks), sleep=lambda s: t.__setitem__(0, t[0] + s),
                                          clock=lambda: t[0])
    t[0] = 0.0
    assert not steam_sync.wait_for_steam_exit(1, running=lambda: True, sleep=lambda s: t.__setitem__(0, t[0] + s),
                                              clock=lambda: t[0])


def test_steam_is_reopened_only_on_the_desktop():
    started = []
    assert steam_sync.start_steam_if_desktop(False, popen=lambda *a, **k: started.append(a)) is True
    assert steam_sync.start_steam_if_desktop(True, popen=lambda *a, **k: started.append(a)) is False
    assert len(started) == 1


def _setup_cli(tmp_path, monkeypatch, running=False):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg = tmp_path / ".local/share/Steam/userdata/42/config"
    cfg.mkdir(parents=True)
    monkeypatch.setattr(ss, "steam_running", lambda *a, **k: running)
    monkeypatch.setattr(steam_apps, "installed_flatpaks", lambda *a, **k: [("org.gimp.GIMP", "GIMP")])
    return cfg


def test_cli_sync_applies_immediately_when_steam_is_closed(tmp_path, monkeypatch, capsys):
    cfg = _setup_cli(tmp_path, monkeypatch)
    assert cli.main(["--json", "sync", "--add", "org.gimp.GIMP,boppos-deck"]) == 0
    names = [e["AppName"] for e in vdf.loads((cfg / "shortcuts.vdf").read_bytes())["shortcuts"].values()]
    assert names == ["GIMP", "BoppOS"]
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert out[-1]["event"] == "done" and out[-1]["ok"] is True
    assert cli.main(["--json", "apps"]) == 0
    rows = {r["key"]: r for r in json.loads(capsys.readouterr().out)["apps"]}
    assert rows["org.gimp.GIMP"]["present"] and rows["boppos-deck"]["present"]
    assert cli.main(["--json", "sync", "--remove", "org.gimp.GIMP"]) == 0
    capsys.readouterr()
    cli.main(["--json", "apps"])
    assert {r["key"]: r["present"] for r in json.loads(capsys.readouterr().out)["apps"]}["org.gimp.GIMP"] is False


def test_cli_sync_while_steam_runs_queues_and_exits_3_unless_restart(tmp_path, monkeypatch, capsys):
    cfg = _setup_cli(tmp_path, monkeypatch, running=True)
    assert cli.main(["--json", "sync", "--add", "org.gimp.GIMP"]) == cli.EXIT_RUNNING
    assert not (cfg / "shortcuts.vdf").exists() and steam_sync.pending_path().exists()
    done = [json.loads(line) for line in capsys.readouterr().out.splitlines()][-1]
    assert done["error"] == "steam-running" and done["queued"]
    # with --restart-steam: Steam is asked to quit and a detached helper does the edit
    calls = []
    monkeypatch.setattr(steam_sync, "spawn_apply_waiter", lambda *a: calls.append("waiter") or True)
    monkeypatch.setattr(steam_sync, "request_steam_shutdown", lambda *a: calls.append("shutdown") or True)
    assert cli.main(["--json", "sync", "--add", "org.gimp.GIMP", "--restart-steam"]) == 0
    assert calls == ["waiter", "shutdown"]  # the helper is running before Steam is told to quit


def test_cli_sync_rejects_unknown_apps_without_queueing(tmp_path, monkeypatch, capsys):
    _setup_cli(tmp_path, monkeypatch)
    assert cli.main(["--json", "sync", "--add", "org.not.Installed"]) == cli.EXIT_UNKNOWN
    assert not steam_sync.pending_path().exists()


def test_cli_apply_pending_reopens_steam_after_a_change(tmp_path, monkeypatch):
    _setup_cli(tmp_path, monkeypatch)
    steam_sync.queue([flatpak()], [])
    reopened = []
    monkeypatch.setattr(steam_sync, "start_steam_if_desktop", lambda *a, **k: reopened.append(1))
    assert cli.main(["--json", "apply-pending", "--reopen-steam"]) == 0 and reopened == [1]
    assert cli.main(["--json", "apply-pending", "--reopen-steam"]) == 0 and reopened == [1]  # nothing queued: no reopen


def test_apps_json_carries_categories_for_the_steam_page(tmp_path, monkeypatch, capsys):
    _setup_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(steam_apps, "installed_flatpaks",
                        lambda *a, **k: [("org.gimp.GIMP", "GIMP"), ("org.unlisted.App", "Unlisted")])
    assert cli.main(["--json", "apps"]) == 0
    rows = {r["key"]: r for r in json.loads(capsys.readouterr().out)["apps"]}
    assert rows["boppos-deck"]["category"] == "BoppOS"
    assert rows["org.gimp.GIMP"]["category"] == "Media & Creative"   # from the curated list
    assert rows["org.unlisted.App"]["category"] == "Other apps"       # anything else that is installed
