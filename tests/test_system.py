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


REGISTRY = ["latest", "v3", "v4", "znver4", "v3-20260407", "v4-20260409", "v4-20260410", "znver4-20260410",
            "v4-test", "znver4-test", "v3-test", "sha256-abc.sig", "sha256-def.att", "weird", "v5", "v4-2026"]


def test_channels_are_v3_latest_v4_and_znver4():
    data = system.classify_tags(REGISTRY, "znver4")
    assert [(c["tag"], c["label"]) for c in data["channels"]] == [
        ("latest", "v3 (default)"), ("v4", "v4"), ("znver4", "znver4")]
    assert data["current_channel"] == "znver4"
    assert system.classify_tags([], "v3")["current_channel"] == "latest"  # v3 is the same channel as latest
    assert system.classify_tags([], "latest")["current_channel"] == "latest"
    assert system.classify_tags([], "something-custom")["current_channel"] is None


def test_older_builds_are_the_current_channels_dated_tags_newest_first():
    assert system.classify_tags(REGISTRY, "v4")["builds"] == ["v4-20260410", "v4-20260409"]
    assert system.classify_tags(REGISTRY, "znver4-20260410")["builds"] == ["znver4-20260410"]  # a dated build counts
    assert system.classify_tags(REGISTRY, "latest")["builds"] == ["v3-20260407"]
    many = [f"v4-202604{d:02d}" for d in range(1, 29)]
    assert len(system.classify_tags(many, "v4")["builds"]) == 14
    assert system.classify_tags(many, "v4")["builds"][0] == "v4-20260428"


def test_test_builds_and_noise():
    data = system.classify_tags(REGISTRY, "v4")
    assert data["test"] == ["v4-test", "v3-test", "znver4-test"]  # the current channel's test build comes first
    everything = data["builds"] + data["test"] + [c["tag"] for c in data["channels"]]
    assert not any(t.startswith("sha256-") or t in ("weird", "v5", "v4-2026") for t in everything)


def test_tag_choices_without_skopeo_still_offers_the_channels(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: None)
    data = system.tag_choices("ghcr.io/o/i", "v4")
    assert len(data["channels"]) == 3 and data["builds"] == [] and data["test"] == []


def test_tag_choices_reads_skopeo(monkeypatch):
    class P:
        returncode = 0
        stdout = json.dumps({"Tags": REGISTRY})

    monkeypatch.setattr("shutil.which", lambda n: "/x")
    monkeypatch.setattr(system, "run_capture", lambda *a, **k: P())
    assert system.tag_choices("ghcr.io/o/i", "v4")["builds"][0] == "v4-20260410"


def test_report_has_sections():
    text = build_report({"about": "x", "empty": ""})
    assert "===== about =====" in text and "(empty)" in text
