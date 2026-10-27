"""Small builders for the Steam-style rows used on every page."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402


def plain(row):
    """Show a row's title and subtitle as plain text (libadwaita parses them as Pango markup)."""
    row.set_use_markup(False)
    return row


def group(title: str | None = None, description: str | None = None) -> Adw.PreferencesGroup:
    g = Adw.PreferencesGroup()
    if title:
        g.set_title(GLib.markup_escape_text(title))
    if description:
        g.set_description(GLib.markup_escape_text(description))
    return g


def tag_label(text: str, style: str = "dim-label") -> Gtk.Label:
    """Small bold label (css classes: success, warning, error, dim-label) shown at a row's right edge."""
    label = Gtk.Label(label=text)
    label.add_css_class(style)
    label.add_css_class("heading")
    label.set_valign(Gtk.Align.CENTER)
    return label


def info_row(title: str, value: str = "", subtitle: str = "", tag: tuple[str, str] | None = None) -> Adw.ActionRow:
    row = plain(Adw.ActionRow(title=title, subtitle=subtitle))
    row.set_subtitle_lines(0)
    row.set_focusable(True)
    row.set_activatable(False)
    if tag:
        row.add_suffix(tag_label(*tag))
    if value:
        label = Gtk.Label(label=value, xalign=1, wrap=True, selectable=False)
        label.add_css_class("value-label")
        label.set_valign(Gtk.Align.CENTER)
        row.add_suffix(label)
    return row


def button_row(title: str, subtitle: str, label: str, on_click: Callable[[], None], *,
               suggested: bool = False, destructive: bool = False,
               tag: tuple[str, str] | None = None) -> Adw.ActionRow:
    """A row with a button. The row is the focus stop; the button only draws the affordance."""
    row = plain(Adw.ActionRow(title=title, subtitle=subtitle))
    row.set_title_lines(0)
    row.set_subtitle_lines(0)
    btn = Gtk.Button(label=label)
    btn.set_valign(Gtk.Align.CENTER)
    btn.set_focusable(False)
    if suggested:
        btn.add_css_class("suggested-action")
    if destructive:
        btn.add_css_class("destructive-action")
    btn.connect("clicked", lambda _b: on_click())
    if tag:
        row.add_suffix(tag_label(*tag))
    row.add_suffix(btn)
    row.set_activatable_widget(btn)
    row.button = btn  # type: ignore[attr-defined]
    return row


def switch_row(title: str, subtitle: str, active: bool, on_toggle: Callable[[bool], None]) -> Adw.SwitchRow:
    row = plain(Adw.SwitchRow(title=title, subtitle=subtitle))
    row.set_title_lines(0)
    row.set_subtitle_lines(0)
    row.set_active(active)
    row.connect("notify::active", lambda r, _p: on_toggle(r.get_active()))
    return row


def combo_row(title: str, options: list[str], selected: int = 0) -> Adw.ComboRow:
    row = plain(Adw.ComboRow(title=title))
    row.set_model(Gtk.StringList.new(options))
    row.set_selected(selected)
    return row


def message_row(text: str) -> Adw.ActionRow:
    row = plain(Adw.ActionRow(title=text))
    row.set_title_lines(0)
    row.set_focusable(True)
    row.set_activatable(False)
    return row


def expander_row(title: str, subtitle: str = "") -> Adw.ExpanderRow:
    # ExpanderRow always parses its title as markup, so escape it instead of switching markup off
    return Adw.ExpanderRow(title=GLib.markup_escape_text(title), subtitle=GLib.markup_escape_text(subtitle))
