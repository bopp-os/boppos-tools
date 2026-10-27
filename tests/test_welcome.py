from boppos import welcome


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
    assert "security" in desktop and "security" not in gaming
    assert desktop[0] == gaming[0] == "welcome" and desktop[-1] == gaming[-1] == "done"


def test_choices_empty():
    c = welcome.Choices()
    assert c.is_empty()
    c.decky = True
    assert not c.is_empty()


def test_initial_choices_match_todays_defaults():
    c = welcome.initial_choices()
    assert "io.github.kolunmi.Bazaar" in c.flatpaks
    assert c.firmware_check
    assert not (c.decky or c.steam_shortcuts or c.howdy or c.tpm_unlock)


def test_app_options_are_grouped_apps_only():
    opts = welcome.app_options()
    assert opts, "the curated list should load from the checkout"
    assert all(a.kind == "app" for apps in opts.values() for a in apps)


def test_karg_options_suggest_by_device():
    opts = welcome.karg_options({"sys_vendor": "Nobody", "product_name": "Nothing"})
    assert opts and not any(o.suggested for o in opts)
    assert [o.name for o in opts] == sorted(o.name for o in opts)


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
    areas = [a.area for a in welcome.build_plan(c)]
    assert areas == ["Apps", "Apps", "Apps", "Hardware", "Hardware", "Security", "Security", "Updates",
                     "Updates"]


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
    assert len(lines) == 3 and all(line.startswith("[dry run] would:") for line in lines)


def test_dry_run_is_on():
    assert welcome.DRY_RUN is True
