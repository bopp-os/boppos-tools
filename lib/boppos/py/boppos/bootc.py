"""Parse `bootc status --format=json` into plain dicts."""

from __future__ import annotations

import json
from typing import Any


def _image(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    img = entry.get("image") or {}
    ref = (img.get("image") or {}).get("image", "")
    ostree = entry.get("ostree") or {}
    return {
        "ref": ref,
        "transport": (img.get("image") or {}).get("transport", "registry"),
        "version": img.get("version"),
        "timestamp": img.get("timestamp"),
        "digest": img.get("imageDigest") or ref,
        "pinned": bool(entry.get("pinned")),
        "checksum": ostree.get("checksum"),
        "serial": ostree.get("deploySerial"),
    }


def parse_status(raw: str) -> dict[str, Any]:
    """Return {booted, staged, rollback, update_available, reboot_required} or {error}."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"error": f"parse: {exc}"}
    status = data.get("status") or {}
    booted = _image(status.get("booted"))
    staged = _image(status.get("staged"))
    rollback = _image(status.get("rollback"))
    return {
        "booted": booted,
        "staged": staged,
        "rollback": rollback,
        "update_available": bool(staged and (not booted or staged["digest"] != booted["digest"])),
        "reboot_required": staged is not None,
    }
