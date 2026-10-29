import pytest
from boppos import hwprofiles


def test_every_profile_is_consistent():
    for key, hw in hwprofiles.PROFILES.items():
        assert hw.key == key and hw.kind in hwprofiles.KINDS and hw.label
        assert set(hw.gpus) <= set(hwprofiles.GPU_VENDORS.values())
    assert {h.kind for h in hwprofiles.PROFILES.values()} == set(hwprofiles.KINDS)


def test_unknown_profile_lists_the_choices():
    with pytest.raises(ValueError, match="steam-deck"):
        hwprofiles.parse_profile("toaster")
    assert hwprofiles.parse_profile("rog-ally").kind == "handheld"


def _root(tmp_path, dmi=None, gpu=None, battery=False, tpm=False):
    for name, value in (dmi or {}).items():
        f = tmp_path / "sys/class/dmi/id" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(value + "\n")
    if gpu:
        f = tmp_path / "sys/class/drm/card0/device/vendor"
        f.parent.mkdir(parents=True)
        f.write_text(gpu + "\n")
    if battery:
        (tmp_path / "sys/class/power_supply/BAT0").mkdir(parents=True)
    if tpm:
        (tmp_path / "dev").mkdir(exist_ok=True)
        (tmp_path / "dev/tpmrm0").touch()
    return tmp_path


def test_detect_a_handheld(tmp_path):
    hw = hwprofiles.detect(_root(tmp_path, {"sys_vendor": "Valve", "product_name": "Jupiter"}, "0x1002", True, True))
    assert hw.kind == "handheld" and hw.gpus == ("amd",) and hw.tpm and not hw.camera


def test_detect_laptop_desktop_and_vm(tmp_path):
    lap = hwprofiles.detect(_root(tmp_path / "a", {"sys_vendor": "Dell", "product_name": "XPS"}, "0x8086", True))
    desk = hwprofiles.detect(_root(tmp_path / "b", {"sys_vendor": "ASUS", "product_name": "PRIME"}, "0x10de"))
    vm = hwprofiles.detect(_root(tmp_path / "c", {"sys_vendor": "QEMU", "product_name": "Standard PC"}))
    assert (lap.kind, desk.kind, vm.kind) == ("laptop", "desktop", "vm")
    assert lap.gpus == ("intel",) and desk.gpus == ("nvidia",) and vm.gpus == ()


def test_detect_on_an_empty_root(tmp_path):
    hw = hwprofiles.detect(tmp_path)
    assert hw.kind == "desktop" and hw.label == "Unknown device" and not (hw.tpm or hw.camera)


def test_device_fix_presets_are_on_for_their_own_profile_only():

    from boppos import kargs

    found = kargs.load_dropins("/")
    fixes = {n: d for n, d in found.items() if d.category == "Device fixes"}
    expected = {"onexplayer-f1": "90-onexplayer-f1-panel", "ayaneo-slide": "91-ayaneo-slide-acpi-strict",
                "framework-intel": "92-framework-intel-keyboard"}
    for key, hw in hwprofiles.PROFILES.items():
        hit = {n for n, d in fixes.items() if kargs.matches_device(d, hw.dmi)}
        assert hit == ({expected[key]} if key in expected else set()), key
