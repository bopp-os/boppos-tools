import json

import pytest
from boppos import flatpaks, flatpaks_cli

LIST = """
# comment
it.mijorus.gearlever
com.github.tchx84.Flatseal   # trailing comment
runtime/org.gtk.Gtk3theme.adw-gtk3/x86_64/3.22
it.mijorus.gearlever
"""


def test_parse_list_dedupes_and_classifies():
    apps = flatpaks.parse_list(LIST)
    assert [a.id for a in apps] == [
        "it.mijorus.gearlever", "com.github.tchx84.Flatseal", "org.gtk.Gtk3theme.adw-gtk3"]
    assert apps[2].kind == "runtime" and apps[2].ref.startswith("runtime/")


@pytest.mark.parametrize("bad", ["foo bar", "-y", "a;b", "$(id)", "../x", "--user"])
def test_parse_list_rejects_odd_refs(bad):
    with pytest.raises(flatpaks.ListError):
        flatpaks.parse_list(bad)


def test_select_only_restricts_to_curated_list():
    apps = flatpaks.parse_list(LIST)
    assert [a.id for a in flatpaks.select(apps, ["com.github.tchx84.Flatseal"])] == ["com.github.tchx84.Flatseal"]
    with pytest.raises(flatpaks.ListError):
        flatpaks.select(apps, ["org.evil.App"])


def test_commands_are_system_scope_and_argv_lists():
    app = flatpaks.parse_list("org.example.App")[0]
    assert flatpaks.install_cmd(app)[:3] == ["flatpak", "install", "--user"]
    assert flatpaks.install_cmd(app, "system")[:3] == ["flatpak", "install", "--system"]
    assert flatpaks.remove_cmd(app)[-1] == "org.example.App"


def test_cli_requires_yes_and_reports_status(tmp_path, monkeypatch, capsys):
    f = tmp_path / "list.txt"
    f.write_text("org.a.A\norg.b.B\n")
    monkeypatch.setattr(flatpaks, "installed_scopes", lambda: {"org.a.A": {"user"}})
    assert flatpaks_cli.main(["--list", str(f), "--status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["id"], r["installed"]) for r in rows] == [("org.a.A", True), ("org.b.B", False)]
    assert flatpaks_cli.main(["--list", str(f)]) == 2


def test_cli_installs_only_missing(tmp_path, monkeypatch):
    f = tmp_path / "list.txt"
    f.write_text("org.a.A\norg.b.B\n")
    monkeypatch.setattr(flatpaks, "installed_scopes", lambda: {"org.a.A": {"user"}})
    monkeypatch.setattr("boppos.runner.log_dir", lambda: tmp_path / "logs")
    ran = []
    monkeypatch.setattr(flatpaks_cli, "run_stream", lambda cmd, emit: ran.append(cmd) or 0)
    assert flatpaks_cli.main(["--list", str(f), "--yes", "--json"]) == 0
    assert [c[-1] for c in ran] == [flatpaks.REMOTE_URL, "org.b.B"]


def test_status_errors_cleanly_without_flatpak(tmp_path, monkeypatch, capsys):
    f = tmp_path / "l.txt"
    f.write_text("org.a.A\n")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("flatpak")))
    assert flatpaks_cli.main(["--list", str(f), "--status"]) == 1
    assert "cannot run flatpak" in capsys.readouterr().err


def test_display_names():
    assert flatpaks.display_name("it.mijorus.gearlever") == "Gear Lever"
    assert flatpaks.display_name("org.virt_manager.virt-manager") == "virt manager"


def test_categories_from_double_hash_lines_and_flat_lists_still_work():
    apps = flatpaks.parse_list("## Gaming\norg.a.A\n# plain comment\norg.b.B\n## Tools\norg.c.C\n")
    assert [(a.id, a.category) for a in apps] == [("org.a.A", "Gaming"), ("org.b.B", "Gaming"), ("org.c.C", "Tools")]
    assert flatpaks.parse_list("org.a.A\n")[0].category == "Other"


def test_shipped_list_parses_and_every_app_has_a_category():
    from pathlib import Path

    apps = flatpaks.parse_list((Path(__file__).parent.parent / "share/boppos/boppos-flatpaks.txt").read_text())
    assert len(apps) == 31 and all(a.category != "Other" for a in apps)
    assert {"com.vysp3r.ProtonPlus", "it.mijorus.gearlever", "com.github.tchx84.Flatseal",
            "io.github.kolunmi.Bazaar"} <= {a.id for a in apps}


def test_defaults_list_is_a_subset_of_the_curated_list():
    from pathlib import Path

    root = Path(__file__).parent.parent / "share/boppos"
    defaults = {a.id for a in flatpaks.parse_list((root / "boppos-flatpaks-default.txt").read_text())}
    curated = {a.id for a in flatpaks.parse_list((root / "boppos-flatpaks.txt").read_text())}
    assert defaults == {"io.github.kolunmi.Bazaar"} and defaults <= curated


def test_defaults_flag_installs_only_missing_default_apps(tmp_path, monkeypatch):
    monkeypatch.setattr(flatpaks, "installed_scopes", lambda: {})
    monkeypatch.setattr("boppos.runner.log_dir", lambda: tmp_path / "logs")
    ran = []
    monkeypatch.setattr(flatpaks_cli, "run_stream", lambda cmd, emit: ran.append(cmd) or 0)
    assert flatpaks_cli.main(["--defaults", "--yes", "--json"]) == 0
    assert [c[-1] for c in ran] == [flatpaks.REMOTE_URL, "io.github.kolunmi.Bazaar"]
    monkeypatch.setattr(flatpaks, "installed_scopes", lambda: {"io.github.kolunmi.Bazaar": {"user"}})
    ran.clear()
    assert flatpaks_cli.main(["--defaults", "--yes", "--json"]) == 0 and ran == []


def test_names_come_from_the_list_comment_and_the_list_wins_over_what_flatpak_reports():
    apps = flatpaks.parse_list("org.a.A  # name: Proper A\norg.b.B\norg.c.C  # just a comment\n")
    assert [a.name for a in apps] == ["Proper A", "", ""]
    assert flatpaks.best_name(apps[0], {}) == "Proper A"
    # Flatpak reports "Bitwarden" / "GNU Image Manipulation Program"; the curated name is the deliberate one
    assert flatpaks.best_name(apps[0], {"org.a.A": "Installed A"}) == "Proper A"
    assert flatpaks.best_name(apps[1], {"org.b.B": "Reported B"}) == "Reported B"  # not in the list's names
    assert flatpaks.best_name(apps[1], {}) == "B"  # last resort: tidied id


def test_extensions_are_not_listed_as_apps():
    apps = flatpaks.parse_list("org.virt_manager.virt_manager.Extension.Qemu\norg.a.A\n")
    assert [a.kind for a in apps] == ["runtime", "app"]


def test_every_shipped_app_has_a_proper_name_and_the_interactive_installer_still_sees_31_refs():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "share/boppos/boppos-flatpaks.txt").read_text()
    apps = [a for a in flatpaks.parse_list(text) if a.kind == "app"]
    assert len(apps) == 28 and all(a.name for a in apps)
    names = {a.id: a.name for a in apps}
    assert names["com.bitwarden.desktop"] == "Bitwarden Desktop" and names["io.github.kolunmi.Bazaar"] == "Bazaar"
    refs = [ln.split("#")[0].strip() for ln in text.splitlines() if ln.split("#")[0].strip()]
    assert len(set(refs)) == 31 and all(" " not in r for r in refs)  # what `sed 's/#.*//'` leaves behind


# --- the extra (opt-in) list -----------------------------------------------------------------------------


def _share():
    from pathlib import Path

    return Path(__file__).parent.parent / "share/boppos"


def test_extra_list_is_valid_named_categorised_and_does_not_repeat_the_core_list():
    extra = flatpaks.parse_list((_share() / "boppos-flatpaks-extra.txt").read_text())
    core = flatpaks.parse_list((_share() / "boppos-flatpaks.txt").read_text())
    assert len(extra) >= 15 and all(a.kind == "app" and a.name and a.category != "Other" for a in extra)
    assert not {a.id for a in extra} & {a.id for a in core}
    assert {"Emulators", "Music & Video"} <= {a.category for a in extra}
    emulators = {a.id for a in extra if a.category == "Emulators"}
    assert {"org.libretro.RetroArch", "org.DolphinEmu.dolphin-emu", "net.pcsx2.PCSX2"} <= emulators


def test_the_interactive_installer_still_installs_only_the_core_list():
    """install-optional-flatpaks (bash, no arguments) reads boppos-flatpaks.txt alone: it must not grow."""
    refs = {ln.split("#")[0].strip() for ln in (_share() / "boppos-flatpaks.txt").read_text().splitlines()}
    refs.discard("")
    assert len(refs) == 31
    assert "org.libretro.RetroArch" not in refs
    from pathlib import Path

    script = (Path(__file__).parent.parent / "bin/install-optional-flatpaks").read_text()
    assert "boppos-flatpaks.txt" in script and "boppos-flatpaks-extra" not in script


def test_load_curated_merges_core_and_extra_and_survives_a_missing_extra(tmp_path):
    merged = flatpaks.load_curated(str(_share() / "boppos-flatpaks.txt"), str(_share() / "boppos-flatpaks-extra.txt"))
    assert len(merged) > 31 and any(a.id == "net.pcsx2.PCSX2" for a in merged)
    core_only = flatpaks.load_curated(str(_share() / "boppos-flatpaks.txt"), str(tmp_path / "missing.txt"))
    assert len(core_only) == 31
    # categories carry from the end of one file into a '##' of the next without leaking
    emu = [a for a in merged if a.id == "net.pcsx2.PCSX2"][0]
    assert emu.category == "Emulators"


def test_cli_status_includes_the_extras_by_default_and_only_the_given_file_with_list(tmp_path, monkeypatch, capsys):
    import json

    monkeypatch.setattr(flatpaks, "installed_scopes", lambda: {})
    monkeypatch.setattr(flatpaks, "installed_names", lambda: {})
    assert flatpaks_cli.main(["--status", "--json"]) == 0
    ids = {r["id"] for r in json.loads(capsys.readouterr().out)}
    assert "net.pcsx2.PCSX2" in ids and "com.vysp3r.ProtonPlus" in ids
    one = tmp_path / "one.txt"
    one.write_text("org.a.A\n")
    assert flatpaks_cli.main(["--status", "--json", "--list", str(one)]) == 0
    assert {r["id"] for r in json.loads(capsys.readouterr().out)} == {"org.a.A"}


def test_defaults_are_still_inside_the_merged_list():
    merged = {a.id for a in flatpaks.load_curated()}
    defaults = {a.id for a in flatpaks.parse_list((_share() / "boppos-flatpaks-default.txt").read_text())}
    assert defaults <= merged


def test_extras_name_the_game_streaming_clients_and_no_end_of_life_apps():
    extra = {a.id: a for a in flatpaks.parse_list((_share() / "boppos-flatpaks-extra.txt").read_text())}
    streaming = {"io.github.streetpea.Chiaki4deck", "io.github.unknownskl.greenlight", "dev.lizardbyte.app.Sunshine"}
    assert streaming <= set(extra)
    # Flathub marks Jellyfin Media Player end-of-life; Jellyfin Desktop replaced it
    assert "com.github.iwalton3.jellyfin-media-player" not in extra and "org.jellyfin.JellyfinDesktop" in extra


def test_install_defaults_to_user_scope_and_system_is_explicit(tmp_path, monkeypatch):
    f = tmp_path / "list.txt"
    f.write_text("org.b.B\n")
    monkeypatch.setattr(flatpaks, "installed_scopes", lambda: {})
    monkeypatch.setattr("boppos.runner.log_dir", lambda: tmp_path / "logs")
    ran = []
    monkeypatch.setattr(flatpaks_cli, "run_stream", lambda cmd, emit: ran.append(cmd) or 0)
    assert flatpaks_cli.main(["--list", str(f), "--yes", "--json"]) == 0
    assert all("--user" in c for c in ran) and not any("--system" in c for c in ran)
    ran.clear()
    assert flatpaks_cli.main(["--list", str(f), "--yes", "--json", "--system"]) == 0
    assert all("--system" in c for c in ran)


def test_remove_follows_every_scope_the_app_is_in(tmp_path, monkeypatch, capsys):
    f = tmp_path / "list.txt"
    f.write_text("org.a.A\n")
    monkeypatch.setattr(flatpaks, "installed_scopes", lambda: {"org.a.A": {"user", "system"}})
    monkeypatch.setattr("boppos.runner.log_dir", lambda: tmp_path / "logs")
    ran = []
    monkeypatch.setattr(flatpaks_cli, "run_stream", lambda cmd, emit: ran.append(cmd) or 0)
    assert flatpaks_cli.main(["--list", str(f), "--yes", "--json", "--remove"]) == 0
    assert [c[2] for c in ran] == ["--system", "--user"]
    capsys.readouterr()
    assert flatpaks_cli.main(["--list", str(f), "--status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["scopes"] == ["system", "user"]


# --- defaults marked in the list, and apps the image already ships -----------------------------------------


def test_the_default_marker_follows_the_name_and_is_not_part_of_it():
    apps = flatpaks.parse_list("org.a.A  # name: Proper A; default\norg.b.B  # name: B\norg.c.C  # default\n")
    assert [(a.name, a.default) for a in apps] == [("Proper A", True), ("B", False), ("", False)]


def test_the_shipped_defaults_are_few_and_the_extras_have_none():
    core = flatpaks.parse_list((_share() / "boppos-flatpaks.txt").read_text())
    extra = flatpaks.parse_list((_share() / "boppos-flatpaks-extra.txt").read_text())
    assert {a.id for a in core if a.default} == {
        "org.mozilla.firefox", "org.videolan.VLC", "io.github.kolunmi.Bazaar", "com.vysp3r.ProtonPlus",
        "com.github.tchx84.Flatseal"}
    assert not any(a.default for a in extra)
    always = {a.id for a in flatpaks.parse_list((_share() / "boppos-flatpaks-default.txt").read_text())}
    assert always <= {a.id for a in core if a.default}  # what --defaults installs is also ticked in the wizard


def test_native_map_is_explicit_and_names_only_listed_apps():
    mapping = flatpaks.parse_native_map((_share() / "boppos-flatpaks-native.txt").read_text())
    listed = {a.id for a in flatpaks.load_curated(str(_share() / "boppos-flatpaks.txt"),
                                                  str(_share() / "boppos-flatpaks-extra.txt"))}
    assert mapping and set(mapping) <= listed
    assert mapping["org.mozilla.firefox"] == ("firefox", "firefox.desktop")
    assert "lutris" in mapping["net.lutris.Lutris"]
    assert "heroic-games-launcher-bin" in mapping["com.heroicgameslauncher.hgl"]
    with pytest.raises(flatpaks.ListError):
        flatpaks.parse_native_map("org.a.A\n")


def test_redundant_matches_packages_and_desktop_files_but_never_unmapped_apps():
    apps = flatpaks.parse_list("org.mozilla.firefox\nnet.lutris.Lutris\norg.gimp.GIMP\norg.x.Unmapped\n")
    mapping = {"org.mozilla.firefox": ("firefox", "firefox.desktop"), "net.lutris.Lutris": ("lutris",),
               "org.gimp.GIMP": ("gimp",)}
    assert flatpaks.redundant(apps, mapping, {"firefox.desktop", "lutris", "Unmapped", "x"}) == {
        "org.mozilla.firefox", "net.lutris.Lutris"}
    assert flatpaks.redundant(apps, mapping, set()) == set()
    assert flatpaks.redundant(apps, {}, {"firefox", "lutris", "gimp"}) == set()


def test_native_installed_reads_pacman_and_desktop_files(tmp_path, monkeypatch):
    import subprocess

    (tmp_path / "usr/share/applications").mkdir(parents=True)
    (tmp_path / "usr/share/applications/steam.desktop").touch()
    (tmp_path / "usr/share/applications/readme.txt").touch()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "lutris\nfirefox\n", ""))
    flatpaks.native_installed.cache_clear()
    try:
        assert flatpaks.native_installed(str(tmp_path)) == {"lutris", "firefox", "steam.desktop"}
    finally:
        flatpaks.native_installed.cache_clear()
