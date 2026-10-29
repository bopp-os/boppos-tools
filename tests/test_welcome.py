from boppos import hwprofiles, welcome


def test_flag_roundtrip(tmp_path):
    env = {"XDG_STATE_HOME": str(tmp_path)}
    assert not welcome.is_done(env)
    welcome.mark_done(env)
    assert welcome.is_done(env)
    assert (tmp_path / "boppos-deck/welcome-done").exists()
    welcome.clear_done(env)
    assert not welcome.is_done(env)


def test_clear_without_flag_is_fine(tmp_path):
    welcome.clear_done({"XDG_STATE_HOME": str(tmp_path)})


def test_gaming_mode_skips_desktop_only_steps():
    desktop = [s.key for s in welcome.steps_for(False)]
    gaming = [s.key for s in welcome.steps_for(True)]
    assert desktop == gaming  # security is the only desktop-only step, and it is not wired yet
    assert "security" in [s.key for s in welcome.STEPS if not s.gaming]
    assert desktop[0] == gaming[0] == "welcome" and desktop[-1] == gaming[-1] == "done"


def test_choices_empty():
    c = welcome.Choices()
    assert c.is_empty()
    c.decky = True
    assert not c.is_empty()


def test_initial_choices_match_todays_defaults():
    c = welcome.initial_choices(hwprofiles.PROFILES["amd-desktop"])
    assert "io.github.kolunmi.Bazaar" in c.flatpaks
    assert not (c.decky or c.steam_shortcuts or c.howdy or c.tpm_unlock)


def test_app_options_are_grouped_apps_only():
    opts = welcome.app_options()
    assert opts, "the curated list should load from the checkout"
    assert all(a.kind == "app" for apps in opts.values() for a in apps)


def test_karg_options_suggest_by_device():
    opts = welcome.karg_options(hwprofiles.PROFILES["vm"])
    assert opts and not any(o.suggested for o in opts)


def test_hardware_detection_uses_the_given_root(tmp_path):
    assert not welcome.has_tpm(tmp_path) and not welcome.has_camera(tmp_path)
    (tmp_path / "dev").mkdir()
    (tmp_path / "dev/tpmrm0").touch()
    (tmp_path / "dev/video0").touch()
    assert welcome.has_tpm(tmp_path) and welcome.has_camera(tmp_path)


def test_plan_lists_each_choice_in_order():
    c = welcome.Choices(flatpaks={"io.github.kolunmi.Bazaar"}, decky=True, steam_shortcuts=True,
                        kargs={"20-sleep-deep"}, firmware_check=True, howdy=True, tpm_unlock=True,
                        auto_updates=True, dev_container=True)
    # choices that are not wired yet add nothing to the plan
    assert [a.area for a in welcome.build_plan(c)] == ["Apps", "Apps", "Apps", "Hardware"]


def test_gaming_plan_defers_steam_items():
    c = welcome.Choices(decky=True, steam_shortcuts=True)
    texts = [a.text for a in welcome.build_plan(c, gaming=True)]
    assert all("first Steam login" in t for t in texts)
    assert not any("first Steam login" in a.text for a in welcome.build_plan(c))


def test_empty_plan():
    assert welcome.build_plan(welcome.Choices()) == []
    assert welcome.run_plan([]) == ["[dry run] nothing chosen, nothing to do"]


def test_run_plan_is_dry(monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise AssertionError("the dry run must not start a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    plan = welcome.build_plan(welcome.Choices(decky=True, kargs={"20-sleep-deep"}, howdy=True))
    lines = welcome.run_plan(plan)
    assert len(lines) == 2 and all(line.startswith("[dry run] would:") for line in lines)


def test_the_wizard_is_live_and_hides_what_is_not_wired():
    assert welcome.DRY_RUN is False
    assert welcome.WIRED == {"flatpaks", "steam_shortcuts", "decky", "kargs"}
    assert {s.key for s in welcome.STEPS if not s.wired} == {"security", "updates"}
    assert not {s.key for s in welcome.steps_for(False)} & {"security", "updates"}


def test_no_kargs_preset_is_ever_preselected():
    assert welcome.initial_choices().kargs == set()
    assert "left alone" in welcome.KARGS_WARNING


def test_kargs_plan_line_warns():
    text = welcome.build_plan(welcome.Choices(kargs={"20-sleep-deep"}))[0].text
    assert "leave these off unless you need them" in text


def test_karg_options_follow_the_graphics_vendor():
    amd = {o.name for o in welcome.karg_options(hwprofiles.PROFILES["amd-desktop"]) if o.suggested}
    nv = {o.name for o in welcome.karg_options(hwprofiles.PROFILES["nvidia-desktop"]) if o.suggested}
    assert "70-amdgpu-no-runpm" in amd and "71-nvidia-drm-modeset" not in amd
    assert "71-nvidia-drm-modeset" in nv and "70-amdgpu-no-runpm" not in nv
    assert "41-intel-psr-off" in nv


def test_handheld_defaults_add_steam_items_but_never_kargs():
    c = welcome.initial_choices(hwprofiles.PROFILES["steam-deck"])
    assert c.steam_shortcuts and c.decky and c.kargs == set()
    assert "com.vysp3r.ProtonPlus" in c.flatpaks and "io.github.kolunmi.Bazaar" in c.flatpaks
    d = welcome.initial_choices(hwprofiles.PROFILES["amd-desktop"])
    assert not (d.steam_shortcuts or d.decky)


def test_unwired_switches_are_never_recommended_on():
    for hw in hwprofiles.PROFILES.values():
        c = welcome.initial_choices(hw)
        assert not (c.firmware_check or c.auto_updates or c.howdy or c.tpm_unlock or c.dev_container)


def test_everything_recommended_starts_on_and_nothing_else():
    for hw in hwprofiles.PROFILES.values():
        recs = welcome.recommended(hw)
        c = welcome.initial_choices(hw)
        assert {r.key for r in recs if r.kind == "flatpak"} == c.flatpaks
        on = {a for a in welcome.FLAGS if getattr(c, a)}
        assert {r.key for r in recs if r.kind == "flag"} == on
        assert all(r.key in welcome.FLAGS for r in recs if r.kind == "flag")
        assert c.kargs == set() and not (c.howdy or c.tpm_unlock or c.dev_container)


def test_recommended_follows_the_kind_and_belongs_to_a_step():
    steps = {s.key for s in welcome.STEPS}
    deck = welcome.recommended(hwprofiles.PROFILES["steam-deck"])
    assert {("apps", "decky"), ("apps", "steam_shortcuts")} <= {(r.step, r.key) for r in deck}
    assert all(r.step in steps for r in deck)


def test_presets_that_cannot_work_go_last_and_say_why():
    opts = welcome.karg_options(hwprofiles.PROFILES["amd-desktop"])
    fit = {o.name: o.fit for o in opts}
    assert fit["70-amdgpu-no-runpm"] == "fits"
    assert fit["71-nvidia-drm-modeset"] == fit["41-intel-psr-off"] == "unsupported"
    assert fit["60-usb-mouse-1khz"] == "optional"
    order = ["fits", "optional", "risky", "unsupported"].index
    assert [o.fit for o in opts] == sorted((o.fit for o in opts), key=order)
    why = {o.name: o.reason for o in opts}
    assert "NVIDIA" in why["71-nvidia-drm-modeset"] and why["60-usb-mouse-1khz"] == ""


def test_a_vm_has_no_supported_graphics_presets():
    fit = {o.name: o.fit for o in welcome.karg_options(hwprofiles.PROFILES["vm"])}
    assert fit["70-amdgpu-no-runpm"] == fit["71-nvidia-drm-modeset"] == "unsupported"
    assert "fits" not in fit.values()


def test_a_model_match_that_misses_is_unsupported(tmp_path):
    from boppos import kargs

    path = tmp_path / "90-x.conf"
    path.write_text("kargs = foo=1\nmatch.sys_vendor = Valve\n")
    d = kargs.parse_dropin("90-x", "usr", path)
    assert d.match and not d.error
    assert welcome.karg_fit(d, hwprofiles.PROFILES["amd-desktop"])[0] == "unsupported"
    assert welcome.karg_fit(d, hwprofiles.PROFILES["steam-deck"])[0] == "fits"


def test_risky_presets_that_fit_anywhere_are_unsupported():
    for hw in hwprofiles.PROFILES.values():
        fit = {o.name: o for o in welcome.karg_options(hw)}
        for name in ("20-sleep-deep", "50-pci-noaer", "80-intel-iommu-on"):
            assert fit[name].fit == "risky" and fit[name].locked and "Medium risk" in fit[name].reason
        assert fit["10-nvme-no-apst"].fit == "optional"


def test_amd_overclock_is_its_own_preset_that_only_fits_amd_graphics():
    for key, hw in hwprofiles.PROFILES.items():
        oc = next(o for o in welcome.karg_options(hw) if o.name == welcome.AMD_OVERCLOCK)
        assert oc.risk_level == "high"
        assert oc.fit == ("fits" if "amd" in hw.gpus else "unsupported"), key
        if oc.fit == "unsupported":
            assert "AMD" in oc.reason
    for hw in hwprofiles.PROFILES.values():
        assert welcome.AMD_OVERCLOCK not in welcome.initial_choices(hw).kargs


def test_every_app_of_the_curated_list_is_recommended_and_switched_on():
    hw = hwprofiles.PROFILES["amd-desktop"]
    recommended = {r.key for r in welcome.recommended(hw) if r.kind == "flatpak"}
    assert "it.mijorus.gearlever" in recommended
    assert welcome.curated_flatpak_ids() and welcome.curated_flatpak_ids() <= recommended
    assert recommended <= welcome.initial_choices(hw).flatpaks
    extra_only = {a.ref for apps in welcome.app_options().values() for a in apps} - recommended
    assert "net.lutris.Lutris" in extra_only  # the opt-in extras stay opt-in


def test_the_choices_become_commands_in_a_fixed_order():
    tool = lambda name: f"/usr/bin/{name}"  # noqa: E731
    assert welcome.apply_commands(welcome.Choices(), tool) == []
    c = welcome.Choices(flatpaks={"it.mijorus.gearlever", "io.github.kolunmi.Bazaar"}, decky=True,
                        steam_shortcuts=True, kargs={"b-two", "a-one"}, howdy=True, firmware_check=True)
    assert welcome.apply_commands(c, tool) == [
        ["/usr/bin/install-optional-flatpaks", "--json", "--yes", "--only",
         "io.github.kolunmi.Bazaar,it.mijorus.gearlever"],
        ["/usr/bin/bopp-steam-shortcut", "--json", "sync", "--add", "boppos-deck", "--queue-ok"],
        ["/usr/bin/bopp-decky", "--json", "install"],
        ["/usr/bin/bopp-kargs", "--json", "enable", "a-one"],
        ["/usr/bin/bopp-kargs", "--json", "enable", "b-two"]]  # howdy and the firmware check run nothing
    plan = welcome.build_plan(welcome.Choices(flatpaks={"it.mijorus.gearlever", "io.github.kolunmi.Bazaar"}))
    assert "Install 2 Flatpak" in plan[0].text and "Gear Lever" in plan[0].text
