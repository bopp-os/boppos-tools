"""What bopp-tray shows: the update state and the menu layout, as plain data (no GUI imports)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

SEP = None  # a separator in a menu spec


@dataclass(frozen=True)
class TrayState:
    kind: str  # "ok", "update", "restart" or "unknown"
    headline: str
    last_checked: str
    has_diff: bool

    @property
    def needs_attention(self) -> bool:
        return self.kind in ("update", "restart")


def last_checked_label(status: dict | None) -> str:
    ts = (status or {}).get("checked_at", "")
    if ts:
        try:
            local = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
            return f"Last checked: {local.strftime('%H:%M')}"
        except ValueError:
            pass
    return "Last checked: never"


def compute_state(status: dict | None, deploy: dict | None) -> TrayState:
    """`status` is /run/boppos/update-status.json; `deploy` is `bopp-system --json status` (or None).

    The status file cannot tell "newer image upstream" from "downloaded, waiting for a restart", so the
    restart state comes from the deployment status.
    """
    checked = last_checked_label(status)
    has_diff = bool(status and status.get("diff"))
    if deploy and deploy.get("reboot_required"):
        return TrayState("restart", "Restart to finish the update", checked, has_diff)
    if status is None:
        return TrayState("unknown", "Update status unavailable", checked, False)
    if status.get("update_available"):
        return TrayState("update", "Update available", checked, has_diff)
    if status.get("error"):
        return TrayState("unknown", "Last update check failed", checked, has_diff)
    return TrayState("ok", "BoppOS is up to date", checked, has_diff)


def tooltip(state: TrayState) -> str:
    return {"ok": "BoppOS: system is up to date", "update": "BoppOS: system update available",
            "restart": "BoppOS: restart to finish the update"}.get(state.kind, "BoppOS: update status unavailable")


def menu_spec(state: TrayState, *, have_settings: bool, have_system: bool, busy: bool) -> list[dict | None]:
    """The menu as nested dicts (id, label, enabled, visible, default, children); None is a separator."""

    def item(id_: str, label: str, **kw) -> dict:
        return {"id": id_, "label": label, "enabled": kw.pop("enabled", True), "visible": kw.pop("visible", True),
                "default": kw.pop("default", False), "children": kw.pop("children", None)}

    install = [
        item("update-all", "Everything (system, apps, containers)"),
        item("update-os", "Operating System", visible=have_system),
        item("update-flatpak", "Flatpak Apps", visible=have_system),
        item("update-firmware", "Firmware", visible=have_system),
        SEP,
        item("update-containers", "Containers (Distrobox & Litterbox)…"),
    ]
    spec: list[dict | None] = [item("status", state.headline, enabled=False)]
    if state.kind == "restart":
        spec.append(item("restart", "Restart to Finish Update…"))
    spec += [
        SEP,
        item("open-settings", "Open BoppOS Settings", visible=have_settings, default=True),
        SEP if have_settings else None,
        item("check", "Check for Updates", enabled=not busy),
        item("install", "Updating…" if busy else "Install Updates", enabled=not busy, children=install),
        item("review", "Review Changes…", enabled=state.has_diff),
        SEP,
        item("last-checked", state.last_checked, enabled=False),
        item("quit", "Quit"),
    ]
    # collapse separators: none at the ends, never two in a row
    out: list[dict | None] = []
    for entry in spec:
        if entry is None and (not out or out[-1] is None):
            continue
        out.append(entry)
    while out and out[-1] is None:
        out.pop()
    return out
