"""DMI identity of this machine, read from sysfs (root is injectable for tests)."""

from __future__ import annotations

from pathlib import Path

DMI_FIELDS = (
    "sys_vendor",
    "product_name",
    "product_version",
    "board_vendor",
    "board_name",
    "bios_vendor",
    "bios_version",
)


def read_dmi(root: str | Path = "/") -> dict[str, str]:
    """Return the DMI fields found under <root>/sys/class/dmi/id (missing ones are skipped)."""
    base = Path(root) / "sys/class/dmi/id"
    out: dict[str, str] = {}
    for field in DMI_FIELDS:
        try:
            value = (base / field).read_text(errors="replace").strip()
        except OSError:
            continue
        if value:
            out[field] = value
    return out


def device_label(dmi: dict[str, str]) -> str:
    vendor = dmi.get("sys_vendor", "")
    product = dmi.get("product_name", "")
    return " ".join(p for p in (vendor, product) if p) or "Unknown device"
