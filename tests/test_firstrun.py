import pytest
from boppos import firstrun as fr

ROOT_LINE = "root:x:0:0::/root:/bin/bash\n"


def make_root(tmp_path, passwd=ROOT_LINE, marker: str | None = None):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(passwd)
    if marker is not None:
        (tmp_path / "var/lib/boppos").mkdir(parents=True, exist_ok=True)
        (tmp_path / "var/lib/boppos/first-run").write_text(marker)
    return tmp_path


def account(name, uid, shell="/bin/bash"):
    return f"{name}:x:{uid}:{uid}::/home/{name}:{shell}\n"


@pytest.mark.parametrize("uid, shell, real", [
    (65534, "/usr/bin/nologin", False),  # nobody
    (65534, "/bin/bash", False),  # nobody, even with a shell
    (1000, "/usr/sbin/nologin", False),
    (1000, "/sbin/nologin", False),
    (1000, "/usr/bin/nologin", False),  # Arch's path
    (1000, "/bin/false", False),
    (1000, "/usr/bin/false", False),
    (999, "/bin/bash", False),
    (1000, "/bin/bash", True),
    (59999, "/usr/bin/fish", True),
    (60000, "/bin/bash", False),
    (1000, "", True),  # an empty shell field is /bin/sh
])
def test_what_counts_as_a_real_account(tmp_path, uid, shell, real):
    root = make_root(tmp_path, ROOT_LINE + account("p", uid, shell))
    assert (fr.real_accounts(root) == {"p": uid}) is real
    assert fr.is_real_account(uid, shell) is real


def test_real_accounts_skips_broken_lines_and_a_missing_passwd(tmp_path):
    root = make_root(tmp_path, ROOT_LINE + "garbage\nx:x:abc:1::/:/bin/sh\n" + account("a", 1000))
    assert fr.real_accounts(root) == {"a": 1000}
    assert fr.real_accounts(tmp_path / "missing") == {}


@pytest.mark.parametrize("text, hint", [
    ("mode=welcome\n", "welcome"), ("mode=oobe", "oobe"), (" mode = oobe \n", "oobe"),
    ("mode=other\n", ""), ("", ""), ("# comment\nmode=welcome\n", "welcome"),
])
def test_marker_hint(tmp_path, text, hint):
    assert fr.read_hint(make_root(tmp_path, marker=text)) == hint


def test_no_marker_and_an_account_means_nothing_to_do(tmp_path):
    root = make_root(tmp_path, ROOT_LINE + account("ripps", 1000))
    d = fr.decide(root)
    assert d.mode == "none" and d.hint is None and not fr.pending(root)


def test_a_bare_install_without_marker_or_account_still_gets_the_setup(tmp_path):
    root = make_root(tmp_path)
    d = fr.decide(root)
    assert d.mode == "setup" and d.hint is None and not d.hint_disagrees and fr.pending(root)


def test_a_nologin_only_system_has_no_real_account(tmp_path):
    root = make_root(tmp_path, ROOT_LINE + account("svc", 1000, "/usr/bin/nologin") + account("nobody", 65534))
    assert fr.decide(root).mode == "setup"


def test_marker_and_no_account_is_setup(tmp_path):
    d = fr.decide(make_root(tmp_path, marker="mode=oobe\n"))
    assert d.mode == "setup" and not d.hint_disagrees


def test_marker_and_an_account_is_the_welcome_tour(tmp_path):
    d = fr.decide(make_root(tmp_path, ROOT_LINE + account("ripps", 1000), "mode=welcome\n"))
    assert d.mode == "welcome" and d.accounts == {"ripps": 1000} and not d.hint_disagrees


@pytest.mark.parametrize("passwd, hint, mode", [
    (ROOT_LINE, "mode=welcome\n", "setup"),  # the installer says it made an account, but there is none
    (ROOT_LINE + account("ripps", 1000), "mode=oobe\n", "welcome"),  # it says none, but there is one
])
def test_the_live_system_wins_over_the_hint(tmp_path, passwd, hint, mode):
    d = fr.decide(make_root(tmp_path, passwd, hint))
    assert d.mode == mode and d.hint_disagrees


def test_an_unknown_hint_still_means_pending(tmp_path):
    d = fr.decide(make_root(tmp_path, marker="garbage\n"))
    assert d.mode == "setup" and d.hint == "" and not d.hint_disagrees


def test_startup_mode_forces_or_decides(tmp_path):
    done = make_root(tmp_path, ROOT_LINE + account("ripps", 1000))
    assert fr.startup_mode(None, done) == "none"
    assert fr.startup_mode("first-run", done) == "none"
    assert fr.startup_mode("welcome", done) == "welcome"  # --welcome keeps working without a marker
    assert fr.startup_mode("setup", done) == "setup"
    (tmp_path / "var/lib/boppos").mkdir(parents=True)
    (tmp_path / "var/lib/boppos/first-run").write_text("mode=welcome\n")
    assert fr.startup_mode("first-run", done) == "welcome"
    assert fr.startup_mode("first-run", make_root(tmp_path / "bare")) == "setup"


# --- start-in config -------------------------------------------------------------------------------------------


def start_in(tmp_path, conf: str | None, sessions=("gs-steam", "plasma")):
    if conf is not None:
        (tmp_path / "usr/share/boppos").mkdir(parents=True, exist_ok=True)
        (tmp_path / "usr/share/boppos/start-in.conf").write_text(conf)
    for name in sessions:
        d = tmp_path / "usr/share/wayland-sessions"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.desktop").write_text("[Desktop Entry]\n")
    return tmp_path


CONF = "[start-in]\ndisplay-manager = sddm\ngaming-session = gs-steam\ndesktop-session = plasma\n"


def test_start_in_comes_from_the_image_and_names_installed_sessions_only(tmp_path):
    cfg, problem = fr.load_start_in(start_in(tmp_path, CONF))
    assert problem == "" and cfg.sessions == {"gaming": "gs-steam", "desktop": "plasma"}
    assert fr.autologin_file(cfg) == fr.SDDM_AUTOLOGIN
    assert fr.autologin_text(cfg, "ripps", "gaming") == "[Autologin]\nUser=ripps\nSession=gs-steam\n"


def test_a_missing_session_drops_that_choice(tmp_path):
    cfg, _ = fr.load_start_in(start_in(tmp_path, CONF, sessions=("plasma",)))
    assert cfg.choices == ("desktop",)


@pytest.mark.parametrize("conf, sessions, needle", [
    (None, (), "missing"),
    ("[start-in]\ndisplay-manager = gdm\ngaming-session = gs-steam\n", ("gs-steam",), "not supported"),
    ("[start-in]\ndisplay-manager = sddm\ngaming-session = ../../etc/x\n", (), "none of the sessions"),
    ("not an ini", (), "cannot read"),
])
def test_the_choice_is_not_offered_without_a_usable_config(tmp_path, conf, sessions, needle):
    cfg, problem = fr.load_start_in(start_in(tmp_path, conf, sessions))
    assert cfg is None and needle in problem


def test_current_start_in_reads_the_autologin_file(tmp_path):
    root = start_in(tmp_path, CONF)
    cfg, _ = fr.load_start_in(root)
    assert fr.current_start_in(cfg, root) is None
    f = root / fr.SDDM_AUTOLOGIN.lstrip("/")
    f.parent.mkdir(parents=True)
    f.write_text(fr.autologin_text(cfg, "ripps", "gaming"))
    assert fr.current_start_in(cfg, root) == "gaming"
