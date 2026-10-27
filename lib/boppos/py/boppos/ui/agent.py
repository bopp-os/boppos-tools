"""Polkit authentication agent for Gaming Mode, where no other agent runs.

It registers for this login session and forwards password requests to the window's gamepad keyboard.
When the Polkit typelibs are missing, or another agent (KDE, GNOME...) already owns the session,
registration fails quietly and password-protected actions behave as they did before.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

import gi

try:
    gi.require_version("Polkit", "1.0")
    gi.require_version("PolkitAgent", "1.0")
    from gi.repository import Gio, GLib, Polkit, PolkitAgent

    AVAILABLE = True
except (ValueError, ImportError):  # typelibs not installed
    AVAILABLE = False

OBJECT_PATH = "/org/boppos/deck/PolkitAgent"
MAX_ATTEMPTS = 3

# ask(message, prompt, echo, user, error, answer, cancel): show the prompt and call answer(text) or cancel()
AskFn = Callable[[str, str, bool, str, str, Callable[[str], None], Callable[[], None]], None]


def pick_identity(identities, uid: int | None = None):
    """Prefer the calling user among the identities that may authorize; else the first user."""
    uid = os.getuid() if uid is None else uid
    users = [i for i in identities if AVAILABLE and isinstance(i, Polkit.UnixUser)]
    for i in users:
        if i.get_uid() == uid:
            return i
    return users[0] if users else None


def identity_name(identity) -> str:
    try:
        import pwd

        return pwd.getpwuid(identity.get_uid()).pw_name
    except (KeyError, AttributeError):
        return "administrator"


if AVAILABLE:

    class DeckAgent(PolkitAgent.Listener):
        def __init__(self, ask: AskFn):
            super().__init__()
            self._ask = ask
            self._handle = None

        # GIO-style async virtual functions: start the work, finish through a Gio.Task.
        def do_initiate_authorization(self, action_id, message, icon_name, details, cookie, identities,
                                      cancellable, callback, *rest):
            task = Gio.Task.new(self, cancellable, callback, None)
            identity = pick_identity(identities)
            if identity is None:
                task.return_error(GLib.Error.new_literal(Gio.io_error_quark(), "no usable identity",
                                                         Gio.IOErrorEnum.FAILED))
                return
            self._attempt(task, message, cookie, identity, cancellable, 0, "")

        def do_initiate_authorization_finish(self, result):
            return result.propagate_boolean()

        def _attempt(self, task, message, cookie, identity, cancellable, attempt, error):
            session = PolkitAgent.Session.new(identity, cookie)
            state = {"cancelled": False, "error": error}

            def on_request(sess, prompt, echo_on):
                def answer(text):
                    sess.response(text)

                def cancel():
                    state["cancelled"] = True
                    sess.cancel()

                self._ask(message, prompt, echo_on, identity_name(identity), state["error"], answer, cancel)

            def on_show_error(_sess, text):
                state["error"] = text
                print(f"boppos-deck: polkit says: {text}", file=sys.stderr)

            def on_show_info(_sess, text):
                print(f"boppos-deck: polkit info: {text}", file=sys.stderr)

            def on_completed(_sess, gained):
                who = identity_name(identity)
                print(f"boppos-deck: authentication as {who}: {'accepted' if gained else 'REFUSED'} "
                      f"(attempt {attempt + 1} of {MAX_ATTEMPTS})", file=sys.stderr)
                if gained:
                    task.return_boolean(True)
                elif state["cancelled"] or (cancellable and cancellable.is_cancelled()):
                    task.return_error(GLib.Error.new_literal(Gio.io_error_quark(), "cancelled",
                                                             Gio.IOErrorEnum.CANCELLED))
                elif attempt + 1 < MAX_ATTEMPTS:
                    # Show polkit's own reason when it gave one (a locked account reads very differently from
                    # a typo), not just a generic message.
                    reason = state["error"].strip() or "Wrong password."
                    self._attempt(task, message, cookie, identity, cancellable, attempt + 1,
                                  f"{reason} Try again.")
                else:
                    task.return_error(GLib.Error.new_literal(Gio.io_error_quark(), "authentication failed",
                                                             Gio.IOErrorEnum.FAILED))

            session.connect("request", on_request)
            session.connect("show-error", on_show_error)
            session.connect("show-info", on_show_info)
            session.connect("completed", on_completed)
            if cancellable:
                cancellable.connect(lambda *_: session.cancel())
            session.initiate()

        def register_for_session(self) -> bool:
            try:
                subject = Polkit.UnixSession.new_for_process_sync(os.getpid(), None)
                self._handle = self.register(PolkitAgent.RegisterFlags.NONE, subject, OBJECT_PATH, None)
                return True
            except Exception as exc:  # GLib.Error when another agent is registered, TypeError on odd bindings
                print(f"boppos-deck: polkit agent not registered ({exc})", file=sys.stderr)
                return False


def start_agent(ask: AskFn):
    """Create and register the agent. Returns it (keep a reference) or None."""
    if not AVAILABLE:
        print("boppos-deck: Polkit typelibs missing; password prompts need another agent", file=sys.stderr)
        return None
    agent = DeckAgent(ask)
    return agent if agent.register_for_session() else None
