import zlib
from pathlib import Path

from boppos import steam_shortcut as ss

ROOT = Path(__file__).parent.parent


def fake_art(tmp_path):
    src = tmp_path / "art"
    src.mkdir()
    for name in ss.ART_NAMES:
        (src / name).write_bytes(b"ours-" + name.encode())
    return src


def app_id():
    return (zlib.crc32(f'"{ss.EXE}"{ss.APP_NAME}'.encode()) | 0x80000000) & 0xFFFFFFFF


def test_artwork_is_named_after_the_shortcut_id(tmp_path):
    grid = tmp_path / "config" / "grid"
    assert ss.install_art(grid, fake_art(tmp_path)) == 5
    i = app_id()
    want = [f"{i}.png", f"{i}p.png", f"{i}_hero.png", f"{i}_logo.png", f"{i}_icon.png"]
    assert sorted(p.name for p in grid.iterdir()) == sorted(want)


def test_existing_artwork_is_never_replaced(tmp_path):
    grid = tmp_path / "grid"
    grid.mkdir()
    (grid / f"{app_id()}p.png").write_bytes(b"mine")
    assert ss.install_art(grid, fake_art(tmp_path)) == 4
    assert (grid / f"{app_id()}p.png").read_bytes() == b"mine"


def test_removal_only_takes_our_own_files(tmp_path):
    src, grid = fake_art(tmp_path), tmp_path / "grid"
    ss.install_art(grid, src)
    (grid / f"{app_id()}_hero.png").write_bytes(b"replaced by the user")
    assert ss.remove_art(grid, src) == 4
    assert [p.name for p in grid.iterdir()] == [f"{app_id()}_hero.png"]


def test_adding_and_removing_the_shortcut_handles_the_artwork(tmp_path, monkeypatch):
    monkeypatch.setenv("BOPPOS_SHARE_DIR", str(tmp_path))
    fake_art(tmp_path)
    (tmp_path / "art").rename(tmp_path / "steam-art")
    cfg = tmp_path / "userdata" / "1234" / "config"
    cfg.mkdir(parents=True)
    path = cfg / "shortcuts.vdf"
    ss.apply_changes(path, [ss.new_entry()], [])
    assert len(list((cfg / "grid").iterdir())) == 5
    ss.apply_changes(path, [], [(ss.EXE, "")])
    assert list((cfg / "grid").iterdir()) == []


def test_other_shortcuts_get_no_boppos_artwork(tmp_path, monkeypatch):
    monkeypatch.setenv("BOPPOS_SHARE_DIR", str(tmp_path))
    (tmp_path / "steam-art").mkdir()
    for name in ss.ART_NAMES:
        (tmp_path / "steam-art" / name).write_bytes(b"x")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    other = ss.new_entry("/usr/bin/flatpak", "Foo", launch_options="run org.foo.Foo")
    ss.apply_changes(cfg / "shortcuts.vdf", [other], [])
    assert not (cfg / "grid").exists()


def test_shipped_artwork_has_the_sizes_steam_asks_for():
    import struct
    sizes = {"capsule.png": (920, 430), "portrait.png": (600, 900), "hero.png": (3840, 1240),
             "logo.png": (640, 193), "icon.png": (256, 256)}
    for name, want in sizes.items():
        head = (ROOT / "share/boppos/steam-art" / name).read_bytes()[16:24]
        assert struct.unpack(">II", head) == want, name
