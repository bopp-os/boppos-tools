import json

import pytest
from boppos import kargs_cli
from boppos.privileged import UsageError, kargs_argv, system_command, validate_image_ref

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
     ["status", "x"], ["switch"], ["switch", "v4"], ["switch", "a", "b"], ["rollback", "now"]],
)
def test_bad_system_argv_rejected(argv):
    with pytest.raises(UsageError):
        system_command(argv)


GOOD = ["ghcr.io/bopp-os/cachyos-plasma:znver4", "ghcr.io/bopp-os/cachyos-gnome:v4-20260409",
        "registry.example.com:5000/team/os/image:1.2", "localhost/os:test",
        "ghcr.io/bopp-os/cachyos-plasma@sha256:" + "a" * 64, "ghcr.io/bopp-os/cachyos-plasma:v4@sha256:" + "b" * 64]


@pytest.mark.parametrize("ref", GOOD)
def test_full_references_are_accepted(ref):
    assert validate_image_ref(ref) == ref
    assert system_command(["switch", ref]) == [
        "/usr/bin/bootc", "switch", "--transport", "registry", "--enforce-container-sigpolicy", ref]


@pytest.mark.parametrize("ref", [
    "v4", "latest", ":v4", "znver4",                                  # a tag is not an image
    "ghcr.io/bopp-os/cachyos-plasma",                                  # no tag: nothing is assumed
    "cachyos-plasma:v4", "bopp-os/cachyos-plasma:v4",                  # no registry host
    "docker://ghcr.io/bopp-os/x:v4", "oci:/tmp/image:v4", "dir:/tmp/x", "containers-storage:localhost/x:1",
    "oci-archive:/tmp/x.tar", "docker-archive:/tmp/x.tar",              # transports can point at local files
    "ghcr.io/Bopp-OS/x:v4", "ghcr.io/bopp-os/x:v4 --apply", "ghcr.io/bopp-os/x:v4;id", "ghcr.io/bopp-os/$(id):v4",
    "ghcr.io/bopp-os/x:v4\nfoo", "--help", "ghcr.io//x:v4", "ghcr.io/bopp-os/x@sha256:abc", "", " ",
    "ghcr.io/" + "a" * 400 + ":v4",
])
def test_anything_short_of_a_full_reference_is_refused(ref):
    with pytest.raises(UsageError):
        validate_image_ref(ref)
    with pytest.raises(UsageError):
        system_command(["switch", ref])


def test_a_bare_tag_error_says_what_to_type():
    with pytest.raises(UsageError, match="full image reference"):
        validate_image_ref("v4")


def test_channel_notes_are_read_only_descriptions():
    from boppos import system

    assert system.channel_note("znver4").startswith("znver4:")
    assert system.channel_note("latest") == system.channel_note("v3")
    assert "test build" in system.channel_note("v4-test") and "20260409" in system.channel_note("znver4-20260409")
    assert system.channel_note("my-branch") == "A custom tag." and system.channel_note(None) == ""


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


ADD_REFUSED = [
    # shell / init / rescue
    "init=/bin/sh", "rdinit=/bin/sh", "rd.break", "rd.break=pre-mount", "rd.shell", "single", "emergency", "1", "S",
    "systemd.unit=rescue.target", "systemd.debug_shell", "systemd.mask=sshd.service", "systemd.run=/bin/sh",
    # security features
    "selinux=0", "enforcing=0", "security=none", "apparmor=0", "lsm=", "lockdown=none", "audit=0", "fips=0",
    "ima_appraise=off",
    # drivers and hardware that can stop the machine starting
    "module_blacklist=nvme", "modprobe.blacklist=amdgpu", "initcall_blacklist=x", "acpi=off", "noacpi", "nosmp",
    "maxcpus=1", "mem=512M", "memmap=1G$4G",
    # what boot needs
    "ostree=/x", "root=/dev/sda", "rootflags=subvol=x", "rootfstype=ext4", "rw", "rd.luks.uuid=1", "rd.lvm.lv=a/b",
    "rd.driver.pre=x", "rd.md.uuid=1", "luks.name=1=x",
    # not even arguments
    "a b", "x;y", "$(id)", "",
]


@pytest.mark.parametrize("arg", ADD_REFUSED)
def test_the_helper_will_not_add_dangerous_or_invalid_arguments(arg):
    with pytest.raises(UsageError):
        kargs_argv(["add", arg])


@pytest.mark.parametrize("arg", ["pci=noaer", "usbhid.mousepoll=1", "nvme_core.default_ps_max_latency_us=0",
                                 "threadirqs", "amdgpu.runpm=0", "intel_iommu=on", "mitigations=off",
                                 "rd.luks.options=tpm2-device=auto"])
def test_ordinary_tuning_arguments_can_still_be_added(arg):
    assert kargs_argv(["add", arg])[-1] == arg


@pytest.mark.parametrize("arg", ["ostree=/x", "root=/dev/sda", "rootflags=subvol=x", "rootfstype=ext4", "rw",
                                 "rd.luks.uuid=1", "rd.lvm.lv=a/b", "rd.driver.pre=x", "luks.name=1=x", "a b", ""])
def test_the_helper_will_not_remove_what_boot_needs(arg):
    with pytest.raises(UsageError):
        kargs_argv(["remove", arg])


@pytest.mark.parametrize("arg", ["selinux=0", "systemd.debug_shell", "acpi=off", "quiet", "mitigations=off",
                                 "rd.luks.options=tpm2-device=auto", "rd.plymouth=0"])
def test_but_a_bad_argument_that_is_already_there_can_always_be_removed(arg):
    assert kargs_argv(["remove", arg])[-1] == arg


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


def test_escalation_prefix(monkeypatch):
    from boppos import system

    monkeypatch.delenv("BOPPOS_HELPER_ESCALATE", raising=False)
    assert system.escalation(euid=1000) == ["pkexec"] and system.escalation(euid=0) == []
    monkeypatch.setenv("BOPPOS_HELPER_ESCALATE", "sudo")
    assert system.escalation(euid=1000) == ["sudo", "-n"] and system.escalation(euid=0) == []
    monkeypatch.setenv("BOPPOS_HELPER_ESCALATE", "anything-else")
    assert system.escalation(euid=1000) == ["pkexec"]
