"""The first-run wizard: a linear flow of steps with Back and Next, shown by `boppos-deck --welcome`.

Dry run for now: the choices are collected and the Review step lists what would happen, but nothing is applied.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import welcome  # noqa: E402
from .widgets import expander_row, group, info_row, switch_row  # noqa: E402

TITLES = {
    "welcome": ("Welcome to BoppOS", "A few quick choices to set this system up. You can skip any step."),
    "done": ("All set", "You can run this wizard again from About."),
}


def toggle(choices_set: set[str], key: str) -> Callable[[bool], None]:
    def on(active: bool) -> None:
        (choices_set.add if active else choices_set.discard)(key)
    return on


def setter(choices: welcome.Choices, attr: str) -> Callable[[bool], None]:
    return lambda active: setattr(choices, attr, active)


def page(*groups: Adw.PreferencesGroup) -> Adw.PreferencesPage:
    p = Adw.PreferencesPage(vexpand=True)
    for g in groups:
        p.add(g)
    return p


class WelcomeFlow(Gtk.Box):
    def __init__(self, gaming: bool, on_finish: Callable[[bool], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.gaming = gaming
        self.steps = welcome.steps_for(gaming)
        self.choices = welcome.initial_choices()
        self.on_finish = on_finish
        self.index = 0

        if welcome.DRY_RUN:
            self.append(Adw.Banner(title="Dry run: nothing you choose here is applied", revealed=True))
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.review = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        builders = {
            "apps": self.apps_page, "hardware": self.hardware_page, "security": self.security_page,
            "updates": self.updates_page,
        }
        for step in self.steps:
            if step.key in TITLES:
                title, text = TITLES[step.key]
                widget = Adw.StatusPage(title=title, description=text)
            elif step.key == "review":
                widget = self.review
            else:
                widget = builders[step.key]()
            self.stack.add_named(widget, step.key)
        self.append(self.stack)

        self.progress = Gtk.Label(css_classes=["dim-label"])
        self.back = Gtk.Button(label="Back")
        self.next = Gtk.Button(label="Next", css_classes=["suggested-action"])
        self.back.connect("clicked", lambda _b: self.go(-1))
        self.next.connect("clicked", lambda _b: self.go(1))
        bar = Gtk.CenterBox(margin_top=12, margin_bottom=12, margin_start=24, margin_end=24)
        bar.set_start_widget(self.back)
        bar.set_center_widget(self.progress)
        bar.set_end_widget(self.next)
        self.append(bar)
        self.show_step()

    # --- steps -------------------------------------------------------------------------------------------
    def apps_page(self) -> Gtk.Widget:
        c = self.choices
        extras = group("Gaming", "Both need Steam; in Gaming Mode they are set up after your first Steam login."
                       if self.gaming else "Both need Steam to be installed and signed in.")
        extras.add(switch_row("BoppOS shortcut in Steam", "Opens boppos-deck from your Steam library.",
                              c.steam_shortcuts, setter(c, "steam_shortcuts")))
        extras.add(switch_row("Decky Loader", "Plugins for the Steam Gaming Mode overlay.",
                              c.decky, setter(c, "decky")))
        apps = group("Flatpak apps", "Installed for you only, from Flathub. Needs a network connection.")
        options = welcome.app_options()
        if not options:
            apps.add(info_row("No app list found", "", "The curated Flatpak list could not be read."))
        for category, items in options.items():
            row = expander_row(category, f"{len(items)} apps")
            for app in items:
                row.add_row(switch_row(app.name or app.id, app.id, app.ref in c.flatpaks,
                                       toggle(c.flatpaks, app.ref)))
            apps.add(row)
        return page(extras, apps)

    def hardware_page(self) -> Gtk.Widget:
        c = self.choices
        here = group("This device")
        here.add(info_row(welcome.device(), "", "Detected from the firmware (DMI)."))
        here.add(switch_row("Check for firmware updates", "Runs fwupd after setup. Nothing is installed "
                            "without asking.", c.firmware_check, setter(c, "firmware_check")))
        options = welcome.karg_options()
        warn = group("Kernel arguments: best left alone", welcome.KARGS_WARNING)
        warn.add_css_class("warning")
        suggested = group("Presets that name this device", "Listed because they match this device, not because "
                          "you need them. They take effect after a reboot.")
        others = group("Other presets")
        for o in options:
            subtitle = o.description + (f"  {o.risk}" if o.risk else "")
            row = switch_row(o.name, subtitle, o.name in c.kargs, toggle(c.kargs, o.name))
            (suggested if o.suggested else others).add(row)
        groups = [here]
        if options:
            groups.append(warn)
        if any(o.suggested for o in options):
            groups.append(suggested)
        if any(not o.suggested for o in options):
            groups.append(others)
        return page(*groups)

    def security_page(self) -> Gtk.Widget:
        c = self.choices
        g = group("Sign-in and disk", "Both run interactive setup, so they are only offered on the desktop.")
        cam, tpm = welcome.has_camera(), welcome.has_tpm()
        face = switch_row("Face unlock (Howdy)", "Sign in and authorise with your face."
                          if cam else "No camera found.", c.howdy, setter(c, "howdy"))
        face.set_sensitive(cam)
        unlock = switch_row("Unlock the disk with the TPM", "Skip typing the disk passphrase at every boot."
                            if tpm else "No TPM found.", c.tpm_unlock, setter(c, "tpm_unlock"))
        unlock.set_sensitive(tpm)
        g.add(face)
        g.add(unlock)
        return page(g)

    def updates_page(self) -> Gtk.Widget:
        c = self.choices
        g = group("Updates")
        g.add(switch_row("Automatic updates", "Download new system images in the background; you choose when "
                         "to reboot.", c.auto_updates, setter(c, "auto_updates")))
        dev = group("Development")
        dev.add(switch_row("Dev container", "A distrobox container for compilers and packages the read-only "
                           "system does not carry.", c.dev_container, setter(c, "dev_container")))
        return page(g, dev)

    def refresh_review(self) -> None:
        child = self.review.get_first_child()
        while child is not None:
            self.review.remove(child)
            child = self.review.get_first_child()
        plan = welcome.build_plan(self.choices, self.gaming)
        g = group("What would happen", "Nothing is applied in this version." if welcome.DRY_RUN else None)
        if not plan:
            g.add(info_row("Nothing chosen", "", "Go back to pick something, or finish to skip setup."))
        for action in plan:
            row = info_row(action.text, "", action.area)
            g.add(row)
        self.review.append(page(g))

    # --- flow --------------------------------------------------------------------------------------------
    def go(self, delta: int) -> None:
        if delta > 0 and self.index == len(self.steps) - 1:
            for line in welcome.run_plan(welcome.build_plan(self.choices, self.gaming)):
                print(line, flush=True)
            self.on_finish(True)
            return
        self.index = max(0, min(len(self.steps) - 1, self.index + delta))
        self.show_step()

    def show_step(self) -> None:
        step = self.steps[self.index]
        if step.key == "review":
            self.refresh_review()
        self.stack.set_visible_child_name(step.key)
        self.progress.set_label(f"{self.index + 1} of {len(self.steps)}: {step.title}")
        self.back.set_sensitive(self.index > 0)
        self.next.set_label("Finish" if self.index == len(self.steps) - 1 else "Next")
