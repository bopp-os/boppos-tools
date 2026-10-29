from pathlib import Path

import pytest
from boppos import firstrun
from boppos import oobe_helper as h
from boppos.privileged import UsageError

PW = "Abcdef12!x\n"
REPO = Path(__file__).parents[1]
BASE = "root:x:0:0::/root:/bin/bash\nnobody:x:65534:65534::/:/bin/nologin\nboppos-oobe:x:972:972::/x:/usr/bin/nologin\n"
RIPPS = "ripps:x:1000:1000:Rip:/home/ripps:/bin/bash\n"
START_IN = "[start-in]\ndisplay-manager = sddm\ngaming-session = gs-steam\ndesktop-session = plasma\n"


def make_root(tmp_path, passwd=BASE, marker="mode=oobe\n", start_in=START_IN):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(passwd)
    zi = tmp_path / "usr/share/zoneinfo"
    zi.mkdir(parents=True, exist_ok=True)
    (zi / "zone1970.tab").write_text("DE\t+52+013\tEurope/Berlin\tGermany\n")
    if marker is not None:
        (tmp_path / "var/lib/boppos").mkdir(parents=True, exist_ok=True)
        (tmp_path / "var/lib/boppos/first-run").write_text(marker)
    if start_in is not None:
        (tmp_path / "usr/share/boppos").mkdir(parents=True, exist_ok=True)
        (tmp_path / "usr/share/boppos/start-in.conf").write_text(start_in)
        (tmp_path / "usr/share/wayland-sessions").mkdir(parents=True, exist_ok=True)
        for name in ("gs-steam", "plasma"):
            (tmp_path / f"usr/share/wayland-sessions/{name}.desktop").write_text("[Desktop Entry]\n")
    return tmp_path


def with_user(tmp_path, **kw):
    return make_root(tmp_path, BASE + RIPPS, **kw)


def test_create_user_is_one_useradd_and_the_password_goes_on_stdin_only(tmp_path):
    steps = h.steps_for(["create-user", "ripps", "Rip S"], PW, make_root(tmp_path))
    useradd, chpasswd = steps
    assert useradd.argv == [h.USERADD, "--create-home", "--groups", "wheel", "--shell", "/bin/bash",
                            "--comment", "Rip S", "--", "ripps"]
    assert chpasswd.argv == [h.CHPASSWD] and chpasswd.stdin == "ripps:Abcdef12!x\n"
    assert not any("Abcdef12" in a for s in steps for a in s.argv)


@pytest.mark.parametrize("args", [["root"], ["Ripps"], ["a b"], ["steam"], ["ripps", "a:b"], ["ripps", "x\ny"], []])
def test_create_user_refuses_bad_names(tmp_path, args):
    with pytest.raises(UsageError):
        h.steps_for(["create-user", *args], PW, make_root(tmp_path))


@pytest.mark.parametrize("stdin", [None, "", "short\n", "Abcdef12!x\nsecond line\n", "x" * 300, "Abcdef\x0012345"])
def test_create_user_refuses_a_bad_password(tmp_path, stdin):
    with pytest.raises(UsageError):
        h.steps_for(["create-user", "ripps"], stdin, make_root(tmp_path))


@pytest.mark.parametrize("argv", [["create-user", "other"], ["set-locale", "en_US.UTF-8"], ["set-keymap", "de"],
                                  ["set-timezone", "Europe/Berlin"], ["set-hostname", "deck"]])
def test_setup_verbs_are_refused_once_a_real_account_exists(tmp_path, argv):
    # the installer already set these when it created the account; the welcome tour must not redo them
    with pytest.raises(UsageError, match="already exists"):
        h.steps_for(argv, PW if argv[0] == "create-user" else None, with_user(tmp_path))


def test_a_nologin_account_in_the_user_range_does_not_end_setup(tmp_path):
    root = make_root(tmp_path, BASE + "svc:x:1000:1000::/:/usr/bin/nologin\n")
    assert h.steps_for(["set-keymap", "de"], None, root) == [h.Run([h.LOCALECTL, "set-keymap", "de"])]


def test_everything_is_refused_without_a_pending_first_run(tmp_path):
    for root in (make_root(tmp_path / "a", marker=None), with_user(tmp_path / "b", marker=None)):
        for argv in (["create-user", "ripps"], ["set-locale", "en_US.UTF-8"], ["finish-setup"],
                     ["set-start-in", "ripps", "gaming"], ["finish-first-run"]):
            with pytest.raises(UsageError, match="no first run"):
                h.steps_for(argv, PW, root)


def test_system_settings_map_to_fixed_commands(tmp_path):
    root = make_root(tmp_path)
    locale = h.steps_for(["set-locale", "de_DE.UTF-8"], None, root)
    assert locale == [h.Run([h.LOCALECTL, "set-locale", "LANG=de_DE.UTF-8"])]
    assert h.steps_for(["set-keymap", "de"], None, root) == [h.Run([h.LOCALECTL, "set-keymap", "de"])]
    zone = h.steps_for(["set-timezone", "Europe/Berlin"], None, root)
    assert zone == [h.Run([h.TIMEDATECTL, "set-timezone", "Europe/Berlin"])]
    assert h.steps_for(["set-hostname", "deck"], None, root) == [h.Run([h.HOSTNAMECTL, "set-hostname", "deck"])]


def test_in_a_chroot_the_settings_are_written_as_files(tmp_path):
    root = make_root(tmp_path)
    (root / "etc/vconsole.conf").write_text("KEYMAP=us\nFONT=ter-v16n\n")
    assert h.steps_for(["set-locale", "de_DE.UTF-8"], None, root, chroot=True) == [
        h.Write("/etc/locale.conf", "LANG=de_DE.UTF-8\n")]
    assert h.steps_for(["set-keymap", "de"], None, root, chroot=True) == [
        h.Write("/etc/vconsole.conf", "FONT=ter-v16n\nKEYMAP=de\n")]  # the console font stays
    assert h.steps_for(["set-timezone", "Europe/Berlin"], None, root, chroot=True) == [
        h.Symlink("/etc/localtime", "../usr/share/zoneinfo/Europe/Berlin")]
    assert h.steps_for(["set-hostname", "deck"], None, root, chroot=True) == [h.Write("/etc/hostname", "deck\n")]


@pytest.mark.parametrize("argv", [
    ["set-locale", "xx_YY.UTF-8"], ["set-locale", "en_US.UTF-8;id"], ["set-keymap", "../../x"], ["set-keymap", "nope"],
    ["set-timezone", "../../etc/passwd"], ["set-timezone", "Mars/Base"], ["set-hostname", "Bad_Name"],
    ["set-hostname", ""], ["set-hostname", "a", "b"], ["set-locale"], ["bash"], [], ["finish-setup", "now"],
    ["finish-first-run", "x"],
])
def test_bad_arguments_are_refused(tmp_path, argv):
    with pytest.raises(UsageError):
        h.steps_for(argv, None, make_root(tmp_path))


def test_the_other_verbs_take_no_stdin(tmp_path):
    with pytest.raises(UsageError, match="no stdin"):
        h.steps_for(["set-keymap", "de"], "secret\n", make_root(tmp_path))


def test_start_in_writes_the_image_s_autologin_file_for_that_user(tmp_path):
    (step,) = h.steps_for(["set-start-in", "ripps", "gaming"], None, with_user(tmp_path))
    assert step == h.Write(firstrun.SDDM_AUTOLOGIN, "[Autologin]\nUser=ripps\nSession=gs-steam\n")
    assert h.steps_for(["set-autologin", "ripps", "desktop"], None, with_user(tmp_path / "x"))[0].text.endswith(
        "Session=plasma\n")  # the old verb name still works


def test_start_in_is_only_for_your_own_account_or_the_setup(tmp_path):
    root = make_root(tmp_path, BASE + RIPPS + "other:x:1001:1001::/home/o:/bin/bash\n")
    assert h.steps_for(["set-start-in", "ripps", "gaming"], None, root, caller=1000)
    assert h.steps_for(["set-start-in", "ripps", "gaming"], None, root, caller=972)  # boppos-oobe, during setup
    with pytest.raises(UsageError, match="your own"):
        h.steps_for(["set-start-in", "ripps", "gaming"], None, root, caller=1001)


@pytest.mark.parametrize("argv", [["set-start-in", "root", "desktop"], ["set-start-in", "ghost", "desktop"],
                                  ["set-start-in", "ripps", "bash"], ["set-start-in", "ripps\nUser=root", "desktop"],
                                  ["set-start-in", "nobody", "desktop"]])
def test_start_in_refuses_other_users_and_choices(tmp_path, argv):
    with pytest.raises(UsageError):
        h.steps_for(argv, None, with_user(tmp_path))


def test_start_in_without_the_image_s_config_says_why(tmp_path):
    with pytest.raises(UsageError, match="start-in.conf is missing"):
        h.steps_for(["set-start-in", "ripps", "gaming"], None, with_user(tmp_path, start_in=None))


def test_finish_setup_starts_the_login_screen_but_needs_a_user_and_keeps_the_marker(tmp_path):
    with pytest.raises(UsageError, match="nobody able"):
        h.steps_for(["finish-setup"], None, make_root(tmp_path))
    (run,) = h.steps_for(["finish"], None, with_user(tmp_path))
    assert run.argv[-1] == "display-manager.service"


def test_finish_first_run_removes_the_marker_for_an_account_holder_only(tmp_path):
    root = with_user(tmp_path)
    assert h.steps_for(["finish-first-run"], None, root, caller=1000) == [h.Remove(firstrun.MARKER)]
    assert h.steps_for(["finish-first-run"], None, root) == [h.Remove(firstrun.MARKER)]  # root itself
    with pytest.raises(UsageError, match="account holder"):
        h.steps_for(["finish-first-run"], None, root, caller=972)  # the setup does not end the first run
    with pytest.raises(UsageError, match="before an account"):
        h.steps_for(["finish-first-run"], None, make_root(tmp_path / "x"))


def test_needed_means_pending_and_no_real_account(tmp_path):
    assert h.needed(make_root(tmp_path))
    assert not h.needed(with_user(tmp_path / "u"))
    assert not h.needed(make_root(tmp_path / "n", marker=None))  # no marker: never start the setup


def test_polkit_waives_only_the_setup_account_and_the_users_own_tour_calls():
    rule = (REPO / "polkit/rules.d/52-boppos-oobe.rules").read_text()
    assert 'subject.user === "boppos-oobe"' in rule and "/var/lib/boppos/first-run" in rule
    assert '" set-start-in " + subject.user + " gaming"' in rule and '" finish-first-run"' in rule
    assert 'subject.isInGroup("wheel")' in rule and "oobe-done" not in rule


def test_the_setup_account_never_counts_as_a_person():
    conf = (REPO / "sysusers/boppos-oobe.conf").read_text()
    assert "\nu boppos-oobe - " in "\n" + conf and "/usr/bin/nologin" in conf
