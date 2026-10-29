import pytest
from boppos import oobe_helper as h
from boppos.privileged import UsageError

PW = "Abcdef12!x\n"


def make_root(tmp_path, passwd="root:x:0:0::/root:/bin/bash\nnobody:x:65534:65534::/:/bin/nologin\n", done=False):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(passwd)
    zi = tmp_path / "usr/share/zoneinfo"
    zi.mkdir(parents=True, exist_ok=True)
    (zi / "zone1970.tab").write_text("DE\t+52+013\tEurope/Berlin\tGermany\n")
    if done:
        (tmp_path / "var/lib/boppos").mkdir(parents=True)
        (tmp_path / "var/lib/boppos/oobe-done").write_text("done\n")
    return tmp_path


def with_user(tmp_path):
    return make_root(tmp_path, "root:x:0:0::/root:/bin/bash\nripps:x:1000:1000:Rip:/home/ripps:/bin/bash\n")


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


def test_create_user_refuses_when_a_person_already_has_an_account(tmp_path):
    with pytest.raises(UsageError, match="already exists"):
        h.steps_for(["create-user", "other"], PW, with_user(tmp_path))


def test_everything_is_refused_once_setup_is_done(tmp_path):
    root = make_root(tmp_path, done=True)
    for argv in (["create-user", "ripps"], ["set-locale", "en_US.UTF-8"], ["finish"], ["set-hostname", "deck"]):
        with pytest.raises(UsageError, match="already finished"):
            h.steps_for(argv, PW, root)


def test_system_settings_map_to_fixed_commands(tmp_path):
    root = make_root(tmp_path)
    locale = h.steps_for(["set-locale", "de_DE.UTF-8"], None, root)
    assert locale == [h.Run([h.LOCALECTL, "set-locale", "LANG=de_DE.UTF-8"])]
    assert h.steps_for(["set-keymap", "de"], None, root) == [h.Run([h.LOCALECTL, "set-keymap", "de"])]
    zone = h.steps_for(["set-timezone", "Europe/Berlin"], None, root)
    assert zone == [h.Run([h.TIMEDATECTL, "set-timezone", "Europe/Berlin"])]
    assert h.steps_for(["set-hostname", "deck"], None, root) == [h.Run([h.HOSTNAMECTL, "set-hostname", "deck"])]


@pytest.mark.parametrize("argv", [
    ["set-locale", "xx_YY.UTF-8"], ["set-locale", "en_US.UTF-8;id"], ["set-keymap", "../../x"], ["set-keymap", "nope"],
    ["set-timezone", "../../etc/passwd"], ["set-timezone", "Mars/Base"], ["set-hostname", "Bad_Name"],
    ["set-hostname", ""], ["set-hostname", "a", "b"], ["set-locale"], ["bash"], [], ["finish", "now"],
])
def test_bad_arguments_are_refused(tmp_path, argv):
    with pytest.raises(UsageError):
        h.steps_for(argv, None, with_user(tmp_path))


def test_the_other_verbs_take_no_stdin(tmp_path):
    with pytest.raises(UsageError, match="no stdin"):
        h.steps_for(["set-keymap", "de"], "secret\n", make_root(tmp_path))


def test_autologin_writes_one_fixed_file_for_the_created_user(tmp_path):
    (step,) = h.steps_for(["set-autologin", "ripps", "gaming"], None, with_user(tmp_path))
    assert step.path == h.AUTOLOGIN_FILE
    assert step.text == "[Autologin]\nUser=ripps\nSession=gamescope-session-steam\n"


@pytest.mark.parametrize("argv", [["set-autologin", "root", "desktop"], ["set-autologin", "ghost", "desktop"],
                                  ["set-autologin", "ripps", "bash"], ["set-autologin", "ripps\nUser=root", "desktop"]])
def test_autologin_refuses_other_users_and_sessions(tmp_path, argv):
    with pytest.raises(UsageError):
        h.steps_for(argv, None, with_user(tmp_path))


def test_finish_marks_done_and_starts_the_login_screen_but_needs_a_user(tmp_path):
    with pytest.raises(UsageError, match="nobody able"):
        h.steps_for(["finish"], None, make_root(tmp_path))
    write, run = h.steps_for(["finish"], None, with_user(tmp_path))
    assert write.path == h.DONE_FILE and run.argv[-1] == "display-manager.service"


def test_human_users_skip_system_accounts_and_nobody(tmp_path):
    root = make_root(tmp_path, "root:x:0:0::/:/bin/sh\nsddm:x:964:964::/:/bin/nologin\n"
                     "nobody:x:65534:65534::/:/bin/nologin\n"
                     "a:x:1000:1000::/home/a:/bin/bash\nb:x:1001:1001::/home/b:/bin/bash\n")
    assert h.human_users(root) == {"a": 1000, "b": 1001}
    assert h.human_users(tmp_path / "missing") == {}


def test_needed_only_before_setup_and_before_any_account(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\nboppos-oobe:x:972:972::/x:/usr/bin/nologin\n")
    assert h.needed(tmp_path)  # the setup account is a system user, not a person
    (tmp_path / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\nripps:x:1000:1000::/home/ripps:/bin/bash\n")
    assert not h.needed(tmp_path)
    (tmp_path / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\n")
    (tmp_path / "var/lib/boppos").mkdir(parents=True)
    (tmp_path / "var/lib/boppos/oobe-done").touch()
    assert not h.needed(tmp_path)


def test_the_setup_account_never_counts_as_a_person():
    from pathlib import Path
    conf = (Path(__file__).parents[1] / "sysusers/boppos-oobe.conf").read_text()
    assert "\nu boppos-oobe - " in "\n" + conf and "/usr/bin/nologin" in conf
    unit = (Path(__file__).parents[1] / "systemd/system/boppos-oobe.service").read_text()
    assert "ConditionPathExists=!/var/lib/boppos/oobe-done" in unit and "User=boppos-oobe" in unit
    assert "ExecCondition=/usr/libexec/boppos/oobe-needed" in unit
    rule = (Path(__file__).parents[1] / "polkit/rules.d/52-boppos-oobe.rules").read_text()
    assert 'subject.user === "boppos-oobe"' in rule and "oobe-done" in rule
