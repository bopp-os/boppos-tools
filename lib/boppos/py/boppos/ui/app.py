"""boppos-deck window and application: sidebar + page panel, progress view, controller + keyboard."""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .. import hwprofiles, oobe, system, welcome  # noqa: E402
from . import agent as polkit_agent  # noqa: E402
from . import backend  # noqa: E402
from .controller import DIRECTIONS, PAGES, Action, InputMapper, Sdl3Gamepads  # noqa: E402
from .hints import hint_items  # noqa: E402
from .oobe import OobeFlow  # noqa: E402
from .osk import PasswordBuffer  # noqa: E402
from .pages import ALL_PAGES  # noqa: E402
from .welcome import WelcomeFlow  # noqa: E402

APP_ID = "org.boppos.Deck"
ICON_NAME = "boppos-icon"  # installed by boppos-artwork; the desktop file is named after APP_ID
DIRECTION_MAP = {
    Action.UP: Gtk.DirectionType.UP,
    Action.DOWN: Gtk.DirectionType.DOWN,
    Action.LEFT: Gtk.DirectionType.LEFT,
    Action.RIGHT: Gtk.DirectionType.RIGHT,
}
def on_main(fn: Callable[[], None]) -> None:
    GLib.idle_add(lambda: (fn(), GLib.SOURCE_REMOVE)[1])


def activate(widget: Gtk.Widget) -> None:
    """Press a focused widget now. Gtk.Button.activate() delays the click for its press animation."""
    if isinstance(widget, Gtk.Button):
        widget.emit("clicked")
    else:
        widget.activate()


def header_expander(widget: Gtk.Widget | None) -> Adw.ExpanderRow | None:
    """The ExpanderRow whose header row has focus (child rows sit inside a Gtk.Revealer)."""
    inside_revealer = False
    while widget is not None:
        if isinstance(widget, Gtk.Revealer):
            inside_revealer = True
        if isinstance(widget, Adw.ExpanderRow):
            return None if inside_revealer else widget
        widget = widget.get_parent()
    return None


def bool_value(v: bool) -> GObject.Value:
    val = GObject.Value()
    val.init(GObject.TYPE_BOOLEAN)
    val.set_boolean(v)
    return val


class DeckWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, gaming: bool, pad: Sdl3Gamepads | None):
        super().__init__(application=app, title="BoppOS", default_width=1280, default_height=800)
        self.gaming = gaming
        self.settings = backend.load_settings()
        self.advanced = bool(self.settings.get("advanced"))
        self.add_css_class("deck")
        self.dialog: Adw.AlertDialog | None = None
        self.job: backend.Job | None = None
        self.pad, self.mapper = pad, InputMapper()
        self.pad_family = pad.family() if pad is not None else "generic"
        # Controller prompts only when a pad is connected or we run in Gaming Mode; otherwise keyboard keys.
        self.input_mode = "pad" if self.pad_available() else "kb"
        self._hint_key: tuple | None = None
        self._used_keyboard = False
        # page key -> the row that had focus there. A strong reference on purpose: a weakref to a PyGObject
        # wrapper dies as soon as Python drops the wrapper, although the row itself is still there.
        self._last_focus: dict[str, Gtk.Widget] = {}
        self._probing = False  # true while a focus move is being tried (sidebar rows must not react)
        self._refresh_timer = 0
        self.pw = PasswordBuffer()
        self.auth_req: dict | None = None
        self.auth_queue: list[dict] = []
        self.auth_prev = "main"

        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.build_main(), "main")
        self.stack.add_named(self.build_progress(), "progress")
        self.stack.add_named(self.build_auth(), "auth")
        self.toasts.set_child(self.stack)

        view = Adw.ToolbarView()
        if not gaming:
            # Desktop Mode: a normal title bar so the window can be moved and closed.
            view.add_top_bar(Adw.HeaderBar())
        view.set_content(self.toasts)
        view.add_bottom_bar(self.build_hints())
        self.set_content(view)
        self.add_breakpoints()

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.connect("close-request", self.on_close_request)
        self.connect("notify::focus-widget", self.on_focus_changed)

        GLib.timeout_add(1500, self.prefetch)
        if gaming:
            self.fullscreen()
        if pad is not None:
            GLib.timeout_add(16, self.poll_pad)
        self.select_page(0)

    # --- layout -------------------------------------------------------------------------------

    def build_main(self) -> Gtk.Widget:
        self.pages = [cls(self) for cls in ALL_PAGES]
        self.content_stack = Gtk.Stack(vexpand=True, hexpand=True)
        for p in self.pages:
            self.content_stack.add_named(p.widget, p.key)

        self.sidebar_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar_list.add_css_class("navigation-sidebar")
        self.sidebar_rows: list[Gtk.ListBoxRow] = []
        for i, p in enumerate(self.pages):
            box = Gtk.Box(spacing=14)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.set_margin_start(8)
            box.append(Gtk.Image.new_from_icon_name(p.icon))
            label = Gtk.Label(label=p.title, xalign=0, hexpand=True)
            box.append(label)
            row = Gtk.ListBoxRow()
            row.set_child(box)
            focus = Gtk.EventControllerFocus()
            focus.connect("enter", lambda _c, idx=i: None if self._probing else self.select_page(idx, move_focus=False))
            row.add_controller(focus)
            self.sidebar_list.append(row)
            self.sidebar_rows.append(row)
        self.sidebar_list.connect("row-activated", lambda _l, row: self.enter_content(self.sidebar_rows.index(row)))

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title = Gtk.Label(label="BoppOS", xalign=0)
        title.add_css_class("sidebar-title")
        side.append(title)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.sidebar_list)
        side.append(scroller)

        self.split = Adw.NavigationSplitView()
        self.split.set_min_sidebar_width(300)
        self.split.set_max_sidebar_width(460)
        self.split.set_sidebar_width_fraction(0.28)
        self.sidebar_page = Adw.NavigationPage.new(side, "BoppOS")
        self.content_page = Adw.NavigationPage.new(self.content_stack, self.pages[0].title)
        self.split.set_sidebar(self.sidebar_page)
        self.split.set_content(self.content_page)
        return self.split

    def build_progress(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(32)
        self.p_title = Gtk.Label(xalign=0)
        self.p_title.add_css_class("progress-title")
        self.p_status = Gtk.Label(xalign=0, wrap=True)
        self.p_spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.START)
        self.p_bar = Gtk.ProgressBar(show_text=True, visible=False)
        self.p_prog_mark: Gtk.TextMark | None = None  # start of the progress line being redrawn
        self.p_log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                  wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.p_log.add_css_class("progress-log")
        buf = self.p_log.get_buffer()
        buf.create_tag("heading", weight=700, scale=1.15, pixels_above_lines=10, pixels_below_lines=4)
        buf.create_tag("ok", weight=700, foreground="#2ec27e")
        buf.create_tag("fail", weight=700, foreground="#e01b24")
        log_scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=120)
        log_scroll.set_child(self.p_log)
        self.p_log_scroll = log_scroll
        self.p_expander = Gtk.Expander(label="Details")
        self.p_expander.set_child(log_scroll)
        self.p_expander.set_vexpand(True)
        actions = Gtk.Box(spacing=12)
        self.p_reboot = Gtk.Button(label="Reboot now")
        self.p_reboot.add_css_class("destructive-action")
        self.p_reboot.connect("clicked", lambda _b: self.reboot_now())
        self.p_done = Gtk.Button(label="Back")
        self.p_done.add_css_class("suggested-action")
        self.p_done.connect("clicked", lambda _b: self.close_progress())
        actions.append(self.p_done)
        actions.append(self.p_reboot)
        for w in (self.p_title, self.p_status, self.p_spinner, self.p_bar, self.p_expander, actions):
            box.append(w)
        return box

    def build_auth(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(28)
        self.a_title = Gtk.Label(xalign=0, wrap=True)
        self.a_title.add_css_class("progress-title")
        self.a_sub = Gtk.Label(xalign=0, wrap=True)
        self.a_error = Gtk.Label(xalign=0, wrap=True)
        self.a_error.add_css_class("error")
        self.a_display = Gtk.Label(xalign=0.5, label="")
        self.a_display.add_css_class("osk-display")
        self.a_display.set_size_request(-1, 56)
        for w in (self.a_title, self.a_sub, self.a_error, self.a_display):
            box.append(w)

        self.osk_keys: list[list[Gtk.Button]] = []
        widths = [10, 10, 10, 9]
        keys = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
        for n in widths:
            row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, homogeneous=True)
            buttons = []
            for _ in range(n):
                b = Gtk.Button()
                b.add_css_class("osk-key")
                b.connect("clicked", lambda btn: self.osk_press(btn.char))
                row.append(b)
                buttons.append(b)
            self.osk_keys.append(buttons)
            keys.append(row)
        bottom = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        self.osk_shift = self.osk_button("Shift", lambda: self.osk_action("shift"), bottom)
        self.osk_sym = self.osk_button("123", lambda: self.osk_action("symbols"), bottom)
        space = self.osk_button("Space", lambda: self.osk_press(" "), bottom)
        space.set_size_request(260, -1)
        self.osk_button("Delete", lambda: self.osk_action("backspace"), bottom)
        self.osk_show = self.osk_button("Show", lambda: self.osk_action("visible"), bottom)
        ok = self.osk_button("OK", self.auth_submit, bottom)
        ok.add_css_class("suggested-action")
        self.osk_button("Cancel", self.auth_cancel, bottom)
        keys.append(bottom)
        box.append(keys)
        self.osk_refresh()
        return box

    def osk_button(self, label: str, fn: Callable[[], None], parent: Gtk.Box) -> Gtk.Button:
        b = Gtk.Button(label=label)
        b.add_css_class("osk-key")
        b.connect("clicked", lambda _b: fn())
        parent.append(b)
        return b

    def osk_refresh(self) -> None:
        rows = self.pw.rows()
        for buttons, chars in zip(self.osk_keys, rows, strict=True):
            for i, b in enumerate(buttons):
                b.set_visible(i < len(chars))
                if i < len(chars):
                    b.set_label(chars[i])
                    b.char = chars[i]
        self.osk_sym.set_label("abc" if self.pw.symbols else "123")
        self.osk_show.set_label("Hide" if self.pw.visible else "Show")
        if self.pw.shift:
            self.osk_shift.add_css_class("suggested-action")
        else:
            self.osk_shift.remove_css_class("suggested-action")
        echo = bool(self.auth_req and self.auth_req["echo"])
        self.osk_show.set_visible(not echo)
        self.a_display.set_label(self.pw.display(echo) or " ")

    def osk_press(self, ch: str) -> None:
        self.pw.type(ch)
        self.osk_refresh()

    def osk_action(self, name: str) -> None:
        {"shift": self.pw.toggle_shift, "symbols": self.pw.toggle_symbols,
         "backspace": self.pw.backspace, "visible": self.pw.toggle_visible}[name]()
        self.osk_refresh()

    def ask_password(self, message: str, prompt: str, echo: bool, user: str, error: str,
                     answer: Callable[[str], None], cancel: Callable[[], None]) -> None:
        """Called by the polkit agent on the main loop. Requests are shown one at a time."""
        req = {"message": message, "prompt": prompt, "echo": echo, "user": user, "error": error,
               "answer": answer, "cancel": cancel}
        if self.auth_req is not None:
            self.auth_queue.append(req)
            return
        self.show_auth(req)

    def ask_text(self, title: str, prompt: str, on_ok: Callable[[str], None],
                 on_cancel: Callable[[], None] | None = None) -> None:
        """Plain text entry with the same on-screen keyboard (the text is shown, not masked)."""
        self.ask_password(title, prompt, True, "", "", on_ok, on_cancel or (lambda: None))

    def show_auth(self, req: dict) -> None:
        self.auth_req = req
        self.pw.clear()
        self.a_title.set_label(req["message"] or "Authentication required")
        who = f"  (for {req['user']})" if req["user"] else ""
        self.a_sub.set_label(f"{req['prompt'].strip() or 'Password:'}{who}")
        self.a_error.set_label(req["error"])
        self.a_error.set_visible(bool(req["error"]))
        current = self.stack.get_visible_child_name()
        self.auth_prev = current if current != "auth" else self.auth_prev
        self.stack.set_visible_child_name("auth")
        self.osk_refresh()
        self.render_hints()
        self.osk_keys[1][0].grab_focus()

    def auth_done(self) -> dict | None:
        req, self.auth_req = self.auth_req, None
        self.pw.clear()
        self.stack.set_visible_child_name(self.auth_prev)
        self.render_hints()
        if self.auth_queue:
            self.show_auth(self.auth_queue.pop(0))
        return req

    def auth_submit(self) -> None:
        if self.auth_req is None:
            return
        text = self.pw.take()
        req = self.auth_done()
        if req:
            req["answer"](text)

    def auth_cancel(self) -> None:
        if self.auth_req is None:
            return
        req = self.auth_done()
        if req:
            req["cancel"]()

    def build_hints(self) -> Gtk.Widget:
        self.hint_box = Gtk.Box(spacing=0)
        self.hint_box.add_css_class("hint-bar")
        self.render_hints()
        return self.hint_box

    def pad_available(self) -> bool:
        return self.gaming or bool(self.pad and self.pad.connected)

    def render_hints(self) -> None:
        mode = "pad" if (self.input_mode == "pad" and self.pad_available()) else "kb"
        auth = self.stack.get_visible_child_name() == "auth"
        key = (mode, self.pad_family if mode == "pad" else "", auth)
        if key == self._hint_key:
            return
        self._hint_key = key
        child = self.hint_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.hint_box.remove(child)
            child = nxt
        for glyph, label, css in hint_items(mode, self.pad_family, auth):
            k = Gtk.Label(label=glyph)
            k.add_css_class("hint-key")
            if css:
                k.add_css_class(css)
            t = Gtk.Label(label=label)
            t.add_css_class("hint-label")
            self.hint_box.append(k)
            self.hint_box.append(t)

    def add_breakpoints(self) -> None:
        for min_w, css in ((1700, "size-l"), (2300, "size-xl")):
            bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"min-width: {min_w}px"))
            bp.connect("apply", lambda _b, c=css: self.add_css_class(c))
            bp.connect("unapply", lambda _b, c=css: self.remove_css_class(c))
            self.add_breakpoint(bp)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 900px"))
        narrow.add_setter(self.split, "collapsed", bool_value(True))
        self.add_breakpoint(narrow)

    # --- pages --------------------------------------------------------------------------------

    @property
    def current_index(self) -> int:
        row = self.sidebar_list.get_selected_row()
        return row.get_index() if row else 0

    def select_page(self, idx: int, move_focus: bool = True) -> None:
        idx %= len(self.pages)
        page = self.pages[idx]
        self.sidebar_list.select_row(self.sidebar_rows[idx])
        if self.content_stack.get_visible_child_name() != page.key:
            self.content_stack.set_visible_child_name(page.key)
            self.content_page.set_title(page.title)
        if not page.built and not page.loading:
            page.refresh()  # first visit: nothing to show yet, so start now
        elif page.needs_refresh():
            self.schedule_refresh(page)  # revisit: show what we have, refresh once you have stopped here
        if move_focus:
            self.sidebar_rows[idx].grab_focus()

    def schedule_refresh(self, page, delay_ms: int = 150) -> None:
        """Refresh only the page you settle on, not every tab you pass on the way."""
        if self._refresh_timer:
            GLib.source_remove(self._refresh_timer)

        def run() -> bool:
            self._refresh_timer = 0
            if self.pages[self.current_index] is page and page.needs_refresh() and not page.loading:
                page.refresh(quiet=True)
            return GLib.SOURCE_REMOVE

        self._refresh_timer = GLib.timeout_add(delay_ms, run)

    def prefetch(self) -> bool:
        """After start-up, quietly build the other pages one at a time so tabs are ready when you get there.
        (Not while developing with pkexec from a checkout: every read would ask for a password.)"""
        dev_polkit = os.environ.get("BOPPOS_HELPER_DIR") and os.environ.get("BOPPOS_HELPER_ESCALATE") != "sudo"
        if dev_polkit or (self.job and self.job.running):
            return GLib.SOURCE_REMOVE
        if any(p.loading for p in self.pages):
            return GLib.SOURCE_CONTINUE
        nxt = next((p for p in self.pages if not p.built and not p.loading
                    and p is not self.pages[self.current_index]), None)
        if nxt is None:
            return GLib.SOURCE_REMOVE
        nxt.refresh()
        return GLib.SOURCE_CONTINUE

    def on_focus_changed(self, *_args) -> None:
        f = self.get_focus()
        if f is None or self.stack.get_visible_child_name() != "main":
            return
        page = self.pages[self.current_index]
        if f.is_ancestor(page.widget):
            self._last_focus[page.key] = f

    def enter_content(self, idx: int | None = None) -> None:
        """Move into the page. An accidental Left (or B) must not cost you your place: go back to the
        row that had focus, and only start from the top when there is none."""
        if idx is None:
            focused = self.get_focus()
            row = focused if focused in self.sidebar_rows else None
            idx = self.sidebar_rows.index(row) if row is not None else self.current_index
        self.select_page(idx, move_focus=False)  # whatever is highlighted must also be what is shown
        if self.split.get_collapsed():
            self.split.set_show_content(True)
        page = self.pages[self.current_index]
        last = self._last_focus.get(page.key)
        if last is not None and last.get_mapped() and last.is_ancestor(page.widget) and last.get_sensitive():
            last.grab_focus()
            return
        if not page.widget.child_focus(Gtk.DirectionType.TAB_FORWARD):
            page.widget.grab_focus()

    def step_inside(self, scope: Gtk.Widget, direction: Gtk.DirectionType, quiet: bool = True) -> bool:
        """Try one focus move and keep it only if focus stays inside `scope`.

        GTK's spatial focus happily leaves a page for the sidebar at the last row (and the sidebar rows
        switch pages), so a move that lands outside is undone and reported as "at the edge". While probing
        out of a page the sidebar must not react (quiet); a real move within the sidebar must (not quiet).
        """
        before = self.get_focus()
        self._probing = quiet
        try:
            scope.child_focus(direction)
            after = self.get_focus()
            if after is not None and after is not before and not after.is_ancestor(scope):
                if before is not None:
                    before.grab_focus()
                return False
            return after is not before
        finally:
            self._probing = False

    def move_focus(self, a: Action) -> None:
        """One D-pad step, kept inside whatever has focus: the page, or the sidebar list. Spatial focus
        would otherwise slip from the last row of a page into the sidebar, whose rows switch pages."""
        direction = DIRECTION_MAP[a]
        if self.focus_in_sidebar():
            if a == Action.RIGHT:
                self.enter_content()
            elif a in (Action.UP, Action.DOWN):
                self.step_inside(self.sidebar_list, direction, quiet=False)  # the row you land on selects its page
            return
        page = self.pages[self.current_index]
        if not self.step_inside(page.widget, direction) and a == Action.LEFT:
            self.sidebar_rows[self.current_index].grab_focus()  # the only way out of a page is Left or B

    def jump(self, a: Action) -> None:
        """Move focus about a screenful (or to the very top/bottom) by stepping through the rows, so the
        focus stays on a real row and the view follows it."""
        page = self.pages[self.current_index]
        sw = page.widget
        f = self.get_focus()
        if f is None or not f.is_ancestor(sw):
            return
        adj = sw.get_vadjustment()

        def y_of(widget: Gtk.Widget) -> float | None:
            ok, rect = widget.compute_bounds(sw)
            return rect.get_y() + adj.get_value() if ok else None

        down = a in (Action.PAGE_DOWN, Action.BOTTOM)
        direction = Gtk.DirectionType.DOWN if down else Gtk.DirectionType.UP
        start = y_of(f)
        if start is None:
            return
        whole = a in (Action.TOP, Action.BOTTOM)
        step = adj.get_page_size() * 0.85
        target = start + step if down else start - step
        for _ in range(400 if whole else 80):
            if not self.step_inside(sw, direction):
                break
            f = self.get_focus()
            if f is None:
                break
            if not whole:
                y = y_of(f)
                if y is None or (down and y >= target) or (not down and y <= target):
                    break

    def focus_in_sidebar(self) -> bool:
        f = self.get_focus()
        return bool(f and f.is_ancestor(self.sidebar_list)) or f is self.sidebar_list

    def set_advanced(self, on: bool) -> None:
        self.advanced = on
        self.settings["advanced"] = on
        try:
            backend.save_settings(self.settings)
        except OSError:
            pass
        for p in self.pages:
            p.stale = True

    # --- services for pages -------------------------------------------------------------------

    def load(self, fn: Callable[[], object], cb: Callable[[object], None]) -> None:
        def work():
            try:
                res = fn()
            except Exception as exc:  # a dead worker would leave its page on "Loading…" for good
                res = {"error": f"{type(exc).__name__}: {exc}"}
            on_main(lambda: cb(res))

        threading.Thread(target=work, daemon=True).start()

    def toast(self, text: str) -> None:
        t = Adw.Toast.new(text)
        t.set_timeout(5)
        self.toasts.add_toast(t)

    def confirm(self, heading: str, body: str, label: str, on_ok: Callable[[], None],
                destructive: bool = False, on_cancel: Callable[[], None] | None = None) -> None:
        dlg = Adw.AlertDialog.new(heading, body)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", label)
        if destructive:
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        else:
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.set_prefer_wide_layout(True)
        dlg.add_css_class("deck-dialog")
        for size in ("size-l", "size-xl"):  # the dialog does not inherit the window's size classes
            if self.has_css_class(size):
                dlg.add_css_class(size)
        self.dialog = dlg

        def finished(d, res):
            self.dialog = None
            if d.choose_finish(res) == "ok":
                on_ok()
            elif on_cancel:
                on_cancel()

        dlg.choose(self, None, finished)
        GLib.idle_add(lambda: (self.set_focus_visible(True), dlg.child_focus(Gtk.DirectionType.TAB_FORWARD),
                               GLib.SOURCE_REMOVE)[-1])

    def confirm_quit(self) -> None:
        self.confirm("Exit BoppOS settings?", "You will return to Steam.", "Exit", self.get_application().quit)

    # --- progress view ------------------------------------------------------------------------

    def start_update(self, targets: list[str] | None) -> None:
        """Open the update progress screen and run the update (what the tray's update entries ask for)."""
        label = ", ".join(targets) if targets else "everything"
        page = next(p for p in self.pages if p.key == "system")
        page.update(targets, label)

    def run_job(self, title: str, cmds: list[list[str]], on_done: Callable[[bool, dict], None]) -> None:
        if self.job and self.job.running:
            self.toast("Another action is still running")
            return
        self.p_title.set_label(title)
        self.p_status.set_label("Working…")
        self.p_log.get_buffer().set_text("")
        self.p_prog_mark = None
        self.p_bar.set_visible(False)
        self.p_spinner.set_spinning(True)
        self.p_spinner.set_visible(True)
        self.p_done.set_sensitive(False)
        self.p_reboot.set_visible(False)
        self.stack.set_visible_child_name("progress")
        self.p_done.grab_focus()
        self._on_done = on_done

        def end_progress_line(buf: Gtk.TextBuffer) -> None:
            if self.p_prog_mark is not None:  # close the progress line before the next line of text
                buf.delete_mark(self.p_prog_mark)
                self.p_prog_mark = None
                buf.insert(buf.get_end_iter(), "\n")

        def on_event(ev: dict) -> None:
            buf = self.p_log.get_buffer()
            if ev["event"] == "progress":
                # A redrawn bar replaces its previous line instead of adding one, and drives the bar above.
                if self.p_prog_mark is None:
                    self.p_prog_mark = buf.create_mark(None, buf.get_end_iter(), True)
                else:
                    buf.delete(buf.get_iter_at_mark(self.p_prog_mark), buf.get_end_iter())
                buf.insert(buf.get_end_iter(), ev["line"])
                pct = ev.get("percent")
                if pct is not None:
                    self.p_bar.set_visible(True)
                    self.p_bar.set_fraction(pct / 100)
                    self.p_bar.set_text(ev.get("bar") or f"{pct:.0f}%")
            elif ev["event"] == "log":
                end_progress_line(buf)
                buf.insert(buf.get_end_iter(), ev["line"] + "\n")
            elif ev["event"] == "step":
                # Each updater gets a heading, and a done/failed line when it finishes
                end_progress_line(buf)
                name = ev.get("label") or ev["id"]
                if ev["status"] == "start":
                    pos = f" ({ev['index']} of {ev['total']})" if ev.get("total", 1) > 1 else ""
                    self.p_status.set_label(f"Updating {name}{pos}…")
                    buf.insert_with_tags_by_name(buf.get_end_iter(), f"{name}\n", "heading")
                    self.p_bar.set_visible(False)  # the next updater's bar starts fresh
                elif ev["status"] in ("ok", "fail"):
                    ok = ev["status"] == "ok"
                    buf.insert_with_tags_by_name(buf.get_end_iter(), f"{'✓' if ok else '✗'} {name}: "
                                                 f"{'done' if ok else 'failed'}\n", "ok" if ok else "fail")
                else:
                    self.p_status.set_label(f"{name}: {ev['status']}")
            if ev["event"] in ("log", "step"):
                adj = self.p_log_scroll.get_vadjustment()
                adj.set_value(adj.get_upper())

        def on_finish(ok: bool, final: dict) -> None:
            self.p_bar.set_visible(False)
            self.p_spinner.set_spinning(False)
            self.p_spinner.set_visible(False)
            self.p_done.set_sensitive(True)
            self.p_done.grab_focus()
            failed = [system.TARGET_LABELS.get(t, t) for t in final.get("failed") or []]
            msg = "Done." if ok else (f"Failed: {', '.join(failed)}. See Details for the log." if failed
                                      else "Something went wrong. Open Details for the log.")
            if final.get("restart_steam") and ok:
                msg += " Restart Steam to finish."
            self.p_status.set_label(msg)
            self.p_reboot.set_visible(bool(final.get("reboot_required")) and ok)
            if not ok:
                self.p_expander.set_expanded(True)
            self._final = (ok, final)

        self.job = backend.Job(cmds, on_main, on_event, on_finish)
        self.job.start()

    def close_progress(self) -> None:
        if self.job and self.job.running:
            return
        self.stack.set_visible_child_name("main")
        ok, final = getattr(self, "_final", (True, {}))
        self._on_done(ok, final)
        self.focus_current()

    def focus_current(self) -> None:
        if not self.get_focus() or self.get_focus() is self.p_done:
            self.sidebar_rows[self.current_index].grab_focus()

    def reboot_now(self) -> None:
        backend.run_json([backend.tool("bopp-system"), "reboot"], timeout=10)

    # --- input --------------------------------------------------------------------------------

    def set_input_mode(self, mode: str) -> None:
        if mode == "kb":
            self._used_keyboard = True
        if mode != self.input_mode:
            self.input_mode = mode
        self.render_hints()

    def poll_pad(self) -> bool:
        now = time.monotonic()
        actions = []
        for ev in self.pad.poll():
            if ev[0] in ("added", "removed"):
                self.pad_family = self.pad.family()
                if ev[0] == "added" and self.input_mode == "kb" and not self._used_keyboard:
                    self.input_mode = "pad"  # a controller was plugged in before any key was pressed
                self.render_hints()
                continue
            got = self.mapper.feed(ev, now)
            if got:  # the device that was actually used decides the glyphs
                self.pad_family = self.pad.family(ev[-1])
            actions += got
        actions += self.mapper.tick(now)
        if not self.is_active():  # SDL hears the controller even behind a game; those presses are not for us
            self.mapper.stop_repeats()
            return GLib.SOURCE_CONTINUE
        if actions:
            self.set_input_mode("pad")
        for a in actions:
            self.handle_action(a)
        return GLib.SOURCE_CONTINUE

    def on_key(self, _ctl, keyval, _code, state) -> bool:
        self.set_input_mode("kb")
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if self.stack.get_visible_child_name() == "auth":
            return self.on_auth_key(keyval, ctrl or bool(state & Gdk.ModifierType.ALT_MASK))
        if keyval == Gdk.KEY_Escape and not self.dialog:
            self.handle_action(Action.BACK)
            return True
        keys = {Gdk.KEY_Page_Up: Action.PAGE_UP, Gdk.KEY_Page_Down: Action.PAGE_DOWN,
                Gdk.KEY_Home: Action.TOP, Gdk.KEY_End: Action.BOTTOM}
        if not ctrl and not self.dialog and keyval in keys and self.stack.get_visible_child_name() == "main":
            self.handle_action(keys[keyval])
            return True
        if ctrl and keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            back = keyval in (Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Page_Up) or (keyval == Gdk.KEY_Tab and shift)
            self.handle_action(Action.PREV if back else Action.NEXT)
            return True
        return False

    def on_auth_key(self, keyval: int, modified: bool) -> bool:
        """Physical (or Steam Input) keyboard while the password prompt is up."""
        if keyval == Gdk.KEY_Escape:
            self.auth_cancel()
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.auth_submit()
        elif keyval == Gdk.KEY_BackSpace:
            self.osk_action("backspace")
        elif not modified and (cp := Gdk.keyval_to_unicode(keyval)) and chr(cp).isprintable():
            self.pw.type(chr(cp))
            self.osk_refresh()
        else:
            return False  # arrows and Tab move between keys
        return True

    def handle_action(self, a: Action) -> None:
        self.set_focus_visible(True)  # GTK only shows focus after keyboard input; a controller needs it too
        if self.stack.get_visible_child_name() == "auth":
            if a in DIRECTIONS:
                self.child_focus(DIRECTION_MAP[a])
            elif a == Action.ACCEPT:
                f = self.get_focus()
                if f is not None:
                    activate(f)
            elif a == Action.BACK:
                self.auth_cancel()
            elif a == Action.PREV:
                self.osk_action("backspace")
            elif a == Action.NEXT:
                self.osk_action("shift")
            elif a == Action.MENU:
                self.auth_submit()
            return
        if self.stack.get_visible_child_name() == "progress":
            if a == Action.ACCEPT and self.p_done.get_sensitive():
                activate(self.p_done)
            elif a == Action.BACK and self.p_done.get_sensitive():
                self.close_progress()
            elif a in DIRECTIONS:
                self.child_focus(DIRECTION_MAP[a])
            return
        if self.dialog is not None:
            if a == Action.BACK:
                self.dialog.close()
            elif a == Action.ACCEPT:
                f = self.get_focus()
                if f is not None:
                    activate(f)
            elif a in DIRECTIONS:
                self.child_focus(DIRECTION_MAP[a])
            return
        if a in PAGES or a in (Action.TOP, Action.BOTTOM):
            if not self.focus_in_sidebar():
                self.jump(a)
            return
        exp = None if self.focus_in_sidebar() else header_expander(self.get_focus())
        if exp is not None and (a == Action.ACCEPT or (a == Action.RIGHT and not exp.get_expanded())
                                or (a == Action.LEFT and exp.get_expanded())):
            exp.set_expanded(a == Action.RIGHT or (a == Action.ACCEPT and not exp.get_expanded()))
        elif a in DIRECTIONS:
            self.move_focus(a)
        elif a == Action.ACCEPT:
            f = self.get_focus()
            if f is None:
                self.select_page(self.current_index)
            elif self.focus_in_sidebar():
                self.enter_content()
            else:
                activate(f)
        elif a == Action.BACK:
            if self.focus_in_sidebar():
                if self.gaming:
                    self.confirm_quit()
            else:
                if self.split.get_collapsed():
                    self.split.set_show_content(False)
                self.sidebar_rows[self.current_index].grab_focus()
        elif a in (Action.PREV, Action.NEXT):
            step = -1 if a == Action.PREV else 1
            was_side = self.focus_in_sidebar()
            self.select_page(self.current_index + step, move_focus=was_side)
        elif a == Action.MENU and self.gaming:
            self.confirm_quit()

    def show_welcome(self, hw: hwprofiles.Hardware | None = None) -> None:
        """Replace the settings view with the first-run wizard until it is finished or skipped.

        hw previews the wizard on another kind of machine instead of the one that is detected."""
        if self.stack.get_child_by_name("welcome") is None:
            self.welcome_flow = WelcomeFlow(self.gaming, self.end_welcome, hw)
            self.stack.add_named(self.welcome_flow, "welcome")
        self.stack.set_visible_child_name("welcome")

    def show_oobe(self) -> None:
        """Replace the settings view with the first-boot setup (language, time zone, account)."""
        if self.stack.get_child_by_name("oobe") is None:
            self.stack.add_named(OobeFlow(self.end_oobe), "oobe")
        self.stack.set_visible_child_name("oobe")

    def end_oobe(self, finished: bool) -> None:
        if finished and not oobe.DRY_RUN:  # the helper started the login screen: this window's job is over
            self.get_application().quit()
            return
        self.stack.set_visible_child_name("main")

    def end_welcome(self, finished: bool) -> None:
        self.stack.set_visible_child_name("main")
        # a dry run or a --hardware preview applied nothing, so it must not count as set up
        if not finished or welcome.DRY_RUN or self.welcome_flow.preview:
            return
        welcome.mark_done()
        if cmds := welcome.apply_commands(self.welcome_flow.choices, backend.tool):
            self.run_job("Setting up BoppOS", cmds, lambda _ok, _final: None)

    def on_close_request(self, *_a) -> bool:
        if self.job and self.job.running:
            self.toast("An action is still running")
            return True
        return False


class DeckApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("update", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Open on the update screen and start updating: all, or a list of "
                             + ",".join(system.TARGETS), "TARGETS")
        self.add_main_option("welcome", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open the first-run setup wizard", None)
        self.add_main_option("oobe", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open the first-boot setup (language, time zone, account); a dry run for now", None)
        self.add_main_option("hardware", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "With --welcome: preview the wizard on other hardware (" + ", ".join(hwprofiles.PROFILES)
                             + ")", "KEY")
        self.add_main_option("if-needed", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "With --welcome: do nothing when the wizard was already finished", None)
        self.gaming = backend.gaming_mode()
        self.pad: Sdl3Gamepads | None = None
        self.agent = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(ICON_NAME)  # taskbar, Alt-Tab and the window's own icon
        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).with_name("style.css")))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        try:
            self.pad = Sdl3Gamepads()
        except OSError as exc:
            print(f"boppos-deck: no gamepad support ({exc}); keyboard and mouse only", file=sys.stderr)

    def run_test_mode(self, win: DeckWindow, mode: str) -> None:
        """BOPPOS_DECK_TEST=keyboard | keyboard-retry | text: open the on-screen keyboard on its own, with a
        pretend request, print how many characters were typed (never the text) and exit. For trying the
        keyboard and the controller without polkit, sudo or a password (nothing typed is checked)."""
        attempts = {"n": 0}
        who = os.environ.get("USER", "you")

        def answer(text: str) -> None:
            attempts["n"] += 1
            if mode == "keyboard-retry" and attempts["n"] == 1:
                print("attempt 1: pretending the password was wrong", flush=True)
                ask("Wrong password. Try again.")
                return
            print(f"OK: typed {len(text)} character(s)", flush=True)
            self.quit()

        def cancel() -> None:
            print("cancelled", flush=True)
            self.quit()

        def ask(error: str = "") -> None:
            win.ask_password("Test prompt: nothing here is checked", "Type anything:", mode == "text",
                             "" if mode == "text" else who, error, answer, cancel)

        GLib.timeout_add(300, lambda: (ask(), GLib.SOURCE_REMOVE)[1])

    def do_activate(self) -> None:
        win = self.props.active_window or DeckWindow(self, self.gaming, self.pad)
        win.present()
        test_mode = os.environ.get("BOPPOS_DECK_TEST")
        if test_mode in ("keyboard", "keyboard-retry", "text"):
            self.run_test_mode(win, test_mode)
        # Gaming Mode has no polkit agent of its own; BOPPOS_DECK_AGENT=1 forces it for testing.
        if self.agent is None and (self.gaming or os.environ.get("BOPPOS_DECK_AGENT")):
            self.agent = polkit_agent.start_agent(win.ask_password)

    def do_command_line(self, cmdline: Gio.ApplicationCommandLine) -> int:
        """boppos-deck [--update TARGETS]. A second launch is handed to the window that is already open."""
        opts = cmdline.get_options_dict().end().unpack()
        targets: list[str] | None = None
        if "update" in opts:
            try:
                targets = system.parse_targets(opts["update"])
            except ValueError as exc:
                print(f"boppos-deck: {exc}", file=sys.stderr)
                return 2
        hw = None
        if "hardware" in opts:
            try:
                hw = hwprofiles.parse_profile(opts["hardware"])
            except ValueError as exc:
                print(f"boppos-deck: {exc}", file=sys.stderr)
                return 2
        if "welcome" in opts and "if-needed" in opts and welcome.is_done():
            return 0
        self.activate()
        win = self.props.active_window
        if isinstance(win, DeckWindow):
            if "update" in opts:
                win.start_update(targets)
            if "oobe" in opts:
                win.show_oobe()
            elif "welcome" in opts or hw is not None:  # --hardware alone opens the wizard preview too
                win.show_welcome(hw)
        return 0

    def do_shutdown(self) -> None:
        if self.pad:
            self.pad.close()
        Adw.Application.do_shutdown(self)


def main() -> int:
    return DeckApp().run(sys.argv)
