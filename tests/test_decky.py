import hashlib
import json
import os

import pytest
from boppos import decky, decky_cli
from boppos.decky import DeckyError

DIGEST = "sha256:" + "a" * 64
URL = "https://github.com/SteamDeckHomebrew/decky-loader/releases/download/v3.3.0/PluginLoader"


def release(tag="v3.3.0", pre=False, name="PluginLoader", digest=DIGEST, url=URL):
    return {"tag_name": tag, "prerelease": pre,
            "assets": [{"name": name, "digest": digest, "browser_download_url": url, "size": 5}]}


def test_pick_release_skips_prereleases_and_other_assets():
    raw = json.dumps([release("v3.4.0-pre1", pre=True), release()])
    rel = decky.pick_release(raw)
    assert rel["version"] == "v3.3.0" and rel["sha256"] == "a" * 64
    assert decky.pick_release(json.dumps([release(name="PluginLoader-arm64")]), "aarch64")["version"] == "v3.3.0"


@pytest.mark.parametrize("bad", [
    release(digest=""), release(digest="md5:abc"), release(url="http://github.com/x"),
    release(url="https://evil.example/PluginLoader"), release(tag="v1; rm -rf /"),
])
def test_pick_release_refuses_unverifiable(bad):
    with pytest.raises(DeckyError):
        decky.pick_release(json.dumps([bad]))


def test_pick_release_no_match():
    with pytest.raises(DeckyError):
        decky.pick_release(json.dumps([release(name="PluginLoader-mac")]))
    with pytest.raises(DeckyError):
        decky.pick_release("not json")


def test_unit_text_uses_home_only():
    text = decky.unit_text("/home/deck")
    assert "ExecStart=/home/deck/homebrew/services/PluginLoader" in text
    assert "UNPRIVILEGED_PATH=/home/deck/homebrew" in text


@pytest.mark.parametrize("home", ["/home/a b", "/home/x\nExecStartPre=/bin/sh", "relative", "/home/../etc", "/h;x"])
def test_unit_text_rejects_hostile_home(home):
    with pytest.raises(DeckyError):
        decky.unit_text(home)


def test_check_binary(tmp_path):
    svc = tmp_path / "homebrew/services"
    svc.mkdir(parents=True)
    binary = svc / "PluginLoader"
    binary.write_text("x")
    binary.chmod(0o755)
    uid = os.getuid()
    decky.check_binary(binary, uid)
    with pytest.raises(DeckyError):
        decky.check_binary(binary, uid + 1)  # owned by someone else
    binary.chmod(0o775)
    with pytest.raises(DeckyError):
        decky.check_binary(binary, uid)  # group writable
    binary.chmod(0o755)
    link = svc / "link"
    link.symlink_to(binary)
    with pytest.raises(DeckyError):
        decky.check_binary(link, uid)  # symlink
    svc.chmod(0o777)
    with pytest.raises(DeckyError):
        decky.check_binary(binary, uid)  # parent writable by others
    svc.chmod(0o755)


def test_download_verifies_digest(tmp_path, monkeypatch):
    payload = b"binary"

    class Resp:
        def __init__(self):
            self._d = [payload, b""]

        def read(self, n=-1):
            return self._d.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
    dest = tmp_path / "s/PluginLoader"
    decky_cli.download("https://x/y", dest, hashlib.sha256(payload).hexdigest(), lambda e: None)
    assert dest.read_bytes() == payload and os.access(dest, os.X_OK)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
    bad = tmp_path / "s/Other"
    with pytest.raises(DeckyError):
        decky_cli.download("https://x/y", bad, "0" * 64, lambda e: None)
    assert not bad.exists() and not (tmp_path / "s/Other.part").exists()
    with pytest.raises(DeckyError):
        decky_cli.download("http://x/y", bad, "0" * 64, lambda e: None)


def test_status_without_install(tmp_path):
    class R:
        stdout = "inactive\n"

    st = decky_cli.read_status(tmp_path, run=lambda *a, **k: R())
    assert st["installed"] is False and st["version"] is None and not st["service_active"]
