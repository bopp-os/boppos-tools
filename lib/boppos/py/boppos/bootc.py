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
    """Return {booted, staged, rollback, update_available, rollback_queued, reboot_required}
    or {error}."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"error": f"parse: {exc}"}
    status = data.get("status") or {}
    booted = _image(status.get("booted"))
    staged = _image(status.get("staged"))
    rollback = _image(status.get("rollback"))
    # ostree lists deployments in boot order: staged first; a queued rollback goes before booted.
    queued = bool(status.get("rollbackQueued"))
    order = [staged, rollback, booted] if queued else [staged, booted, rollback]
    for i, entry in enumerate(e for e in order if e):
        entry["index"] = i
    return {
        "booted": booted,
        "staged": staged,
        "rollback": rollback,
        "update_available": bool(staged and (not booted or staged["digest"] != booted["digest"])),
        "rollback_queued": bool(status.get("rollbackQueued")),
        "reboot_required": staged is not None or bool(status.get("rollbackQueued")),
    }


def legacy_lines(raw: str, staged_only: bool = False) -> list[str]:
    """The KEY:value lines update-check.sh reads (what parse-bootc-status.py has always printed)."""
    parsed = parse_status(raw)
    if "error" in parsed:
        return [] if staged_only else [f"ERROR:parse:{parsed['error'].removeprefix('parse: ')}"]
    staged = parsed["staged"]
    booted = parsed["booted"]
    staged_digest = staged["digest"] if staged else ""
    if staged_only:
        return [staged_digest]
    current = (booted["digest"] if booted else "") or "unknown"
    transport = (((json.loads(raw).get("spec") or {}).get("image") or {}).get("transport")) or "registry"
    return [
        f"UPDATE:{bool(staged_digest and staged_digest != current)}",
        f"CURRENT:{current}",
        f"STAGED:{staged_digest}",
        f"TRANSPORT:{transport}",
        f"IMAGE_REF:{booted['ref'] if booted else ''}",
    ]
