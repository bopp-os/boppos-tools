"""Small builders for the Steam-style rows used on every page."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


def group(title: str | None = None, description: str | None = None) -> Adw.PreferencesGroup:
    g = Adw.PreferencesGroup()
    if title:
        g.set_title(title)
    if description:
        g.set_description(description)
    return g


def info_row(title: str, value: str = "", subtitle: str = "") -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    row.set_subtitle_lines(0)
    row.set_focusable(True)
    row.set_activatable(False)
    if value:
        label = Gtk.Label(label=value, xalign=1, wrap=True, selectable=False)
        label.add_css_class("value-label")
        label.set_valign(Gtk.Align.CENTER)
        row.add_suffix(label)
    return row


def button_row(title: str, subtitle: str, label: str, on_click: Callable[[], None], *,
               suggested: bool = False, destructive: bool = False) -> Adw.ActionRow:
    """A row with a button. The row is the focus stop; the button only draws the affordance."""
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    row.set_subtitle_lines(0)
    btn = Gtk.Button(label=label)
    btn.set_valign(Gtk.Align.CENTER)
    btn.set_focusable(False)
    if suggested:
        btn.add_css_class("suggested-action")
    if destructive:
        btn.add_css_class("destructive-action")
    btn.connect("clicked", lambda _b: on_click())
    row.add_suffix(btn)
    row.set_activatable_widget(btn)
    row.button = btn  # type: ignore[attr-defined]
    return row


def switch_row(title: str, subtitle: str, active: bool, on_toggle: Callable[[bool], None]) -> Adw.SwitchRow:
    row = Adw.SwitchRow(title=title, subtitle=subtitle)
    row.set_subtitle_lines(0)
    row.set_active(active)
    row.connect("notify::active", lambda r, _p: on_toggle(r.get_active()))
    return row


def combo_row(title: str, options: list[str], selected: int = 0) -> Adw.ComboRow:
    row = Adw.ComboRow(title=title)
    row.set_model(Gtk.StringList.new(options))
    row.set_selected(selected)
    return row


def message_row(text: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=text)
    row.set_title_lines(0)
    row.set_focusable(True)
    row.set_activatable(False)
    return row
