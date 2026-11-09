import zlib
from pathlib import Path

import pytest
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
    want = [f"{i}.png", f"{i}p.png", f"{i}_hero.png", f"{i}_logo.png", f"{i}_icon.jpg", f"{i}.json"]
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
    assert len(list((cfg / "grid").iterdir())) == 6  # five images and the logo position
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
    grid = cfg / "grid"
    assert not grid.exists() or not [p for p in grid.iterdir() if p.name.startswith(str(app_id()))]


def test_shipped_artwork_has_the_sizes_steam_asks_for():
    import struct
    sizes = {"capsule.png": (920, 430), "portrait.png": (600, 900), "hero.png": (3840, 1240),
             "logo.png": (640, 193)}
    for name, want in sizes.items():
        head = (ROOT / "share/boppos/steam-art" / name).read_bytes()[16:24]
        assert struct.unpack(">II", head) == want, name
    # the sidebar icon is a JPEG: Steam did not draw PNG icons there
    assert (ROOT / "share/boppos/steam-art/icon.jpg").read_bytes()[:2] == b"\xff\xd8"


def _png(path, size=(64, 64), rgba=(255, 0, 0, 0)):
    from PIL import Image
    Image.new("RGBA", size, rgba).save(path)


def test_other_shortcuts_get_a_jpeg_icon_made_from_their_png(tmp_path):
    pytest.importorskip("PIL")
    src = tmp_path / "app.png"
    _png(src)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    entry = ss.new_entry("/usr/bin/flatpak", "Foo", icon=str(src), launch_options="run org.foo.Foo")
    ss.apply_changes(cfg / "shortcuts.vdf", [entry], [])
    stored = next(iter(ss.vdf.loads((cfg / "shortcuts.vdf").read_bytes())["shortcuts"].values()))
    icon = Path(stored["icon"])
    assert icon.parent == cfg / "grid" and icon.name.endswith("_icon.jpg") and icon.read_bytes()[:2] == b"\xff\xd8"


def test_an_existing_png_shortcut_is_repaired_but_a_chosen_icon_is_not(tmp_path):
    pytest.importorskip("PIL")
    src = tmp_path / "app.png"
    _png(src)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    path = cfg / "shortcuts.vdf"
    old = ss.new_entry("/usr/bin/flatpak", "Foo", icon=str(src), launch_options="run org.foo.Foo")
    ss.apply_changes(path, [{**old, "icon": str(src)}], [])  # as an older version stored it (add keeps a PNG too)
    data = ss.vdf.loads(path.read_bytes())
    next(iter(data["shortcuts"].values()))["icon"] = str(src)
    path.write_bytes(ss.vdf.dumps(data))
    res = ss.apply_changes(path, [old], [])
    assert res == {"added": 0, "removed": 0, "updated": 1}
    chosen = ss.vdf.loads(path.read_bytes())
    next(iter(chosen["shortcuts"].values()))["icon"] = "/home/me/mine.jpg"
    path.write_bytes(ss.vdf.dumps(chosen))
    assert ss.apply_changes(path, [old], [])["updated"] == 0


def test_removing_a_shortcut_removes_the_icon_made_for_it(tmp_path):
    pytest.importorskip("PIL")
    src = tmp_path / "app.png"
    _png(src)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    entry = ss.new_entry("/usr/bin/flatpak", "Foo", icon=str(src), launch_options="run org.foo.Foo")
    ss.apply_changes(cfg / "shortcuts.vdf", [entry], [])
    assert len(list((cfg / "grid").iterdir())) == 6  # icon, capsule, portrait, hero, logo, logo position
    ss.apply_changes(cfg / "shortcuts.vdf", [], [("/usr/bin/flatpak", "run org.foo.Foo")])
    assert list((cfg / "grid").iterdir()) == []


def _flatpak_id(name="Foo"):
    return ss.shortcut_appid('"/usr/bin/flatpak"', name) & 0xFFFFFFFF


def test_flatpak_shortcuts_get_library_artwork_in_steams_sizes(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    src = tmp_path / "app.png"
    _png(src, (128, 128), (200, 40, 40, 255))
    grid = tmp_path / "grid"
    entry = ss.new_entry("/usr/bin/flatpak", "Foo Bar", icon=str(src), launch_options="run org.foo.Foo")
    assert ss.install_app_art(grid, entry) == 4
    i = _flatpak_id("Foo Bar")
    sizes = {f"{i}.png": (920, 430), f"{i}p.png": (600, 900), f"{i}_hero.png": (1920, 620),
             f"{i}_logo.png": (400, 400)}
    for name, want in sizes.items():
        with Image.open(grid / name) as im:
            assert im.size == want, name
    assert ss.install_app_art(grid, entry) == 0  # nothing re-rendered


def test_an_app_without_an_icon_still_gets_artwork_but_no_logo(tmp_path):
    pytest.importorskip("PIL")
    grid = tmp_path / "grid"
    entry = ss.new_entry("/usr/bin/flatpak", "Foo", icon="", launch_options="run org.foo.Foo")
    assert ss.install_app_art(grid, entry) == 3
    assert not (grid / f"{_flatpak_id()}_logo.png").exists()


def test_artwork_you_put_there_is_neither_replaced_nor_removed(tmp_path):
    pytest.importorskip("PIL")
    src = tmp_path / "app.png"
    _png(src)
    cfg = tmp_path / "cfg"
    grid = cfg / "grid"
    grid.mkdir(parents=True)
    mine = grid / f"{_flatpak_id()}p.png"
    _png(mine, (600, 900), (1, 2, 3, 255))  # e.g. from SteamGridDB: a PNG without our tag
    before = mine.read_bytes()
    entry = ss.new_entry("/usr/bin/flatpak", "Foo", icon=str(src), launch_options="run org.foo.Foo")
    ss.apply_changes(cfg / "shortcuts.vdf", [entry], [])
    assert mine.read_bytes() == before
    hero = grid / f"{_flatpak_id()}_hero.png"
    _png(hero, (1920, 620), (9, 9, 9, 255))  # replaced by the user after we wrote ours
    ss.apply_changes(cfg / "shortcuts.vdf", [], [("/usr/bin/flatpak", "run org.foo.Foo")])
    assert sorted(p.name for p in grid.iterdir()) == sorted([mine.name, hero.name])


def test_a_logo_position_you_changed_in_steam_is_kept(tmp_path):
    src, grid = fake_art(tmp_path), tmp_path / "grid"
    ss.install_art(grid, src)
    pos = grid / f"{app_id()}.json"
    assert pos.read_bytes() == ss.LOGO_POSITION
    pos.write_text('{"nVersion":1,"logoPosition":{"pinnedPosition":"CenterCenter","nWidthPct":40,"nHeightPct":40}}')
    ss.remove_art(grid, src)
    assert pos.exists()


def test_no_logo_position_without_a_logo(tmp_path):
    pytest.importorskip("PIL")
    grid = tmp_path / "grid"
    ss.install_app_art(grid, ss.new_entry("/usr/bin/flatpak", "Foo", icon="", launch_options="run org.foo.Foo"))
    assert not (grid / f"{_flatpak_id()}.json").exists()


def _library_with_foo(tmp_path, icon):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    entry = ss.new_entry("/usr/bin/flatpak", "Foo", icon=str(icon), launch_options="run org.foo.Foo")
    ss.apply_changes(cfg / "shortcuts.vdf", [entry], [])
    return cfg


def test_refresh_redraws_our_artwork_and_keeps_yours(tmp_path):
    pytest.importorskip("PIL")
    icon = tmp_path / "app.png"
    _png(icon, (64, 64), (200, 0, 0, 255))
    cfg = _library_with_foo(tmp_path, icon)
    grid, i = cfg / "grid", _flatpak_id()
    before = (grid / f"{i}p.png").read_bytes()
    _png(grid / f"{i}_hero.png", (1920, 620), (9, 9, 9, 255))  # yours
    mine = (grid / f"{i}_hero.png").read_bytes()
    (grid / f"{i}.png").unlink()  # went missing
    _png(icon, (64, 64), (0, 0, 200, 255))  # the app's icon changed
    sources = {("/usr/bin/flatpak", "run org.foo.Foo"): str(icon)}
    assert ss.refresh_art(cfg / "shortcuts.vdf", sources) == 3  # capsule, portrait, logo
    assert (grid / f"{i}.png").exists()
    assert (grid / f"{i}p.png").read_bytes() != before
    assert (grid / f"{i}_hero.png").read_bytes() == mine


def test_refresh_leaves_shortcuts_it_was_not_asked_about(tmp_path):
    pytest.importorskip("PIL")
    icon = tmp_path / "app.png"
    _png(icon)
    cfg = _library_with_foo(tmp_path, icon)
    (cfg / "grid" / f"{_flatpak_id()}.png").unlink()
    assert ss.refresh_art(cfg / "shortcuts.vdf", {}) == 0
    assert not (cfg / "grid" / f"{_flatpak_id()}.png").exists()


def test_refresh_brings_old_boppos_artwork_up_to_date(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL.PngImagePlugin import PngInfo
    monkeypatch.setenv("BOPPOS_SHARE_DIR", str(tmp_path))
    art = tmp_path / "steam-art"
    art.mkdir()
    for name in ss.ART_NAMES:
        _png(art / name, (8, 8), (1, 1, 1, 255)) if name.endswith(".png") else (art / name).write_bytes(b"jpg")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    ss.apply_changes(cfg / "shortcuts.vdf", [ss.new_entry()], [])
    from PIL import Image
    info = PngInfo()
    info.add_text("Software", ss.APP_ART_TAG)
    old = cfg / "grid" / f"{app_id()}_hero.png"
    Image.new("RGBA", (8, 8), (5, 5, 5, 255)).save(old, pnginfo=info)  # an older release's (tagged) hero
    yours = cfg / "grid" / f"{app_id()}p.png"
    _png(yours, (8, 8), (7, 7, 7, 255))  # untagged: yours
    mine = yours.read_bytes()
    assert ss.refresh_art(cfg / "shortcuts.vdf", {(ss.EXE, ""): ""}) == 1
    assert old.read_bytes() == (art / "hero.png").read_bytes()
    assert yours.read_bytes() == mine


def test_shipped_png_artwork_is_tagged_as_ours():
    pytest.importorskip("PIL")
    for name in ("capsule.png", "portrait.png", "hero.png", "logo.png"):
        assert ss._is_ours(ROOT / "share/boppos/steam-art" / name), name


def test_copies_get_the_current_time_not_the_images_1970_date(tmp_path):
    import os
    import time
    src, grid = fake_art(tmp_path), tmp_path / "grid"
    for f in src.iterdir():
        os.utime(f, (0, 0))  # as files in a bootc image's /usr are dated
    ss.install_art(grid, src)
    assert all(p.stat().st_mtime > time.time() - 60 for p in grid.iterdir())


def test_artwork_an_older_release_copied_with_the_1970_date_is_redated(tmp_path):
    import os
    src, grid = fake_art(tmp_path), tmp_path / "grid"
    ss.install_art(grid, src)
    copies = [p for p in grid.iterdir() if p.suffix != ".json"]  # the logo position is written, not copied
    for p in copies:
        os.utime(p, (0, 0))
    assert ss.install_art(grid, src, refresh=True) == 5
    assert all(p.stat().st_mtime > ss.EPOCH_DATED for p in copies)
    assert ss.install_art(grid, src, refresh=True) == 0


def _with_stored_appid(path, exe, launch, new_id):
    data = ss.vdf.loads(path.read_bytes())
    for e in data["shortcuts"].values():
        if ss._matches(e, exe, launch):
            e["appid"] = ss.Int32(new_id - (1 << 32) if new_id >= 1 << 31 else new_id)
    path.write_bytes(ss.vdf.dumps(data))


def test_artwork_follows_an_id_steam_gave_the_shortcut(tmp_path, monkeypatch):
    monkeypatch.setenv("BOPPOS_SHARE_DIR", str(tmp_path))
    fake_art(tmp_path)
    (tmp_path / "art").rename(tmp_path / "steam-art")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    path = cfg / "shortcuts.vdf"
    ss.apply_changes(path, [ss.new_entry()], [])
    _with_stored_appid(path, ss.EXE, "", 2765338429)  # as seen on a Legion Go
    assert ss.refresh_art(path, {(ss.EXE, ""): ""}) == 5
    assert {p.name for p in (cfg / "grid").glob("2765338429*")} == {
        "2765338429.png", "2765338429p.png", "2765338429_hero.png", "2765338429_logo.png",
        "2765338429_icon.jpg", "2765338429.json"}
    ss.apply_changes(path, [], [(ss.EXE, "")])
    assert list((cfg / "grid").iterdir()) == []  # both the old and the new id's copies go


def test_flatpak_artwork_follows_an_id_steam_gave_the_shortcut(tmp_path):
    pytest.importorskip("PIL")
    icon = tmp_path / "app.png"
    _png(icon)
    cfg = _library_with_foo(tmp_path, icon)
    path = cfg / "shortcuts.vdf"
    _with_stored_appid(path, "/usr/bin/flatpak", "run org.foo.Foo", 2469320567)
    ss.apply_changes(path, [ss.new_entry("/usr/bin/flatpak", "Foo", icon=str(icon),
                                         launch_options="run org.foo.Foo")], [])
    assert (cfg / "grid" / "2469320567p.png").exists()


def test_a_shortcut_made_with_bin_flatpak_matches_usr_bin_flatpak():
    import os
    if os.path.realpath("/bin") != "/usr/bin":
        pytest.skip("/bin is not a link to /usr/bin here")
    e = ss.new_entry("/bin/flatpak", "Foo", launch_options="run org.foo.Foo")
    assert ss._matches(e, "/usr/bin/flatpak", "run org.foo.Foo")
    assert not ss._matches(e, "/usr/bin/flatpak", "run org.bar.Bar")


def test_an_app_with_only_an_svg_icon_gets_a_png_made_from_it(tmp_path):
    import shutil

    from boppos import steam_apps
    if not shutil.which("rsvg-convert"):
        pytest.skip("rsvg-convert missing")
    svg = tmp_path / "icons/hicolor/scalable/apps/org.foo.Foo.svg"
    svg.parent.mkdir(parents=True)
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" '
                   'fill="#c00"/></svg>')
    cache = tmp_path / "cache"
    png = steam_apps.find_icon("org.foo.Foo", (tmp_path / "icons",), cache)
    assert png.startswith(str(cache)) and open(png, "rb").read(4) == b"\x89PNG"
    assert steam_apps.find_icon("org.foo.Foo", (tmp_path / "icons",), cache) == png  # cached
    svg.write_text(svg.read_text().replace("#c00", "#00c"))  # the app updated its icon
    newer = steam_apps.find_icon("org.foo.Foo", (tmp_path / "icons",), cache)
    assert newer != png and [p.name for p in cache.iterdir()] == [Path(newer).name]
