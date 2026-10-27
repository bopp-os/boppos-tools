"""The pages of boppos-deck. Each page builds rows from CLI output and runs actions as jobs."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import system  # noqa: E402
from ..privileged import UsageError, validate_image_ref  # noqa: E402
from . import backend  # noqa: E402
from .backend import fingerprint, run_json, tool  # noqa: E402
from .widgets import (  # noqa: E402
    button_row,
    expander_row,
    group,
    info_row,
    message_row,
    switch_row,
    tag_label,
)

BAZAAR = "io.github.kolunmi.Bazaar"


class Page:
    """A scrolling column built from data that is fetched on a worker thread.

    Switching to a page that was built before keeps showing it while fresh data loads, and rebuilds only
    when the data really changed and the user is not in the middle of using it. Only a page that was never
    built shows "Loading…". A failure shows an error with a retry instead of leaving a blank page.
    """

    title = ""
    icon = "emblem-system-symbolic"
    key = ""

    def __init__(self, win):
        self.win = win
        self._gen = 0
        self.stale = True
        self.built = False
        self.loading = False
        self.loaded_at = 0.0
        self._shown: str | None = None
        self._view = None  # view_state() the content on screen was built with
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

    def view_state(self):
        """Settings that change how the same data is laid out (part of the rebuild decision)."""
        return (self.win.advanced, json.dumps(self.win.settings, sort_keys=True, default=str))

    def has_focus(self) -> bool:
        f = self.win.get_focus()
        return bool(f and f.is_ancestor(self.widget))

    def refresh(self, refocus: bool = False, quiet: bool = False) -> None:
        """Load fresh data. quiet=True keeps the current content on screen until the data is in."""
        self.stale = False
        self.loading = True
        self.loaded_at = time.monotonic()
        self._gen += 1
        gen = self._gen
        # Content built under other settings (Advanced switched on or off) is replaced at once: keeping it on screen
        # would show switches that are enabled although the setting now says they must not be.
        quiet = quiet and self.built and self._view == self.view_state()
        if not quiet:
            self.clear()
            g = group()
            g.add(message_row("Loading…"))
            self.box.append(g)

        def done(data):
            if gen != self._gen:
                return
            self.loading = False
            shown = fingerprint(data, self.view_state())
            if quiet and shown is not None and shown == self._shown:
                return  # nothing changed: leave the page exactly as it is
            if quiet and self.has_focus():
                self.stale = True  # do not pull rows from under someone who is using them
                return
            try:
                self.clear()
                self.build(data)
                self.built, self._shown, self._view = True, shown, self.view_state()
            except Exception as exc:  # a broken page must say so and offer a retry, not stay blank
                self.show_error(exc)
                return
            if refocus:
                self.widget.child_focus(Gtk.DirectionType.TAB_FORWARD)

        self.win.load(self.fetch, done)

    def show_error(self, exc: BaseException) -> None:
        self.clear()
        self.built, self._shown, self._view = False, None, None
        g = group("Something went wrong")
        g.add(message_row(f"This page could not be loaded: {exc}"))
        g.add(button_row("Try again", "", "Retry", self.refresh, suggested=True))
        self.box.append(g)

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
        return {"status": run_json([tool("bopp-system"), "--json", "status"])}

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
            self.build_advanced(st)

    def build_advanced(self, st: dict) -> None:
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

        self.build_image(st)

    def build_image(self, st: dict) -> None:
        booted = st.get("booted") or {}
        g = group("Image", "Advanced. Updates keep following this image. Moving to a different image or version is "
                  "not an update, so it is deliberately not a menu: to go back one version use Roll back above.")
        note = system.channel_note(st.get("tag"))
        # the reference goes in the subtitle, which spans the row, instead of a right-hand value that wraps
        g.add(info_row("Following", "", "\n".join(x for x in (booted.get("ref", "unknown"), note) if x)))
        g.add(button_row("Use a different image…", "You type the whole reference yourself, registry, name and tag",
                         "Start…", lambda: self.switch_image(booted.get("ref", "")), destructive=True))
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

        def done(ok: bool, final: dict) -> None:
            self.refresh()
            self.win.load(lambda: backend.trigger_update_check(), lambda _res: None)

        self.win.run_job(f"Updating: {label}", [cmd], done)

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

    def switch_image(self, current: str) -> None:
        """Three deliberate steps: read the warning, type the complete reference, confirm the exact change."""
        def typed(text: str) -> None:
            try:
                ref = validate_image_ref(text)
            except UsageError as exc:
                self.win.toast(str(exc))
                return
            if ref.split("@", 1)[0] == current.split("@", 1)[0]:
                self.win.toast("The system already follows that image")
                return
            signed = ref.startswith("ghcr.io/bopp-os/")
            note = ("Its signature is checked against the BoppOS key." if signed else
                    "This image is not from BoppOS. It is checked only as far as the system's signature policy "
                    "demands, and it can replace everything.")
            self.win.confirm(
                "Switch to this image?",
                f"From:  {current or 'unknown'}\nTo:      {ref}\n\n{note}\n\nIt is downloaded now and used after the "
                "next restart. If it does not start, pick the previous entry in the boot menu.",
                "Switch to it",
                lambda: self.win.run_job("Switching image", [[tool("bopp-system"), "--json", "switch", ref]],
                                         lambda ok, final: self.refresh()),
                destructive=True)

        self.win.confirm(
            "Switch to a different image?",
            "This is not an update. It replaces the operating system with another image or version. Do it only "
            "if you know exactly which image you want and trust it: a wrong or broken image can leave the system "
            "unable to start, and changes you made outside /etc, /var and your home folder are not carried over.\n\n"
            "Next you type the whole reference, for example  ghcr.io/bopp-os/cachyos-plasma:v4",
            "I understand, continue",
            lambda: self.win.ask_text("Image to switch to", "Full reference, registry/name:tag", typed),
            destructive=True)


# --- Kernel arguments -------------------------------------------------------------------------


class KargsPage(Page):
    title, key, icon = "Kernel arguments", "kargs", "utilities-terminal-symbolic"

    RISK_LABEL = {"low": "Low risk", "medium": "Medium risk", "high": "High risk"}
    RISK_CLASS = {"low": "success", "medium": "warning", "high": "error"}
    blocked = False  # set while an update is staged; the switches are then off even in Advanced

    def fetch(self):
        from concurrent.futures import ThreadPoolExecutor

        def cmdline():
            try:
                return Path("/proc/cmdline").read_text().strip()
            except OSError:
                return ""

        if not self.win.advanced:  # normal mode only looks: the presets are listed, locked, and nothing else is asked
            return {"dropins": run_json([tool("bopp-kargs"), "--json", "dropins"]), "cmdline": cmdline(),
                    "advanced": False}
        with ThreadPoolExecutor(max_workers=3) as pool:  # three separate tools: no reason to wait in a queue
            dropins = pool.submit(run_json, [tool("bopp-kargs"), "--json", "dropins"])
            status = pool.submit(run_json, [tool("bopp-kargs"), "--json", "status"])
            listing = pool.submit(run_json, [tool("bopp-kargs"), "--json", "list"])
            return {"dropins": dropins.result(), "status": status.result(), "list": listing.result(),
                    "cmdline": cmdline(), "advanced": True}

    def build(self, data) -> None:
        if not self.win.advanced:
            g = group("Kernel arguments")
            g.add(message_row("Kernel arguments can stop the system from starting, so everything here is locked. "
                              "Turn on Advanced options (About) to change the presets or add arguments."))
            self.box.append(g)
            dropins = data.get("dropins") or {}
            if "error" in dropins:
                g = group("Presets")
                g.add(message_row(f"Could not read the presets: {dropins['error']}"))
                self.box.append(g)
            else:
                self.build_presets(dropins.get("dropins", []), locked=True)
            g = group("Running kernel")
            g.add(info_row("Command line", "", data.get("cmdline") or "unavailable"))
            self.box.append(g)
            return
        dropins = data["dropins"]
        if "error" in dropins:
            g = group("Kernel arguments")
            g.add(message_row(f"Could not read the presets: {dropins['error']}"))
            self.box.append(g)
            return
        status = data["status"]
        blocked = status.get("blocked_reason") if "error" not in status else status["error"]
        self.blocked = bool(blocked)
        if blocked:
            text = {"staged": "An update is staged. Reboot before changing kernel arguments."}.get(
                blocked, f"Changes are unavailable: {blocked}")
            banner = Adw.Banner.new(text)
            banner.set_use_markup(False)
            banner.set_revealed(True)
            self.box.append(banner)
        items = dropins.get("dropins", [])
        self.build_presets(items, locked=False)

        self.preset_text = {t: d["description"] for d in items for t in d["kargs"] if d["description"]}
        if self.win.advanced:
            self.build_advanced(data, items)

        g = group("Running kernel")
        g.add(info_row("Command line", "", data["cmdline"] or "unavailable"))
        self.box.append(g)

    SECTION_TEXT = {
        "on": ("Switched on", "Presets that are on now. Switch one off to stop using it after the next reboot."),
        "fits": ("Presets for this hardware", "Listed because they fit this hardware's graphics or model, not "
                 "because you need them."),
        "optional": ("Other presets", "Not recommended. Only for a problem you actually have."),
        "risky": ("Risky presets", "Not specific to this hardware, and medium or high risk. Each asks you to "
                  "confirm."),
        "unsupported": ("Unsupported", "Locked: they are for other hardware, so they cannot work here."),
        "invalid": ("Invalid presets", "These could not be read and are never applied."),
    }

    def build_presets(self, items: list[dict], locked: bool) -> None:
        """Presets by how well they fit this hardware (kargs.fit), not by category. The category is on each row.

        locked: every switch is greyed out and cannot be changed (normal mode; only Advanced changes presets)."""
        from .. import kargs

        if not items:
            g = group("Presets")
            g.add(message_row("No presets are installed on this system."))
            self.box.append(g)
            return
        warn = group("Kernel arguments: best left alone", kargs.KARGS_WARNING)
        warn.add_css_class("warning")
        self.box.append(warn)
        for key, rows in kargs.sections(items).items():
            if not rows:
                continue
            title, text = self.SECTION_TEXT[key]
            g = group(title, text + " Changes take effect after a reboot." if key in ("on", "fits") else text)
            for d in rows:
                if key == "invalid":
                    g.add(info_row(d["name"], "invalid", d["error"]))
                elif key == "unsupported":
                    g.add(info_row(d["description"] or d["name"], "", d.get("fit_reason") or " ".join(d["kargs"]),
                                   tag=("Unsupported", "dim-label")))
                else:
                    g.add(self.preset_row(d, locked))
            self.box.append(g)

    def preset_row(self, d: dict, locked: bool = False) -> Adw.SwitchRow:
        level = d.get("risk_level", "low")
        lines = [d["description"] or d["name"], f"{d.get('category') or 'Other'}  ·  Adds: " + " ".join(d["kargs"])]
        if d.get("risk"):
            lines.append(f"Risk: {d['risk']}")
        if d.get("already_active") and not d["enabled"]:
            # Running now but not set by this preset (the image's own arguments, or added by hand). The switch shows
            # that it is on; switching it off removes the arguments one by one.
            row = switch_row(d["description"] or d["name"],
                             "Active now, set by the image or by hand. Switch off to remove it.\n"
                             + "\n".join(lines[1:]), True, lambda on, dd=d: None if on else self.deactivate(dd))
            row.add_suffix(tag_label("Active", "success"))
            row.set_sensitive(not (locked or self.blocked))
            return row
        row = switch_row(d["description"] or d["name"], "\n".join(lines[1:]), d["enabled"],
                         lambda on, dd=d: self.toggle(dd, on))
        row.add_suffix(tag_label(self.RISK_LABEL.get(level, level), self.RISK_CLASS.get(level, "dim-label")))
        row.set_sensitive(not (locked or self.blocked))
        return row

    def build_advanced(self, data: dict, items: list[dict]) -> None:
        g = group("Custom arguments", "Advanced. A wrong argument can stop the system from booting. The previous "
                  "deployment stays available to roll back to.")
        g.add(button_row("Add an argument", "For example pci=noaer. You type it with the on-screen keyboard.",
                         "Add…", self.add_custom))
        self.box.append(g)

        listing = data.get("list") if isinstance(data.get("list"), dict) else {}
        details = listing.get("details") or []
        if details:
            g = group("Current arguments", "What the next boot uses, with a short explanation where one is known. "
                      "Arguments set by a preset are changed with its switch.")
            for d in details:
                tok = d["token"]
                if tok.split("=", 1)[0] in ("ostree", "root"):
                    continue
                g.add(self.argument_row(d))
            self.box.append(g)

    def argument_row(self, d: dict):
        tok = d["token"]
        lines = [d["summary"] or self.preset_text.get(tok) if d["managed"] else d["summary"]]
        if not lines[0]:
            lines = ["No description yet: this argument is not in BoppOS's built-in list."]
        if d["risk"]:
            lines.append(f"Risk: {d['risk']}")
        sub = "\n".join(lines)
        if d["critical"]:
            return info_row(tok, "", sub, tag=("Required to boot", "warning"))
        if d["managed"]:
            return info_row(tok, "", sub, tag=("Preset", "dim-label"))
        tag = ("Image default", "dim-label") if d["image_default"] else None
        if not self.can_remove(tok):
            return info_row(tok, "", sub, tag=tag)
        return button_row(tok, sub, "Remove", lambda t=tok, dd=d: self.remove_custom(t, dd), destructive=True, tag=tag)

    @staticmethod
    def can_remove(tok: str) -> bool:
        from ..privileged import UsageError, checked_karg

        try:
            checked_karg(tok)
        except UsageError:
            return False
        return True

    # actions
    def toggle(self, d: dict, on: bool) -> None:
        if not self.win.advanced:  # presets only change in Advanced options
            return
        level = d.get("risk_level", "low")
        if on and level in ("medium", "high"):
            self.win.confirm(f"Turn on “{d['description'] or d['name']}”?",
                             d.get("risk") or "This changes boot behaviour.", "Turn on",
                             lambda: self.run_verb("enable", d["name"]),
                             destructive=level == "high", on_cancel=self.refresh)
        else:
            self.run_verb("enable" if on else "disable", d["name"])

    def deactivate(self, d: dict) -> None:
        """Take out the arguments of a preset that is running without having been switched on here."""
        if not self.win.advanced:
            return
        toks = list(d["kargs"])

        def step(rest: list[str]) -> None:
            if not rest:
                self.win.toast("Saved. Reboot to apply.")
                self.refresh()
                return

            def done(res):
                if isinstance(res, dict) and "error" in res:
                    self.win.toast(f"Could not remove {rest[0]}: {res['error']}")
                    self.refresh()
                else:
                    step(rest[1:])

            self.win.load(lambda: run_json([tool("bopp-kargs"), "--json", "remove", rest[0]], timeout=180), done)

        risk = f"\n\nRisk: {d['risk']}" if d.get("risk") else ""
        self.win.confirm(f"Turn off “{d['description'] or d['name']}”?",
                         f"Removes {' '.join(toks)} from the kernel arguments. The image sets this by default."
                         f"{risk}\n\nThe previous deployment stays available to roll back to.",
                         "Turn off", lambda: step(toks), on_cancel=self.refresh)

    def run_verb(self, verb: str, arg: str) -> None:
        def done(res):
            if isinstance(res, dict) and "error" in res:
                self.win.toast(f"Could not {verb}: {res['error']}")
            else:
                self.win.toast("Saved. Reboot to apply.")
            self.refresh()

        self.win.load(lambda: run_json([tool("bopp-kargs"), "--json", verb, arg], timeout=180), done)

    def add_custom(self) -> None:
        from ..privileged import UsageError, checked_karg

        def typed(text: str) -> None:
            arg = text.strip()
            try:
                checked_karg(arg, "add")
            except UsageError as exc:
                self.win.toast(f"{exc}. A terminal can still do it: sudo bopp-kargs add --force …")
                return

            def described(info) -> None:
                if not isinstance(info, dict) or "error" in info:
                    self.win.toast("Could not look the argument up: " + str((info or {}).get("error", "unknown")))
                    return
                known = bool(info.get("known"))
                body = (info.get("summary") or "") + (f"\n\nRisk: {info['risk']}" if info.get("risk") else "")
                if not known:
                    body = "BoppOS has no description for this argument, so it cannot say what it does."

                def final() -> None:
                    self.win.confirm(f"Add “{arg}”?", f"{body}\n\nIt is added to the next boot. If the system fails "
                                     "to start, pick the previous entry in the boot menu.", "Add",
                                     lambda: self.run_verb("add", arg), destructive=True)

                if known:
                    final()
                else:  # an extra, separate question for anything BoppOS cannot vouch for
                    self.win.confirm(f"BoppOS does not recognise “{arg}”",
                                     "Nobody has described this argument here, so there is no way to tell what it "
                                     "does or whether the system still starts with it. Only go on if you know "
                                     "exactly what it does.", "I know what it does", final, destructive=True)

            self.win.load(lambda: run_json([tool("bopp-kargs"), "--json", "describe", arg]), described)

        self.win.ask_text("Add a kernel argument", "Argument (key or key=value):", typed)

    def remove_custom(self, tok: str, d: dict | None = None) -> None:
        note = (d or {}).get("summary") or "BoppOS has no description for this argument."
        extra = f"\n\nRisk: {d['risk']}" if d and d.get("risk") else ""
        image = "\n\nThe image sets this by default; it stays removed until you add it back." \
            if d and d.get("image_default") else ""
        self.win.confirm(f"Remove “{tok}”?", f"{note}{extra}{image}\n\nThe previous deployment stays available to "
                         "roll back to if the system will not boot.", "Remove",
                         lambda: self.run_verb("remove", tok), destructive=True)


# --- Apps -------------------------------------------------------------------------------------


class AppsPage(Page):
    title, key, icon = "Apps", "apps", "view-app-grid-symbolic"

    def __init__(self, win):
        super().__init__(win)
        self.expanded: set[str] = set()
        self.want: dict[str, bool] = {}  # install switch per app id
        self.steam_base: dict[str, bool] = {}  # which apps have a Steam shortcut (removed with the app)
        self.steam_running = False

    def fetch(self):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            apps = pool.submit(run_json, [tool("install-optional-flatpaks"), "--json", "--status"], 60)
            steam = pool.submit(run_json, [tool("bopp-steam-shortcut"), "--json", "apps"], 60)
            return {"apps": apps.result(), "steam": steam.result()}

    def build(self, data) -> None:
        listing = data["apps"]
        if isinstance(listing, dict) and "error" in listing:
            g = group("Apps")
            g.add(message_row(f"Could not read the app list: {listing['error']}"))
            self.box.append(g)
            return
        steam = data.get("steam")
        steam_ok = isinstance(steam, dict) and "error" not in steam and steam.get("steam_found")
        self.steam_running = bool(steam_ok and steam.get("steam_running"))
        self.steam_base = {r["key"]: r["present"] for r in steam["apps"]} if steam_ok else {}
        self.apps = [a for a in listing if a.get("kind") == "app"]
        previous = self.want
        self.want = {a["id"]: previous.get(a["id"], a["installed"]) for a in self.apps}
        only_installed = bool(self.win.settings.get("apps_installed_only"))

        g = group()
        g.add(switch_row("Only show installed apps", "Hides everything you have not installed", only_installed,
                         self.set_only_installed))
        self.box.append(g)

        categories: dict[str, list[dict]] = {}
        for a in self.apps:
            categories.setdefault(a.get("category") or "Other", []).append(a)
        g = group("BoppOS apps", "Open a category, switch apps on to install them or off to remove them, then press "
                  "Apply. Steam shortcuts are on the Steam library page.")
        for cat, apps in categories.items():
            visible = [a for a in apps if a["installed"] or not only_installed]
            if not visible:
                continue
            done = sum(1 for a in apps if a["installed"])
            exp = expander_row(cat, f"{done} of {len(apps)} installed")
            exp.set_expanded(cat in self.expanded)
            exp.connect("notify::expanded", lambda e, _p, c=cat: self.remember(c, e.get_expanded()))
            for a in visible:
                notes = ["installed" if a["installed"] else "", "installed by default" if a.get("default") else ""]
                sub = a["id"] + "".join(f"  ·  {n}" for n in notes if n)
                exp.add_row(switch_row(a["name"], sub, self.want[a["id"]],
                                       lambda on, i=a["id"]: self.set_want(i, on)))
            g.add(exp)
        self.box.append(g)
        g = group()
        self.apply_row = button_row("Apply changes", "No changes yet", "Apply", self.apply, suggested=True)
        g.add(self.apply_row)
        if self.win.advanced:
            g.add(switch_row("Install for all users", "Off: new apps are installed for you only. On: they are "
                             "installed system-wide (needs admin rights). Apps already installed are removed "
                             "from wherever they are.", bool(self.win.settings.get("apps_system")), self.set_system))
        bazaar_installed = any(a["id"] == BAZAAR and a["installed"] for a in self.apps)
        if self.win.gaming:
            g.add(message_row("To browse all of Flathub, open Bazaar from Desktop Mode. Bazaar is made for a "
                              "mouse and keyboard, not a controller."))
        elif bazaar_installed:
            g.add(button_row("Browse more apps", "Opens Bazaar, the store for everything on Flathub",
                             "Open Bazaar", self.open_bazaar))
        else:
            g.add(message_row("Switch Bazaar on above and press Apply to browse all of Flathub."))
        self.box.append(g)
        self.update_apply_row()

    def open_bazaar(self) -> None:
        try:
            subprocess.Popen(["flatpak", "run", BAZAAR], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            self.win.toast("Opening Bazaar…")
        except OSError as exc:
            self.win.toast(f"Could not start Bazaar: {exc}")

    def remember(self, cat: str, open_: bool) -> None:
        (self.expanded.add if open_ else self.expanded.discard)(cat)

    def set_only_installed(self, on: bool) -> None:
        self.win.settings["apps_installed_only"] = on
        try:
            backend.save_settings(self.win.settings)
        except OSError:
            pass
        self.refresh(refocus=True)

    def diff(self) -> tuple[list[str], list[str]]:
        add = [a["id"] for a in self.apps if self.want[a["id"]] and not a["installed"]]
        rem = [a["id"] for a in self.apps if not self.want[a["id"]] and a["installed"]]
        return add, rem

    def steam_removals(self) -> list[str]:
        """Shortcuts to drop with the apps being uninstalled: they would point nowhere."""
        _, uninstall = self.diff()
        return [k for k in uninstall if self.steam_base.get(k)]

    def update_apply_row(self) -> None:
        add, rem = self.diff()
        changed = bool(add or rem)
        self.apply_row.set_sensitive(changed)
        self.apply_row.set_subtitle(f"{len(add)} to install, {len(rem)} to remove" if changed else "No changes yet")

    def set_system(self, on: bool) -> None:
        self.win.settings["apps_system"] = on
        try:
            backend.save_settings(self.win.settings)
        except OSError:
            pass

    def set_want(self, app_id: str, on: bool) -> None:
        self.want[app_id] = on
        self.update_apply_row()

    def commands(self) -> list[list[str]]:
        """Install/remove first, dropping the shortcuts of removed apps last: it may close Steam, and in Gaming
        Mode that can restart the session, so nothing may be left to do after it."""
        add, rem = self.diff()
        s_rem = self.steam_removals()
        base = [tool("install-optional-flatpaks"), "--json", "--yes"]
        cmds = []
        if add:
            system = ["--system"] if self.win.advanced and self.win.settings.get("apps_system") else []
            cmds.append([*base, *system, "--only", ",".join(add)])
        if rem:
            cmds.append([*base, "--remove", "--only", ",".join(rem)])
        if s_rem:
            cmds.append([tool("bopp-steam-shortcut"), "--json", "sync", "--remove", ",".join(s_rem),
                         "--restart-steam"])
        return cmds

    def apply(self) -> None:
        _, rem = self.diff()
        s_rem = self.steam_removals()
        cmds = self.commands()

        def go():
            self.want = {}
            self.win.run_job("Updating apps", cmds, lambda ok, final: self.refresh())

        def steam_then_go():
            if s_rem and self.steam_running:
                self.win.confirm("Close and reopen Steam?", "Steam has to be closed to change its library, so any "
                                 "game that is running will be closed. Steam comes back by itself.", "Continue", go,
                                 destructive=True)
            else:
                go()

        if rem:
            self.win.confirm("Remove apps?", f"{len(rem)} app(s) will be uninstalled. Their data is kept.",
                             "Apply", steam_then_go, destructive=True)
        else:
            steam_then_go()


# --- Steam library -------------------------------------------------------------------------------


class SteamPage(Page):
    title, key, icon = "Steam library", "steam", "applications-games-symbolic"

    def __init__(self, win):
        super().__init__(win)
        self.base: dict[str, bool] = {}
        self.want: dict[str, bool] = {}
        self.expanded: set[str] = set()

    def fetch(self):
        return run_json([tool("bopp-steam-shortcut"), "--json", "apps"], timeout=60)

    def build(self, data) -> None:
        if "error" in data:
            g = group("Steam library")
            g.add(message_row(f"Could not read the Steam library: {data['error']}"))
            self.box.append(g)
            return
        self.info = data
        if not data["steam_found"]:
            g = group("Steam library")
            g.add(message_row("Steam has not created a library for this user yet. Start Steam once, then come back."))
            self.box.append(g)
            return
        previous = self.want
        self.base = {a["key"]: a["present"] for a in data["apps"]}
        self.want = {k: previous.get(k, v) for k, v in self.base.items()}

        def row(a: dict):
            sub = a["key"] + (f"  ·  queued: {a['queued']} when Steam closes" if a["queued"] else "")
            return switch_row(a["name"], sub, self.want[a["key"]], lambda on, k=a["key"]: self.set_want(k, on))

        # What is already in Steam stays short and visible; everything else hides in collapsed categories.
        inside = [a for a in data["apps"] if self.base[a["key"]]]
        g = group("In your Steam library", "Switch one off to remove its shortcut." if inside else None)
        if inside:
            for a in inside:
                g.add(row(a))
        else:
            g.add(message_row("Nothing yet. Open a category below and switch apps on."))
        self.box.append(g)

        categories: dict[str, list[dict]] = {}
        for a in data["apps"]:
            if not self.base[a["key"]]:
                categories.setdefault(a.get("category") or "Other apps", []).append(a)
        if categories:
            g = group("Add to Steam", "Switch an app on to add it as a non-Steam game, so you can start it from "
                      "Gaming Mode. This page only adds or removes shortcuts; it never installs or removes an app. "
                      "Apps you install later show up here.")
            for cat, apps in sorted(categories.items(), key=lambda kv: (kv[0] == "Other apps", kv[0])):
                exp = expander_row(cat, f"{len(apps)} available")
                exp.set_expanded(cat in self.expanded)
                exp.connect("notify::expanded", lambda e, _p, c=cat: self.remember(c, e.get_expanded()))
                for a in apps:
                    exp.add_row(row(a))
                g.add(exp)
            self.box.append(g)

        g = group()
        self.apply_row = button_row("Apply changes", "No changes yet", "Apply", self.apply, suggested=True)
        g.add(self.apply_row)
        g.add(message_row("Steam has to be closed to change its library. Applying closes it and brings it back"
                          + ("." if data["gaming"] else " on the desktop.")
                          if data["steam_running"] else "Steam is not running, so changes apply right away."))
        self.box.append(g)
        self.update_apply_row()

    def remember(self, cat: str, open_: bool) -> None:
        (self.expanded.add if open_ else self.expanded.discard)(cat)

    def diff(self) -> tuple[list[str], list[str]]:
        add = [k for k, on in self.want.items() if on and not self.base.get(k)]
        rem = [k for k, on in self.want.items() if not on and self.base.get(k)]
        return add, rem

    def update_apply_row(self) -> None:
        add, rem = self.diff()
        self.apply_row.set_sensitive(bool(add or rem))
        self.apply_row.set_subtitle(f"{len(add)} to add, {len(rem)} to remove" if add or rem else "No changes yet")

    def set_want(self, key: str, on: bool) -> None:
        self.want[key] = on
        self.update_apply_row()

    def apply(self) -> None:
        add, rem = self.diff()
        cmd = [tool("bopp-steam-shortcut"), "--json", "sync", "--add", ",".join(add), "--remove", ",".join(rem),
               "--restart-steam"]

        def go():
            self.want = {}
            self.win.run_job("Updating the Steam library", [cmd], lambda ok, final: self.refresh())

        if self.info.get("steam_running"):
            self.win.confirm("Close and reopen Steam?", "Steam has to be closed to change its library, so any game "
                             "that is running will be closed. Steam comes back by itself.", "Continue", go,
                             destructive=True)
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


ALL_PAGES = [SystemPage, KargsPage, AppsPage, SteamPage, DeckyPage, SupportPage, AboutPage]
