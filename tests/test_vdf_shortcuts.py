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
