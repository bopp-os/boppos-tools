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
POLKIT_NAME = "org.freedesktop.PolicyKit1"
POLKIT_PATH = "/org/freedesktop/PolicyKit1/Authority"
POLKIT_IFACE = "org.freedesktop.PolicyKit1.Authority"
ERROR_CANCELLED = "org.freedesktop.PolicyKit1.Error.Cancelled"
ERROR_FAILED = "org.freedesktop.PolicyKit1.Error.Failed"
AGENT_XML = """<node><interface name="org.freedesktop.PolicyKit1.AuthenticationAgent">
  <method name="BeginAuthentication">
    <arg type="s" name="action_id" direction="in"/><arg type="s" name="message" direction="in"/>
    <arg type="s" name="icon_name" direction="in"/><arg type="a{ss}" name="details" direction="in"/>
    <arg type="s" name="cookie" direction="in"/><arg type="a(sa{sv})" name="identities" direction="in"/>
  </method>
  <method name="CancelAuthentication"><arg type="s" name="cookie" direction="in"/></method>
</interface></node>"""
# Two, not three: pam_faillock locks the whole account (sudo and login too) after 3 failures in a row, so a
# third attempt from here could lock someone out. Fewer tries and a clean stop are better than that.
MAX_ATTEMPTS = 2

# ask(message, prompt, echo, user, error, answer, cancel): show the prompt and call answer(text) or cancel()
AskFn = Callable[[str, str, bool, str, str, Callable[[str], None], Callable[[], None]], None]


def parse_identities(raw) -> list:
    """Polkit identities from BeginAuthentication's a(sa{sv}). polkitd has already expanded groups into users."""
    return [Polkit.UnixUser.new(details["uid"]) for kind, details in raw if kind == "unix-user" and "uid" in details]


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


class DeckAgent:
    """polkit's authentication agent D-Bus interface, implemented directly.

    Not a PolkitAgent.Listener subclass: that means overriding a C async virtual function from Python. Each request's
    D-Bus invocation is answered when its authentication ends; PolkitAgent.Session talks to the setuid helper.
    """

    def __init__(self, ask: AskFn):
        self._ask = ask
        self._active: dict[str, dict] = {}  # cookie -> request; also keeps each Session alive
        self._authority = None

    def _on_call(self, _conn, sender, _path, _iface, method, params, invocation):
        if sender != self._authority.get_name_owner():  # only polkitd may ask for a password
            invocation.return_dbus_error(ERROR_FAILED, "not polkitd")
        elif method == "BeginAuthentication":
            self._begin(params.unpack(), invocation)
        else:  # CancelAuthentication
            self._cancel(params.unpack()[0])
            invocation.return_value(None)

    def _begin(self, args, invocation):
        _action_id, message, _icon, _details, cookie, raw_identities = args
        identity = pick_identity(parse_identities(raw_identities))
        if identity is None:
            invocation.return_dbus_error(ERROR_FAILED, "no usable identity")
            return
        request = {"invocation": invocation, "message": message, "cookie": cookie, "identity": identity,
                   "cancelled": False, "session": None}
        self._active[cookie] = request
        self._attempt(request, 0, "")

    def _cancel(self, cookie):
        request = self._active.get(cookie)
        if request:
            request["cancelled"] = True
            request["session"].cancel()

    def _finish(self, request, error=None):
        self._active.pop(request["cookie"], None)
        if error:
            request["invocation"].return_dbus_error(*error)
        else:
            request["invocation"].return_value(None)

    def _attempt(self, request, attempt, error):
        identity = request["identity"]
        session = PolkitAgent.Session.new(identity, request["cookie"])
        request["session"] = session
        state = {"error": error}

        def on_request(sess, prompt, echo_on):
            def cancel():
                request["cancelled"] = True
                sess.cancel()

            self._ask(request["message"], prompt, echo_on, identity_name(identity), state["error"], sess.response,
                      cancel)

        def on_show_error(_sess, text):
            state["error"] = text
            print(f"boppos-deck: polkit says: {text}", file=sys.stderr)

        def on_show_info(_sess, text):
            print(f"boppos-deck: polkit info: {text}", file=sys.stderr)

        def on_completed(_sess, gained):
            print(f"boppos-deck: authentication as {identity_name(identity)}: {'accepted' if gained else 'REFUSED'} "
                  f"(attempt {attempt + 1} of {MAX_ATTEMPTS})", file=sys.stderr)
            if gained:
                self._finish(request)
            elif request["cancelled"]:
                self._finish(request, (ERROR_CANCELLED, "cancelled"))
            elif attempt + 1 < MAX_ATTEMPTS:
                # Show polkit's own reason when it gave one (a locked account reads very differently from
                # a typo), not just a generic message.
                reason = state["error"].strip() or "Wrong password."
                self._attempt(request, attempt + 1, f"{reason} Try again.")
            else:
                self._finish(request, (ERROR_FAILED, "authentication failed"))

        session.connect("request", on_request)
        session.connect("show-error", on_show_error)
        session.connect("show-info", on_show_info)
        session.connect("completed", on_completed)
        session.initiate()

    def register_for_session(self) -> bool:
        try:
            conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._authority = Gio.DBusProxy.new_sync(conn, Gio.DBusProxyFlags.NONE, None, POLKIT_NAME, POLKIT_PATH,
                                                     POLKIT_IFACE, None)
            info = Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0]
            conn.register_object(OBJECT_PATH, info, self._on_call, None, None)
            session_id = Polkit.UnixSession.new_for_process_sync(os.getpid(), None).get_session_id()
            subject = ("unix-session", {"session-id": GLib.Variant("s", session_id)})
            locale = os.environ.get("LANG") or "C"
            self._authority.call_sync("RegisterAuthenticationAgent",
                                      GLib.Variant("((sa{sv})ss)", (subject, locale, OBJECT_PATH)),
                                      Gio.DBusCallFlags.NONE, -1, None)
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
