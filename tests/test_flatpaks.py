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
    assert flatpaks.install_cmd(app)[:3] == ["flatpak", "install", "--system"]
    assert flatpaks.remove_cmd(app)[-1] == "org.example.App"


def test_cli_requires_yes_and_reports_status(tmp_path, monkeypatch, capsys):
    f = tmp_path / "list.txt"
    f.write_text("org.a.A\norg.b.B\n")
    monkeypatch.setattr(flatpaks, "installed_ids", lambda: {"org.a.A"})
    assert flatpaks_cli.main(["--list", str(f), "--status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["id"], r["installed"]) for r in rows] == [("org.a.A", True), ("org.b.B", False)]
    assert flatpaks_cli.main(["--list", str(f)]) == 2


def test_cli_installs_only_missing(tmp_path, monkeypatch):
    f = tmp_path / "list.txt"
    f.write_text("org.a.A\norg.b.B\n")
    monkeypatch.setattr(flatpaks, "installed_ids", lambda: {"org.a.A"})
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
    assert len(apps) == 29 and all(a.category != "Other" for a in apps)
    assert {"com.vysp3r.ProtonPlus", "it.mijorus.gearlever", "com.github.tchx84.Flatseal",
            "io.github.kolunmi.Bazaar"} <= {a.id for a in apps}


def test_defaults_list_is_a_subset_of_the_curated_list():
    from pathlib import Path

    root = Path(__file__).parent.parent / "share/boppos"
    defaults = {a.id for a in flatpaks.parse_list((root / "boppos-flatpaks-default.txt").read_text())}
    curated = {a.id for a in flatpaks.parse_list((root / "boppos-flatpaks.txt").read_text())}
    assert defaults == {"io.github.kolunmi.Bazaar"} and defaults <= curated


def test_defaults_flag_installs_only_missing_default_apps(tmp_path, monkeypatch):
    monkeypatch.setattr(flatpaks, "installed_ids", lambda: set())
    monkeypatch.setattr("boppos.runner.log_dir", lambda: tmp_path / "logs")
    ran = []
    monkeypatch.setattr(flatpaks_cli, "run_stream", lambda cmd, emit: ran.append(cmd) or 0)
    assert flatpaks_cli.main(["--defaults", "--yes", "--json"]) == 0
    assert [c[-1] for c in ran] == [flatpaks.REMOTE_URL, "io.github.kolunmi.Bazaar"]
    monkeypatch.setattr(flatpaks, "installed_ids", lambda: {"io.github.kolunmi.Bazaar"})
    ran.clear()
    assert flatpaks_cli.main(["--defaults", "--yes", "--json"]) == 0 and ran == []


def test_names_come_from_the_list_comment_and_installed_apps_win():
    apps = flatpaks.parse_list("org.a.A  # name: Proper A\norg.b.B\norg.c.C  # just a comment\n")
    assert [a.name for a in apps] == ["Proper A", "", ""]
    assert flatpaks.best_name(apps[0], {}) == "Proper A"
    assert flatpaks.best_name(apps[0], {"org.a.A": "Installed A"}) == "Installed A"  # its own metadata wins
    assert flatpaks.best_name(apps[1], {}) == "B"  # last resort: tidied id


def test_extensions_are_not_listed_as_apps():
    apps = flatpaks.parse_list("org.virt_manager.virt_manager.Extension.Qemu\norg.a.A\n")
    assert [a.kind for a in apps] == ["runtime", "app"]


def test_every_shipped_app_has_a_proper_name_and_the_interactive_installer_still_sees_29_refs():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "share/boppos/boppos-flatpaks.txt").read_text()
    apps = [a for a in flatpaks.parse_list(text) if a.kind == "app"]
    assert len(apps) == 26 and all(a.name for a in apps)
    names = {a.id: a.name for a in apps}
    assert names["com.bitwarden.desktop"] == "Bitwarden Desktop" and names["io.github.kolunmi.Bazaar"] == "Bazaar"
    refs = [ln.split("#")[0].strip() for ln in text.splitlines() if ln.split("#")[0].strip()]
    assert len(set(refs)) == 29 and all(" " not in r for r in refs)  # what `sed 's/#.*//'` leaves behind
