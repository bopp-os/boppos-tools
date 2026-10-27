"""The first-run wizard: a linear flow of steps with Back and Next, shown by `boppos-deck --welcome`."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import welcome  # noqa: E402

# Placeholder text until each step gets its real content.
BLURB = {
    "welcome": ("Welcome to BoppOS", "A few quick choices to set this system up. You can skip any of them."),
    "apps": ("Apps", "Pick the Flatpaks, Decky Loader and Steam shortcuts to set up."),
    "hardware": ("Hardware", "Kernel argument presets for this device, and a firmware check."),
    "security": ("Security", "Face unlock and TPM2 disk unlock."),
    "updates": ("Updates", "Update channel, automatic updates and the dev container."),
    "review": ("Review", "Everything below is applied when you continue."),
    "done": ("All set", "You can run this wizard again from About."),
}


class WelcomeFlow(Gtk.Box):
    def __init__(self, gaming: bool, on_finish: Callable[[bool], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.steps = welcome.steps_for(gaming)
        self.choices = welcome.Choices()
        self.on_finish = on_finish
        self.index = 0

        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        for step in self.steps:
            title, text = BLURB[step.key]
            self.stack.add_named(Adw.StatusPage(title=title, description=text), step.key)
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

    def go(self, delta: int) -> None:
        if delta > 0 and self.index == len(self.steps) - 1:
            self.on_finish(True)
            return
        self.index = max(0, min(len(self.steps) - 1, self.index + delta))
        self.show_step()

    def show_step(self) -> None:
        step = self.steps[self.index]
        self.stack.set_visible_child_name(step.key)
        self.progress.set_label(f"{self.index + 1} of {len(self.steps)}: {step.title}")
        self.back.set_sensitive(self.index > 0)
        self.next.set_label("Finish" if self.index == len(self.steps) - 1 else "Next")
