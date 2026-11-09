import json

import pytest
from boppos import debug
from boppos.privileged import SSH_STOP, UsageError, debug_commands


@pytest.mark.parametrize("props,state", [
    ({"LoadState": "not-found"}, "missing"),
    ({"LoadState": "loaded", "UnitFileState": "enabled", "ActiveState": "inactive"}, "always"),
    ({"LoadState": "loaded", "UnitFileState": "disabled", "ActiveState": "active"}, "on"),
    ({"LoadState": "loaded", "UnitFileState": "disabled", "ActiveState": "inactive"}, "off"),
])
def test_ssh_state(props, state):
    assert debug.ssh_state(props) == state


def test_ssh_start_has_a_stop_timer_and_never_enables():
    start = debug_commands(["ssh-start"])
    assert start[0] == ["/usr/bin/systemctl", "start", "sshd.service"]
    assert start[1][0] == "/usr/bin/systemd-run" and start[1][-3:] == SSH_STOP
    assert not any("enable" in part for cmd in start for part in cmd)
    assert debug_commands(["ssh-stop"]) == [SSH_STOP]


@pytest.mark.parametrize("argv", [[], ["ssh-start", "x"], ["start"], ["ssh-stop;id"]])
def test_bad_debug_argv_rejected(argv):
    with pytest.raises(UsageError):
        debug_commands(argv)


def test_addresses_skip_loopback():
    text = json.dumps([{"flags": ["LOOPBACK", "UP"], "addr_info": [{"local": "127.0.0.1"}]},
                       {"flags": ["UP"], "addr_info": [{"local": "192.168.1.5"}]}])
    assert debug.parse_addresses(text) == ["192.168.1.5"]
    assert debug.parse_addresses("not json") == []


def test_backlight_report(tmp_path):
    dev = tmp_path / "amdgpu_bl0"
    dev.mkdir()
    (dev / "brightness").write_text("100\n")
    (dev / "max_brightness").write_text("255\n")
    report = debug.backlight_report(tmp_path)
    assert "amdgpu_bl0: " in report and "brightness=100" in report and "max_brightness=255" in report
    assert debug.backlight_report(tmp_path / "none") == "no backlight device"
