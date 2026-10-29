import pytest
from boppos import oobe


@pytest.mark.parametrize("name", ["ripps", "a", "_x", "deck-user", "user_1", "a" * 32])
def test_good_user_names(name):
    assert oobe.username_problem(name) is None


@pytest.mark.parametrize("name", ["", "Root", "1abc", "a b", "a:b", "a" * 33, "-x", "ripps\n", "root",
                                  "boppos-oobe", "steam"])
def test_bad_user_names(name):
    assert oobe.username_problem(name)


def test_full_name_may_not_break_the_passwd_line():
    assert oobe.fullname_problem("Jo Smith") is None
    assert oobe.fullname_problem("") is None
    for bad in ("a:b", "a,b", "a=b", 'a"b', "a\nb", "x" * 65):
        assert oobe.fullname_problem(bad), bad


def test_hostname_rules():
    assert oobe.hostname_problem("") is None
    assert oobe.hostname_problem("deck-01") is None
    for bad in ("-a", "a-", "A", "a_b", "a.b", "x" * 64):
        assert oobe.hostname_problem(bad), bad


def test_password_hint_levels_and_the_password_stays_out_of_reprs():
    assert oobe.password_hint("")[0] == "empty"
    assert oobe.password_hint("short")[0] == "weak"
    assert oobe.password_hint("alllowercase")[0] == "ok"
    assert oobe.password_hint("Abcdef12!x")[0] == "strong"
    assert "hunter22" not in repr(oobe.Choices(password="hunter22"))


def test_locale_keymap_and_zone_are_checked_against_the_lists():
    assert oobe.locale_problem("en_US.UTF-8", oobe.COMMON_LOCALES) is None
    assert oobe.locale_problem("xx", oobe.COMMON_LOCALES)
    assert oobe.locale_problem("en_US.UTF-8; rm", None)
    assert oobe.keymap_problem("us", oobe.COMMON_KEYMAPS) is None
    assert oobe.keymap_problem("../x")
    assert oobe.timezone_problem("Europe/Berlin", ["Europe/Berlin"]) is None
    assert oobe.timezone_problem("../etc/passwd")
    assert oobe.timezone_problem("Europe/Berlin", ["UTC"])


def test_account_step_blocks_until_everything_is_valid():
    c = oobe.Choices()
    assert oobe.step_problem("account", c)
    c.username, c.password = "ripps", "Abcdef12!x"
    assert oobe.step_problem("account", c) is None
    c.password = "short"
    assert oobe.step_problem("account", c)
    c.password, c.hostname = "Abcdef12!x", "Bad_Name"
    assert oobe.step_problem("account", c)


def test_problems_lists_each_blocked_step():
    assert len(oobe.problems(oobe.Choices())) == 1  # only the account is missing
    assert oobe.problems(oobe.Choices(username="ripps", password="Abcdef12!x")) == []


def test_plan_never_contains_the_password_and_names_the_session():
    c = oobe.Choices(username="ripps", fullname="Rip", password="hunter22-secret", session="gaming", hostname="deck")
    text = "\n".join(a.text for a in oobe.build_plan(c))
    assert "hunter22" not in text and "Gaming Mode" in text and "deck" in text
    assert all(line.startswith("[dry run] would:") for line in oobe.run_plan(oobe.build_plan(c)))


def test_timezones_read_tzdata_and_fall_back(tmp_path):
    assert oobe.timezones(tmp_path) == ["UTC"]
    zi = tmp_path / "usr/share/zoneinfo"
    zi.mkdir(parents=True)
    (zi / "zone1970.tab").write_text("# c\nDE\t+52+013\tEurope/Berlin\tGermany\nUS,CA\t+4\tAmerica/New_York\n")
    assert oobe.timezones(tmp_path) == ["UTC", "America/New_York", "Europe/Berlin"]


def test_online_needs_a_link_that_is_up(tmp_path):
    assert not oobe.online(tmp_path)
    for name, state in (("lo", "unknown"), ("wlan0", "down")):
        d = tmp_path / "sys/class/net" / name
        d.mkdir(parents=True)
        (d / "operstate").write_text(state + "\n")
    assert not oobe.online(tmp_path)
    (tmp_path / "sys/class/net/wlan0/operstate").write_text("up\n")
    assert oobe.online(tmp_path)


def test_a_trailing_newline_never_slips_through_any_check():
    assert oobe.hostname_problem("deck\n")
    assert oobe.locale_problem("en_US.UTF-8\n")
    assert oobe.keymap_problem("us\n")
    assert oobe.timezone_problem("UTC\n")


def _good(**kw):
    return oobe.Choices(username="ripps", fullname="Rip", password="Abcdef12!x", session="gaming", **kw)


def test_helper_calls_create_the_user_before_signing_them_in_and_finish_last():
    verbs = [c.args[0] for c in oobe.helper_calls(_good(hostname="deck"))]
    assert verbs == ["set-locale", "set-keymap", "set-timezone", "set-hostname", "create-user",
                     "set-start-in", "finish-setup"]
    assert "set-hostname" not in [c.args[0] for c in oobe.helper_calls(_good())]


def test_no_start_in_call_when_the_image_cannot_sign_in_automatically():
    assert "set-start-in" not in [c.args[0] for c in oobe.helper_calls(_good(), start_in=False)]
    assert "Sign " not in " ".join(a.text for a in oobe.build_plan(_good(), start_in=False))


def test_only_create_user_carries_the_password_and_never_in_args_or_repr():
    calls = oobe.helper_calls(_good())
    assert [c.args[0] for c in calls if c.stdin] == ["create-user"]
    assert next(c for c in calls if c.args[0] == "create-user").stdin == "Abcdef12!x\n"
    assert all("Abcdef12" not in " ".join(c.args) and "Abcdef12" not in repr(c) for c in calls)


def test_apply_stops_at_the_first_failure():
    seen = []

    def run(argv, stdin):
        seen.append(argv[0])
        return (1, "boom\n") if argv[0] == "set-keymap" else (0, "")

    ok, msg = oobe.apply_choices(_good(), lambda a: a, run)
    assert not ok and "set-keymap failed: boom" in msg and "Abcdef12" not in msg
    assert seen == ["set-locale", "set-keymap"]


def test_apply_refuses_invalid_choices_without_running_anything():
    ran = []
    ok, _ = oobe.apply_choices(oobe.Choices(), lambda a: a, lambda *a: ran.append(a) or (0, ""))
    assert not ok and not ran


def test_apply_runs_everything_when_all_succeed():
    ran = []
    ok, _ = oobe.apply_choices(_good(), lambda a: a, lambda argv, stdin: ran.append(argv[0]) or (0, ""))
    assert ok and ran[-1] == "finish-setup" and len(ran) == 6
