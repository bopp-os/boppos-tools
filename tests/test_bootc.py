import json
from pathlib import Path

from boppos import bootc
from boppos.bootc import parse_status
from boppos.dmi import device_label, read_dmi

FIX = Path(__file__).parent / "fixtures"


def test_parse_full_status():
    st = parse_status((FIX / "bootc-status.json").read_text())
    assert st["booted"]["digest"] == "sha256:aaa"
    assert st["staged"]["version"] == "43.2"
    assert st["rollback"]["pinned"] is True
    assert st["update_available"] and st["reboot_required"]


def test_no_staged_means_no_update():
    st = parse_status('{"status":{"booted":{"image":{"imageDigest":"sha256:a"}}}}')
    assert st["staged"] is None
    assert not st["update_available"] and not st["reboot_required"]


def test_bad_json_reports_error():
    assert "error" in parse_status("not json")


def test_dmi_reads_fake_sysfs(tmp_path):
    d = tmp_path / "sys/class/dmi/id"
    d.mkdir(parents=True)
    (d / "sys_vendor").write_text("Valve\n")
    (d / "product_name").write_text("Jupiter\n")
    dmi = read_dmi(tmp_path)
    assert dmi == {"sys_vendor": "Valve", "product_name": "Jupiter"}
    assert device_label(dmi) == "Valve Jupiter"
    assert device_label({}) == "Unknown device"


def test_deployment_indexes_follow_boot_order():
    st = parse_status((FIX / "bootc-status.json").read_text())
    assert [st[k]["index"] for k in ("staged", "booted", "rollback")] == [0, 1, 2]
    queued = parse_status(
        '{"status":{"rollbackQueued":true,"booted":{"image":{"imageDigest":"a"}},'
        '"rollback":{"image":{"imageDigest":"b"}}}}'
    )
    assert queued["rollback"]["index"] == 0 and queued["booted"]["index"] == 1
    assert queued["reboot_required"]


def test_legacy_lines_match_what_update_check_reads():
    raw = json.dumps({
        "spec": {"image": {"image": "r:t", "transport": "registry"}},
        "status": {
            "booted": {"image": {"image": {"image": "r:t"}, "imageDigest": "sha256:a"}},
            "staged": {"image": {"image": {"image": "r:t"}, "imageDigest": "sha256:b"}},
        },
    })
    assert bootc.legacy_lines(raw) == [
        "UPDATE:True", "CURRENT:sha256:a", "STAGED:sha256:b", "TRANSPORT:registry", "IMAGE_REF:r:t"]
    assert bootc.legacy_lines(raw, staged_only=True) == ["sha256:b"]
    assert bootc.legacy_lines("nope")[0].startswith("ERROR:parse:Expecting")
    assert bootc.legacy_lines("nope", staged_only=True) == []
    assert bootc.legacy_lines("{}")[:2] == ["UPDATE:False", "CURRENT:unknown"]
