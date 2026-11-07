"""The network page shared by the account setup (ui/oobe.py) and the welcome tour's Network step (ui/welcome.py).

Controller-first: the Wi-Fi list is a column of buttons, a hidden network is typed in by name, and every text entry
goes through the window's own on-screen keyboard (ask_password / ask_text), which also takes a USB keyboard. It needs
no Steam (there is none yet on the deck image's first boot) and no desktop portal.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import firstboot, network, oobe  # noqa: E402
from .widgets import button_row, group, info_row  # noqa: E402


def _capture(argv: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return p.returncode, p.stdout


def is_online() -> bool:
    up = firstboot.nm_online()
    return oobe.online() if up is None else up


class NetworkPanel:
    """Builds the page (self.page) and keeps it current. notice(text) shows one line under the page; changed() is
    called whenever the connection may have changed (to re-check a Next button, say)."""

    def __init__(self, ask_password: Callable | None, ask_text: Callable | None, notice: Callable[[str], None],
                 changed: Callable[[], None] = lambda: None, dry_run: bool = False, intro: str | None = None):
        self.ask_password, self.ask_text = ask_password, ask_text
        self.notice, self.changed, self.dry_run = notice, changed, dry_run
        self.online = False
        self.busy = False
        g = group("Network", intro or "A cable needs nothing. For Wi-Fi, pick a network below.")
        self.status = info_row("", "", "")
        g.add(self.status)
        self.wifi_group = group("Wi-Fi networks")
        self.wifi_group.set_visible(False)
        self.wifi_rows: list[Gtk.Widget] = []
        self.page = Adw.PreferencesPage(vexpand=True)
        self.page.add(g)
        self.page.add(self.wifi_group)
        self.refresh()
        self.scan()

    # --- state -------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        """Re-read the connection state in the background."""
        def work() -> None:
            code, devices = _capture(network.device_argv())
            wired = code == 0 and network.wired_connected(devices)
            GLib.idle_add(self.show_status, is_online(), wired)

        threading.Thread(target=work, daemon=True).start()

    def show_status(self, up: bool, wired: bool) -> None:
        self.online = up
        if wired:
            self.status.set_title("Connected by cable")
            self.status.set_subtitle("Ethernet is plugged in and working.")
        elif up:
            self.status.set_title("Connected")
            self.status.set_subtitle("A network connection is active.")
        else:
            self.status.set_title("Not connected")
            self.status.set_subtitle("Plug in a cable or join Wi-Fi below.")
        self.changed()

    def scan(self) -> None:
        """Look for a Wi-Fi adapter and its networks in the background; the list appears when it is ready."""
        def work() -> None:
            code, devices = _capture(network.device_argv())
            nets: list[network.Wifi] = []
            adapter = code == 0 and network.has_wifi_device(devices)
            if adapter:
                code, out = _capture(network.scan_argv())
                nets = network.parse_scan(out) if code == 0 else []
            GLib.idle_add(self.show_wifi, nets, adapter)

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
                                       lambda n=n: self.join(n)))
        if adapter and not nets:
            rows.append(info_row("No networks found", "", "Move closer to the router, or scan again."))
        if self.ask_text is not None:
            rows.append(button_row("Hidden network", "Type the network's name yourself.", "Add", self.add_hidden))
        rows.append(button_row("Scan again", "", "Scan", self.scan))
        for row in rows:
            self.wifi_group.add(row)
        self.wifi_rows = rows

    # --- joining -----------------------------------------------------------------------------------------
    def add_hidden(self) -> None:
        def got_name(ssid: str) -> None:
            ssid = ssid.strip()
            problem = network.ssid_problem(ssid)
            if problem:
                self.notice(problem)
                return
            if self.ask_password is None:
                self.do_join(network.hidden_network(ssid, False), "")
                return
            # An empty password means an open network; anything else is WPA/WPA2 personal.
            self.ask_password(f"Join {ssid}", "Wi-Fi password (leave empty for an open network):", False, "", "",
                              lambda pw: self.do_join(network.hidden_network(ssid, bool(pw)), pw), lambda: None)

        self.ask_text("Hidden network", "Network name (SSID):", got_name)

    def join(self, net: network.Wifi, error: str = "") -> None:
        if self.dry_run:
            self.notice("Dry run: not joining any network")
            return
        if net.open or self.ask_password is None:
            self.do_join(net, "")
            return
        self.ask_password(f"Join {net.ssid}", "Wi-Fi password:", False, "", error,
                          lambda text: self.do_join(net, text), lambda: None)

    def do_join(self, net: network.Wifi, password: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.notice(f"Joining {net.ssid}…")

        def work() -> None:
            ok, msg = network.join(net, password)
            GLib.idle_add(self.joined, net, ok, msg)

        threading.Thread(target=work, daemon=True).start()

    def joined(self, net: network.Wifi, ok: bool, msg: str) -> None:
        self.busy = False
        self.notice(msg)
        self.refresh()
        if ok:
            self.scan()
        elif msg == "That password was not accepted." and self.ask_password is not None and not net.open:
            self.join(net, msg)  # ask again, with the reason on the keyboard screen
