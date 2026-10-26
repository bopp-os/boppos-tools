"""boppos-deck window and application: sidebar + page panel, progress view, controller + keyboard."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk  # noqa: E402

from . import backend  # noqa: E402
from .controller import DIRECTIONS, Action, InputMapper, Sdl3Gamepads  # noqa: E402
from .pages import ALL_PAGES  # noqa: E402

APP_ID = "org.boppos.Deck"
DIRECTION_MAP = {
    Action.UP: Gtk.DirectionType.UP,
    Action.DOWN: Gtk.DirectionType.DOWN,
    Action.LEFT: Gtk.DirectionType.LEFT,
    Action.RIGHT: Gtk.DirectionType.RIGHT,
}
HINTS = {
    "pad": [("A", "Select"), ("B", "Back"), ("LB / RB", "Page")],
    "kb": [("Enter", "Select"), ("Esc", "Back"), ("Ctrl+Tab", "Page")],
}


def on_main(fn: Callable[[], None]) -> None:
    GLib.idle_add(lambda: (fn(), GLib.SOURCE_REMOVE)[1])


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
        self.input_mode = "pad" if gaming else "kb"
        self.pad, self.mapper = pad, InputMapper()

        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.build_main(), "main")
        self.stack.add_named(self.build_progress(), "progress")
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
            focus.connect("enter", lambda _c, idx=i: self.select_page(idx, move_focus=False))
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
        self.p_log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                  wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.p_log.add_css_class("progress-log")
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
        for w in (self.p_title, self.p_status, self.p_spinner, self.p_expander, actions):
            box.append(w)
        return box

    def build_hints(self) -> Gtk.Widget:
        self.hint_box = Gtk.Box(spacing=0)
        self.hint_box.add_css_class("hint-bar")
        self.render_hints()
        return self.hint_box

    def render_hints(self) -> None:
        child = self.hint_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.hint_box.remove(child)
            child = nxt
        for key, label in HINTS[self.input_mode]:
            k = Gtk.Label(label=key)
            k.add_css_class("hint-key")
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
        if page.needs_refresh():
            page.refresh()
        if move_focus:
            self.sidebar_rows[idx].grab_focus()

    def enter_content(self, idx: int | None = None) -> None:
        if idx is not None and idx != self.current_index:
            self.select_page(idx, move_focus=False)
        if self.split.get_collapsed():
            self.split.set_show_content(True)
        page = self.pages[self.current_index]
        if not page.widget.child_focus(Gtk.DirectionType.TAB_FORWARD):
            page.widget.grab_focus()

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
            res = fn()
            on_main(lambda: cb(res))

        threading.Thread(target=work, daemon=True).start()

    def toast(self, text: str) -> None:
        t = Adw.Toast.new(text)
        t.set_timeout(5)
        self.toasts.add_toast(t)

    def confirm(self, heading: str, body: str, label: str, on_ok: Callable[[], None],
                destructive: bool = False) -> None:
        dlg = Adw.AlertDialog.new(heading, body)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", label)
        if destructive:
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        else:
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        self.dialog = dlg

        def finished(d, res):
            self.dialog = None
            if d.choose_finish(res) == "ok":
                on_ok()

        dlg.choose(self, None, finished)

    def confirm_quit(self) -> None:
        self.confirm("Exit BoppOS settings?", "You will return to Steam.", "Exit", self.get_application().quit)

    # --- progress view ------------------------------------------------------------------------

    def run_job(self, title: str, cmds: list[list[str]], on_done: Callable[[bool, dict], None]) -> None:
        if self.job and self.job.running:
            self.toast("Another action is still running")
            return
        self.p_title.set_label(title)
        self.p_status.set_label("Working…")
        self.p_log.get_buffer().set_text("")
        self.p_spinner.set_spinning(True)
        self.p_spinner.set_visible(True)
        self.p_done.set_sensitive(False)
        self.p_reboot.set_visible(False)
        self.stack.set_visible_child_name("progress")
        self.p_done.grab_focus()
        self._on_done = on_done

        def on_event(ev: dict) -> None:
            if ev["event"] == "log":
                buf = self.p_log.get_buffer()
                buf.insert(buf.get_end_iter(), ev["line"] + "\n")
                adj = self.p_log_scroll.get_vadjustment()
                adj.set_value(adj.get_upper())
            elif ev["event"] == "step":
                self.p_status.set_label(f"{ev['id']}: {ev['status']}")

        def on_finish(ok: bool, final: dict) -> None:
            self.p_spinner.set_spinning(False)
            self.p_spinner.set_visible(False)
            self.p_done.set_sensitive(True)
            self.p_done.grab_focus()
            msg = "Done." if ok else "Something went wrong. Open Details for the log."
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
        if mode != self.input_mode:
            self.input_mode = mode
            self.render_hints()

    def poll_pad(self) -> bool:
        now = time.monotonic()
        actions = []
        for ev in self.pad.poll():
            actions += self.mapper.feed(ev, now)
        actions += self.mapper.tick(now)
        if actions:
            self.set_input_mode("pad")
        for a in actions:
            self.handle_action(a)
        return GLib.SOURCE_CONTINUE

    def on_key(self, _ctl, keyval, _code, state) -> bool:
        self.set_input_mode("kb")
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if keyval == Gdk.KEY_Escape and not self.dialog:
            self.handle_action(Action.BACK)
            return True
        if ctrl and keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            back = keyval in (Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Page_Up) or (keyval == Gdk.KEY_Tab and shift)
            self.handle_action(Action.PREV if back else Action.NEXT)
            return True
        return False

    def handle_action(self, a: Action) -> None:
        if self.stack.get_visible_child_name() == "progress":
            if a == Action.ACCEPT and self.p_done.get_sensitive():
                self.p_done.activate()
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
                    f.activate()
            elif a in DIRECTIONS:
                self.child_focus(DIRECTION_MAP[a])
            return
        if a in DIRECTIONS:
            if self.focus_in_sidebar() and a == Action.RIGHT:
                self.enter_content()
            else:
                self.child_focus(DIRECTION_MAP[a])
        elif a == Action.ACCEPT:
            f = self.get_focus()
            if f is None:
                self.select_page(self.current_index)
            elif self.focus_in_sidebar():
                self.enter_content()
            else:
                f.activate()
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

    def on_close_request(self, *_a) -> bool:
        if self.job and self.job.running:
            self.toast("An action is still running")
            return True
        return False


class DeckApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self.gaming = backend.gaming_mode()
        self.pad: Sdl3Gamepads | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).with_name("style.css")))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        try:
            self.pad = Sdl3Gamepads()
        except OSError as exc:
            print(f"boppos-deck: no gamepad support ({exc}); keyboard and mouse only", file=sys.stderr)

    def do_activate(self) -> None:
        win = self.props.active_window or DeckWindow(self, self.gaming, self.pad)
        win.present()

    def do_shutdown(self) -> None:
        if self.pad:
            self.pad.close()
        Adw.Application.do_shutdown(self)


def main() -> int:
    return DeckApp().run(sys.argv)
