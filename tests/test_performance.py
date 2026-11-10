import json
from pathlib import Path

import pytest
from boppos import hwprofiles, performance
from boppos.performance import Settings
from boppos.privileged import UsageError, performance_argv


def put(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def handheld(tmp_path):
    put(tmp_path, "sys/class/drm/card1-eDP-1/amdgpu/panel_power_savings", "0")
    put(tmp_path, "sys/module/amdgpu/parameters/abmlevel", "-1")
    put(tmp_path, "sys/devices/system/cpu/cpufreq/boost", "1")
    for n in (0, 1):
        put(tmp_path, f"sys/devices/system/cpu/cpufreq/policy{n}/boost", "1")
    put(tmp_path, "sys/class/power_supply/BAT0/type", "Battery")
    put(tmp_path, "sys/class/power_supply/BAT0/status", "Discharging")
    put(tmp_path, "sys/class/power_supply/ADP0/type", "Mains")
    put(tmp_path, "sys/class/power_supply/ADP0/online", "0")
    return tmp_path


def values(root: Path) -> tuple[str, list[str]]:
    abm = (root / "sys/class/drm/card1-eDP-1/amdgpu/panel_power_savings").read_text()
    return abm, [f.read_text() for f in performance.boost_files(root)]


def test_settings_round_trip_and_bad_files(tmp_path):
    assert performance.load(tmp_path) == Settings()
    performance.save(Settings(3, "off-on-battery"), tmp_path)
    assert performance.load(tmp_path) == Settings(3, "off-on-battery")
    put(tmp_path, "etc/boppos/performance.json", json.dumps({"abm": 9, "boost": "turbo"}))
    assert performance.load(tmp_path) == Settings()
    put(tmp_path, "etc/boppos/performance.json", "not json")
    assert performance.load(tmp_path) == Settings()


@pytest.mark.parametrize("online,status,expected", [("0", "Discharging", True), ("1", "Charging", False),
                                                    ("1", "Full", False)])
def test_on_battery_follows_the_charger(handheld, online, status, expected):
    put(handheld, "sys/class/power_supply/ADP0/online", online)
    put(handheld, "sys/class/power_supply/BAT0/status", status)
    assert performance.on_battery(handheld) is expected


def test_on_battery_without_a_charger_entry_uses_battery_status(tmp_path):
    put(tmp_path, "sys/class/power_supply/BAT0/type", "Battery")
    put(tmp_path, "sys/class/power_supply/BAT0/status", "Discharging")
    assert performance.on_battery(tmp_path)
    put(tmp_path, "sys/class/power_supply/BAT0/status", "Not charging")
    assert not performance.on_battery(tmp_path)


def test_no_battery_is_never_on_battery(tmp_path):
    put(tmp_path, "sys/class/power_supply/ADP0/type", "Mains")
    put(tmp_path, "sys/class/power_supply/ADP0/online", "0")
    assert not performance.on_battery(tmp_path)


def test_apply_on_battery_then_on_charger(handheld):
    s = Settings(3, "off-on-battery")
    assert performance.apply(s, handheld, battery=True)
    assert values(handheld) == ("3", ["0", "0", "0"])
    assert performance.apply(s, handheld, battery=True) == []  # nothing differs: no writes
    performance.apply(s, handheld, battery=False)
    assert values(handheld) == ("0", ["1", "1", "1"])


def test_system_settings_are_left_alone(handheld):
    put(handheld, "sys/class/drm/card1-eDP-1/amdgpu/panel_power_savings", "2")  # ppd's choice
    assert performance.apply(Settings(), handheld, battery=True) == []
    assert values(handheld) == ("2", ["1", "1", "1"])


def test_abm_karg_locks_the_level(handheld):
    put(handheld, "sys/module/amdgpu/parameters/abmlevel", "1")
    assert performance.abm_locked(handheld)
    performance.apply(Settings(3, "system"), handheld, battery=True)
    assert values(handheld)[0] == "0"


def test_release_restores_boost_and_screen(handheld):
    performance.apply(Settings(3, "off-on-battery"), handheld, battery=True)
    performance.release(Settings(3, "off-on-battery"), Settings(), handheld)
    assert values(handheld) == ("0", ["1", "1", "1"])


def test_change_settings_without_side_effects(handheld):
    performance.change_settings(Settings(2, "system"), handheld, side_effects=False)
    assert performance.load(handheld) == Settings(2, "system")
    assert values(handheld)[0] == "2"


@pytest.mark.parametrize("text,expected", [
    ("running Lavd in Auto mode", ("lavd", "auto")),
    ("running scx_bpfland in Gaming mode\n", ("bpfland", "gaming")),
    ("no scx scheduler running", ("none", "")),
])
def test_parse_scx_get(text, expected):
    assert performance.parse_scx_get(text) == expected


def test_parse_scx_list():
    assert performance.parse_scx_list('supported schedulers: ["bpfland", "flash", "lavd", "scx_rusty"]') == \
        ["bpfland", "flash", "lavd", "rusty"]
    assert performance.parse_scx_list("supported schedulers: bpfland, lavd") == ["bpfland", "lavd"]


VENDOR = 'default_sched = "scx_bpfland"\ndefault_mode = "Auto"\n\n[scheds.scx_lavd]\nauto_mode = []\n'


def test_edit_scx_config_keeps_tables():
    out = performance.edit_scx_config(VENDOR, "lavd", "powersave")
    assert performance.parse_scx_config(out) == ("lavd", "powersave")
    assert 'default_mode = "PowerSave"' in out and "[scheds.scx_lavd]\nauto_mode = []" in out
    assert out.count("default_sched") == 1
    assert performance.parse_scx_config(performance.edit_scx_config(VENDOR, "none", "auto")) == ("", "")


def test_write_scx_config_starts_from_the_vendor_file(tmp_path):
    put(tmp_path, "usr/share/scx_loader/config.toml", VENDOR)
    path = performance.write_scx_config("lavd", "gaming", tmp_path)
    assert path == tmp_path / "etc/scx_loader.toml"
    assert "[scheds.scx_lavd]" in path.read_text()
    assert performance.parse_scx_config(performance.scx_config(tmp_path).read_text()) == ("lavd", "gaming")


def test_write_scx_config_prefers_the_etc_directory_file(tmp_path):
    put(tmp_path, "etc/scx_loader/config.toml", VENDOR)
    assert performance.write_scx_config("lavd", "auto", tmp_path) == tmp_path / "etc/scx_loader/config.toml"


def test_scx_commands():
    assert performance.scx_commands("lavd", "auto", "none") == [
        ["/usr/bin/scxctl", "start", "--sched", "lavd", "--mode", "auto"]]
    assert performance.scx_commands("lavd", "gaming", "bpfland")[0][1] == "switch"
    assert performance.scx_commands("none", "auto", "lavd") == [["/usr/bin/scxctl", "stop"]]
    assert performance.scx_commands("none", "auto", "none") == []


def test_validate_scx():
    performance.validate_scx("lavd", "auto", ["lavd"])
    performance.validate_scx("none", "auto", [])
    with pytest.raises(ValueError):
        performance.validate_scx("rusty", "auto", ["lavd"])
    with pytest.raises(ValueError):
        performance.validate_scx("lavd", "turbo", ["lavd"])


@pytest.mark.parametrize("hardware,profile,abm,sched", [
    ("legion-go", "legion-go", 3, "lavd"),
    ("steam-deck", "steam-deck", "system", "lavd"),
    ("rog-ally", "handheld", 2, "lavd"),
    ("onexplayer-f1", "handheld", 2, "lavd"),
    ("framework-intel", "laptop", 1, "none"),
    ("amd-laptop", "laptop", 1, "none"),
    ("amd-desktop", "desktop", "system", "none"),
    ("nvidia-desktop", "desktop", "system", "none"),
    ("vm", "vm", "system", "none"),
])
def test_profile_for_each_kind_of_hardware(hardware, profile, abm, sched):
    rec = performance.recommended(hwprofiles.PROFILES[hardware])
    assert (rec.profile, rec.abm, rec.sched) == (profile, abm, sched)
    assert rec.boost == "system" and rec.mode == "auto" and rec.note


def test_every_profile_is_valid_and_reachable():
    keys = [p.key for p in performance.PERF_PROFILES]
    assert len(keys) == len(set(keys))
    for prof in performance.PERF_PROFILES:
        performance.parse_abm(prof.abm)
        performance.parse_boost(prof.boost)
        assert prof.sched == performance.SCX_NONE or performance._SCHED.match(prof.sched)
        assert prof.mode in performance.SCX_MODES
        assert prof.kinds and set(prof.kinds) <= set(hwprofiles.KINDS)
    for kind in hwprofiles.KINDS:  # no kind falls through to a guess
        assert any(not p.products and kind in p.kinds for p in performance.PERF_PROFILES)


def test_device_profile_needs_the_right_kind():
    # a desktop board that reports product "83E1" is not a Legion Go
    hw = hwprofiles.Hardware("detected", "x", "desktop", {"product_name": "83E1"})
    assert performance.profile_for(hw).key == "desktop"


def test_unknown_kind_gets_the_desktop_profile():
    assert performance.profile_for(hwprofiles.Hardware("detected", "x", "toaster")).key == "desktop"


def test_effective_settings_follow_the_hardware(handheld, tmp_path_factory):
    rec = performance.recommended(hwprofiles.PROFILES["legion-go"])
    assert performance.effective(rec, handheld) == Settings(3, "system")
    assert performance.effective(rec, tmp_path_factory.mktemp("bare")) == Settings()  # no battery
    for f in performance.panel_files(handheld):
        f.unlink()
    assert performance.effective(rec, handheld) == Settings()  # a battery but no amdgpu panel: nothing to manage


def test_recommended_label_for_a_detected_legion_go():
    hw = hwprofiles.Hardware("detected", "LENOVO 83E1", "handheld",
                             {"sys_vendor": "LENOVO", "product_name": "83E1"})
    assert performance.recommended(hw).label == "Lenovo Legion Go"


@pytest.mark.parametrize("argv", [["set-abm", "3"], ["set-abm", "system"], ["set-boost", "off-on-battery"],
                                  ["scx", "lavd", "powersave"], ["scx", "none", "auto"], ["recommended"]])
def test_helper_argv_allowed(argv):
    assert performance_argv(argv) == argv


@pytest.mark.parametrize("argv", [[], ["set-abm", "4"], ["set-abm", "3", "x"], ["set-boost", "on"],
                                  ["scx", "lavd;id", "auto"], ["scx", "lavd", "turbo"], ["scx", "lavd"],
                                  ["recommended", "now"], ["apply"]])
def test_helper_argv_rejected(argv):
    with pytest.raises(UsageError):
        performance_argv(argv)


SCX_UP = {"available": True, "sched": "lavd", "mode": "auto", "schedulers": ["bpfland", "lavd"],
          "default_sched": "lavd", "default_mode": "auto"}


def legion():
    return performance.recommended(hwprofiles.PROFILES["legion-go"])


def test_plan_lists_each_change_with_from_and_to(handheld):
    scx = {**SCX_UP, "sched": "bpfland", "mode": "gaming", "default_sched": "none"}
    plan = performance.plan(legion(), Settings(1, "system"), scx, handheld)
    assert plan == [
        {"what": "Screen power saving on battery", "from": "Low", "to": "Strong"},
        {"what": "CPU scheduler", "from": "scx_bpfland (Gaming)", "to": "scx_lavd (Auto)"},
        {"what": "CPU scheduler at boot", "from": "None (kernel scheduler)", "to": "scx_lavd (Auto)"},
    ]


def test_plan_is_empty_when_everything_matches(handheld):
    assert performance.plan(legion(), Settings(3, "system"), SCX_UP, handheld) == []


def test_plan_reports_boost_being_given_back_to_the_system(handheld):
    plan = performance.plan(legion(), Settings(3, "off-on-battery"), SCX_UP, handheld)
    assert plan == [{"what": "CPU boost", "from": "Off on battery", "to": "System default"}]


def test_plan_stopping_the_scheduler_on_a_desktop(tmp_path_factory):
    desktop = performance.recommended(hwprofiles.PROFILES["amd-desktop"])
    bare = tmp_path_factory.mktemp("desktop")
    plan = performance.plan(desktop, Settings(), SCX_UP, bare)
    assert [c["to"] for c in plan] == ["None (kernel scheduler)"] * 2
    assert performance.plan(desktop, Settings(), {"available": False}, bare) == []


def test_plan_skips_a_scheduler_that_is_not_installed(handheld):
    scx = {**SCX_UP, "sched": "none", "schedulers": ["bpfland"], "default_sched": "none"}
    assert performance.plan(legion(), Settings(3, "system"), scx, handheld) == []


def test_plan_leaves_out_what_the_machine_cannot_use(tmp_path_factory):
    # a machine with no battery drops the saved screen level, and the plan says so
    bare = tmp_path_factory.mktemp("nobattery")
    assert performance.plan(legion(), Settings(3, "system"), {"available": False}, bare) == [
        {"what": "Screen power saving on battery", "from": "Strong", "to": "System default"}]
