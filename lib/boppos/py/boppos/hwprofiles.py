"""What kind of machine the wizard is running on, and named fake machines to preview it on.

`boppos-deck --welcome --hardware KEY` uses one of PROFILES instead of detecting this machine, so every
hardware-dependent page of the wizard can be looked at from any computer. Nothing here reads more than sysfs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .dmi import device_label, read_dmi

KINDS = ("handheld", "laptop", "desktop", "vm")

# sysfs PCI vendor ids of the display adapters
GPU_VENDORS = {"0x1002": "amd", "0x8086": "intel", "0x10de": "nvidia"}


@dataclass(frozen=True)
class Hardware:
    key: str
    label: str
    kind: str
    dmi: dict[str, str] = field(default_factory=dict)
    gpus: tuple[str, ...] = ()
    tpm: bool = False
    camera: bool = False


PROFILES: dict[str, Hardware] = {h.key: h for h in (
    Hardware("steam-deck", "Valve Steam Deck", "handheld",
             {"sys_vendor": "Valve", "product_name": "Jupiter", "board_name": "Jupiter"}, ("amd",), tpm=True),
    Hardware("rog-ally", "ASUS ROG Ally", "handheld",
             {"sys_vendor": "ASUSTeK COMPUTER INC.", "product_name": "ROG Ally RC71L"}, ("amd",), tpm=True),
    Hardware("legion-go", "Lenovo Legion Go", "handheld",
             {"sys_vendor": "LENOVO", "product_name": "83E1"}, ("amd",), tpm=True),
    Hardware("amd-laptop", "AMD laptop", "laptop",
             {"sys_vendor": "Generic", "product_name": "AMD Laptop"}, ("amd",), tpm=True, camera=True),
    Hardware("intel-laptop", "Intel laptop", "laptop",
             {"sys_vendor": "Generic", "product_name": "Intel Laptop"}, ("intel",), tpm=True, camera=True),
    Hardware("amd-desktop", "AMD desktop", "desktop",
             {"sys_vendor": "Generic", "product_name": "AMD Desktop"}, ("amd",), tpm=True),
    Hardware("nvidia-desktop", "Intel + NVIDIA desktop", "desktop",
             {"sys_vendor": "Generic", "product_name": "Intel Desktop"}, ("intel", "nvidia"), tpm=True),
    Hardware("vm", "Virtual machine", "vm", {"sys_vendor": "QEMU", "product_name": "Standard PC"}),
)}

# Handhelds by DMI, for detection on a real machine (product_name patterns, any case).
HANDHELD_PRODUCTS = ("jupiter", "galileo", "rog ally*", "83e1", "83n0", "83n1", "claw*", "onexplayer*", "ayaneo*")


def parse_profile(name: str) -> Hardware:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown hardware {name!r}; choose one of: {', '.join(PROFILES)}") from None


def gpus_in(root: str | Path = "/") -> tuple[str, ...]:
    found: list[str] = []
    for vendor_file in sorted((Path(root) / "sys/class/drm").glob("card[0-9]*/device/vendor")):
        try:
            vendor = GPU_VENDORS.get(vendor_file.read_text().strip().lower())
        except OSError:
            continue
        if vendor and vendor not in found:
            found.append(vendor)
    return tuple(found)


def _kind(dmi: dict[str, str], root: Path) -> str:
    import fnmatch

    product = dmi.get("product_name", "").lower()
    if dmi.get("sys_vendor", "").lower() in ("qemu", "kvm", "vmware, inc.", "innotek gmbh") or \
            (Path(root) / "sys/hypervisor").exists():
        return "vm"
    if dmi.get("sys_vendor") == "Valve" or any(fnmatch.fnmatchcase(product, p) for p in HANDHELD_PRODUCTS):
        return "handheld"
    if any((Path(root) / "sys/class/power_supply").glob("BAT*")):
        return "laptop"
    return "desktop"


def detect(root: str | Path = "/") -> Hardware:
    """This machine, from DMI and sysfs. The root is injectable for tests."""
    root = Path(root)
    dmi = read_dmi(root)
    return Hardware(
        "detected", device_label(dmi), _kind(dmi, root), dmi, gpus_in(root),
        tpm=(root / "dev/tpmrm0").exists() or (root / "dev/tpm0").exists(),
        camera=any((root / "dev").glob("video*")),
    )


# What is switched on at the start for each kind of machine, on top of the wizard's own defaults. Never kargs.
KIND_DEFAULTS: dict[str, dict[str, object]] = {
    "handheld": {"steam_shortcuts": True, "decky": True, "extra_flatpaks": ("com.vysp3r.ProtonPlus",)},
    "laptop": {"auto_updates": True},
    "desktop": {},
    "vm": {"firmware_check": False},
}
