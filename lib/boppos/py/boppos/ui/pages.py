"""The pages of boppos-deck. Each page builds rows from CLI output and runs actions as jobs."""

from __future__ import annotations

import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .backend import run_json, tool  # noqa: E402
from .widgets import button_row, combo_row, group, info_row, message_row, switch_row  # noqa: E402


class Page:
    """Base class: a scrolling column that is rebuilt from fresh data by refresh()."""

    title = ""
    icon = "emblem-system-symbolic"
    key = ""

    def __init__(self, win):
        self.win = win
        self._gen = 0
        self.stale = True
        self.loaded_at = 0.0
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.box.set_margin_top(24)
        self.box.set_margin_bottom(24)
        self.box.set_margin_start(24)
        self.box.set_margin_end(24)
        clamp = Adw.Clamp(maximum_size=1400, tightening_threshold=1000)
        clamp.set_child(self.box)
        self.widget = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.widget.set_child(clamp)

    def clear(self) -> None:
        child = self.box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.box.remove(child)
            child = nxt

    def needs_refresh(self) -> bool:
        return self.stale or time.monotonic() - self.loaded_at > 30

    def refresh(self) -> None:
        """Show a placeholder, load data on a thread, then call build(data) on the main thread."""
        self.stale = False
        self.loaded_at = time.monotonic()
        self._gen += 1
        gen = self._gen
        self.clear()
        g = group()
        g.add(message_row("Loading…"))
        self.box.append(g)

        def done(data):
            if gen == self._gen:
                self.clear()
                self.build(data)

        self.win.load(self.fetch, done)

    def fetch(self):  # runs on a worker thread
        return None

    def build(self, data) -> None:  # runs on the main thread
        raise NotImplementedError


# --- System & updates -------------------------------------------------------------------------


class SystemPage(Page):
    title, key, icon = "System & updates", "system", "software-update-available-symbolic"

    def __init__(self, win):
        super().__init__(win)
        self.check: dict | None = None

    def fetch(self):
        return {
            "status": run_json([tool("bopp-system"), "--json", "status"]),
            "tags": run_json([tool("bopp-system"), "--json", "tags"], timeout=60) if self.win.advanced else None,
        }

    def _update_text(self, target: str, st: dict) -> str:
        if self.check is None:
            return "Press “Check for updates”"
        if target == "os":
            os_st = self.check.get("os", {})
            return os_st["error"] if "error" in os_st else (
                "Update available" if os_st.get("update_available") else "Up to date")
        count = (self.check.get(target) or {}).get("count")
        if count is None:
            return "Unknown"
        return f"{count} update(s) available" if count else "Up to date"

    def build(self, data) -> None:
        st = data["status"]
        if "error" in st:
            g = group("System")
            g.add(message_row(f"Could not read the system status: {st['error']}"))
            self.box.append(g)
            return
        if st.get("reboot_required"):
            banner = Adw.Banner.new("Reboot required to finish the update")
            banner.set_button_label("Reboot now")
            banner.set_revealed(True)
            banner.connect("button-clicked", lambda _b: self.reboot())
            self.box.append(banner)
        booted = st.get("booted") or {}
        g = group("This system")
        g.add(info_row("Current image", st.get("tag") or "", booted.get("ref", "")))
        if booted.get("version"):
            g.add(info_row("Version", booted["version"]))
        if st.get("staged"):
            g.add(info_row("Staged update", (st["staged"].get("version") or "ready"), "Applies on next reboot"))
        self.box.append(g)

        g = group("Updates")
        g.add(button_row("Check for updates", "Looks at the OS image, Flatpaks and firmware",
                         "Check", self.do_check, suggested=True))
        for target, label in (("os", "Operating system"), ("fw", "Firmware"), ("flatpak", "Flatpaks"),
                              ("brew", "Homebrew")):
            g.add(button_row(label, self._update_text(target, st), "Update",
                             lambda t=target, n=label: self.update([t], n)))
        g.add(button_row("Update everything", "Operating system, Flatpaks and Homebrew", "Update all",
                         lambda: self.update(None, "Everything"), suggested=True))
        self.box.append(g)

        if self.win.advanced:
            self.build_advanced(st, data.get("tags") or {})

    def build_advanced(self, st: dict, tags: dict) -> None:
        g = group("Deployments", "Advanced. Pinned deployments are kept when new ones are installed.")
        for key, label in (("staged", "Staged"), ("booted", "Booted"), ("rollback", "Rollback")):
            d = st.get(key)
            if not d:
                continue
            sub = f"{d.get('version') or 'unknown version'} · {str(d.get('digest', ''))[:19]}"
            idx = d.get("index")
            pinned = d.get("pinned")
            g.add(button_row(f"{label}{' (pinned)' if pinned else ''}", sub, "Unpin" if pinned else "Pin",
                             lambda i=idx, p=pinned, n=label: self.pin(i, p, n)))
        if st.get("rollback"):
            g.add(button_row("Roll back", "Boot the previous deployment next time", "Roll back",
                             self.rollback, destructive=True))
        self.box.append(g)

        names = tags.get("tags") or []
        if names:
            g = group("Update channel", "Advanced. Changes which tag of this image BoppOS follows.")
            cur = tags.get("current")
            combo = combo_row("Tag", names, names.index(cur) if cur in names else 0)
            g.add(combo)
            g.add(button_row("Switch to the selected tag", "Downloads that tag on the next update", "Switch",
                             lambda: self.switch(names[combo.get_selected()]), destructive=True))
            self.box.append(g)

    # actions
    def do_check(self) -> None:
        self.win.toast("Checking for updates…")

        def done(res):
            self.check = res if isinstance(res, dict) and "error" not in res else None
            if self.check is None:
                self.win.toast("Check failed: " + str(res.get("error", "unknown error")))
            self.refresh()

        self.win.load(lambda: run_json([tool("bopp-system"), "--json", "check"], timeout=240), done)

    def update(self, only: list[str] | None, label: str) -> None:
        cmd = [tool("bopp-system"), "--json", "upgrade"] + (["--only", ",".join(only)] if only else [])
        self.win.run_job(f"Updating: {label}", [cmd], lambda ok, final: self.refresh())

    def reboot(self) -> None:
        self.win.confirm("Reboot now?", "Save your game first. The system restarts immediately.", "Reboot",
                         lambda: self.win.run_job("Rebooting", [[tool("bopp-system"), "--json", "reboot"]],
                                                  lambda ok, final: None), destructive=True)

    def pin(self, index, pinned: bool, name: str) -> None:
        verb = "unpin" if pinned else "pin"
        self.win.confirm(f"{verb.capitalize()} the {name.lower()} deployment?",
                         "You can undo this at any time.", verb.capitalize(),
                         lambda: self.win.run_job("Pinning" if verb == "pin" else "Unpinning",
                                                  [[tool("bopp-system"), "--json", verb, str(index)]],
                                                  lambda ok, final: self.refresh()))

    def rollback(self) -> None:
        self.win.confirm("Roll back to the previous version?",
                         "The previous deployment will boot after the next reboot.", "Roll back",
                         lambda: self.win.run_job("Rolling back", [[tool("bopp-system"), "--json", "rollback"]],
                                                  lambda ok, final: self.refresh()), destructive=True)

    def switch(self, tag: str) -> None:
        self.win.confirm(f"Follow the “{tag}” tag?", "The next update will download that tag. "
                         "Switching to an older tag can downgrade the system.", "Switch",
                         lambda: self.win.run_job(f"Switching to {tag}",
                                                  [[tool("bopp-system"), "--json", "switch", tag]],
                                                  lambda ok, final: self.refresh()), destructive=True)


# --- Kernel arguments -------------------------------------------------------------------------


class KargsPage(Page):
    title, key, icon = "Kernel arguments", "kargs", "utilities-terminal-symbolic"

    def fetch(self):
        try:
            cmdline = Path("/proc/cmdline").read_text().strip()
        except OSError:
            cmdline = ""
        return {
            "dropins": run_json([tool("bopp-kargs"), "--json", "dropins"]),
            "status": run_json([tool("bopp-kargs"), "--json", "status"]),
            "cmdline": cmdline,
        }

    def build(self, data) -> None:
        dropins = data["dropins"]
        if "error" in dropins:
            g = group("Kernel arguments")
            g.add(message_row(f"Could not read the presets: {dropins['error']}"))
            self.box.append(g)
            return
        status = data["status"]
        blocked = status.get("blocked_reason") if "error" not in status else status["error"]
        if blocked:
            text = {"staged": "An update is staged. Reboot before changing kernel arguments."}.get(
                blocked, f"Changes are unavailable: {blocked}")
            banner = Adw.Banner.new(text)
            banner.set_revealed(True)
            self.box.append(banner)
        items = dropins.get("dropins", [])
        mine = [d for d in items if d["matches_device"] and not d["error"]]
        g = group("Presets for this device",
                  "Switches add or remove kernel arguments. They take effect after a reboot.")
        if not mine:
            g.add(message_row("No presets are available for this device."))
        for d in mine:
            sub = (d["description"] + "\n" if d["description"] else "") + " ".join(d["kargs"])
            row = switch_row(d["name"].replace("-", " ").capitalize(), sub, d["enabled"],
                             lambda on, name=d["name"]: self.toggle(name, on))
            row.set_sensitive(not blocked)
            g.add(row)
        self.box.append(g)

        g = group("Running kernel")
        g.add(info_row("Command line", "", data["cmdline"] or "unavailable"))
        self.box.append(g)

        others = [d for d in items if not d["matches_device"] or d["error"]]
        if self.win.advanced and others:
            g = group("Other presets", "Advanced. These do not match this device and are never applied.")
            for d in others:
                g.add(info_row(d["name"], "invalid" if d["error"] else "other device",
                               d["error"] or " ".join(d["kargs"])))
            self.box.append(g)

    def toggle(self, name: str, on: bool) -> None:
        verb = "enable" if on else "disable"

        def done(res):
            if isinstance(res, dict) and "error" in res:
                self.win.toast(f"Could not {verb} {name}: {res['error']}")
            else:
                self.win.toast("Saved. Reboot to apply.")
            self.refresh()

        self.win.load(lambda: run_json([tool("bopp-kargs"), "--json", verb, name], timeout=180), done)


# --- Apps -------------------------------------------------------------------------------------


class AppsPage(Page):
    title, key, icon = "Apps", "apps", "view-app-grid-symbolic"

    def fetch(self):
        return run_json([tool("install-optional-flatpaks"), "--json", "--status"], timeout=60)

    def build(self, data) -> None:
        if isinstance(data, dict) and "error" in data:
            g = group("Apps")
            g.add(message_row(f"Could not read the app list: {data['error']}"))
            self.box.append(g)
            return
        self.apps = [a for a in data if a.get("kind") == "app"]
        self.want = {a["id"]: a["installed"] for a in self.apps}
        g = group("BoppOS apps", "Switch an app on to install it, off to remove it, then press Apply.")
        for a in self.apps:
            g.add(switch_row(a["name"], a["id"] + ("  ·  installed" if a["installed"] else ""), a["installed"],
                             lambda on, i=a["id"]: self.set_want(i, on)))
        self.box.append(g)
        g = group()
        self.apply_row = button_row("Apply changes", "No changes yet", "Apply", self.apply, suggested=True)
        self.apply_row.set_sensitive(False)
        g.add(self.apply_row)
        g.add(message_row("To browse everything else on Flathub, open Bazaar."))
        self.box.append(g)

    def diff(self) -> tuple[list[str], list[str]]:
        add = [a["id"] for a in self.apps if self.want[a["id"]] and not a["installed"]]
        rem = [a["id"] for a in self.apps if not self.want[a["id"]] and a["installed"]]
        return add, rem

    def set_want(self, app_id: str, on: bool) -> None:
        self.want[app_id] = on
        add, rem = self.diff()
        self.apply_row.set_sensitive(bool(add or rem))
        self.apply_row.set_subtitle(f"{len(add)} to install, {len(rem)} to remove" if add or rem else "No changes yet")

    def apply(self) -> None:
        add, rem = self.diff()
        base = [tool("install-optional-flatpaks"), "--json", "--yes"]
        cmds = []
        if add:
            cmds.append([*base, "--only", ",".join(add)])
        if rem:
            cmds.append([*base, "--remove", "--only", ",".join(rem)])

        def go():
            self.win.run_job("Installing and removing apps", cmds, lambda ok, final: self.refresh())

        if rem:
            self.win.confirm("Remove apps?", f"{len(rem)} app(s) will be uninstalled. Their data is kept.",
                             "Apply", go, destructive=True)
        else:
            go()


# --- Decky ------------------------------------------------------------------------------------


class DeckyPage(Page):
    title, key, icon = "Decky Loader", "decky", "application-x-addon-symbolic"

    def fetch(self):
        return run_json([tool("bopp-decky"), "--json", "status"])

    def build(self, data) -> None:
        g = group("Decky Loader", "Plugin loader for Steam Gaming Mode. Installed for your user in ~/homebrew.")
        if "error" in data:
            g.add(message_row(f"Could not read the status: {data['error']}"))
            self.box.append(g)
            return
        if data["installed"]:
            state = "running" if data["service_active"] else "installed, not running"
            g.add(info_row("Status", state, data.get("version") or ""))
            g.add(button_row("Update Decky Loader", "Downloads the latest stable release and verifies it",
                             "Update", self.install, suggested=True))
        else:
            g.add(info_row("Status", "not installed"))
            g.add(button_row("Install Decky Loader", "Downloads the latest stable release and verifies it",
                             "Install", self.install, suggested=True))
        g.add(message_row("Steam restarts after installing so that Decky can attach to it."))
        self.box.append(g)
        if self.win.advanced and data["installed"]:
            g = group("Remove", "Advanced. Plugins and settings in ~/homebrew are kept.")
            g.add(button_row("Uninstall Decky Loader", "Stops and removes the service", "Uninstall",
                             self.uninstall, destructive=True))
            self.box.append(g)

    def install(self) -> None:
        self.win.run_job("Installing Decky Loader", [[tool("bopp-decky"), "--json", "install"]],
                         lambda ok, final: self.refresh())

    def uninstall(self) -> None:
        self.win.confirm("Uninstall Decky Loader?",
                         "The service is removed. Plugins and settings stay in ~/homebrew.",
                         "Uninstall", lambda: self.win.run_job(
                             "Removing Decky Loader", [[tool("bopp-decky"), "--json", "uninstall"]],
                             lambda ok, final: self.refresh()), destructive=True)


# --- Support ----------------------------------------------------------------------------------


class SupportPage(Page):
    title, key, icon = "Support", "support", "help-browser-symbolic"

    def fetch(self):
        return None

    def build(self, data) -> None:
        g = group("Support report",
                  "Collects system details and recent action logs into one text file in your home folder.")
        g.add(button_row("Create support report", "Includes image, device, kernel arguments and the last logs",
                         "Create", lambda: self.win.run_job(
                             "Creating support report", [[tool("bopp-system"), "--json", "support-report"]],
                             lambda ok, final: None), suggested=True))
        self.box.append(g)


# --- About ------------------------------------------------------------------------------------


class AboutPage(Page):
    title, key, icon = "About", "about", "help-about-symbolic"

    def fetch(self):
        return run_json([tool("bopp-system"), "--json", "about"])

    def build(self, data) -> None:
        g = group("BoppOS")
        if "error" in data:
            g.add(message_row(f"Could not read the system information: {data['error']}"))
        else:
            for label, key in (("Image", "image"), ("Tag", "tag"), ("Flavor", "flavor"), ("Kernel", "kernel"),
                               ("Device", "device"), ("BoppOS tools", "boppos_version")):
                g.add(info_row(label, str(data.get(key) or "unknown")))
        self.box.append(g)
        g = group("Options")
        g.add(switch_row("Advanced options", "Show rollback, pinning, update channel and removal actions",
                         self.win.advanced, self.win.set_advanced))
        if self.win.gaming:
            g.add(button_row("Exit to Steam", "Closes boppos-deck", "Exit", self.win.confirm_quit))
        self.box.append(g)


ALL_PAGES = [SystemPage, KargsPage, AppsPage, DeckyPage, SupportPage, AboutPage]
