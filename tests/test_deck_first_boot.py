"""The deck image's first boot: which steps boppos-deck shows, and Start-in on plasmalogin."""

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest
from boppos import firstboot, firstrun, network, welcome
from boppos import oobe_helper as h

REPO = Path(__file__).parents[1]
Live = firstboot.Live
ALL_OK = Live(marker=False, steam=True, online=True)


def keys(env, state, gaming=True, ask=False):
    return [s.key for s in welcome.steps_for(gaming, ask, firstboot.plan(env, state))]


# --- the variable ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value, want", [
    ("first-run steam network", {"first-run", "steam", "network"}),
    ("steam", {"steam"}),
    ("steam,network", {"steam", "network"}),
    ("steam bogus", {"steam"}),
    ("bogus", None),
    ("", None),
    (None, None),
])
def test_parse_keeps_only_known_words(value, want):
    assert firstboot.parse(value) == (None if want is None else frozenset(want))


def test_launched_by_image_means_the_variable_is_set_even_empty():
    assert firstboot.launched_by_image({firstboot.ENV: ""})
    assert not firstboot.launched_by_image({})


# --- what is missing: variable x marker x live checks ---------------------------------------------------------


@pytest.mark.parametrize("env, state, want", [
    # the image named it: trusted
    ({"BOPPOS_FIRST_BOOT_MISSING": "steam network"}, Live(False, False, False), {"steam", "network"}),
    ({"BOPPOS_FIRST_BOOT_MISSING": "steam"}, Live(False, False, True), {"steam"}),
    ({"BOPPOS_FIRST_BOOT_MISSING": "first-run steam"}, Live(True, False, True), {"first-run", "steam"}),
    # Steam without a network cannot download: network joins it
    ({"BOPPOS_FIRST_BOOT_MISSING": "steam"}, Live(False, False, False), {"steam", "network"}),
    # named words without first-run: only those, even with the marker (the tour comes later, from Steam)
    ({"BOPPOS_FIRST_BOOT_MISSING": "steam"}, Live(True, False, True), {"steam"}),
    ({"BOPPOS_FIRST_BOOT_MISSING": "steam network"}, Live(True, False, False), {"steam", "network"}),
    # empty or unknown: the live system decides
    ({"BOPPOS_FIRST_BOOT_MISSING": ""}, Live(False, False, False), {"steam", "network"}),
    ({"BOPPOS_FIRST_BOOT_MISSING": "what"}, Live(False, False, True), {"steam"}),
    ({"BOPPOS_FIRST_BOOT_MISSING": ""}, Live(True, True, False), {"first-run", "network"}),
    ({"BOPPOS_FIRST_BOOT_MISSING": ""}, ALL_OK, set()),
    # not launched by the image: the marker still asks the live system, a manual run assumes nothing
    ({}, Live(True, False, False), {"first-run", "steam", "network"}),
    ({}, Live(True, True, True), {"first-run"}),
    ({}, Live(False, False, False), set()),
])
def test_missing(env, state, want):
    assert firstboot.plan(env, state).missing == frozenset(want)


# --- which steps --------------------------------------------------------------------------------------------------


def test_a_manual_tour_is_unchanged():
    assert keys({}, Live(False, False, False)) == [s.key for s in welcome.steps_for(True)]
    assert "network" not in keys({}, ALL_OK) and "steam" not in keys({}, ALL_OK)


def test_later_boot_shows_only_the_missing_steps():
    env = {"BOPPOS_FIRST_BOOT_MISSING": "steam network"}
    assert keys(env, Live(False, False, False)) == ["network", "steam", "done"]
    assert keys({"BOPPOS_FIRST_BOOT_MISSING": "steam"}, Live(False, False, True)) == ["steam", "done"]
    assert keys({"BOPPOS_FIRST_BOOT_MISSING": "network"}, Live(False, True, False)) == ["network", "done"]
    assert firstboot.plan(env, Live(False, False, False)).only_missing


def test_first_run_adds_network_and_steam_to_the_tour_after_welcome():
    env = {"BOPPOS_FIRST_BOOT_MISSING": "first-run steam network"}
    got = keys(env, Live(True, False, False), ask=True)
    assert got[:3] == ["welcome", "network", "steam"]
    assert {"apps", "hardware", "session", "review", "done"} <= set(got)
    assert not firstboot.plan(env, Live(True, False, False)).only_missing


def test_first_run_with_everything_there_is_the_plain_tour():
    env = {"BOPPOS_FIRST_BOOT_MISSING": "first-run"}
    assert keys(env, Live(True, True, True)) == [s.key for s in welcome.steps_for(True)]


def test_the_image_found_something_that_is_there_by_now():
    plan = firstboot.plan({"BOPPOS_FIRST_BOOT_MISSING": ""}, ALL_OK)
    assert not plan.missing and not plan.only_missing


def test_live_checks_use_the_steam_folder_and_the_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setattr(firstboot, "nm_online", lambda: False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "root"
    root.mkdir()
    assert firstboot.live(str(root)).steam  # no bootstrap: nothing to download with, so no Steam step
    boot = root / "usr/lib/steam/bootstraplinux_ubuntu12_32.tar.xz"
    boot.parent.mkdir(parents=True)
    boot.write_bytes(b"x")
    state = firstboot.live(str(root))
    assert not state.steam and not state.online and not state.marker
    ui = tmp_path / "home/.local/share/Steam/ubuntu12_32/steamui.so"
    ui.parent.mkdir(parents=True)
    ui.write_bytes(b"")
    assert firstboot.live(str(root)).steam


# --- the network helpers ------------------------------------------------------------------------------------------


def test_hidden_networks_are_probed_and_open_ones_need_no_secret():
    secured = network.hidden_network("Home", True)
    add, up = network.join_argvs(secured, "/run/x")
    assert ["802-11-wireless.hidden", "yes"] == add[add.index("802-11-wireless.hidden"):][:2]
    assert "wifi-sec.key-mgmt" in add and up[-2:] == ["passwd-file", "/run/x"]
    add, up = network.join_argvs(network.hidden_network("Cafe", False), None)
    assert "802-11-wireless.hidden" in add and "wifi-sec.key-mgmt" not in add
    assert up == [network.NMCLI, "connection", "up", "id", "Cafe"]


def test_a_failed_open_hidden_join_removes_its_profile():
    calls = []

    class P:
        def __init__(self, rc):
            self.returncode, self.stdout, self.stderr = rc, "", "no network with SSID"

    def run(argv, **_kw):
        calls.append(argv)
        return P(1 if argv[:3] == [network.NMCLI, "connection", "up"] else 0)

    ok, _msg = network.join(network.hidden_network("Cafe", False), "", run=run)
    assert not ok and calls[-1] == network.delete_argv(network.hidden_network("Cafe", False))


def test_wired_connected():
    assert network.wired_connected("ethernet:connected\nwifi:disconnected\n")
    assert not network.wired_connected("ethernet:unavailable\nwifi:connected\n")


# --- Start in on plasmalogin -------------------------------------------------------------------------------------

PASSWD = ("root:x:0:0::/root:/bin/bash\nboppos-oobe:x:972:972::/x:/usr/bin/nologin\n"
          "ripps:x:1000:1000:Rip:/home/ripps:/bin/bash\n")
DECK_START_IN = ("[start-in]\ndisplay-manager = plasmalogin\ngaming-session = gamescope-session\n"
                 "desktop-session = plasma\n")


def deck_root(tmp_path, marker=True):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(PASSWD)
    if marker:
        (tmp_path / "var/lib/boppos").mkdir(parents=True, exist_ok=True)
        (tmp_path / "var/lib/boppos/first-run").write_text("mode=welcome\n")
    (tmp_path / "usr/share/boppos").mkdir(parents=True, exist_ok=True)
    (tmp_path / "usr/share/boppos/start-in.conf").write_text(DECK_START_IN)
    sessions = tmp_path / "usr/share/wayland-sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    for name in ("gamescope-session", "plasma"):
        (sessions / f"{name}.desktop").write_text("[Desktop Entry]\n")
    # what the image writes on every boot: the user, and gamescope-session as the default
    conf = tmp_path / "etc/plasmalogin.conf.d"
    conf.mkdir(parents=True, exist_ok=True)
    (conf / "00-boppos-autologin.conf").write_text("[Autologin]\nUser=ripps\nRelogin=true\n"
                                                    "Session=gamescope-session.desktop\n")
    return tmp_path


def load_helper_script():
    loader = importlib.machinery.SourceFileLoader("oobe_helper_script", str(REPO / "libexec/boppos/oobe-helper"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def apply(steps, root):
    """Run the helper's own write() with the paths moved under the temporary root."""
    script = load_helper_script()
    for step in steps:
        assert isinstance(step, h.Write)
        script.write(h.Write(str(root / step.path.lstrip("/")), step.text, step.mode))


def test_plasmalogin_is_supported(tmp_path):
    cfg, problem = firstrun.load_start_in(deck_root(tmp_path))
    assert problem == "" and cfg.display_manager == "plasmalogin"
    assert cfg.sessions == {"gaming": "gamescope-session", "desktop": "plasma"}


@pytest.mark.parametrize("choice, session", [("desktop", "plasma.desktop"),
                                              ("gaming", "gamescope-session.desktop")])
def test_set_start_in_writes_the_steamos_session_file(tmp_path, choice, session):
    root = deck_root(tmp_path)
    steps = h.steps_for(["set-start-in", "ripps", choice], None, root, caller=1000)
    assert [s.path for s in steps] == ["/etc/plasmalogin.conf.d/zz-steamos-autologin.conf"]
    apply(steps, root)
    zz = root / "etc/plasmalogin.conf.d/zz-steamos-autologin.conf"
    text = zz.read_text()
    assert "[Autologin]" in text and "User=ripps\n" in text and f"Session={session}\n" in text
    assert oct(zz.stat().st_mode & 0o777) == "0o644"
    # the image's own file is left alone; ours sorts after it and wins
    assert "gamescope-session.desktop" in (root / "etc/plasmalogin.conf.d/00-boppos-autologin.conf").read_text()
    cfg, _ = firstrun.load_start_in(root)
    assert firstrun.current_start_in(cfg, root) == choice


def test_current_start_in_reads_what_steam_set_session_wrote(tmp_path):
    root = deck_root(tmp_path)
    cfg, _ = firstrun.load_start_in(root)
    assert firstrun.current_start_in(cfg, root) is None  # nobody chose yet: the tour asks
    (root / "etc/plasmalogin.conf.d/zz-steamos-autologin.conf").write_text("[Autologin]\nSession=plasma.desktop\n")
    assert firstrun.current_start_in(cfg, root) == "desktop"


def test_plasmalogin_start_in_is_refused_without_a_pending_first_run(tmp_path):
    from boppos.privileged import UsageError

    with pytest.raises(UsageError):
        h.steps_for(["set-start-in", "ripps", "gaming"], None, deck_root(tmp_path, marker=False), caller=1000)


def test_pre_steam_fallback_with_the_marker_shows_only_network_and_steam():
    env = {"BOPPOS_FIRST_BOOT_MISSING": "steam network"}
    marker_there = Live(True, False, False)
    assert keys(env, marker_there) == ["network", "steam", "done"]
    assert firstboot.plan(env, marker_there).only_missing  # end_welcome applies nothing: the marker stays
