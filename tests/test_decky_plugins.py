import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest
from boppos import decky_cli, decky_plugins
from boppos.decky import DeckyError

from .test_decky import load_helper

REPO = Path(__file__).resolve().parents[1]


def make_zip(path: Path, members, symlink=None, device=None) -> Path:
    """members: {name: bytes or None (folder)}."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            if data is None:
                zf.writestr(zipfile.ZipInfo(name if name.endswith("/") else name + "/"), b"")
            else:
                zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink[0])
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, symlink[1])
        if device:
            info = zipfile.ZipInfo(device)
            info.external_attr = (stat.S_IFCHR | 0o600) << 16
            zf.writestr(info, b"")
    return path


def good_members(version="1.0.0", flags=None):
    return {
        "Foo/": None,
        "Foo/plugin.json": json.dumps({"name": "Foo", "flags": flags or []}).encode(),
        "Foo/package.json": json.dumps({"version": version}).encode(),
        "Foo/dist/index.js": b"js",
        "Foo/bin/tool": b"#!/bin/sh\n",
    }


def pin_for(path: Path, **over) -> dict:
    data = path.read_bytes()
    entry = {"id": "foo", "name": "Foo", "repo": "o/foo", "version": "1.0.0", "url": "https://example.invalid/f.zip",
             "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "dir": "Foo"}
    entry.update(over)
    return decky_plugins.parse_pin(entry)


# --- Pins -------------------------------------------------------------------------------------


def test_shipped_list_parses_and_pins_mako():
    pins = decky_plugins.load_pins(REPO / "share/boppos/decky-plugins.json")
    mako = decky_plugins.find_pin("mako", pins)
    assert mako["dir"] == "Mako" and not mako["allow_root"]
    assert mako["url"].startswith("https://github.com/eugeniosegala/MAKO/releases/download/plugin-v")
    assert mako["url"].endswith(mako["asset"].format(version=mako["version"]))
    assert decky_plugins.default_pins_path() == REPO / "share/boppos/decky-plugins.json"


@pytest.mark.parametrize("over, msg", [
    ({"url": "http://x/f.zip"}, "https"),
    ({"sha256": "abc"}, "sha256"),
    ({"size": 0}, "size"),
    ({"size": True}, "size"),
    ({"dir": "../etc"}, "folder"),
    ({"dir": ".."}, "folder"),
    ({"id": "Foo Bar"}, "bad id"),
    ({"repo": "nope"}, "repo"),
    ({"version": "1.0/../x"}, "version"),
])
def test_bad_pins_are_refused(tmp_path, over, msg):
    z = make_zip(tmp_path / "f.zip", good_members())
    with pytest.raises(DeckyError, match=msg):
        pin_for(z, **over)


def test_list_rejects_duplicates_and_wrong_schema(tmp_path):
    z = make_zip(tmp_path / "f.zip", good_members())
    e = pin_for(z)
    f = tmp_path / "pins.json"
    f.write_text(json.dumps({"schema": 1, "plugins": [e, dict(e, id="other")]}))
    with pytest.raises(DeckyError, match="duplicate dir"):
        decky_plugins.load_pins(f)
    f.write_text(json.dumps({"schema": 2, "plugins": []}))
    with pytest.raises(DeckyError, match="schema"):
        decky_plugins.load_pins(f)


def test_find_pin_takes_only_listed_ids():
    pins = decky_plugins.load_pins()
    for bad in ("https://evil/x.zip", "../mako", "/tmp/x", "nope"):
        with pytest.raises(DeckyError):
            decky_plugins.find_pin(bad, pins)


# --- Download checks --------------------------------------------------------------------------


class FakeOpener:
    def __init__(self, data):
        self.data = data

    def open(self, req, timeout=None):
        return io.BytesIO(self.data)


def test_download_verifies_size_and_checksum(tmp_path):
    z = make_zip(tmp_path / "f.zip", good_members())
    pin = pin_for(z)
    seen = []
    decky_plugins.download(pin, tmp_path / "ok.zip", seen.append, FakeOpener(z.read_bytes()))
    assert seen[-1] == 100
    with pytest.raises(DeckyError, match="checksum mismatch"):
        bad = bytearray(z.read_bytes())
        bad[-1] ^= 1
        decky_plugins.download(pin, tmp_path / "bad.zip", None, FakeOpener(bytes(bad)))
    with pytest.raises(DeckyError, match="larger"):
        decky_plugins.download(pin, tmp_path / "big.zip", None, FakeOpener(z.read_bytes() + b"x"))
    with pytest.raises(DeckyError, match="size mismatch"):
        decky_plugins.download(pin, tmp_path / "short.zip", None, FakeOpener(z.read_bytes()[:-1]))


def test_redirect_to_http_is_refused():
    h = decky_plugins._HttpsOnly()
    with pytest.raises(DeckyError, match="redirect"):
        h.redirect_request(None, None, 302, "", {}, "http://evil/x.zip")


# --- Extraction checks ------------------------------------------------------------------------


def extract(tmp_path, members, **kw):
    z = make_zip(tmp_path / "p.zip", members, **kw)
    stage = tmp_path / "stage"
    stage.mkdir()
    return decky_plugins.extract_plugin(z, stage, pin_for(z))


def test_good_zip_extracts_with_sane_modes(tmp_path):
    root = extract(tmp_path, good_members())
    assert (root / "dist/index.js").read_bytes() == b"js"
    assert stat.S_IMODE((root / "plugin.json").stat().st_mode) == 0o644
    assert stat.S_IMODE((root / "dist").stat().st_mode) == 0o755


@pytest.mark.parametrize("name", ["../evil", "Foo/../../evil", "/etc/evil", "Foo/..\\..\\evil", "C:/evil"])
def test_traversal_is_refused(tmp_path, name):
    m = good_members()
    m[name] = b"x"
    with pytest.raises(DeckyError, match="refused"):
        extract(tmp_path, m)
    assert not (tmp_path / "evil").exists()


def test_symlink_is_refused(tmp_path):
    with pytest.raises(DeckyError, match="symlink"):
        extract(tmp_path, good_members(), symlink=("Foo/link", b"/etc/shadow"))


def test_device_is_refused(tmp_path):
    with pytest.raises(DeckyError, match="device"):
        extract(tmp_path, good_members(), device="Foo/dev")


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_hardlink_style_duplicate_is_refused(tmp_path):
    z = tmp_path / "p.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, data in good_members().items():
            if data is not None:
                zf.writestr(name, data)
        zf.writestr("Foo/dist/index.js", b"again")  # zip has no hardlinks; a repeated name is the closest thing
    stage = tmp_path / "s"
    stage.mkdir()
    with pytest.raises(DeckyError, match="twice"):
        decky_plugins.extract_plugin(z, stage, pin_for(z))


@pytest.mark.parametrize("members, msg", [
    ({"Foo/plugin.json": b"{}", "Bar/x": b"x"}, "one top-level"),
    ({"Foo/plugin.json": b"{}", "loose.txt": b"x"}, "outside"),
    ({"Foo/x": b"x"}, "no plugin.json"),
    ({"Other/plugin.json": b"{}"}, "the pin says"),
])
def test_layout_is_checked(tmp_path, members, msg):
    with pytest.raises(DeckyError, match=msg):
        extract(tmp_path, members)


def test_oversize_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(decky_plugins, "unpacked_cap", lambda size: 1000)
    m = good_members()
    m["Foo/big.bin"] = b"\0" * 5000  # compresses to almost nothing: a tiny ZIP bomb
    with pytest.raises(DeckyError, match="more than 1000 bytes"):
        extract(tmp_path, m)


def test_root_flag_is_refused_unless_allowed(tmp_path):
    z = make_zip(tmp_path / "p.zip", good_members(flags=["root", "debug"]))
    root = decky_plugins.extract_plugin(z, tmp_path, pin_for(z))
    with pytest.raises(DeckyError, match="as root"):
        decky_plugins.check_plugin(root, pin_for(z))
    decky_plugins.check_plugin(root, pin_for(z, allow_root=True))


def test_version_must_match_pin(tmp_path):
    z = make_zip(tmp_path / "p.zip", good_members(version="2.0.0"))
    root = decky_plugins.extract_plugin(z, tmp_path, pin_for(z))
    with pytest.raises(DeckyError, match="version 2.0.0"):
        decky_plugins.check_plugin(root, pin_for(z))


# --- Install, update, remove, rollback ---------------------------------------------------------


def test_install_update_remove_rollback(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    z1 = make_zip(tmp_path / "v1.zip", good_members("1.0.0"))
    z2 = make_zip(tmp_path / "v2.zip", good_members("2.0.0"))
    p1, p2 = pin_for(z1), pin_for(z2, version="2.0.0")
    log = []
    assert decky_plugins.install_from_zip(z1, state, p1, log.append) is None
    assert decky_plugins.status([p1], state)[0]["state"] == "up to date"
    assert decky_plugins.status([p2], state)[0]["state"] == "update available"
    assert decky_plugins.install_from_zip(z2, state, p2, log.append) == "1.0.0"
    assert decky_plugins.plugin_version(state / "plugins/Foo") == "2.0.0"
    assert decky_plugins.plugin_version(state / "plugin-backups/Foo") == "1.0.0"
    assert sorted(p.name for p in state.iterdir()) == ["plugin-backups", "plugins"]  # staging cleaned up
    assert decky_plugins.rollback(state, p2, log.append) == "1.0.0"
    assert decky_plugins.plugin_version(state / "plugins/Foo") == "1.0.0"
    assert decky_plugins.plugin_version(state / "plugin-backups/Foo") == "2.0.0"
    assert decky_plugins.remove(state, p1, log.append)
    assert not (state / "plugins/Foo").exists()
    assert decky_plugins.status([p1], state)[0]["state"] == "not installed"
    assert not decky_plugins.remove(state, p1, log.append)


def test_failed_check_leaves_installed_copy(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    z1 = make_zip(tmp_path / "v1.zip", good_members("1.0.0"))
    decky_plugins.install_from_zip(z1, state, pin_for(z1), print)
    bad = make_zip(tmp_path / "bad.zip", good_members("1.0.0", flags=["root"]))
    with pytest.raises(DeckyError, match="root"):
        decky_plugins.install_from_zip(bad, state, pin_for(bad), print)
    assert json.loads((state / "plugins/Foo/plugin.json").read_text())["flags"] == []
    assert sorted(p.name for p in state.iterdir()) == ["plugin-backups", "plugins"]


# --- Helper and CLI ---------------------------------------------------------------------------


def test_helper_refuses_urls_and_unknown_ids(monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(helper.pwd, "getpwuid", lambda uid: type("P", (), {"pw_name": "deck"})())
    called = []
    monkeypatch.setattr(helper, "plugin_install", lambda pw, pin: called.append(pin))

    def run(*args):
        monkeypatch.setenv("PKEXEC_UID", "1000")  # main() clears the environment
        return helper.main(list(args))

    assert run("plugin-install", "https://evil/x.zip") == 1
    assert run("plugin-install", "nope") == 1
    assert run("plugin-install") == 2
    assert run("plugin-install", "mako", "extra") == 2
    assert run("install", "mako") == 2
    assert not called
    assert run("plugin-install", "mako") == 0
    assert called[0]["dir"] == "Mako"
    assert helper.PINS == REPO / "share/boppos/decky-plugins.json"


def fake_status(state="not installed", enabled=True):
    return lambda: {"decky_enabled": enabled, "plugins": [
        {"id": "foo", "name": "Foo", "pinned": "2", "installed": None if state == "not installed" else "1",
         "state": state, "after_install": "Press the button"}]}


def test_cli_plugin_actions():
    cmds, events = [], []

    def stream(cmd, emit):
        cmds.append(cmd[-2:])
        return 0

    r = decky_cli.do_plugin("install", "foo", events.append, fake_status(), stream)
    assert cmds == [["plugin-install", "foo"]] and r["after_install"] == "Press the button"
    r = decky_cli.do_plugin("update", "foo", events.append, fake_status("update available"), stream)
    assert cmds[-1] == ["plugin-install", "foo"]
    r = decky_cli.do_plugin("install", "foo", events.append, fake_status("up to date"), stream)
    assert r["changed"] is False and len(cmds) == 2
    with pytest.raises(DeckyError, match="not installed"):
        decky_cli.do_plugin("update", "foo", events.append, fake_status(), stream)
    with pytest.raises(DeckyError, match="not turned on"):
        decky_cli.do_plugin("install", "foo", events.append, fake_status(enabled=False), stream)
    with pytest.raises(DeckyError, match="not in the BoppOS"):
        decky_cli.do_plugin("install", "bar", events.append, fake_status(), stream)
    decky_cli.do_plugin("remove", "foo", events.append, fake_status("up to date"), stream)
    assert cmds[-1] == ["plugin-remove", "foo"]
    with pytest.raises(DeckyError, match="failed"):
        decky_cli.do_plugin("remove", "foo", events.append, fake_status("up to date"), lambda c, e: 1)
