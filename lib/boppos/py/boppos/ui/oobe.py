"""The first-run setup (`boppos-deck --first-run` with no account yet): language, network, time zone, account and
where to start.

A dry run for now (oobe.DRY_RUN): the choices are checked and listed, nothing is created or changed.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import firstrun, network, oobe, system  # noqa: E402
from .widgets import button_row, combo_row, group, info_row, plain  # noqa: E402

TITLES = {
    "welcome": ("Welcome to BoppOS", "Let's set this computer up: language, time zone and your account."),
    "done": ("All set", "BoppOS will sign you in and start up."),
}


def _run(argv: list[str], stdin: str) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return p.returncode, p.stderr or p.stdout


def page(*groups: Adw.PreferencesGroup) -> Adw.PreferencesPage:
    p = Adw.PreferencesPage(vexpand=True)
    for g in groups:
        p.add(g)
    return p


def _capture(argv: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return p.returncode, p.stdout


class OobeFlow(Gtk.Box):
    def __init__(self, on_finish: Callable[[bool], None], ask_password: Callable | None = None):
        """ask_password(message, prompt, echo, user, error, answer, cancel): the window's masked on-screen keyboard
        prompt, used for the Wi-Fi password."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.ask_password = ask_password
        self.choices = oobe.Choices()
        # Where to start is the image's to offer (its start-in.conf); without it the step and the call are left out.
        self.start_in, self.start_in_problem = firstrun.load_start_in()
        if self.start_in is not None and self.choices.session not in self.start_in.choices:
            self.choices.session = self.start_in.choices[0]
        self.steps = [s for s in oobe.STEPS if s.key != "session" or self.start_in is not None]
        self.on_finish = on_finish
        self.index = 0
        self.zones = oobe.timezones()

        if oobe.DRY_RUN:
            self.append(Adw.Banner(title="Dry run: no account is created and nothing is changed", revealed=True))
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.review = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        builders = {"language": self.language_page, "network": self.network_page, "timezone": self.timezone_page,
                    "account": self.account_page, "session": self.session_page}
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

        self.notice = Gtk.Label(css_classes=["dim-label"], wrap=True, margin_bottom=6)
        self.append(self.notice)
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
    def chooser(self, title: str, options: list[str], current: str, attr: str,
                search: bool = True) -> Adw.ComboRow:
        row = combo_row(title, options, options.index(current) if current in options else 0, search)

        def changed(r: Adw.ComboRow, _p) -> None:
            setattr(self.choices, attr, options[r.get_selected()])
            self.refresh_gate()
        row.connect("notify::selected", changed)
        return row

    def entry(self, title: str, attr: str, password: bool = False) -> Adw.EntryRow:
        row = plain(Adw.PasswordEntryRow if password else Adw.EntryRow, title=title)

        def changed(r: Adw.EntryRow) -> None:
            setattr(self.choices, attr, r.get_text())
            self.refresh_gate()
        row.connect("changed", changed)
        return row

    def language_page(self) -> Gtk.Widget:
        g = group("Language and keyboard")
        g.add(self.chooser("Language", oobe.COMMON_LOCALES, self.choices.locale, "locale"))
        g.add(self.chooser("Keyboard layout", oobe.COMMON_KEYMAPS, self.choices.keymap, "keymap"))
        return page(g)

    def network_page(self) -> Gtk.Widget:
        g = group("Network", "A cable needs nothing. For Wi-Fi, pick a network below.")
        self.net_status = info_row("", "", "")
        g.add(self.net_status)
        self.wifi_group = group("Wi-Fi networks")
        self.wifi_group.set_visible(False)
        self.wifi_rows: list[Gtk.Widget] = []
        self.net_busy = False
        self.update_net_status()
        self.scan_wifi()
        return page(g, self.wifi_group)

    def update_net_status(self) -> None:
        up = oobe.online()
        self.net_status.set_title("Connected" if up else "Not connected")
        self.net_status.set_subtitle("A network link is up." if up else
                                     "Plug in a cable or join Wi-Fi below. You can also do it later.")

    def scan_wifi(self) -> None:
        """Look for a Wi-Fi adapter and its networks in the background; the list appears when it is ready."""
        def work() -> None:
            code, devices = _capture(network.device_argv())
            nets: list[network.Wifi] = []
            if code == 0 and network.has_wifi_device(devices):
                code, out = _capture(network.scan_argv())
                nets = network.parse_scan(out) if code == 0 else []
                GLib.idle_add(self.show_wifi, nets, True)
            else:
                GLib.idle_add(self.show_wifi, nets, False)

        threading.Thread(target=work, daemon=True).start()

    def show_wifi(self, nets: list[network.Wifi], adapter: bool) -> None:
        for row in self.wifi_rows:
            self.wifi_group.remove(row)
        self.wifi_rows = []
        self.wifi_group.set_visible(adapter)
        rows: list[Gtk.Widget] = []
        for n in nets:
            how = "Open" if n.open else "Secured"
            sub = f"{how}, signal {n.signal}%" + (", connected" if n.in_use else "")
            if n.unsupported:
                rows.append(info_row(n.ssid, "", n.unsupported))
            else:
                rows.append(button_row(n.ssid, sub, "Connected" if n.in_use else "Join",
                                       lambda n=n: self.join_wifi(n)))
        rows.append(button_row("Scan again", "", "Scan", self.scan_wifi))
        for row in rows:
            self.wifi_group.add(row)
        self.wifi_rows = rows

    def join_wifi(self, net: network.Wifi, error: str = "") -> None:
        if oobe.DRY_RUN:
            self.notice.set_label("Dry run: not joining any network")
            return
        if net.open or self.ask_password is None:
            self.do_join(net, "")
            return
        self.ask_password(f"Join {net.ssid}", "Wi-Fi password:", False, "", error,
                          lambda text: self.do_join(net, text), lambda: None)

    def do_join(self, net: network.Wifi, password: str) -> None:
        if self.net_busy:
            return
        self.net_busy = True
        self.notice.set_label(f"Joining {net.ssid}…")

        def work() -> None:
            ok, msg = network.join(net, password)
            GLib.idle_add(self.joined, net, ok, msg)

        threading.Thread(target=work, daemon=True).start()

    def joined(self, net: network.Wifi, ok: bool, msg: str) -> None:
        self.net_busy = False
        self.refresh_gate()
        self.notice.set_label(msg)
        self.update_net_status()
        if ok:
            self.scan_wifi()
        elif msg == "That password was not accepted." and self.ask_password is not None:
            self.join_wifi(net, msg)  # ask again, with the reason on the keyboard screen

    def timezone_page(self) -> Gtk.Widget:
        g = group("Time zone")
        g.add(self.chooser("Time zone", self.zones, self.choices.timezone, "timezone"))
        return page(g)

    def account_page(self) -> Gtk.Widget:
        g = group("Your account", "You become the administrator of this computer.")
        for title, attr, password in (("Full name (optional)", "fullname", False), ("User name", "username", False),
                                      ("Password", "password", True), ("Computer name (optional)", "hostname", False)):
            g.add(self.entry(title, attr, password))
        return page(g)

    def session_page(self) -> Gtk.Widget:
        keys = list(self.start_in.choices)
        titles = [f"{oobe.SESSIONS[k][0]}: {oobe.SESSIONS[k][1]}" for k in keys]
        g = group("Start in", "You can change this later.")
        row = combo_row("After sign-in", titles, keys.index(self.choices.session))

        def changed(r: Adw.ComboRow, _p) -> None:
            self.choices.session = keys[r.get_selected()]
        row.connect("notify::selected", changed)
        g.add(row)
        return page(g)

    def refresh_review(self) -> None:
        child = self.review.get_first_child()
        while child is not None:
            self.review.remove(child)
            child = self.review.get_first_child()
        g = group("What would happen", "Nothing is applied in this version." if oobe.DRY_RUN else None)
        for action in oobe.build_plan(self.choices, self.start_in is not None):
            g.add(info_row(action.text, "", action.area))
        self.review.append(page(g))

    # --- flow --------------------------------------------------------------------------------------------
    def refresh_gate(self) -> None:
        step = self.steps[self.index].key
        problem = oobe.step_problem(step, self.choices)
        self.notice.set_label(problem or "")
        self.next.set_sensitive(problem is None)

    def go(self, delta: int) -> None:
        if delta > 0 and self.index == len(self.steps) - 1:
            if oobe.DRY_RUN:
                for line in oobe.run_plan(oobe.build_plan(self.choices, self.start_in is not None)):
                    print(line, flush=True)
                self.on_finish(True)
                return
            self.next.set_sensitive(False)
            self.notice.set_label("Setting up…")

            def work() -> None:
                ok, msg = oobe.apply_choices(self.choices, lambda a: system.helper_cmd("oobe-helper", a), _run,
                                             self.start_in is not None)
                GLib.idle_add(self.applied, ok, msg)

            threading.Thread(target=work, daemon=True).start()
            return
        self.index = max(0, min(len(self.steps) - 1, self.index + delta))
        self.show_step()

    def applied(self, ok: bool, msg: str) -> None:
        self.notice.set_label(msg)
        if ok:
            self.on_finish(True)
        else:
            self.next.set_sensitive(True)

    def show_step(self) -> None:
        step = self.steps[self.index]
        if step.key == "review":
            self.refresh_review()
        self.stack.set_visible_child_name(step.key)
        self.progress.set_label(f"{self.index + 1} of {len(self.steps)}: {step.title}")
        self.back.set_sensitive(self.index > 0)
        self.next.set_label("Finish" if self.index == len(self.steps) - 1 else "Next")
        self.refresh_gate()
