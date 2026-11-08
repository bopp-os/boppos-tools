"""boppos.ui.agent with gi stubbed out: one request answered exactly once, retries capped, only polkitd obeyed."""

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class Session:
    made = []

    def __init__(self, identity, cookie):
        self.handlers, self.identity, self.cookie = {}, identity, cookie
        self.response, self.cancel, self.initiate = MagicMock(), MagicMock(), MagicMock()
        Session.made.append(self)

    def connect(self, name, fn):
        self.handlers[name] = fn


class UnixUser:
    def __init__(self, uid):
        self.uid = uid

    def get_uid(self):
        return self.uid


@pytest.fixture
def agent(monkeypatch):
    Session.made = []
    UnixUser.new = staticmethod(UnixUser)
    polkit = types.SimpleNamespace(UnixUser=UnixUser)
    repo = types.ModuleType("gi.repository")
    repo.Gio, repo.GLib, repo.Polkit = MagicMock(), MagicMock(), polkit
    repo.PolkitAgent = types.SimpleNamespace(Session=types.SimpleNamespace(new=Session))
    gi = types.ModuleType("gi")
    gi.require_version, gi.repository = lambda *_: None, repo
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    monkeypatch.delitem(sys.modules, "boppos.ui.agent", raising=False)
    monkeypatch.setitem(sys.modules, "boppos.ui", types.ModuleType("boppos.ui"))
    sys.modules["boppos.ui"].__path__ = [str(Path(__file__).parent.parent / "lib/boppos/py/boppos/ui")]
    mod = importlib.import_module("boppos.ui.agent")
    a = mod.DeckAgent(MagicMock())
    a._authority = MagicMock()
    a._authority.get_name_owner.return_value = ":1.2"
    return mod, a


def begin(a, cookie="c", uid=1000):
    params, inv = MagicMock(), MagicMock()
    params.unpack.return_value = ("a", "msg", "", {}, cookie, [("unix-user", {"uid": uid})])
    a._on_call(None, ":1.2", "", "", "BeginAuthentication", params, inv)
    return inv


def test_accepted_answers_once(agent):
    _, a = agent
    inv = begin(a)
    Session.made[0].handlers["completed"](None, True)
    inv.return_value.assert_called_once_with(None)
    inv.return_dbus_error.assert_not_called()
    assert a._active == {}


def test_prompt_reaches_ui_and_answer_reaches_session(agent):
    _, a = agent
    begin(a)
    s = Session.made[0]
    s.handlers["request"](s, "Password:", False)
    args = a._ask.call_args.args
    assert args[0:3] == ("msg", "Password:", False)
    args[5]("secret")
    s.response.assert_called_once_with("secret")


def test_retries_are_capped(agent):
    mod, a = agent
    inv = begin(a)
    for i in range(mod.MAX_ATTEMPTS):
        Session.made[i].handlers["completed"](None, False)
    assert len(Session.made) == mod.MAX_ATTEMPTS
    inv.return_dbus_error.assert_called_once_with(mod.ERROR_FAILED, "authentication failed")


def test_cancel_from_polkitd(agent):
    mod, a = agent
    inv = begin(a)
    params, cancel_inv = MagicMock(), MagicMock()
    params.unpack.return_value = ("c",)
    a._on_call(None, ":1.2", "", "", "CancelAuthentication", params, cancel_inv)
    Session.made[0].cancel.assert_called_once()
    cancel_inv.return_value.assert_called_once_with(None)
    Session.made[0].handlers["completed"](None, False)
    inv.return_dbus_error.assert_called_once_with(mod.ERROR_CANCELLED, "cancelled")


def test_other_senders_are_refused(agent):
    _, a = agent
    params, inv = MagicMock(), MagicMock()
    a._on_call(None, ":1.99", "", "", "BeginAuthentication", params, inv)
    inv.return_dbus_error.assert_called_once()
    assert Session.made == []
