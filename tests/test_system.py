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


def test_fw_rc2_means_nothing_to_do():
    assert system.accepted_rc("fw", [], 2) and not system.accepted_rc("flatpak", [], 2)
    assert system.parse_fw_updates("", 2) == {"count": 0, "devices": []}
    raw = json.dumps({"Devices": [{"Name": "Dock", "Releases": [{"Version": "1.2"}]}]})
    assert system.parse_fw_updates(raw, 0)["devices"] == [{"name": "Dock", "version": "1.2"}]


def test_report_has_sections():
    text = build_report({"about": "x", "empty": ""})
    assert "===== about =====" in text and "(empty)" in text
