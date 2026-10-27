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
