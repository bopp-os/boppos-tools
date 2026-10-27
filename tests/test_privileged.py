import json

import pytest
from boppos import kargs_cli
from boppos.privileged import UsageError, kargs_argv, switch_target, system_command

FIX = json.dumps(
    {"status": {"booted": {"image": {"image": {"image": "ghcr.io/bopp-os/cachyos-plasma:stable"}}}}}
)


def test_fixed_verbs():
    assert system_command(["upgrade"]) == ["/usr/bin/bootc", "upgrade"]
    assert system_command(["check"])[-1] == "--check"
    assert system_command(["pin", "1"]) == ["/usr/bin/ostree", "admin", "pin", "1"]
    assert system_command(["unpin", "2"])[-2:] == ["--unpin", "2"]


@pytest.mark.parametrize(
    "argv",
    [[], ["bash"], ["upgrade", "--apply"], ["pin"], ["pin", "10"], ["pin", "-1"], ["pin", "0; id"],
     ["status", "x"], ["switch"], ["switch", "../x"], ["switch", "a b"], ["switch", "x:y"]],
)
def test_bad_system_argv_rejected(argv):
    with pytest.raises(UsageError):
        system_command(argv, FIX)


def test_switch_only_changes_tag():
    assert switch_target(FIX, "testing") == "ghcr.io/bopp-os/cachyos-plasma:testing"
    assert system_command(["switch", "v1.2"], FIX) == [
        "/usr/bin/bootc", "switch", "ghcr.io/bopp-os/cachyos-plasma:v1.2"]


def test_switch_handles_missing_tag_digest_and_registry_port():
    def ref(r):
        return json.dumps({"status": {"booted": {"image": {"image": {"image": r}}}}})

    assert switch_target(ref("localhost:5000/os"), "t") == "localhost:5000/os:t"
    assert switch_target(ref("ghcr.io/o/i:old@sha256:abc"), "t") == "ghcr.io/o/i:t"
    with pytest.raises(UsageError):
        switch_target(json.dumps({"status": {}}), "t")


def test_kargs_helper_verbs():
    assert kargs_argv(["apply"]) == ["--json", "apply"]
    assert kargs_argv(["list"]) == ["--json", "list"]
    assert kargs_argv(["enable", "steam-deck"]) == ["--json", "enable", "steam-deck"]
    assert kargs_argv(["add", "pci=noaer"]) == ["--json", "add", "pci=noaer"]
    assert kargs_argv(["remove", "quiet"]) == ["--json", "remove", "quiet"]
    for bad in ([], ["edit"], ["enable"], ["enable", "../etc/x"], ["enable", "A"], ["disable", "a", "b"],
                ["apply", "x"], ["add"], ["add", "a", "b"], ["list", "0"]):
        with pytest.raises(UsageError):
            kargs_argv(bad)


@pytest.mark.parametrize("arg", [
    "init=/bin/sh", "rdinit=/bin/sh", "rd.break", "rd.break=pre-mount", "single", "emergency", "1", "S",
    "systemd.unit=rescue.target", "systemd.debug_shell", "ostree=/x", "root=/dev/sda", "a b", "x;y", "$(id)", "",
])
def test_add_remove_refuse_dangerous_or_invalid_args(arg):
    for verb in ("add", "remove"):
        with pytest.raises(UsageError):
            kargs_argv([verb, arg])


def test_cli_uses_helper_when_not_root(monkeypatch, capsys):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr("os.path.exists", lambda p: p == kargs_cli.HELPER)
    calls = []

    class P:
        returncode = 0
        stdout = json.dumps({"added": ["a"], "removed": [], "changed": True, "reboot_required": True})
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda argv, **kw: calls.append(argv) or P())
    assert kargs_cli.main(["enable", "deck"]) == 0
    assert calls == [["pkexec", kargs_cli.HELPER, "enable", "deck"]]
    monkeypatch.setenv("BOPPOS_HELPER_DIR", "/work/libexec/boppos")
    monkeypatch.setattr("os.path.exists", lambda p: p == "/work/libexec/boppos/kargs-helper")
    calls.clear()
    assert kargs_cli.main(["disable", "deck"]) == 0
    assert calls == [["pkexec", "/work/libexec/boppos/kargs-helper", "disable", "deck"]]
    assert "+ a" in capsys.readouterr().out


def test_cli_add_still_needs_root(monkeypatch, capsys):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    assert kargs_cli.main(["add", "x"]) == 1


def test_cli_list_and_add_go_through_the_helper(monkeypatch, capsys):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr("os.path.exists", lambda p: p == kargs_cli.HELPER)
    calls = []

    class P:
        returncode = 0
        stderr = ""

        def __init__(self, argv):
            self.stdout = json.dumps({"options": ["quiet", "rw"]} if "list" in argv else
                                     {"added": ["pci=noaer"], "removed": [], "changed": True, "reboot_required": True})

    monkeypatch.setattr("subprocess.run", lambda argv, **kw: calls.append(argv) or P(argv))
    assert kargs_cli.main(["list"]) == 0
    assert capsys.readouterr().out.split() == ["quiet", "rw"]
    assert kargs_cli.main(["add", "pci=noaer"]) == 0
    assert calls[-1] == ["pkexec", kargs_cli.HELPER, "add", "pci=noaer"]
    assert kargs_cli.main(["list", "1"]) == 1  # an explicit index needs root
