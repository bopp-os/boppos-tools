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


def test_kargs_helper_exposes_only_dropin_verbs():
    assert kargs_argv(["apply"]) == ["--json", "apply"]
    assert kargs_argv(["enable", "steam-deck"]) == ["--json", "enable", "steam-deck"]
    for bad in ([], ["add", "init=/bin/sh"], ["remove", "quiet"], ["edit"], ["enable"],
                ["enable", "../etc/x"], ["enable", "A"], ["disable", "a", "b"], ["apply", "x"]):
        with pytest.raises(UsageError):
            kargs_argv(bad)


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
    assert "+ a" in capsys.readouterr().out


def test_cli_add_still_needs_root(monkeypatch, capsys):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    assert kargs_cli.main(["add", "x"]) == 1
