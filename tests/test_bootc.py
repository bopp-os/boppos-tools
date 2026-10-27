import json
from pathlib import Path

from boppos.bootc import ProgressReader, human_size, parse_status
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


def _pull(got, steps, total=1_200_000_000, cached=0):
    return json.dumps({"type": "ProgressBytes", "task": "pulling", "description": "Pulling Image: sha256:abc",
                       "id": "sha256:abc", "bytesCached": cached, "bytes": got, "bytesTotal": total,
                       "stepsCached": 0, "steps": steps, "stepsTotal": 450, "subtasks": []})


def test_human_size():
    assert [human_size(n) for n in (512, 1_500, 310_000_000, 1_200_000_000)] == ["512 B", "1.5 kB", "310.0 MB",
                                                                               "1.2 GB"]


def test_pull_progress_shows_layers_bytes_speed_and_time_left():
    r = ProgressReader()
    assert r.feed("Fetching layers", 0) is None and r.feed("{not json", 0) is None
    assert r.feed('{"type":"Start","version":"0.1"}', 0) == []
    first = r.feed(_pull(0, 0, cached=50_000_000), 0.0)
    assert first[0] == {"event": "log", "line": "Downloading 450 layers (1.2 GB), 50.0 MB already on disk"}
    assert first[1]["line"] == "Layer 1 of 450 · 0 B of 1.2 GB" and first[1]["percent"] == 0
    assert r.feed(_pull(1_000_000, 0), 0.1) == []  # redraws are throttled
    ev = r.feed(_pull(300_000_000, 11), 3.0)[0]
    assert ev["line"] == "Layer 12 of 450 · 300.0 MB of 1.2 GB · 100.0 MB/s · under a minute left"
    assert ev["percent"] == 25 and ev["bar"] == "25% · 300.0 MB of 1.2 GB"
    assert r.feed(_pull(1_200_000_000, 450), 3.01)[0]["percent"] == 100  # the last update is never dropped


def test_step_tasks_get_a_heading_and_the_active_subtask():
    r = ProgressReader()
    r.feed(_pull(0, 0), 0)
    ev = json.dumps({"type": "ProgressSteps", "task": "staging", "description": "Deploying Image", "id": "x",
                     "stepsCached": 0, "steps": 1, "stepsTotal": 3,
                     "subtasks": [{"subtask": "a", "description": "Staging", "id": "a", "completed": True},
                                  {"subtask": "b", "description": "Pulling bound images", "id": "b",
                                   "completed": False}]})
    out = r.feed(ev, 1)
    assert out[0] == {"event": "log", "line": "Deploying Image"}
    assert out[1]["line"] == "Deploying Image: Pulling bound images (1 of 3)"
    assert round(out[1]["percent"]) == 33
