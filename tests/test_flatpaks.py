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
