import json

from boppos import system
from boppos.bootc import parse_status
from boppos.support import build_report

REF = "ghcr.io/bopp-os/cachyos-plasma:stable"
ST = parse_status(json.dumps({"status": {"booted": {"image": {"version": "43.1", "image": {"image": REF}}}}}))


def test_helper_cmd_uses_pkexec_unless_root():
    assert system.helper_cmd("bootc-helper", ["status"], euid=1000) == [
        "pkexec", "/usr/libexec/boppos/bootc-helper", "status"]
    assert system.helper_cmd("bootc-helper", ["status"], euid=0)[0] == "/usr/libexec/boppos/bootc-helper"


def test_pkexec_hints():
    assert "not authorized" in system.explain_pkexec(126, "")
    assert "cancelled" in system.explain_pkexec(127, "")
    assert system.explain_pkexec(1, "") is None


def test_about_fields(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/os-release").write_text('NAME="BoppOS"\nVARIANT_ID=x\n')
    a = system.about(ST, tmp_path)
    assert a["image"] == "ghcr.io/bopp-os/cachyos-plasma" and a["tag"] == "stable"
    assert a["flavor"] == "plasma" and a["version"] == "43.1"


def test_flavor_fallbacks():
    assert system.flavor_from_ref("ghcr.io/bopp-os/cachyos-gnome:stable") == "gnome"
    assert system.flavor_from_ref("quay.io/x/y:1", "deck") == "deck"
    assert system.flavor_from_ref("") == "unknown"


def test_upgrade_steps_default_order_and_missing_tools():
    steps = system.upgrade_steps(None, which=lambda n: "/x/" + n if n in ("flatpak", "brew") else None)
    assert [t for t, _ in steps] == ["os", "flatpak", "brew"]
    assert system.upgrade_steps(["fw"], which=lambda n: None) == []
    only = system.upgrade_steps(["os"], which=lambda n: None)
    assert only[0][1][0][-1] == "upgrade" and "bootc-helper" in only[0][1][0][-2] + only[0][1][0][-3]


def test_appimage_and_proton_use_the_native_binary_or_the_flatpak_export():
    native = {"gearlever": "/usr/bin/gearlever", "protonplus": "/usr/bin/protonplus"}
    steps = dict(system.upgrade_steps(["appimage", "proton"], which=native.get))
    assert steps == {"appimage": [["/usr/bin/gearlever", "--update", "--all", "--yes"]],
                     "proton": [["/usr/bin/protonplus", "update", "all"]]}
    export = "/var/lib/flatpak/exports/bin/com.vysp3r.ProtonPlus"
    steps = system.upgrade_steps(["appimage", "proton"], which=lambda n: n if n == export else None)
    assert steps == [("proton", [[export, "update", "all"]])]


def test_appimage_and_proton_are_in_the_default_set():
    steps = system.upgrade_steps(None, which=lambda n: "/x/" + n if n in ("gearlever", "protonplus") else None)
    assert [t for t, _ in steps] == ["os", "appimage", "proton"]
    assert system.parse_targets("proton,appimage") == ["appimage", "proton"]


def test_containers_go_through_boppos_update_and_are_in_the_default_set():
    tools = {"distrobox": "/usr/bin/distrobox", "litterbox": "/usr/bin/litterbox",
             "boppos-update": "/usr/bin/boppos-update"}
    steps = dict(system.upgrade_steps(["distrobox", "litterbox"], which=tools.get))
    assert steps == {"distrobox": [["/usr/bin/boppos-update", "--distrobox", "--yes", "--verbose"]],
                     "litterbox": [["/usr/bin/boppos-update", "--litterbox", "--yes", "--verbose"]]}
    assert [t for t, _ in system.upgrade_steps(None, which=tools.get)] == ["os", "distrobox", "litterbox"]
    assert system.upgrade_steps(["litterbox"], which={"boppos-update": "/x"}.get) == []  # no litterbox


def test_fw_rc2_means_nothing_to_do():
    assert system.accepted_rc("fw", [], 2) and not system.accepted_rc("flatpak", [], 2)
    assert system.parse_fw_updates("", 2) == {"count": 0, "devices": []}
    raw = json.dumps({"Devices": [{"Name": "Dock", "Releases": [{"Version": "1.2"}]}]})
    assert system.parse_fw_updates(raw, 0)["devices"] == [{"name": "Dock", "version": "1.2"}]


def test_report_has_sections():
    text = build_report({"about": "x", "empty": ""})
    assert "===== about =====" in text and "(empty)" in text


def test_upgrade_steps_carry_a_label_and_their_position(monkeypatch):
    from types import SimpleNamespace

    from boppos import system_cli

    events = []
    monkeypatch.setattr(system_cli, "make_emitter", lambda *_a: events.append)
    monkeypatch.setattr(system_cli, "open_log", lambda _a: None)
    monkeypatch.setattr(system, "upgrade_steps", lambda _only: [("fw", [["a"]]), ("proton", [["b"]])])
    monkeypatch.setattr(system_cli, "run_stream", lambda cmd, _emit: 0 if cmd == ["a"] else 1)
    monkeypatch.setattr(system, "get_status", lambda: {})
    assert system_cli._upgrade(SimpleNamespace(only=None, json=True)) == 1
    steps = [e for e in events if e["event"] == "step"]
    assert steps[0] == {"event": "step", "id": "fw", "status": "start", "label": "Firmware", "index": 1, "total": 2}
    assert steps[3] == {"event": "step", "id": "proton", "status": "fail", "label": "Proton Latest (ProtonPlus)"}
    assert events[-1]["failed"] == ["proton"]
