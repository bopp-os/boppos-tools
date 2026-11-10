import importlib.machinery
import importlib.util
import io
import os
import pwd
import stat
import tarfile
from pathlib import Path

import pytest
from boppos import decky, decky_cli
from boppos.decky import DeckyError

HELPER = Path(__file__).resolve().parents[1] / "libexec/boppos/decky-helper"


def load_helper():
    loader = importlib.machinery.SourceFileLoader("decky_helper", str(HELPER))
    spec = importlib.util.spec_from_loader("decky_helper", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def legacy_home(home: Path) -> Path:
    hb = home / "homebrew"
    (hb / "plugins/Foo/dist").mkdir(parents=True)
    (hb / "plugins/Foo/plugin.json").write_text('{"name": "Foo"}')
    (hb / "plugins/Foo/dist/index.js").write_text("js")
    (hb / "settings").mkdir()
    (hb / "settings/loader.json").write_text("{}")
    (hb / "data/Foo").mkdir(parents=True)
    (hb / "data/Foo/state.json").write_text("data")
    (hb / "logs/Foo").mkdir(parents=True)
    (hb / "logs/Foo/1.log").write_text("log")
    (hb / "services").mkdir()
    (hb / "services/PluginLoader").write_text("elf")
    (hb / "services/.loader.version").write_text("v3.0.0\n")
    return hb


@pytest.mark.parametrize("name", ["deck", "a_b-1", "_x"])
def test_unit_name(name):
    assert decky.unit_name(name) == f"decky-loader@{name}.service"
    assert decky.state_dir(name) == Path("/var/lib/decky-loader") / name


@pytest.mark.parametrize("name", ["", "Deck", "a b", "../x", "x/y", "x\nExecStart=/bin/sh", "a" * 40, "-x"])
def test_unit_name_rejects_odd_users(name):
    with pytest.raises(DeckyError):
        decky.unit_name(name)


def test_copy_tree_skips_links_and_specials(tmp_path):
    hb = legacy_home(tmp_path / "home")
    secret = tmp_path / "secret"
    secret.write_text("root only")
    (hb / "data/Foo/link").symlink_to(secret)
    (hb / "data/linkdir").symlink_to(tmp_path)
    os.mkfifo(hb / "data/Foo/fifo")
    (hb / "logs/Foo/1.log").chmod(0o4777)
    out = tmp_path / "out"
    out.mkdir()
    assert decky.copy_tree(hb, decky.UNPRIVILEGED_TOPS, out) == 2
    assert (out / "data/Foo/state.json").read_text() == "data"
    assert not any(p.is_symlink() for p in out.rglob("*"))
    assert not (out / "data/Foo/fifo").exists() and not (out / "data/Foo/link").exists()
    assert stat.S_IMODE((out / "logs/Foo/1.log").stat().st_mode) == 0o755  # setuid and g/o write dropped


def test_top_that_is_a_symlink_is_skipped(tmp_path):
    hb = tmp_path / "homebrew"
    hb.mkdir()
    (hb / "plugins").symlink_to(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    assert decky.copy_tree(hb, decky.PRIVILEGED_TOPS, out) == 0
    assert list(out.iterdir()) == []


def test_tar_round_trip(tmp_path):
    hb = legacy_home(tmp_path / "home")
    (hb / "plugins/Foo/evil").symlink_to("/etc/shadow")
    buf = io.BytesIO()
    decky.write_tar(hb, decky.PRIVILEGED_TOPS, buf)
    buf.seek(0)
    out = tmp_path / "out"
    out.mkdir()
    assert decky.extract_tar(buf, decky.PRIVILEGED_TOPS, out) == 3
    assert (out / "plugins/Foo/dist/index.js").read_text() == "js"
    assert (out / "settings/loader.json").exists()
    assert not (out / "plugins/Foo/evil").exists() and not (out / "data").exists()


def _tar(*members) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    buf.seek(0)
    return buf


def _member(name, kind=tarfile.REGTYPE, data=b"x", linkname=""):
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, linkname
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    return info, data if kind == tarfile.REGTYPE else None


@pytest.mark.parametrize("member", [
    _member("../etc/passwd"), _member("/etc/passwd"), _member("plugins/../../x"), _member("data/x"),
    _member("plugins//x"), _member("plugins/l", tarfile.SYMTYPE, linkname="/etc/shadow"),
    _member("plugins/h", tarfile.LNKTYPE, linkname="plugins/x"), _member("plugins/d", tarfile.CHRTYPE),
])
def test_extract_tar_refuses_hostile_members(tmp_path, member):
    with pytest.raises(DeckyError):
        decky.extract_tar(_tar(member), decky.PRIVILEGED_TOPS, tmp_path)
    assert not (tmp_path.parent / "etc").exists()


def test_merge_keeps_existing_entries(tmp_path):
    staging, dest = tmp_path / "s", tmp_path / "d"
    (staging / "plugins/Foo").mkdir(parents=True)
    (staging / "plugins/Bar").mkdir(parents=True)
    (dest / "plugins/Foo").mkdir(parents=True)
    (dest / "plugins/Foo/newer").write_text("keep")
    moved, kept = decky.merge_into(staging, dest, decky.PRIVILEGED_TOPS)
    assert moved == ["plugins/Bar"] and kept == ["plugins/Foo"]
    assert (dest / "plugins/Foo/newer").read_text() == "keep" and (dest / "plugins/Bar").is_dir()


def test_merge_refuses_symlinked_top(tmp_path):
    (tmp_path / "s/plugins/Foo").mkdir(parents=True)
    (tmp_path / "d").mkdir()
    (tmp_path / "d/plugins").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(DeckyError):
        decky.merge_into(tmp_path / "s", tmp_path / "d", decky.PRIVILEGED_TOPS)


# --- the root helper, run as the test user with systemctl and the privilege drop faked ---------


@pytest.fixture
def helper(tmp_path, monkeypatch):
    mod = load_helper()
    calls = []
    monkeypatch.setattr(mod, "systemctl", lambda *a, check=True: calls.append(a))
    monkeypatch.setattr(mod, "drop_to", lambda pw: os.chdir(pw.pw_dir))
    monkeypatch.setattr(decky, "STATE_ROOT", str(tmp_path / "var/lib/decky-loader"))
    monkeypatch.setattr(decky, "LEGACY_UNIT_PATH", str(tmp_path / "etc/plugin_loader.service"))
    monkeypatch.setattr(decky, "LOADER_BIN", str(tmp_path / "usr/bin/decky-loader"))
    (tmp_path / "var/lib").mkdir(parents=True)
    (tmp_path / "etc").mkdir()
    home = tmp_path / "home/deck"
    home.mkdir(parents=True)
    me = pwd.getpwuid(os.getuid())
    pw = pwd.struct_passwd(("deck", "x", me.pw_uid, me.pw_gid, "", str(home), "/bin/sh"))
    return mod, pw, calls, tmp_path


def test_helper_migrates_old_install(helper):
    mod, pw, calls, root = helper
    home = Path(pw.pw_dir)
    legacy_home(home)
    (root / "etc/plugin_loader.service").write_text("[Service]\nUser=root\n")
    assert mod.migrate(pw) is True
    state = root / "var/lib/decky-loader/deck"
    assert (state / "plugins/Foo/dist/index.js").read_text() == "js"
    assert (state / "settings/loader.json").exists() and not (state / "data").exists()
    unpriv = home / ".local/var/opt/decky-loader"
    assert (unpriv / "data/Foo/state.json").read_text() == "data" and (unpriv / "logs/Foo/1.log").exists()
    assert not (home / "homebrew/services/PluginLoader").exists()
    assert (home / "homebrew/plugins/Foo/plugin.json").exists()  # the old folder is kept
    assert not (root / "etc/plugin_loader.service").exists()
    assert ("disable", "--now", "plugin_loader.service") in calls
    assert [p.name for p in (root / "var/lib/decky-loader").iterdir()] == ["deck"]  # staging cleaned up
    assert not [p for p in unpriv.iterdir() if p.name.startswith(".migrate")]
    # A second run finds nothing to do and keeps what Decky changed in the meantime
    (state / "plugins/Foo/plugin.json").write_text("updated")
    calls.clear()
    assert mod.migrate(pw) is False
    assert (state / "plugins/Foo/plugin.json").read_text() == "updated" and calls == []


def test_helper_install_needs_the_package(helper):
    mod, pw, calls, root = helper
    with pytest.raises(DeckyError, match="Update your system first"):
        mod.install(pw)
    assert calls == []


def test_helper_install_enables_the_user_unit(helper, monkeypatch):
    mod, pw, calls, root = helper
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/bin/decky-loader").write_text("")
    monkeypatch.setattr(mod.os.path, "realpath", lambda p: str(p).replace(str(root), ""))
    mod.install(pw)
    assert calls == [("enable", "--now", "decky-loader@deck.service")]


def test_helper_install_refuses_unexpected_home(helper):
    mod, pw, calls, root = helper
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/bin/decky-loader").write_text("")
    with pytest.raises(DeckyError, match="expects /home/deck"):
        mod.install(pw)  # the test home is not /home/deck
    assert calls == []


def test_helper_uninstall_keeps_state(helper):
    mod, pw, calls, root = helper
    state = root / "var/lib/decky-loader/deck/plugins/Foo"
    state.mkdir(parents=True)
    mod.uninstall(pw)
    assert calls == [("disable", "--now", "decky-loader@deck.service")] and state.is_dir()


@pytest.mark.parametrize("argv,env", [
    ([], {"PKEXEC_UID": "1000"}), (["foo"], {"PKEXEC_UID": "1000"}), (["install", "x"], {"PKEXEC_UID": "1000"}),
    (["install"], {}), (["install"], {"PKEXEC_UID": "0"}), (["install"], {"PKEXEC_UID": "999"}),
    (["install"], {"PKEXEC_UID": "65534"}), (["install"], {"PKEXEC_UID": "1000x"}),
])
def test_helper_rejects_bad_calls(argv, env, monkeypatch):
    mod = load_helper()
    for var in ("PKEXEC_UID", "SUDO_UID"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert mod.main(argv) == 2


# --- bopp-decky ---------------------------------------------------------------------------------


class R:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


def test_status_without_package(tmp_path):
    st = decky_cli.read_status(tmp_path, "deck", run=lambda *a, **k: R("enabled\n"),
                               loader_bin=str(tmp_path / "missing"), legacy_unit=str(tmp_path / "none"))
    assert st["available"] is False and st["installed"] is False and st["version"] is None
    assert st["unit"] == "decky-loader@deck.service" and st["legacy"] is False


def test_status_enabled_and_legacy(tmp_path):
    loader = tmp_path / "decky-loader"
    loader.write_text("")
    unit = tmp_path / "plugin_loader.service"
    unit.write_text("")
    legacy_home(tmp_path)

    def run(cmd, **k):
        if cmd[0] == "pacman":
            return R("decky-loader 3.1.5-1\n")
        return R({"is-enabled": "enabled\n", "is-active": "active\n"}[cmd[1]])

    st = decky_cli.read_status(tmp_path, "deck", run=run, loader_bin=str(loader), legacy_unit=str(unit))
    assert st["installed"] and st["service_active"] and st["version"] == "3.1.5-1"
    assert st["legacy"] and st["legacy_files"]


def test_install_without_package_fails_before_asking(tmp_path, monkeypatch):
    monkeypatch.setattr(decky_cli, "run_helper", lambda *a: pytest.fail("helper must not run"))
    with pytest.raises(DeckyError, match="Update your system first"):
        decky_cli.do_install(lambda e: None, tmp_path, loader_bin=str(tmp_path / "missing"))
